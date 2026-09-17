"""Basic agent class. See https://github.com/Muilrz/Arkui-UT-Code-Agent for visual explanation
or https://minimal-agent.com for a tutorial on the basic building principles.
"""

import copy
import json
import logging
import time
import traceback
from pathlib import Path

from jinja2 import StrictUndefined, Template
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from arkui_ut_agent import Environment, Model, __version__
from arkui_ut_agent.agents.context import ContextBuilder
from arkui_ut_agent.agents.control import (
    _DIAGNOSE_INSTRUCTION,
    ControlDecision,
    DecisionKind,
    _parse_control_decision,
)
from arkui_ut_agent.agents.integration import _evidence_from_tool_observation
from arkui_ut_agent.agents.planning import (
    _INITIAL_PLANNING_INSTRUCTION,
    _parse_initial_plan,
    _parse_replan,
    _replanning_instruction,
)
from arkui_ut_agent.agents.state import AgentState, MemoryUpdateEvent, StopReason
from arkui_ut_agent.agents.stop import StopPolicy
from arkui_ut_agent.agents.trace import ExecutionStepTrace
from arkui_ut_agent.exceptions import FormatError, InterruptAgentFlow, LimitsExceeded, Submitted, TimeExceeded
from arkui_ut_agent.models.utils.content_string import get_content_string
from arkui_ut_agent.tools.contracts import Observation, ToolResult, normalize_tool_result
from arkui_ut_agent.tools.execution import _backend_attribute, _effective_timeout, _normalize_execution_result
from arkui_ut_agent.utils.serialize import recursive_merge

_JSON_ADAPTER = TypeAdapter(JsonValue, config=ConfigDict(allow_inf_nan=False))
_RUNTIME_FEEDBACK_MAX_CHARS = 2_000
_RUNTIME_FEEDBACK_OMISSION_MARKER = "\n…[runtime feedback omitted]…\n"
_DIAGNOSIS_RECORDS_MAX_CHARS = 4_000
_DIAGNOSIS_RECORDS_OMISSION_MARKER = "\n…[tool observation records omitted]…\n"
_CONTROL_ONLY_PHASES = frozenset({"initial_planning", "diagnose", "replan"})
_TRACE_MISSING = object()


class AgentConfig(BaseModel):
    """Check the config files in arkui_ut_agent/config for example settings."""

    system_template: str
    """Template for the system message (the first message)."""
    instance_template: str
    """Template for the first user message specifying the task (the second message overall)."""
    step_limit: int = 0
    """Maximum number of steps the agent can take."""
    cost_limit: float = 3.0
    """Stop agent after exceeding (!) this cost."""
    wall_time_limit_seconds: int = 0
    """Stop agent after this many seconds of wall-clock time. 0 means no limit."""
    max_consecutive_format_errors: int = 3
    """Exit after this many format errors in a row (0 = no limit)."""
    initial_planning: bool = True
    """Create a structured Plan before the first model-driven action step."""
    diagnose_after_observation: bool = True
    """Create a control-only diagnosis after actual Tool Observations."""
    repeat_detection: bool = True
    """Trigger Replan after an exact repeated Tool/Input/Observation triplet."""
    integrated_control_loop: bool = True
    """Require explicit decisions and apply Stage 4 control/stop semantics."""
    output_path: Path | None = None
    """Save the trajectory to this path."""


