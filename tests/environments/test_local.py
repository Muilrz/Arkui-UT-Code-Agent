import os
import shlex
import signal
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from arkui_ut_agent.environments.local import LocalEnvironment, LocalEnvironmentConfig
from arkui_ut_agent.environments.shell import (
    PosixShellBackend,
    PowerShellBackend,
    get_shell_backend,
)

IS_WINDOWS = os.name == "nt"


def native_command(*, powershell: str, posix: str) -> str:
    """Select equivalent commands written in the native shell dialect."""
    return powershell if IS_WINDOWS else posix


def assert_same_path(expected: str | Path, shell_output: str) -> None:
    """Compare filesystem identity instead of platform-dependent path spelling."""
    actual = Path(shell_output.strip())
    assert actual.exists()
    assert actual.samefile(expected)


def test_local_environment_config_defaults():
    """Test that LocalEnvironmentConfig has correct default values."""
    config = LocalEnvironmentConfig()

    assert config.cwd == ""
    assert config.env == {}
    assert config.timeout == 30
    assert config.shell_backend == "auto"


def test_local_environment_uses_native_shell_backend():
    env = LocalEnvironment()

    assert env.backend.name == ("powershell" if IS_WINDOWS else "posix")


def test_local_environment_exposes_shell_template_vars():
    env = LocalEnvironment()

    template_vars = env.get_template_vars()
    assert template_vars["os_name"]
    assert template_vars["shell_name"] == env.backend.name
    assert template_vars["shell_dialect"] == env.backend.dialect
    assert template_vars["shell_executable"] == env.backend.executable


def test_shell_backend_command_argv_is_explicit():
    powershell = PowerShellBackend("pwsh")
    posix = PosixShellBackend("bash")

    assert powershell.argv("Write-Output 'ok'")[-2:] == ["-Command", "Write-Output 'ok'"]
    assert posix.argv("printf ok") == ["bash", "-lc", "printf ok"]


def test_get_shell_backend_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown shell backend"):
        get_shell_backend("unknown")  # type: ignore[arg-type]


def test_local_environment_basic_execution():
    """Test basic command execution in local environment."""
    env = LocalEnvironment()

    command = native_command(powershell="Write-Output 'hello world'", posix="printf '%s\\n' 'hello world'")
    result = env.execute({"command": command})
    assert result["returncode"] == 0
    assert "hello world" in result["output"]


def test_local_environment_set_env_variables():
    """Test setting environment variables in the local environment."""
    env = LocalEnvironment(env={"TEST_VAR": "test_value", "ANOTHER_VAR": "another_value"})

    # Test single environment variable
    command = native_command(powershell="Write-Output $env:TEST_VAR", posix="printf '%s\\n' \"$TEST_VAR\"")
    result = env.execute({"command": command})
    assert result["returncode"] == 0
    assert "test_value" in result["output"]

    # Test multiple environment variables
    command = native_command(
        powershell="Write-Output \"$env:TEST_VAR $env:ANOTHER_VAR\"",
        posix="printf '%s %s\\n' \"$TEST_VAR\" \"$ANOTHER_VAR\"",
    )
    result = env.execute({"command": command})
    assert result["returncode"] == 0
    assert "test_value another_value" in result["output"]


def test_local_environment_existing_env_variables():
    """Test that existing environment variables are preserved and merged."""
    with patch.dict(os.environ, {"EXISTING_VAR": "existing_value"}):
        env = LocalEnvironment(env={"NEW_VAR": "new_value"})

        # Test that both existing and new variables are available
        command = native_command(
            powershell="Write-Output \"$env:EXISTING_VAR $env:NEW_VAR\"",
            posix="printf '%s %s\\n' \"$EXISTING_VAR\" \"$NEW_VAR\"",
        )
        result = env.execute({"command": command})
        assert result["returncode"] == 0
        assert "existing_value new_value" in result["output"]


def test_local_environment_env_variable_override():
    """Test that config env variables override existing ones."""
    with patch.dict(os.environ, {"CONFLICT_VAR": "original_value"}):
        env = LocalEnvironment(env={"CONFLICT_VAR": "override_value"})

        command = native_command(
            powershell="Write-Output $env:CONFLICT_VAR",
            posix="printf '%s\\n' \"$CONFLICT_VAR\"",
        )
        result = env.execute({"command": command})
        assert result["returncode"] == 0
        assert "override_value" in result["output"]


