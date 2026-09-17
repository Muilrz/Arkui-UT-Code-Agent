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
from arkui_ut_agent.agents.control import _DIAGNOSE_INSTRUCTION, _parse_control_decision
from arkui_ut_agent.agents.integration import _evidence_from_tool_observation
from arkui_ut_agent.agents.planning import _INITIAL_PLANNING_INSTRUCTION, _parse_initial_plan
from arkui_ut_agent.agents.state import AgentState, MemoryUpdateEvent
from arkui_ut_agent.exceptions import FormatError, InterruptAgentFlow, LimitsExceeded, TimeExceeded
from arkui_ut_agent.models.utils.content_string import get_content_string
from arkui_ut_agent.tools.contracts import Observation, ToolResult, normalize_tool_result
from arkui_ut_agent.tools.execution import _backend_attribute, _effective_timeout, _normalize_execution_result
from arkui_ut_agent.utils.serialize import recursive_merge

_JSON_ADAPTER = TypeAdapter(JsonValue, config=ConfigDict(allow_inf_nan=False))
_RUNTIME_FEEDBACK_MAX_CHARS = 2_000
_RUNTIME_FEEDBACK_OMISSION_MARKER = "\n…[runtime feedback omitted]…\n"
_DIAGNOSIS_RECORDS_MAX_CHARS = 4_000
_DIAGNOSIS_RECORDS_OMISSION_MARKER = "\n…[tool observation records omitted]…\n"
_CONTROL_ONLY_PHASES = frozenset({"initial_planning", "diagnose"})


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
        self._step_count = 0
        self._active_step_id: str | None = None
        self._pending_diagnosis_records: list[dict] = []
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
        return self.add_messages(
            self.model.format_message(
                role="exit",
                content=str(e),
                extra={
                    "exit_status": type(e).__name__,
                    "submission": "",
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
        self._step_count = 0
        self._active_step_id = None
        self._pending_diagnosis_records = []
        self.add_messages(
            self.model.format_message(role="system", content=self._render_template(self.config.system_template)),
            self.model.format_message(role="user", content=self._render_template(self.config.instance_template)),
        )
        while True:
            try:
                if self._should_create_initial_plan():
                    self.create_initial_plan()
                elif self._should_diagnose_pending_observations():
                    self.diagnose_pending_observations()
                else:
                    self.step()
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
                    self.add_messages(
                        *e.messages,
                        {
                            "role": "exit",
                            "content": "RepeatedFormatError",
                            "extra": {"exit_status": "RepeatedFormatError", "submission": ""},
                        },
                    )
                else:
                    self.add_messages(*e.messages)
            except InterruptAgentFlow as e:
                self.add_messages(*e.messages)
            except Exception as e:
                self.handle_uncaught_exception(e)
                raise
            finally:
                self.save(self.config.output_path)
            if self.messages[-1].get("role") == "exit":
                break
        return self.messages[-1].get("extra", {})

    def step(self) -> list[dict]:
        """Query the LM, execute actions."""
        return self.execute_actions(self.query())

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
        self.state = self.state.updated(current_plan=plan)

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
        if 0 < self.config.step_limit <= self.n_calls or 0 < self.config.cost_limit <= self.cost:
            raise LimitsExceeded(
                {
                    "role": "exit",
                    "content": "LimitsExceeded",
                    "extra": {"exit_status": "LimitsExceeded", "submission": ""},
                }
            )
        if 0 < self.config.wall_time_limit_seconds <= int(time.time() - self._start_time):
            raise TimeExceeded(
                {
                    "role": "exit",
                    "content": "TimeExceeded",
                    "extra": {"exit_status": "TimeExceeded", "submission": ""},
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
        outputs = [self._execute_action(action) for action in message.get("extra", {}).get("actions", [])]
        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))

    def _begin_step(self) -> str:
        """Allocate one task-local ordinal at an accepted query (including parse failures).

        Limit checks do not allocate steps. Human queries use this same boundary;
        n_calls remains model-call accounting, not a fabricated human model call.
        current_step remains the runtime producing-step label. PlanStep selection is
        represented separately by Plan.active_step_id.
        """
        step_id = f"step-{self._step_count + 1}"
        state = self.state if self.state is not None else AgentState(goal="Unspecified task")
        self.state = state.updated(current_step=step_id, next_action=None)
        self._step_count += 1
        self._active_step_id = step_id
        self.memory_updates.append(MemoryUpdateEvent(step_id=step_id, regions=("working_memory",)))
        return step_id

    def _execute_action(self, action: dict) -> dict:
        """Execute once, then update memory before model-specific observation formatting."""
        step_id = self._active_step_id or self._begin_step()
        tool_input = _JSON_ADAPTER.validate_python(action)
        command = action.get("command")
        self.state = self.state.updated(next_action=command if isinstance(command, str) and command.strip() else None)
        self.memory_updates.append(MemoryUpdateEvent(step_id=step_id, regions=("working_memory",)))
        try:
            output = self.env.execute(action)
        except BaseException:
            # Submission/interrupts expose no raw Tool result. Preserve control flow,
            # record the attempted call, and never promote exit text to Evidence.
            self.tool_executions.append({
                "step_id": step_id, "tool_name": "run_command", "tool_input": tool_input,
                "observation": None, "evidence_identity": None, "status": "interrupted",
            })
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
            "evidence_identity": identity, "status": status,
        }
        self.tool_executions.append(execution_record)
        if self.config.diagnose_after_observation:
            self._pending_diagnosis_records.append(copy.deepcopy(execution_record))
        return recorded_observation

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
