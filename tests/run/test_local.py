import json
import re
from unittest.mock import patch

from arkui_ut_agent.models.test_models import DeterministicModel, make_output
from arkui_ut_agent.run.mini import DEFAULT_CONFIG_FILE, main
from tests.conftest import assert_observations_match


def _make_model_from_fixture(text_outputs: list[str], cost_per_call: float = 1.0, **kwargs) -> DeterministicModel:
    """Create a DeterministicModel from trajectory fixture data (raw text outputs)."""

    def parse_command(text: str) -> list[dict]:
        match = re.search(r"```mswea_bash_command\s*\n(.*?)\n```", text, re.DOTALL)
        return [{"command": match.group(1)}] if match else []

    plan = {
        "revision": 1,
        "steps": [{"id": "execute", "description": "Execute and verify the requested task"}],
        "active_step_id": "execute",
    }
    outputs = [make_output("Initial plan", [{"command": json.dumps(plan)}], cost=cost_per_call)]
    for text in text_outputs:
        actions = parse_command(text)
        outputs.append(make_output(text, actions, cost=cost_per_call))
        if actions and "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in actions[0]["command"]:
            decision = {
                "kind": "verify",
                "rationale": "The Tool Observation succeeded; verify the requested outcome next",
            }
            outputs.append(make_output(
                "Diagnose the Tool Observation",
                [{"command": json.dumps(decision)}],
                cost=cost_per_call,
            ))
    return DeterministicModel(
        outputs=outputs,
        cost_per_call=cost_per_call,
        **kwargs,
    )


def test_local_end_to_end(local_test_data):
    """Test the complete flow from CLI to final result using real environment but deterministic model"""

    model_responses = local_test_data["model_responses"]
    expected_observations = local_test_data["expected_observations"]

    with (
        patch("arkui_ut_agent.run.mini.configure_if_first_time"),
        patch("arkui_ut_agent.models.litellm_model.LitellmModel") as mock_model_class,
        patch("arkui_ut_agent.agents.utils.prompt_user.prompt_session.prompt", side_effect=lambda *a, **kw: ""),
        patch(
            "arkui_ut_agent.agents.utils.prompt_user._multiline_prompt_session.prompt", side_effect=lambda *a, **kw: ""
        ),
        patch("builtins.input", return_value=""),  # For LimitsExceeded handling
    ):
        mock_model_class.return_value = _make_model_from_fixture(model_responses)
        agent = main(
            model_name="tardis",
            config_spec=[str(DEFAULT_CONFIG_FILE)],
            yolo=True,
            task="Blah blah blah",
            output=None,
            cost_limit=10,
            model_class=None,
            agent_class=None,
            environment_class=None,
        )  # type: ignore

    assert agent is not None
    messages = agent.messages

    # Verify we have the right number of messages
    # system + task + planning response + one Diagnose response after each non-terminal action
    diagnosis_calls = sum(
        "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in response for response in model_responses
    )
    expected_total_messages = 3 + (len(model_responses) * 2) + diagnosis_calls
    assert len(messages) == expected_total_messages, f"Expected {expected_total_messages} messages, got {len(messages)}"

    assert messages[2]["extra"]["phase"] == "initial_planning"
    assert sum(message.get("extra", {}).get("phase") == "diagnose" for message in messages) == diagnosis_calls
    assert_observations_match(expected_observations, [*messages[:2], *messages[3:]])

    assert agent.n_calls == len(model_responses) + diagnosis_calls + 1
