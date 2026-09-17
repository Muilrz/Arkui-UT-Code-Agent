"""Stage 2 acceptance without LLMs, network, MCP or a real repository."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from arkui_ut_agent.agents import AgentState, Evidence, MemoryUpdateEvent, Plan, PlanStep, TaskMemory
from arkui_ut_agent.agents.default import DefaultAgent
from arkui_ut_agent.agents.integration import _evidence_from_tool_observation
from arkui_ut_agent.agents.interactive import InteractiveAgent
from arkui_ut_agent.exceptions import FormatError, Submitted
from arkui_ut_agent.models.test_models import DeterministicModel, make_output
from arkui_ut_agent.tools import Diagnostic, Observation, Provenance, ToolRegistry, ToolResult


class FakeEnvironment:
    def __init__(self, outputs=None, registry=None):
        self.outputs = iter(outputs or [])
        self.registry = registry
        self.calls = []
        self.config = SimpleNamespace(cwd="workspace", timeout=12, env={"MODE": "test"})
        self.backend = SimpleNamespace(name="fake", dialect="fake")

    def execute(self, action):
        self.calls.append(dict(action))
        if action["command"] == "submit":
            raise Submitted({"role": "exit", "content": "done", "extra": {"exit_status": "Submitted"}})
        if self.registry is not None:
            return self.registry.dispatch(action["command"], action.get("arguments"))
        result = next(self.outputs)
        if isinstance(result, BaseException):
            raise result
        return result

    def get_template_vars(self):
        return {}

    def serialize(self):
        return {}


def agent_for(steps, env, **config):
    outputs = [make_output("An unconfirmed model hypothesis", actions) for actions in steps]
    return DefaultAgent(
        DeterministicModel(outputs=outputs), env,
        system_template="system", instance_template="{{ task }}", cost_limit=0,
        step_limit=len(steps), initial_planning=False, diagnose_after_observation=False,
        repeat_detection=False, **config,
    )


def raw_result(output="fact", returncode=0, **fields):
    return {"output": output, "returncode": returncode, "exception_info": "", **fields}


def tool_observation(**changes):
    return Observation(
        **{
            "tool_name": "read_file", "success": True,
            "data": {"snippet": "  source\n", "values": [1]}, "summary": "Read implementation",
            "provenance": [Provenance(source="read_file", location="src/text.cc:1", metadata={"revision": "abc"})],
            **changes,
        }
    )


def test_deterministic_steps_same_step_multiple_tools_and_different_steps_do_not_cross():
    steps = [[{"command": "one", "tool_call_id": "native-1"}, {"command": "two"}], [{"command": "three"}]]
    traces = []
    for _ in range(2):
        env = FakeEnvironment([raw_result("one"), raw_result("two"), raw_result("three")])
        agent = agent_for(steps, env)
        assert agent.run("task")["exit_status"] == "LimitsExceeded"
        assert agent.n_calls == agent._step_count == 2  # blocked query does not allocate a step
        assert agent.state.current_step == "step-2"
        assert agent.state.next_action == "three"
        assert [item["step_id"] for item in agent.tool_executions] == ["step-1", "step-1", "step-2"]
        assert [item.step_id for item in agent.state.evidence_memory.records] == ["step-1", "step-1", "step-2"]
        assert [msg["extra"]["step_id"] for msg in agent.messages if msg.get("role") == "assistant"] == ["step-1", "step-2"]
        assert agent.tool_executions[0]["tool_input"]["tool_call_id"] == "native-1"
        assert env.calls == [item for step in steps for item in step]
        traces.append(agent.serialize()["tool_executions"])
    assert traces[0] == traces[1]


def test_parse_failure_has_step_and_does_not_promote_error_or_assistant_text():
    agent = agent_for([[{"command": "fact"}]], FakeEnvironment([raw_result()]))
    original_query = agent.model.query
    calls = 0

    def query(messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FormatError({"role": "assistant", "content": "model claim", "extra": {"cost": 0}})
        return original_query(messages)

    agent.model.query = query
    agent.config.step_limit = 2
    agent.run("task")
    assert agent.messages[2]["extra"]["step_id"] == "step-1"
    assert agent.tool_executions[0]["step_id"] == "step-2"
    assert len(agent.state.evidence_memory.records) == 1
    assert agent.state.evidence_memory.records[0].step_id == "step-2"


def test_run_resets_task_local_state_trace_and_identity_scope_not_existing_call_accounting():
    agent = agent_for([[{"command": "fact"}]], FakeEnvironment([raw_result(), raw_result()]))
    agent.run("first")
    first = agent.state
    first_updates = agent.memory_updates
    first_calls = agent.tool_executions
    agent.model = DeterministicModel(outputs=[make_output("second guess", [{"command": "fact"}])])
    agent.config.step_limit = 2  # existing call-limit accounting is unchanged
    agent.run("second")
    assert agent.n_calls == 2
    assert first.goal == "first" and agent.state.goal == "second"
    assert agent.tool_executions[0]["step_id"] == "step-1"
    assert len(first.evidence_memory.records) == len(agent.state.evidence_memory.records) == 1
    assert first_calls is not agent.tool_executions
    assert first_updates is not agent.memory_updates
    assert [item["status"] for item in agent.tool_executions] == ["added"]


def test_empty_default_task_is_explicitly_unspecified():
    agent = agent_for([[{"command": "submit"}]], FakeEnvironment())
    agent.run()
    assert agent.state.goal == "Unspecified task"


def test_limit_before_any_query_does_not_allocate_a_step_or_tool_record():
    agent = agent_for([[{"command": "fact"}]], FakeEnvironment())
    agent.cost = 1
    agent.config.cost_limit = 0.5
    assert agent.run("task")["exit_status"] == "LimitsExceeded"
    assert agent.n_calls == agent._step_count == 0
    assert agent.state.current_step is None
    assert agent.memory_updates == agent.tool_executions == []


def test_invalid_parent_snapshot_fails_before_model_query_or_tool_execution():
    env = FakeEnvironment()
    agent = agent_for([[{"command": "fact"}]], env)
    agent.state = AgentState(goal="task")
    agent.state.open_questions.append("")
    with pytest.raises(ValidationError):
        agent.step()
    assert agent.n_calls == agent._step_count == 0
    assert env.calls == [] and agent.memory_updates == []


def test_successful_mapping_copies_entire_envelope_and_all_stage_one_provenance():
    observation = tool_observation(provenance=[
        Provenance(source="read_file", location="src/text.cc:1", metadata={"nested": {"values": [1]}}),
        Provenance(source="rg_search", location="src/other.cc:2", metadata={"revision": "abc"}),
    ])
    evidence, status = _evidence_from_tool_observation(observation, "step-3")
    assert status == "mapped"
    assert evidence.source == "read_file" and evidence.step_id == "step-3"
    assert evidence.location is None  # no arbitrary first-location selection
    assert evidence.content == {"success": True, "data": observation.data, "diagnostics": []}
    assert evidence.provenance == observation.provenance
    assert all(type(item) is Provenance for item in evidence.provenance)
    observation.data["values"].append(2)
    observation.provenance[0].metadata["nested"]["values"].append(2)
    assert evidence.content["data"]["values"] == [1]
    assert evidence.provenance[0].metadata["nested"]["values"] == [1]


@pytest.mark.parametrize("changes", [
    {"data": None, "summary": "actual Tool failure", "diagnostics": []},
    {"data": None, "summary": "", "diagnostics": [Diagnostic(code="compile_error", message="compiler failure")]},
    {"data": {"returncode": 1}, "summary": "", "diagnostics": []},
])
def test_failure_records_actual_tool_data_diagnostics_or_summary_never_claims_success(changes):
    observation = tool_observation(success=False, **changes)
    evidence, status = _evidence_from_tool_observation(observation, "step-1")
    assert status == "mapped" and evidence.content["success"] is False
    assert evidence.content["data"] == observation.data
    assert evidence.content["diagnostics"] == [item.model_dump(mode="json") for item in observation.diagnostics]
    assert evidence.provenance == observation.provenance


@pytest.mark.parametrize(("changes", "reason"), [
    ({"provenance": []}, "missing_provenance"),
    ({"provenance": [Provenance(source=" ")]}, "invalid_provenance_or_payload"),
    ({"provenance": [Provenance(source="tool", metadata={"bad": object()})]}, "invalid_provenance_or_payload"),
    ({"data": object()}, "invalid_provenance_or_payload"),
    ({"data": {"number": float("nan")}}, "invalid_provenance_or_payload"),
    ({"data": None, "summary": "", "diagnostics": []}, "empty_result"),
    ({"success": False, "data": [], "summary": " ", "diagnostics": []}, "empty_result"),
])
def test_unusable_tool_observations_degrade_without_fabricating_origins_or_payload(changes, reason):
    evidence, status = _evidence_from_tool_observation(tool_observation(**changes), "step-1")
    assert evidence is None and status == reason


def test_mutated_stage_one_instances_are_revalidated_before_mapping():
    observation = tool_observation()
    observation.provenance[0].source = 5
    assert _evidence_from_tool_observation(observation, "step-1") == (None, "invalid_provenance_or_payload")
    observation = tool_observation()
    observation.diagnostics.append("model text")
    assert _evidence_from_tool_observation(observation, "step-1") == (None, "invalid_provenance_or_payload")


@pytest.mark.parametrize("value", ["model hypothesis", {"role": "assistant", "content": "claim"}, {"tool_name": "fake"}])
def test_conversion_rejects_plain_messages_text_and_dicts(value):
    with pytest.raises(TypeError):
        _evidence_from_tool_observation(value, "step-1")


def test_actual_registry_execution_retains_provenance_and_deduplicates_across_steps():
    registry = ToolRegistry()
    result = ToolResult(success=True, data={"snippet": "source"}, summary="read", provenance=[
        Provenance(source="read_file", location="src/text.cc", metadata={"revision": "abc"}),
    ])
    invoked = []

    def read(arguments):
        invoked.append(arguments)
        return result

    registry.register("read_file", read)
    agent = agent_for([[{"command": "read_file"}], [{"command": "read_file"}]], FakeEnvironment(registry=registry))
    agent.run("task")
    assert invoked == [{}, {}]
    assert [item["status"] for item in agent.tool_executions] == ["added", "duplicate"]
    assert [item["step_id"] for item in agent.tool_executions] == ["step-1", "step-2"]
    evidence = agent.state.evidence_memory.records[0]
    assert evidence.step_id == "step-1" and evidence.provenance == result.provenance
    assert [item["evidence_identity"] for item in agent.tool_executions] == [evidence.identity] * 2
    updates = [event for event in agent.memory_updates if event.regions == ("evidence_memory",)]
    assert len(updates) == 1 and updates[0].evidence_identities == (evidence.identity,)
    assert updates[0].step_id == "step-1"


def test_actual_tool_result_return_and_registry_exception_diagnostic_mapping():
    result = ToolResult(success=True, data=0, provenance=[Provenance(source="run_command")])
    agent = agent_for([[{"command": "zero"}]], FakeEnvironment([result]))
    agent.run("task")
    assert agent.state.evidence_memory.records[0].content["data"] == 0
    registry = ToolRegistry()

    def broken(arguments):
        raise ValueError("real Tool exception")

    registry.register("broken", broken)
    failed = agent_for([[{"command": "broken"}]], FakeEnvironment(registry=registry))
    failed.run("task")
    evidence = failed.state.evidence_memory.records[0]
    assert evidence.content["success"] is False
    assert evidence.content["diagnostics"][0]["code"] == "tool_execution_error"
    assert evidence.provenance[0].source == "broken"
    assert evidence.provenance[0].metadata == {"phase": "execution"}


@pytest.mark.parametrize(("raw", "diagnostic"), [
    (raw_result("compiler diagnostic", 1), "command_failed"),
    (raw_result("partial output", -1, exception_info="timeout", extra={"exception_type": "TimeoutExpired"}), "command_timeout"),
    (raw_result("", -1, exception_info="backend missing", extra={"exception_type": "FileNotFoundError"}), "backend_unavailable"),
    ({"output": 5}, "invalid_execution_result"),
])
def test_raw_execution_failure_and_adapter_diagnostic_are_evidence(raw, diagnostic):
    agent = agent_for([[{"command": "compile"}]], FakeEnvironment([raw]))
    agent.run("task")
    evidence = agent.state.evidence_memory.records[0]
    assert evidence.content["success"] is False
    assert evidence.content["diagnostics"][0]["code"] == diagnostic
    assert evidence.provenance[0].metadata["command"] == "compile"
    assert evidence.provenance[0].metadata["env_keys"] == ["MODE"]
    assert evidence.location == "workspace"


@pytest.mark.parametrize("observation", [tool_observation(provenance=[]), tool_observation(data=object())])
def test_integration_skips_unusable_structured_output_and_trajectory_remains_json(observation, caplog):
    agent = agent_for([[{"command": "read"}]], FakeEnvironment([observation]))
    agent.run("task")
    assert agent.state.evidence_memory.records == []
    assert agent.tool_executions[0]["observation"] is None
    assert agent.tool_executions[0]["evidence_identity"] is None
    assert agent.tool_executions[0]["status"] in ("missing_provenance", "invalid_provenance_or_payload")
    assert all(event.regions != ("evidence_memory",) for event in agent.memory_updates)
    assert "Evidence skipped" in caplog.text
    json.dumps(agent.serialize(), allow_nan=False)


def test_assistant_observation_extra_and_hypotheses_are_never_consumed():
    agent = agent_for([[]], FakeEnvironment())
    agent.model.config.outputs[0]["extra"]["observation"] = tool_observation().model_dump(mode="json")
    agent.model.config.outputs[0]["extra"]["step_id"] = "forged"
    agent.run("task")
    assert agent.state.evidence_memory.records == []
    assert agent.tool_executions == []
    assert agent.messages[2]["extra"]["step_id"] == "step-1"
    agent.state = agent.state.updated(hypotheses=["The model claims it passed"])
    assert agent.state.evidence_memory.records == []


def test_submission_and_raised_execution_exception_do_not_promote_exit_or_exception_text():
    agent = agent_for([[{"command": "fact"}, {"command": "submit"}]], FakeEnvironment([raw_result()]))
    assert agent.run("task")["exit_status"] == "Submitted"
    assert len(agent.state.evidence_memory.records) == 1
    assert [item["status"] for item in agent.tool_executions] == ["added", "interrupted"]
    assert agent.tool_executions[-1]["observation"] is None
    failing = agent_for([[{"command": "fact"}]], FakeEnvironment([RuntimeError("execution failed")]))
    with pytest.raises(RuntimeError, match="execution failed"):
        failing.run("task")
    assert failing.state.evidence_memory.records == []
    assert failing.tool_executions[0]["status"] == "interrupted"


def test_memory_event_minimal_full_immutable_isolated_and_json_roundtrip():
    identity = _evidence_from_tool_observation(tool_observation(), "step-1")[0].identity
    regions = ["working_memory", "evidence_memory"]
    identities = [identity]
    event = MemoryUpdateEvent(step_id="step-1", regions=regions, evidence_identities=identities)
    regions.clear()
    identities.clear()
    assert event.regions == ("working_memory", "evidence_memory")
    assert event.evidence_identities == (identity,)
    assert MemoryUpdateEvent.model_validate_json(event.model_dump_json()) == event
    assert MemoryUpdateEvent(step_id="step-2", regions=["task_memory"]).evidence_identities == ()
    with pytest.raises(ValidationError):
        event.step_id = "other"
    with pytest.raises(TypeError):
        event.regions[0] = "task_memory"


@pytest.mark.parametrize("changes", [
    {"step_id": ""}, {"regions": []}, {"regions": ["unknown"]},
    {"evidence_identities": ["random"]}, {"evidence_identities": ["sha256:" + "A" * 64]},
    {"messages": []}, {"timestamp": "future"},
])
def test_memory_event_invalid_and_unknown_fields_fail(changes):
    with pytest.raises(ValidationError):
        MemoryUpdateEvent(**{"step_id": "step-1", "regions": ["working_memory"], **changes})


def test_memory_event_required_fields_and_unsafe_copy_are_validated_before_dump():
    for payload in ({}, {"step_id": "step-1"}, {"regions": ["working_memory"]}):
        with pytest.raises(ValidationError):
            MemoryUpdateEvent.model_validate(payload)
    event = MemoryUpdateEvent(step_id="step-1", regions=["working_memory"])
    with pytest.raises(PydanticSerializationError):
        event.model_copy(update={"regions": ["invalid"]}).model_dump_json()


def test_stage_two_acceptance_save_restore_all_memory_without_any_messages(tmp_path):
    observation = tool_observation()
    agent = agent_for([[{"command": "read"}]], FakeEnvironment([observation]))
    parent = AgentState(
        goal="Add Text UT",
        current_plan=Plan(
            steps=(
                PlanStep(id="inspect", description="Inspect Text implementation"),
                PlanStep(id="verify", description="Verify the Text unit test"),
            ),
            active_step_id="inspect",
        ),
        hypotheses=["Unconfirmed guess"], open_questions=["Which branch?"], information_gap="coverage",
        task_memory=TaskMemory(target_component="Text", target_files=["src/text.cc"],
                               relevant_tests=["TextTest.CoversProperty"], build_target="text_test"),
    )
    agent.state = parent
    agent.step()
    assert parent.current_step is None and parent.evidence_memory.records == []
    assert parent.current_plan.active_step_id == agent.state.current_plan.active_step_id == "inspect"
    assert agent.state.task_memory is not parent.task_memory
    assert agent.state.hypotheses == parent.hypotheses
    path = tmp_path / "trajectory.json"
    data = agent.save(path)
    assert json.loads(path.read_text()) == data
    assert data["trajectory_format"] == "arkui-ut-code-agent-1.1"
    assert "messages" in data and "agent_state" in data
    restored = AgentState.from_trajectory({"agent_state": data["agent_state"]})
    assert restored == agent.state
    assert restored.current_step == "step-1" and restored.current_plan == parent.current_plan
    assert restored.task_memory == parent.task_memory
    evidence = restored.evidence_memory.records[0]
    assert evidence.identity == agent.state.evidence_memory.records[0].identity
    assert evidence.identity == data["tool_executions"][0]["evidence_identity"]
    assert evidence.step_id == data["tool_executions"][0]["step_id"] == "step-1"
    assert evidence.provenance == observation.provenance
    assert Evidence.model_validate_json(evidence.model_dump_json()).identity == evidence.identity
    assert data["memory_updates"][-1] == {
        "step_id": "step-1", "regions": ["evidence_memory"], "evidence_identities": [evidence.identity],
    }
    restored.task_memory.target_files.append("restored only")
    restored.evidence_memory.records[0].content["data"]["values"].append(2)
    data["tool_executions"][0]["observation"]["data"]["values"].append(3)
    assert agent.state.task_memory.target_files == ["src/text.cc"]
    assert agent.state.evidence_memory.records[0].content["data"]["values"] == [1]
    assert agent.tool_executions[0]["observation"]["data"]["values"] == [1]


def test_legacy_trajectory_and_legacy_state_restore_never_infer_from_messages():
    messages = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "claim"}]
    assert AgentState.from_trajectory({"trajectory_format": "arkui-ut-code-agent-1.1", "messages": messages}) is None
    assert AgentState.from_trajectory({"agent_state": None, "messages": messages}) is None
    for snapshot in ({"goal": "2A"}, {"goal": "2B", "task_memory": {"target_component": "Text"}},
                     {"goal": "2C", "evidence_memory": {"records": []}}):
        assert AgentState.from_trajectory({"agent_state": snapshot, "messages": object()}).goal == snapshot["goal"]
    for snapshot in ({}, {"goal": "task", "messages": messages}, "model text", []):
        with pytest.raises(ValidationError):
            AgentState.from_trajectory({"agent_state": snapshot})


def test_snapshot_and_events_are_authoritative_not_extra_dictionary_patches():
    agent = agent_for([[{"command": "fact"}]], FakeEnvironment([raw_result()]))
    assert agent.serialize()["agent_state"] is None  # pre-run save does not invent task state
    agent.run("task")
    data = agent.serialize({"agent_state": {"goal": "forged"}, "memory_updates": [], "tool_executions": []})
    assert data["agent_state"] == agent.state.model_dump(mode="json")
    assert data["memory_updates"] and data["tool_executions"]


@pytest.mark.parametrize("mutation", ["working", "task", "content", "provenance"])
def test_state_raw_mutation_rejected_by_execution_updates_and_trajectory_save(mutation):
    agent = agent_for([[{"command": "read"}]], FakeEnvironment([tool_observation()]))
    agent.run("task")
    if mutation == "working":
        agent.state.hypotheses.append("")
    elif mutation == "task":
        agent.state.task_memory.target_files.append(object())
    elif mutation == "content":
        agent.state.evidence_memory.records[0].content["data"]["values"].append(object())
    else:
        agent.state.evidence_memory.records[0].provenance[0].metadata["bad"] = object()
    with pytest.raises(ValidationError):
        agent.state.updated(next_action="verify")
    with pytest.raises(PydanticSerializationError):
        agent.serialize({"agent_state": None})
    with pytest.raises(PydanticSerializationError):
        agent.save(None)


def test_interactive_human_steps_use_same_lifecycle_without_model_calls(monkeypatch):
    env = FakeEnvironment([raw_result()])
    agent = InteractiveAgent(
        DeterministicModel(outputs=[]), env, system_template="system", instance_template="{{ task }}",
        cost_limit=0, mode="human", confirm_exit=False,
    )
    commands = iter(["fact", "submit"])
    monkeypatch.setattr(agent, "_prompt_and_handle_slash_commands", lambda *args: next(commands))
    agent.run("task")
    assert agent.n_calls == 0
    assert [record["step_id"] for record in agent.tool_executions] == ["step-1", "step-2"]
    assert agent.state.evidence_memory.records[0].step_id == "step-1"