class DefaultAgent:
    def __init__(self, model: Model, env: Environment, *, config_class: type = AgentConfig, **kwargs):
        """See the `AgentConfig` class for permitted keyword arguments."""
        self.config = config_class(**kwargs)
        self.messages: list[dict] = []
        self.model = model
        self.env = env
        self.extra_template_vars = {}
        self.logger = logging.getLogger("agent")
        self.cost = 0.0
        self.n_calls = 0
        self.n_consecutive_format_errors = 0
        self._start_time = time.time()
        self.state: AgentState | None = None
        self.memory_updates: list[MemoryUpdateEvent] = []
        self.tool_executions: list[dict] = []
        self.step_traces: list[ExecutionStepTrace] = []
        self._step_count = 0
        self._active_step_id: str | None = None
        self._pending_diagnosis_records: list[dict] = []
        self._repeat_replan_signature: str | None = None
        self._post_replan_evidence_identities: frozenset[str] | None = None
        self.context_builder = ContextBuilder()

    def get_template_vars(self, **kwargs) -> dict:
        return recursive_merge(
            self.config.model_dump(),
            self.env.get_template_vars(),
            self.model.get_template_vars(),
            {
                "n_model_calls": self.n_calls,
                "model_cost": self.cost,
                "elapsed_seconds": int(time.time() - self._start_time),
            },
            self.extra_template_vars,
            kwargs,
        )

    def _render_template(self, template: str) -> str:
        return Template(template, undefined=StrictUndefined).render(**self.get_template_vars())

    def add_messages(self, *messages: dict) -> list[dict]:
        self.logger.debug(messages)  # set log level to debug to see
        self.messages.extend(messages)
        return list(messages)

    def handle_uncaught_exception(self, e: Exception) -> list[dict]:
        self._set_stop_reason(StopReason.IRRECOVERABLE_FAILURE)
        return self.add_messages(
            self.model.format_message(
                role="exit",
                content=str(e),
                extra={
                    "exit_status": type(e).__name__,
                    "submission": "",
                    "stop_reason": StopReason.IRRECOVERABLE_FAILURE.value,
                    "exception_str": str(e),
                    "traceback": traceback.format_exc(),
                },
            )
        )

    def run(self, task: str = "", **kwargs) -> dict:
        """Run step() until agent is finished. Returns dictionary with exit_status, submission keys."""
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        # Reset task-local state/trace, preserving existing cost/call limit semantics.
        self.state = AgentState(goal=task.strip() or "Unspecified task")
        self.memory_updates = []
        self.tool_executions = []
        self.step_traces = []
        self._step_count = 0
        self._active_step_id = None
        self._pending_diagnosis_records = []
        self._repeat_replan_signature = None
        self._post_replan_evidence_identities = None
        self.add_messages(
            self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
            self.model.format_message(role="user", content=self._render_template(self.config.instance_template)),
        )
        while True:
            try:
                if self.state is not None and self.state.stop_reason is not None:
                    self._emit_stop_message(self.state.stop_reason)
                elif self._should_create_initial_plan():
                    self.create_initial_plan()
                elif stop_reason := StopPolicy.for_decision(
                    None if self.state is None else self.state.current_decision
                ):
                    self._set_stop_reason(stop_reason)
                    self._emit_stop_message(stop_reason)
                elif self._should_replan_current_plan():
                    self.replan()
                elif self._should_diagnose_pending_observations():
                    self.diagnose_pending_observations()
                elif self._can_execute_action_boundary():
                    self.step()
                else:
                    self._set_stop_reason(StopReason.IRRECOVERABLE_FAILURE)
                    self._emit_stop_message(
                        StopReason.IRRECOVERABLE_FAILURE,
                        content="No explicit executable control decision is available.",
                    )
                self.n_consecutive_format_errors = 0  # reset on any clean step
            except FormatError as e:
                # The call was billed before parsing failed, so query() never got to charge it.
                self.cost += e.messages[0].get("extra", {}).get("cost", 0.0)
                for message in e.messages:
                    extra = message.setdefault("extra", {})
                    if extra.get("phase") not in _CONTROL_ONLY_PHASES and self._active_step_id is not None:
                        extra["step_id"] = self._active_step_id
                self.n_consecutive_format_errors += 1
                if 0 < self.config.max_consecutive_format_errors <= self.n_consecutive_format_errors:
                    self._set_stop_reason(StopReason.IRRECOVERABLE_FAILURE)
                    self.add_messages(
                        *e.messages,
                        {
                            "role": "exit",
                            "content": "RepeatedFormatError",
                            "extra": {
                                "exit_status": "RepeatedFormatError",
                                "submission": "",
                                "stop_reason": StopReason.IRRECOVERABLE_FAILURE.value,
                            },
                        },
                    )
                else:
                    self.add_messages(*e.messages)
            except InterruptAgentFlow as e:
                self._apply_interrupt_stop_reason(e)
                self.add_messages(*e.messages)
            except Exception as e:
                self.handle_uncaught_exception(e)
                raise
            finally:
                self.save(self.config.output_path)
            if self.messages[-1].get("role") == "exit":
                break
        return self.messages[-1].get("extra", {})

    def _set_stop_reason(self, reason: StopReason) -> None:
        if self.state is None:
            return
        self.state = self.state.updated(stop_reason=reason)

    def _emit_stop_message(self, reason: StopReason, *, content: str | None = None) -> None:
        self.add_messages({
            "role": "exit",
            "content": content or reason.value,
            "extra": {
                "exit_status": reason.value,
                "submission": "",
                "stop_reason": reason.value,
            },
        })

    def _apply_interrupt_stop_reason(self, error: InterruptAgentFlow) -> None:
        reason = None
        for message in error.messages:
            raw_reason = message.get("extra", {}).get("stop_reason")
            if raw_reason is not None:
                try:
                    reason = StopReason(raw_reason)
                except ValueError:
                    continue
                else:
                    break
        if reason is None and isinstance(error, Submitted):
            reason = StopReason.SUCCESS
        elif reason is None and isinstance(error, TimeExceeded):
            reason = StopReason.IRRECOVERABLE_FAILURE
        elif reason is None and isinstance(error, LimitsExceeded):
            reason = StopPolicy.for_limits(
                step_limit=self.config.step_limit,
                n_calls=self.n_calls,
                cost_limit=self.config.cost_limit,
                cost=self.cost,
            ) or StopReason.IRRECOVERABLE_FAILURE
        elif reason is None and any(message.get("role") == "exit" for message in error.messages):
            reason = StopReason.IRRECOVERABLE_FAILURE
        if reason is None:
            return
        self._set_stop_reason(reason)
        for message in error.messages:
            if message.get("role") == "exit":
                message.setdefault("extra", {}).setdefault("stop_reason", reason.value)

    def step(self) -> list[dict]:
        """Query the LM, execute actions."""
        return self.execute_actions(self.query())

    def _can_execute_action_boundary(self) -> bool:
        if not self.config.integrated_control_loop:
            return True
        return bool(
            self.state is not None
            and self.state.current_decision is not None
            and self.state.current_decision.kind in StopPolicy.ACTIONABLE_DECISIONS
        )

    def _ensure_action_decision(self) -> ControlDecision:
        if self.state is None:
            self.state = AgentState(goal="Unspecified task")
            decision = ControlDecision(
                kind=DecisionKind.ACT,
                rationale="The public action boundary was invoked directly.",
            )
            self.state = self.state.updated(current_decision=decision)
            return decision
        decision = self.state.current_decision
        if decision is not None and decision.kind in StopPolicy.ACTIONABLE_DECISIONS:
            return decision
        if not self.config.integrated_control_loop:
            decision = ControlDecision(
                kind=DecisionKind.ACT,
                rationale="Baseline compatibility action boundary.",
            )
            self.state = self.state.updated(current_decision=decision)
            return decision
        raise RuntimeError("An explicit actionable decision is required before a runtime step.")

    def _should_create_initial_plan(self) -> bool:
        return bool(
            self.config.initial_planning
            and self.state is not None
            and self.state.current_plan is None
        )

    def create_initial_plan(self) -> None:
        """Generate and persist a Plan without allocating or executing a runtime step."""
        self._check_query_limits()
        try:
            message = self._call_model(self._initial_planning_input_messages())
        except FormatError as error:
            for feedback in error.messages:
                feedback.setdefault("extra", {}).setdefault("phase", "initial_planning")
            raise
        message.setdefault("extra", {})["phase"] = "initial_planning"
        self.add_messages(message)
        try:
            plan = _parse_initial_plan(message)
        except (TypeError, ValueError) as error:
            feedback = self.model.format_message(
                role="user",
                content=f"Invalid initial Plan: {error}",
                extra={"interrupt_type": "FormatError", "phase": "initial_planning"},
            )
            raise FormatError(feedback) from error
        if self.state is None:  # The run setup establishes this invariant.
            raise RuntimeError("AgentState must exist before initial planning.")
        self.state = self.state.updated(
            current_plan=plan,
            current_decision=ControlDecision(
                kind=DecisionKind.ACT,
                rationale="The validated initial Plan is ready for its first action.",
            ),
        )

    def _should_replan_current_plan(self) -> bool:
        return bool(
            self.state is not None
            and self.state.current_plan is not None
            and self.state.current_decision is not None
            and self.state.current_decision.kind is DecisionKind.REPLAN
        )

    def replan(self) -> None:
        """Atomically replace the current Plan without allocating a runtime step."""
        if self.state is None or self.state.current_plan is None:
            raise RuntimeError("A current Plan must exist before Replan.")
        self._check_query_limits()
        current_plan = self.state.current_plan
        expected_revision = current_plan.revision + 1
        try:
            message = self._call_model(self._replanning_input_messages(expected_revision))
        except FormatError as error:
            for feedback in error.messages:
                extra = feedback.setdefault("extra", {})
                extra.setdefault("phase", "replan")
                extra.setdefault("previous_revision", current_plan.revision)
                extra.setdefault("expected_revision", expected_revision)
            raise
        extra = message.setdefault("extra", {})
        extra["phase"] = "replan"
        extra["previous_revision"] = current_plan.revision
        extra["expected_revision"] = expected_revision
        self.add_messages(message)
        try:
            plan = _parse_replan(message, current_plan)
        except (TypeError, ValueError) as error:
            feedback = self.model.format_message(
                role="user",
                content=f"Invalid replacement Plan: {error}",
                extra={
                    "interrupt_type": "FormatError",
                    "phase": "replan",
                    "previous_revision": current_plan.revision,
                    "expected_revision": expected_revision,
                },
            )
            raise FormatError(feedback) from error
        self.state = self.state.updated(
            current_plan=plan,
            current_decision=ControlDecision(
                kind=DecisionKind.ACT,
                rationale="The validated replacement Plan is ready for the next action.",
            ),
            next_action=None,
        )
        if self._repeat_replan_signature is not None:
            self._post_replan_evidence_identities = self._evidence_identities()

    def _replanning_input_messages(self, expected_revision: int) -> list[dict]:
        """Build a bounded Plan-replacement view without trajectory replay."""
        if self.state is None:
            raise RuntimeError("AgentState must exist before Replan.")
        context = self.context_builder.build(self.state).text
        replanning_message = self.model.format_message(
            role="user",
            content=f"{_replanning_instruction(expected_revision)}\n\n{context}",
        )
        messages = [*copy.deepcopy(self.messages[:2]), replanning_message]
        if feedback := self._latest_runtime_feedback():
            messages.append(feedback)
        return messages

    def _should_diagnose_pending_observations(self) -> bool:
        return bool(
            self.config.diagnose_after_observation
            and self.state is not None
            and self._pending_diagnosis_records
        )

    def diagnose_pending_observations(self) -> None:
        """Record a next decision without allocating a step or executing its action."""
        self._check_query_limits()
        source_step_ids = list(dict.fromkeys(
            record["step_id"] for record in self._pending_diagnosis_records
        ))
        try:
            message = self._call_model(self._diagnosis_input_messages())
        except FormatError as error:
            for feedback in error.messages:
                extra = feedback.setdefault("extra", {})
                extra.setdefault("phase", "diagnose")
                extra.setdefault("source_step_ids", source_step_ids)
            raise
        extra = message.setdefault("extra", {})
        extra["phase"] = "diagnose"
        extra["source_step_ids"] = source_step_ids
        self.add_messages(message)
        try:
            decision = _parse_control_decision(message)
        except (TypeError, ValueError) as error:
            feedback = self.model.format_message(
                role="user",
                content=f"Invalid ControlDecision: {error}",
                extra={
                    "interrupt_type": "FormatError",
                    "phase": "diagnose",
                    "source_step_ids": source_step_ids,
                },
            )
            raise FormatError(feedback) from error
        if self.state is None:
            raise RuntimeError("AgentState must exist before Diagnose.")
        self.state = self.state.updated(current_decision=decision)
        if decision.kind is not DecisionKind.DIAGNOSE:
            self._pending_diagnosis_records = []

    def _diagnosis_input_messages(self) -> list[dict]:
        """Build a bounded control-only view of state and actual Tool records."""
        if self.state is None:
            raise RuntimeError("AgentState must exist before Diagnose.")
        context = self.context_builder.build(self.state).text
        records = json.dumps(
            self._pending_diagnosis_records,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        records = self._bound_runtime_text(
            records,
            _DIAGNOSIS_RECORDS_MAX_CHARS,
            _DIAGNOSIS_RECORDS_OMISSION_MARKER,
        )
        diagnosis_message = self.model.format_message(
            role="user",
            content=(
                f"{_DIAGNOSE_INSTRUCTION}\n\n{context}"
                f"\n\n## recent_tool_observations\n{records}"
            ),
        )
        messages = [*copy.deepcopy(self.messages[:2]), diagnosis_message]
        if feedback := self._latest_runtime_feedback():
            messages.append(feedback)
        return messages

    def _initial_planning_input_messages(self) -> list[dict]:
        """Build a bounded planning-only view without replaying trajectory history."""
        if self.state is None:
            raise RuntimeError("AgentState must exist before initial planning.")
        context = self.context_builder.build(self.state).text
        planning_message = self.model.format_message(
            role="user",
            content=f"{_INITIAL_PLANNING_INSTRUCTION}\n\n{context}",
        )
        messages = [*copy.deepcopy(self.messages[:2]), planning_message]
        if feedback := self._latest_runtime_feedback():
            messages.append(feedback)
        return messages

    def query(self) -> dict:
        """Query the model and return model messages. Override to add hooks."""
        self._check_query_limits()
        self._begin_step()
        message = self._call_model(self._model_input_messages())
        message.setdefault("extra", {})["step_id"] = self._active_step_id
        self.add_messages(message)
        return message

    def _check_query_limits(self) -> None:
        if reason := StopPolicy.for_limits(
            step_limit=self.config.step_limit,
            n_calls=self.n_calls,
            cost_limit=self.config.cost_limit,
            cost=self.cost,
        ):
            self._set_stop_reason(reason)
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": "LimitsExceeded",
                    "extra": {
                        "exit_status": "LimitsExceeded",
                        "submission": "",
                        "stop_reason": reason.value,
                    },
                }
            )
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            self._set_stop_reason(StopReason.IRRECOVERABLE_FAILURE)
            raise TimeExceeded(
                {
                    "role": "exit",
                    "content": "TimeExceeded",
                    "extra": {
                        "exit_status": "TimeExceeded",
                        "submission": "",
                        "stop_reason": StopReason.IRRECOVERABLE_FAILURE.value,
                    },
                }
            )

    def _call_model(self, messages: list[dict]) -> dict:
        """Make one visible, accounted call through the existing Model boundary."""
        self.n_calls += 1
        message = self.model.query(messages)
        self.cost += message.get("extra", {}).get("cost", 0.0)
        return message

    def _model_input_messages(self) -> list[dict]:
        """Build the bounded per-call view without discarding the full trajectory.

        The first system and instance messages are the fixed instruction prefix for
        one run. Everything after that remains available in ``self.messages`` for
        trace persistence, but is replaced at the model boundary by the current
        AgentState-derived Context. The latest format/user interruption is retained
        as bounded runtime feedback so existing correction and interactive flows do
        not depend on replaying the complete history.
        """
        if self.state is None:  # _begin_step() establishes this invariant.
            raise RuntimeError("AgentState must exist before building model input.")
        context_message = self.model.format_message(
            role="user", content=self.context_builder.build(self.state).text
        )
        messages = [*copy.deepcopy(self.messages[:2]), context_message]
        if feedback := self._latest_runtime_feedback():
            messages.append(feedback)
        return messages

    def _latest_runtime_feedback(self) -> dict | None:
        """Return at most one bounded control-flow message from the trajectory."""
        for message in reversed(self.messages[2:]):
            interrupt_type = message.get("extra", {}).get("interrupt_type")
            if not isinstance(interrupt_type, str) or not interrupt_type:
                continue
            content = get_content_string(message)
            if not content:
                continue
            content = self._bound_runtime_text(
                content,
                _RUNTIME_FEEDBACK_MAX_CHARS,
                _RUNTIME_FEEDBACK_OMISSION_MARKER,
            )
            return self.model.format_message(role="user", content=content)
        return None

    @staticmethod
    def _bound_runtime_text(value: str, limit: int, marker: str) -> str:
        if len(value) <= limit:
            return value
        available = limit - len(marker)
        head = (available + 1) // 2
        tail = available - head
        return value[:head] + marker + value[-tail:] if tail else value[:head] + marker

    def execute_actions(self, message: dict) -> list[dict]:
        """Execute actions in message, add observation messages, return them."""
        outputs = []
        for action in message.get("extra", {}).get("actions", []):
            outputs.append(self._execute_action(action))
            stopped = self.state is not None and self.state.stop_reason is not None
            if stopped or self._should_replan_current_plan():
                break
        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))

    def _begin_step(self) -> str:
        """Allocate one task-local ordinal at an accepted query (including parse failures).

        Limit checks do not allocate steps. Human queries use this same boundary;
        n_calls remains model-call accounting, not a fabricated human model call.
        current_step remains the runtime producing-step label. PlanStep selection is
        represented separately by Plan.active_step_id.
        """
        decision = self._ensure_action_decision()
        step_id = f"step-{self._step_count + 1}"
        state = self.state if self.state is not None else AgentState(goal="Unspecified task")
        self.state = state.updated(current_step=step_id, next_action=None)
        self._step_count += 1
        self._active_step_id = step_id
        self.memory_updates.append(MemoryUpdateEvent(step_id=step_id, regions=("working_memory",)))
        self.step_traces.append(ExecutionStepTrace(
            step_id=step_id,
            current_goal=self.state.goal,
            decision=decision,
            state_updates=("working_memory.current_step",),
        ))
        return step_id

    def _execute_action(self, action: dict) -> dict:
        """Execute once, then update memory before model-specific observation formatting."""
        step_id = self._active_step_id or self._begin_step()
        tool_input = _JSON_ADAPTER.validate_python(action)
        command = action.get("command")
        self.state = self.state.updated(next_action=command if isinstance(command, str) and command.strip() else None)
        self.memory_updates.append(MemoryUpdateEvent(step_id=step_id, regions=("working_memory",)))
        self._update_active_step_trace(
            action=tool_input,
            state_updates=("working_memory.next_action",),
        )
        try:
            output = self.env.execute(action)
        except BaseException:
            # Submission/interrupts expose no raw Tool result. Preserve control flow,
            # record the attempted call, and never promote exit text to Evidence.
            execution_record = {
                "step_id": step_id, "tool_name": "run_command", "tool_input": tool_input,
                "observation": None, "evidence_identity": None, "status": "interrupted",
                "repeat_of_step_id": None,
            }
            self.tool_executions.append(execution_record)
            self._update_active_step_trace(observation=execution_record)
            raise
        if isinstance(output, Observation):
            observation = output
        elif isinstance(output, ToolResult):
            observation = normalize_tool_result("run_command", output)
        else:
            # Reuse Stage 1's normalization of the already-executed command result;
            # do not dispatch/re-run the command or change submission semantics.
            cwd = getattr(getattr(self.env, "config", None), "cwd", "") or None
            configured_env = getattr(getattr(self.env, "config", None), "env", {})
            result = _normalize_execution_result(
                tool_name="run_command", command=command or "", cwd=cwd,
                timeout=_effective_timeout(self.env, None), env_keys=sorted(configured_env),
                backend_name=_backend_attribute(self.env, "name"),
                backend_dialect=_backend_attribute(self.env, "dialect"), raw_result=output,
            )
            observation = normalize_tool_result("run_command", result)
        recorded_observation = self._record_tool_observation(step_id, tool_input, observation)
        if not isinstance(output, (Observation, ToolResult)) and any(item.code == "invalid_execution_result" for item in observation.diagnostics):
            return {"output": "", "returncode": -1, "exception_info": observation.summary}
        if isinstance(output, (Observation, ToolResult)):
            # Optional structured environments still feed the existing model formatter
            # its shell-shaped output; memory mapping above never consumes this text.
            text = json.dumps(recorded_observation) if recorded_observation is not None else (
                observation.summary if isinstance(observation.summary, str) else ""
            )
            return {"output": text, "returncode": 0 if observation.success else 1, "exception_info": ""}
        return output

    def _record_tool_observation(self, step_id: str, tool_input: JsonValue, observation: Observation) -> dict | None:
        """Only called with outputs of _execute_action, never assistant message extras."""
        evidence, status = _evidence_from_tool_observation(observation, step_id)
        identity = None
        if evidence is not None:
            identity = evidence.identity
            memory, inserted = self.state.evidence_memory.add(evidence)
            if inserted:
                self.state = self.state.updated(evidence_memory=memory)
                self.memory_updates.append(MemoryUpdateEvent(
                    step_id=step_id, regions=("evidence_memory",), evidence_identities=(identity,),
                ))
            status = "added" if inserted else "duplicate"
            # Evidence already validated/isolate-copied the entire result envelope.
            payload = evidence.model_dump(mode="json")
            recorded_observation = {"tool_name": observation.tool_name, **payload["content"], "summary": observation.summary,
                                    "provenance": payload["provenance"]}
        else:
            # Invalid Any data/metadata must not break saving or be silently stringified.
            recorded_observation = None
            self.logger.warning("Evidence skipped for %s: %s", step_id, status)
        execution_record = {
            "step_id": step_id, "tool_name": observation.tool_name, "tool_input": tool_input, "observation": recorded_observation,
            "evidence_identity": identity, "status": status, "repeat_of_step_id": None,
        }
        repeated_record = self._find_repeated_execution(execution_record)
        if repeated_record is not None:
            execution_record["repeat_of_step_id"] = repeated_record["step_id"]
        self.tool_executions.append(execution_record)
        state_updates = (f"evidence_memory.add:{identity}",) if status == "added" else ()
        self._update_active_step_trace(
            observation=execution_record,
            state_updates=state_updates,
        )
        terminal_reason = StopPolicy.for_observation(observation)
        if terminal_reason is not None:
            self._set_stop_reason(terminal_reason)
            self._update_active_step_trace(state_updates=("working_memory.stop_reason",))
            self._pending_diagnosis_records = []
        elif repeated_record is not None:
            signature = self._execution_triplet_signature(execution_record)
            if (
                signature == self._repeat_replan_signature
                and self._post_replan_evidence_identities is not None
            ):
                reason = StopPolicy.for_repeat_after_replan(
                    evidence_changed=self._evidence_identities() != self._post_replan_evidence_identities
                )
                self._set_stop_reason(reason)
                self._update_active_step_trace(state_updates=("working_memory.stop_reason",))
                self._pending_diagnosis_records = []
                return recorded_observation
            self.state = self.state.updated(current_decision=ControlDecision(
                kind=DecisionKind.REPLAN,
                rationale=(
                    "Exact Tool/Input/Observation triplet repeated; revise the Plan before "
                    "another normal action."
                ),
            ))
            self._repeat_replan_signature = signature
            self._post_replan_evidence_identities = None
            self._update_active_step_trace(state_updates=("working_memory.current_decision",))
            self._pending_diagnosis_records = []
        elif self.config.diagnose_after_observation:
            self._pending_diagnosis_records.append(copy.deepcopy(execution_record))
        return recorded_observation

    def _evidence_identities(self) -> frozenset[str]:
        if self.state is None:
            return frozenset()
        return frozenset(record.identity for record in self.state.evidence_memory.records)

    def _update_active_step_trace(
        self,
        *,
        action: object = _TRACE_MISSING,
        observation: object = _TRACE_MISSING,
        state_updates: tuple[str, ...] = (),
    ) -> None:
        if not self.step_traces or self._active_step_id is None:
            return
        trace = self.step_traces[-1]
        if trace.step_id != self._active_step_id:
            raise RuntimeError("Active step trace does not match the runtime producing step.")
        changes = {
            "state_updates": (*trace.state_updates, *state_updates),
        }
        if action is not _TRACE_MISSING:
            changes["actions"] = (*trace.actions, copy.deepcopy(action))
        if observation is not _TRACE_MISSING:
            changes["observations"] = (*trace.observations, copy.deepcopy(observation))
        self.step_traces[-1] = trace.updated(**changes)

    def _find_repeated_execution(self, candidate: dict) -> dict | None:
        """Return the latest exact prior triplet, independent of Evidence dedup."""
        if not self.config.repeat_detection:
            return None
        signature = self._execution_triplet_signature(candidate)
        if signature is None:
            return None
        for previous in reversed(self.tool_executions):
            if self._execution_triplet_signature(previous) == signature:
                return previous
        return None

    @staticmethod
    def _execution_triplet_signature(record: dict) -> str | None:
        observation = record.get("observation")
        if observation is None:
            return None
        tool_input = copy.deepcopy(record.get("tool_input"))
        if isinstance(tool_input, dict):
            tool_input.pop("tool_call_id", None)
        return json.dumps(
            {
                "tool_name": record.get("tool_name"),
                "tool_input": tool_input,
                "observation": observation,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def serialize(self, *extra_dicts) -> dict:
        """Serialize agent state to a json-compatible nested dictionary for saving."""
        last_message = self.messages[-1] if self.messages else {}
        last_extra = last_message.get("extra", {})
        agent_data = {
            "info": {
                "model_stats": {
                    "instance_cost": self.cost,
                    "api_calls": self.n_calls,
                },
                "config": {
                    "agent": self.config.model_dump(mode="json"),
                    "agent_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
                "mini_version": __version__,
                "exit_status": last_extra.get("exit_status", ""),
                "submission": last_extra.get("submission", ""),
                "stop_reason": None if self.state is None or self.state.stop_reason is None else self.state.stop_reason.value,
            },
            "messages": self.messages,
            "trajectory_format": "arkui-ut-code-agent-1.1",
        }
        data = recursive_merge(agent_data, self.model.serialize(), self.env.serialize(), *extra_dicts)
        # Additive 1.1 fields are authoritative, not recursively patchable by extras.
        data.update({
            "agent_state": None if self.state is None else self.state.model_dump(mode="json"),
            "memory_updates": [MemoryUpdateEvent.model_validate(event).model_dump(mode="json") for event in self.memory_updates],
            "tool_executions": _JSON_ADAPTER.validate_python(self.tool_executions),
            "step_traces": [ExecutionStepTrace.model_validate(trace).model_dump(mode="json") for trace in self.step_traces],
        })
        return data

    def save(self, path: Path | None, *extra_dicts) -> dict:
        """Save the trajectory of the agent to a file if path is given. Returns full serialized data.
        You can pass additional dictionaries with extra data to be (recursively) merged into the output data.
        """
        data = self.serialize(*extra_dicts)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
        return data
