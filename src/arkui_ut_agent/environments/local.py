import os
import platform
import subprocess
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

from arkui_ut_agent.environments.shell import ShellBackend, ShellBackendName, get_shell_backend
from arkui_ut_agent.exceptions import Submitted
from arkui_ut_agent.utils.serialize import recursive_merge


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int = 30
    shell_backend: ShellBackendName = "auto"


class LocalEnvironment:
    def __init__(
        self,
        *,
        config_class: type = LocalEnvironmentConfig,
        backend: ShellBackend | None = None,
        **kwargs,
    ):
        """Execute commands using the native or explicitly configured shell backend."""
        self.config = config_class(**kwargs)
        self.backend = backend or get_shell_backend(self.config.shell_backend)

    def execute(self, action: dict, cwd: str = "", *, timeout: float | None = None) -> dict[str, Any]:
        """Execute a command in the local environment and return the result as a dict."""
        output = self.execute_command(action.get("command", ""), cwd=cwd, timeout=timeout)
        self._check_finished(output)
        return output

    def execute_command(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute a command without applying agent-level submission sentinel semantics."""
        cwd = cwd or self.config.cwd or os.getcwd()
        effective_env = os.environ | self.config.env | dict(env or {})
        try:
            result = _run(command, cwd, effective_env, timeout or self.config.timeout, self.backend)
            output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        return output

    def _check_finished(self, output: dict):
        """Raises Submitted if the output indicates task completion."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output["returncode"] == 0:
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        runtime = {
            "os_name": platform.system(),
            "shell_name": self.backend.name,
            "shell_dialect": self.backend.dialect,
            "shell_executable": self.backend.executable,
        }
        return recursive_merge(self.config.model_dump(), platform.uname()._asdict(), os.environ, runtime, kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                    "shell_backend": self.backend.name,
                    "shell_dialect": self.backend.dialect,
                }
            }
        }


def _run(
    command: str,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    backend: ShellBackend,
) -> subprocess.CompletedProcess[str]:
    """Like subprocess.run, but kills the whole process group on timeout so no children are orphaned."""
    process = subprocess.Popen(
        backend.argv(command),
        text=True,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **backend.popen_kwargs(),
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        backend.terminate_process_tree(process)
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout)
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)
