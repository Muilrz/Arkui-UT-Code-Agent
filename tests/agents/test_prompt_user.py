from unittest.mock import Mock, patch

from arkui_ut_agent.agents.utils.prompt_user import _LazyPromptSession


def test_prompt_session_is_created_lazily():
    session = _LazyPromptSession(multiline=True)
    concrete_session = Mock()
    concrete_session.prompt.return_value = "answer"

    assert session._session is None  # noqa: SLF001
    with patch("arkui_ut_agent.agents.utils.prompt_user.PromptSession", return_value=concrete_session) as factory:
        assert session.prompt("> ") == "answer"

    factory.assert_called_once()
    concrete_session.prompt.assert_called_once_with("> ")
