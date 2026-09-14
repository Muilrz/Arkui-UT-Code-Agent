import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import arkui_ut_agent.environments.local as local_module
import arkui_ut_agent.tools.execution as execution_module
from arkui_ut_agent.environments.local import LocalEnvironment
from arkui_ut_agent.exceptions import Submitted
from arkui_ut_agent.tools import ExecutionTools, ToolRegistry, register_execution_tools


class StubEnvironment:
    def __init__(self, results, *, timeout=30, backend_name="recording", backend_dialect="recording"):
        self.results = list(results)
        self.calls = []
        self.config = SimpleNamespace(timeout=timeout)
        self.backend = SimpleNamespace(name=backend_name, dialect=backend_dialect)

    def execute_command(self, command, cwd="", *, timeout=None, env=None):
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout, "env": env})
        return self.results.pop(0)


class StubBackend:
    name = "stub"
    dialect = "stub"
    executable = "stub-shell"


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    return root


def raw_result(*, output="", returncode=0, exception_info="", exception_type=None):
    result = {"output": output, "returncode": returncode, "exception_info": exception_info}
    if exception_type is not None:
        result["extra"] = {"exception_type": exception_type, "exception": exception_info}
    return result


def assert_failure(result, code, source):
    assert result.success is False
    assert result.diagnostics[0].code == code
    assert result.provenance[0].source == source


class TestRunCommand:
    def test_success_preserves_command_output_and_backend_provenance(self, repository):
        environment = StubEnvironment([raw_result(output="done\n")])
        tools = ExecutionTools(repository, environment=environment)

        result = tools.run_command({"command": "verbatim command"})

        assert result.success is True
        assert result.data == {
            "command": "verbatim command",
            "cwd": ".",
            "output": "done\n",
            "returncode": 0,
            "timed_out": False,
            "execution_failed": False,
            "timeout_seconds": 30,
            "env_keys": [],
        }
        assert environment.calls[0]["command"] == "verbatim command"
        assert environment.calls[0]["cwd"] == str(repository.resolve())
        assert result.provenance[0].metadata["backend_name"] == "recording"
        assert result.provenance[0].metadata["backend_dialect"] == "recording"

    def test_nonzero_exit_is_structured_and_preserves_raw_result(self, repository):
        environment = StubEnvironment([raw_result(output="compiler error\n", returncode=7)])

        result = ExecutionTools(repository, environment=environment).run_command({"command": "compile"})

        assert_failure(result, "command_failed", "run_command")
        assert result.data["command"] == "compile"
        assert result.data["output"] == "compiler error\n"
        assert result.data["returncode"] == 7
        assert result.data["timed_out"] is False
        assert result.diagnostics[0].details == {"returncode": 7}

    def test_cwd_and_env_override_are_forwarded_without_exposing_values(self, repository):
        nested = repository / "nested"
        nested.mkdir()
        environment = StubEnvironment([raw_result(output="ok")])
        secret_value = "credential-value-that-must-not-be-serialized"

        result = ExecutionTools(repository, environment=environment).run_command(
            {
                "command": "print configured value",
                "cwd": "nested",
                "timeout": 4.5,
                "env": {"TOKEN": secret_value, "MODE": "test"},
            }
        )

        assert result.success is True
        assert environment.calls == [
            {
                "command": "print configured value",
                "cwd": str(nested.resolve()),
                "timeout": 4.5,
                "env": {"TOKEN": secret_value, "MODE": "test"},
            }
        ]
        assert result.data["cwd"] == "nested"
        assert result.data["env_keys"] == ["MODE", "TOKEN"]
        assert result.data["timeout_seconds"] == 4.5
        assert secret_value not in str(result.model_dump(mode="json"))

    def test_timeout_is_structured_and_preserves_partial_output(self, repository):
        environment = StubEnvironment(
            [
                raw_result(
                    output="partial output",
                    returncode=-1,
                    exception_info="command timed out",
                    exception_type="TimeoutExpired",
                )
            ]
        )

        result = ExecutionTools(repository, environment=environment).run_command(
            {"command": "slow", "timeout": 2}
        )

        assert_failure(result, "command_timeout", "run_command")
        assert result.data["output"] == "partial output"
        assert result.data["returncode"] == -1
        assert result.data["timed_out"] is True
        assert result.data["execution_failed"] is True

    def test_backend_unavailable_is_structured(self, repository, monkeypatch):
        def unavailable_environment(**_kwargs):
            raise RuntimeError("PowerShell is unavailable")

        monkeypatch.setattr(execution_module, "LocalEnvironment", unavailable_environment)

        result = ExecutionTools(repository).run_command({"command": "build"})

        assert_failure(result, "backend_unavailable", "run_command")
        assert result.data["command"] == "build"
        assert result.data["returncode"] is None

    def test_process_start_failure_is_structured(self, repository):
        environment = StubEnvironment(
            [
                raw_result(
                    returncode=-1,
                    exception_info="An error occurred while executing the command: access denied",
                    exception_type="PermissionError",
                )
            ]
        )

        result = ExecutionTools(repository, environment=environment).run_command({"command": "build"})

        assert_failure(result, "process_start_failed", "run_command")
        assert result.data["execution_failed"] is True
        assert result.diagnostics[0].details["exception_type"] == "PermissionError"


@pytest.mark.parametrize(
    ("tool_name", "returncode"),
    [("build", 0), ("build", 2), ("test", 0), ("test", 3)],
)
def test_build_and_test_use_shared_execution_path_with_distinct_identity(repository, tool_name, returncode):
    environment = StubEnvironment([raw_result(output="tool output", returncode=returncode)])
    tools = ExecutionTools(repository, environment=environment)

    result = getattr(tools, tool_name)({"command": "explicit command"})

    assert result.success is (returncode == 0)
    assert result.provenance[0].source == tool_name
    assert result.data["command"] == "explicit command"
    assert result.data["output"] == "tool output"
    if returncode:
        assert result.diagnostics[0].code == "command_failed"
    assert environment.calls[0]["command"] == "explicit command"


