"""Regression tests for issue #784 — FormatError must carry the unparsed
LLM response in `e.messages[0]["extra"]["response"]` for trajectory inspection.

Each test triggers FormatError via an unrecognised tool name on the parser,
then asserts that the response payload was persisted into the FormatError's
extra dict before re-raise. On the pre-fix code these tests fail because
`e.messages[0]["extra"]` only contains `{"interrupt_type": "FormatError"}`
and no `response` key.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from arkui_ut_agent.exceptions import FormatError

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _bad_tool_call_mock() -> MagicMock:
    """Build a tool-call mock whose function name is not 'bash'."""
    tc = MagicMock()
    tc.id = "call_xyz"
    tc.function.name = "unknown_tool"
    tc.function.arguments = "{}"
    return tc


# --------------------------------------------------------------------------- #
# litellm_model.LitellmModel
# --------------------------------------------------------------------------- #


def test_litellm_model_format_error_persists_response() -> None:
    from arkui_ut_agent.models.litellm_model import LitellmModel

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.tool_calls = [_bad_tool_call_mock()]
    serialized = {"id": "resp_1", "choices": [{"message": {"tool_calls": [{"function": {"name": "unknown_tool"}}]}}]}
    response.model_dump.return_value = serialized

    model = LitellmModel(model_name="test/model")

    with (
        patch.object(LitellmModel, "_query", return_value=response),
        patch.object(LitellmModel, "_calculate_cost", return_value={"cost": 0.02}),
    ):
        with pytest.raises(FormatError) as excinfo:
            model.query([{"role": "user", "content": "hi"}])

    extra = excinfo.value.messages[0]["extra"]
    assert "response" in extra, "response key missing — fix not applied"
    assert extra["response"], "response payload empty — fix not applied"
    # model_dump(mode='json') was used: result must be JSON-serialisable
    json.dumps(extra["response"])
    response.model_dump.assert_any_call(mode="json")
    assert extra["cost"] == 0.02


# --------------------------------------------------------------------------- #
# Trajectory log round-trip — the persisted response must JSON-serialise
# --------------------------------------------------------------------------- #


def test_persisted_response_round_trips_through_json() -> None:
    """The whole point of the fix is that the trajectory log can dump the
    payload. If model_dump(mode='json') is missing, datetimes/Decimals leak
    through and json.dumps raises TypeError."""
    from arkui_ut_agent.models.litellm_model import LitellmModel

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.tool_calls = [_bad_tool_call_mock()]
    response.model_dump.return_value = {"id": "abc", "nested": {"k": "v"}}

    model = LitellmModel(model_name="test/model")

    with (
        patch.object(LitellmModel, "_query", return_value=response),
        patch.object(LitellmModel, "_calculate_cost", return_value={"cost": 0.0}),
    ):
        with pytest.raises(FormatError) as excinfo:
            model.query([{"role": "user", "content": "hi"}])

    # Simulate trajectory log serialisation: dump the whole exception messages.
    payload = excinfo.value.messages[0]
    serialised = json.dumps(payload)
    assert '"response"' in serialised
    reloaded = json.loads(serialised)
    assert reloaded["extra"]["response"] == {"id": "abc", "nested": {"k": "v"}}


# --------------------------------------------------------------------------- #
# ATK-01: model_dump failure inside except block must not swallow FormatError
# --------------------------------------------------------------------------- #


def test_format_error_not_swallowed_when_model_dump_raises() -> None:
    """If response.model_dump(mode='json') raises (e.g. serialization error),
    the original FormatError must still propagate AND extra['response'] must be
    set to repr(response) — the spec contract holds unconditionally."""
    from arkui_ut_agent.models.litellm_model import LitellmModel

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.tool_calls = [_bad_tool_call_mock()]
    # Simulate model_dump raising regardless of kwargs.
    response.model_dump.side_effect = TypeError("unserializable object")

    model = LitellmModel(model_name="test/model")

    with (
        patch.object(LitellmModel, "_query", return_value=response),
        patch.object(LitellmModel, "_calculate_cost", return_value={"cost": 0.0}),
    ):
        # Must raise FormatError, not TypeError from the failed model_dump.
        with pytest.raises(FormatError) as excinfo:
            model.query([{"role": "user", "content": "hi"}])

    extra = excinfo.value.messages[0]["extra"]
    # repr fallback must set the key (spec: response MUST be persisted)
    assert "response" in extra, "extra['response'] missing — fallback not applied"
    assert isinstance(extra["response"], str), "repr fallback must produce a string"
    assert extra["response"], "repr fallback must be non-empty"
