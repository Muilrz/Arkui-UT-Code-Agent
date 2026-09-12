"""finish_reason is threaded into format_error_template across the parse utilities, so a config can
report a max_tokens truncation as "cut off" instead of a misleading format error."""

from unittest.mock import MagicMock

import pytest

from arkui_ut_agent.exceptions import FormatError
from arkui_ut_agent.models.utils.actions_text import parse_regex_actions
from arkui_ut_agent.models.utils.actions_toolcall import parse_toolcall_actions

_TEMPLATE = "{% if finish_reason == 'length' %}cut off{% else %}{{ error }}{% endif %}"
# Mirrors the production condition in mini.yaml.
_TOOLCALL_TEMPLATE = (
    "{% if finish_reason is defined and (finish_reason == 'length' "
    "or (finish_reason == 'tool_calls' and not has_tool_calls)) %}cut off{% else %}{{ error }}{% endif %}"
)


def _toolcall(name: str, arguments: str):
    tc = MagicMock()
    tc.function.name = name
    tc.function.arguments = arguments
    tc.id = "call_1"
    return tc


class TestRegexActionsTemplateKwargs:
    def test_finish_reason_reported_on_zero_actions(self):
        # a truncated text response yields zero parsed actions
        with pytest.raises(FormatError) as exc:
            parse_regex_actions(
                "no action here",
                action_regex=r"```bash\n(.*?)\n```",
                format_error_template=_TEMPLATE,
                template_kwargs={"finish_reason": "length"},
            )
        assert exc.value.messages[0]["content"] == "cut off"

    def test_without_template_kwargs_still_works(self):
        with pytest.raises(FormatError) as exc:
            parse_regex_actions("nope", action_regex=r"```bash\n(.*?)\n```", format_error_template="{{ error }}")
        assert "found 0" in exc.value.messages[0]["content"]


class TestHasToolCallsDistinction:
    """A malformed tool call (finish_reason == "tool_calls") must surface the real error, while an
    empty payload with the same finish_reason is the truncation case -- the has_tool_calls flag lets
    the template tell them apart (issue #894)."""

    def test_malformed_toolcall_with_tool_calls_reason_shows_real_error(self):
        with pytest.raises(FormatError) as exc:
            parse_toolcall_actions(
                [_toolcall("shell", '{"command": "ls"}')],
                format_error_template=_TOOLCALL_TEMPLATE,
                template_kwargs={"finish_reason": "tool_calls"},
            )
        assert "Unknown tool 'shell'" in exc.value.messages[0]["content"]

    def test_empty_payload_with_tool_calls_reason_reports_truncation(self):
        with pytest.raises(FormatError) as exc:
            parse_toolcall_actions(
                [], format_error_template=_TOOLCALL_TEMPLATE, template_kwargs={"finish_reason": "tool_calls"}
            )
        assert exc.value.messages[0]["content"] == "cut off"

    def test_malformed_toolcall_with_length_reason_still_reports_truncation(self):
        # arguments cut off mid-JSON is a genuine truncation, so keep the "cut off" hint
        with pytest.raises(FormatError) as exc:
            parse_toolcall_actions(
                [_toolcall("bash", "{not json")],
                format_error_template=_TOOLCALL_TEMPLATE,
                template_kwargs={"finish_reason": "length"},
            )
        assert exc.value.messages[0]["content"] == "cut off"