class TestExecutionArgumentsAndCwd:
    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"command": ""},
            {"command": "   "},
            {"command": 1},
            {"command": "ok", "timeout": 0},
            {"command": "ok", "timeout": True},
            {"command": "ok", "env": []},
            {"command": "ok", "env": {"TOKEN": 1}},
            {"command": "ok", "unknown": True},
        ],
    )
    def test_invalid_arguments_are_structured(self, repository, arguments):
        result = ExecutionTools(repository, environment=StubEnvironment([])).run_command(arguments)

        assert_failure(result, "invalid_arguments", "run_command")

    @pytest.mark.parametrize(
        ("cwd", "code"),
        [("missing", "cwd_not_found"), ("file.txt", "cwd_not_a_directory")],
    )
    def test_invalid_working_directories_are_structured(self, repository, cwd, code):
        (repository / "file.txt").write_text("content", encoding="utf-8")

        result = ExecutionTools(repository, environment=StubEnvironment([])).run_command(
            {"command": "unchanged", "cwd": cwd, "env": {"MODE": "test"}}
        )

        assert_failure(result, code, "run_command")
        assert result.data["command"] == "unchanged"
        assert result.data["env_keys"] == ["MODE"]

    def test_outside_repository_working_directory_is_rejected(self, repository):
        outside = repository.parent / "outside"
        outside.mkdir()

        result = ExecutionTools(repository, environment=StubEnvironment([])).run_command(
            {"command": "unchanged", "cwd": str(outside)}
        )

        assert_failure(result, "cwd_outside_repository", "run_command")
        assert result.data["command"] == "unchanged"

    def test_symlink_cwd_cannot_escape_repository(self, repository):
        outside = repository.parent / "outside"
        outside.mkdir()
        link = repository / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"Symlinks are unavailable in this environment: {exc}")

        result = ExecutionTools(repository, environment=StubEnvironment([])).run_command(
            {"command": "unchanged", "cwd": "linked"}
        )

        assert_failure(result, "cwd_outside_repository", "run_command")


def test_local_environment_command_primitive_merges_env_in_explicit_order(repository, monkeypatch):
    captured = {}

    def fake_run(command, cwd, env, timeout, backend):
        captured.update(command=command, cwd=cwd, env=env, timeout=timeout, backend=backend)
        return subprocess.CompletedProcess(command, 0, stdout="ok")

    monkeypatch.setattr(local_module, "_run", fake_run)
    with patch.dict(os.environ, {"SHARED_STAGE1D_KEY": "process", "PROCESS_ONLY_STAGE1D_KEY": "process"}):
        environment = LocalEnvironment(
            backend=StubBackend(),
            env={"SHARED_STAGE1D_KEY": "config", "CONFIG_ONLY_STAGE1D_KEY": "config"},
            timeout=20,
        )
        result = environment.execute_command(
            "literal command",
            cwd=str(repository),
            timeout=5,
            env={"SHARED_STAGE1D_KEY": "call", "CALL_ONLY_STAGE1D_KEY": "call"},
        )

    assert result == {"output": "ok", "returncode": 0, "exception_info": ""}
    assert captured["env"]["PROCESS_ONLY_STAGE1D_KEY"] == "process"
    assert captured["env"]["CONFIG_ONLY_STAGE1D_KEY"] == "config"
    assert captured["env"]["CALL_ONLY_STAGE1D_KEY"] == "call"
    assert captured["env"]["SHARED_STAGE1D_KEY"] == "call"
    assert captured["command"] == "literal command"
    assert captured["timeout"] == 5
    assert captured["backend"] is environment.backend


def test_execution_tool_does_not_apply_submission_sentinel_but_legacy_execute_does(repository, monkeypatch):
    sentinel_output = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nordinary tool output\n"

    def fake_run(command, cwd, env, timeout, backend):
        return subprocess.CompletedProcess(command, 0, stdout=sentinel_output)

    monkeypatch.setattr(local_module, "_run", fake_run)
    environment = LocalEnvironment(backend=StubBackend(), cwd=str(repository))
    tools = ExecutionTools(repository, environment=environment)

    result = tools.run_command({"command": "literal command"})

    assert result.success is True
    assert result.data["output"] == sentinel_output
    with pytest.raises(Submitted):
        environment.execute({"command": "literal command"})


def test_registry_dispatch_preserves_execution_success_failure_and_provenance(repository):
    environment = StubEnvironment([raw_result(output="ok"), raw_result(output="bad", returncode=8)])
    registry = ToolRegistry()
    register_execution_tools(registry, repository, environment=environment)

    success = registry.dispatch("build", {"command": "compile"})
    failure = registry.dispatch("test", {"command": "unit tests"})

    assert registry.names == ("build", "run_command", "test")
    assert success.tool_name == "build"
    assert success.success is True
    assert success.provenance[0].source == "build"
    assert failure.tool_name == "test"
    assert failure.success is False
    assert failure.data["output"] == "bad"
    assert failure.diagnostics[0].code == "command_failed"
    assert failure.provenance[0].metadata["returncode"] == 8


def test_registration_is_atomic_when_an_execution_tool_name_already_exists(repository):
    registry = ToolRegistry()
    registry.register("test", lambda _arguments: None)

    with pytest.raises(ValueError, match="already registered"):
        ExecutionTools(repository, environment=StubEnvironment([])).register(registry)

    assert registry.names == ("test",)