def test_local_environment_custom_cwd():
    """Test executing commands in a custom working directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        env = LocalEnvironment(cwd=temp_dir)

        command = native_command(powershell="(Get-Location).Path", posix="pwd")
        result = env.execute({"command": command})
        assert result["returncode"] == 0
        assert_same_path(temp_dir, result["output"])


def test_local_environment_cwd_parameter_override():
    """Test that the cwd parameter in execute() overrides the config cwd."""
    with tempfile.TemporaryDirectory() as temp_dir1, tempfile.TemporaryDirectory() as temp_dir2:
        env = LocalEnvironment(cwd=temp_dir1)

        # Execute with different cwd parameter
        command = native_command(powershell="(Get-Location).Path", posix="pwd")
        result = env.execute({"command": command}, cwd=temp_dir2)
        assert result["returncode"] == 0
        assert_same_path(temp_dir2, result["output"])


def test_local_environment_default_cwd():
    """Test that commands use os.getcwd() when no cwd is specified."""
    env = LocalEnvironment()
    current_dir = os.getcwd()

    command = native_command(powershell="(Get-Location).Path", posix="pwd")
    result = env.execute({"command": command})
    assert result["returncode"] == 0
    assert_same_path(current_dir, result["output"])


def test_local_environment_command_failure():
    """Test that command failures are properly captured."""
    env = LocalEnvironment()

    result = env.execute({"command": "exit 1"})
    assert result["returncode"] == 1
    assert result["output"] == ""


def test_local_environment_nonexistent_command():
    """Test execution of non-existent command."""
    env = LocalEnvironment()

    result = env.execute({"command": "nonexistent_command_12345"})
    assert result["returncode"] != 0
    assert "nonexistent_command_12345" in result["output"] or "command not found" in result["output"]


def test_local_environment_stderr_capture():
    """Test that stderr is properly captured."""
    env = LocalEnvironment()

    command = native_command(
        powershell="[Console]::Error.WriteLine('error message')",
        posix="printf '%s\\n' 'error message' >&2",
    )
    result = env.execute({"command": command})
    assert result["returncode"] == 0
    assert "error message" in result["output"]


def test_local_environment_timeout():
    """Test timeout functionality returns structured output instead of raising."""
    env = LocalEnvironment(timeout=1)

    command = native_command(powershell="Start-Sleep -Seconds 2", posix="sleep 2")
    result = env.execute({"command": command})
    assert result["returncode"] == -1
    assert "timed out" in result["exception_info"]
    assert result["extra"]["exception_type"] == "TimeoutExpired"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-specific")
def test_local_environment_timeout_kills_child_process():
    """Test that timeout kills shell-spawned child processes."""
    with tempfile.TemporaryDirectory() as temp_dir:
        pid_file = Path(temp_dir) / "child.pid"
        script = Path(temp_dir) / "child.py"
        script.write_text(
            "\n".join(
                [
                    "import os",
                    "import sys",
                    "import time",
                    "from pathlib import Path",
                    "Path(sys.argv[1]).write_text(str(os.getpid()))",
                    "while True:",
                    "    time.sleep(1)",
                ]
            )
        )

        env = LocalEnvironment(timeout=1)
        result = env.execute(
            {"command": f"{shlex.quote(sys.executable)} {shlex.quote(str(script))} {shlex.quote(str(pid_file))}"}
        )

        assert result["returncode"] == -1
        child_pid = _read_pid(pid_file)
        try:
            assert _process_exited(child_pid)
        finally:
            _kill_process_if_running(child_pid)


def _read_pid(pid_file: Path) -> int:
    for _ in range(50):
        if pid_file.is_file() and (content := pid_file.read_text().strip()):
            return int(content)
        time.sleep(0.1)
    raise AssertionError(f"child never wrote its pid to {pid_file}")


def _process_exited(pid: int) -> bool:
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def _kill_process_if_running(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_local_environment_custom_timeout():
    """Test custom timeout configuration."""
    config = LocalEnvironmentConfig(timeout=5)
    env = LocalEnvironment(**config.__dict__)

    assert env.config.timeout == 5


@pytest.mark.parametrize(
    ("command", "expected_returncode"),
    [
        (native_command(powershell="Write-Output 'test'", posix="printf test"), 0),
        ("exit 1", 1),
        ("exit 42", 42),
    ],
)
def test_local_environment_return_codes(command, expected_returncode):
    """Test that various return codes are properly captured."""
    env = LocalEnvironment()

    result = env.execute({"command": command})
    assert result["returncode"] == expected_returncode


def test_local_environment_multiline_output():
    """Test handling of multiline command output."""
    env = LocalEnvironment()

    command = native_command(
        powershell="Write-Output 'line1','line2','line3'",
        posix="printf 'line1\\nline2\\nline3\\n'",
    )
    result = env.execute({"command": command})
    assert result["returncode"] == 0
    output_lines = result["output"].strip().split("\n")
    assert len(output_lines) == 3
    assert "line1" in output_lines[0]
    assert "line2" in output_lines[1]
    assert "line3" in output_lines[2]


def test_local_environment_file_operations():
    """Test file operations in the local environment."""
    with tempfile.TemporaryDirectory() as temp_dir:
        env = LocalEnvironment(cwd=temp_dir)

        # Create a file
        write_command = native_command(
            powershell="Set-Content -LiteralPath test.txt -Value 'test content'",
            posix="printf '%s\\n' 'test content' > test.txt",
        )
        result = env.execute({"command": write_command})
        assert result["returncode"] == 0

        # Read the file
        read_command = native_command(powershell="Get-Content -LiteralPath test.txt", posix="cat test.txt")
        result = env.execute({"command": read_command})
        assert result["returncode"] == 0
        assert "test content" in result["output"]

        # Verify file exists
        test_file = Path(temp_dir) / "test.txt"
        assert test_file.exists()
        assert test_file.read_text().strip() == "test content"


def test_local_environment_shell_features():
    """Test that shell features like pipes and redirects work."""
    env = LocalEnvironment()

    # Test pipe
    pipe_command = native_command(
        powershell="Write-Output 'hello world' | ForEach-Object { $_.ToUpperInvariant() }",
        posix="printf '%s\\n' 'hello world' | tr '[:lower:]' '[:upper:]'",
    )
    result = env.execute({"command": pipe_command})
    assert result["returncode"] == 0
    assert result["output"].strip() == "HELLO WORLD"

    # Test command substitution
    nested_command = native_command(
        powershell="Write-Output (Write-Output 'nested')",
        posix="printf '%s\\n' \"$(printf nested)\"",
    )
    result = env.execute({"command": nested_command})
    assert result["returncode"] == 0
    assert "nested" in result["output"]
