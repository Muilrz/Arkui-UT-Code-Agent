"""Platform shell backends used by the local execution environment."""

import os
import shutil
import signal
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

ShellBackendName = Literal["auto", "powershell", "posix"]


class ShellBackend(ABC):
    """Translate a command into a concrete shell process invocation."""

    name: str
    dialect: str

    def __init__(self, executable: str):
        self.executable = executable

    @abstractmethod
    def argv(self, command: str) -> list[str]:
        """Return the process arguments that execute ``command``."""

    def popen_kwargs(self) -> dict[str, Any]:
        """Return platform-specific process creation options."""
        return {}

    @abstractmethod
    def terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        """Terminate the shell and processes spawned by it."""


class PowerShellBackend(ShellBackend):
    """Execute commands using PowerShell syntax."""

    name = "powershell"
    dialect = "powershell"

    def __init__(self, executable: str | None = None):
        resolved = executable or shutil.which("pwsh") or shutil.which("powershell")
        if not resolved:
            raise RuntimeError("PowerShell is required for the PowerShell local environment backend.")
        super().__init__(resolved)

    def argv(self, command: str) -> list[str]:
        return [self.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]

    def popen_kwargs(self) -> dict[str, Any]:
        if os.name != "nt":
            return {}
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if process.poll() is None:
            process.kill()


class PosixShellBackend(ShellBackend):
    """Execute commands using Bash when available, otherwise POSIX sh."""

    name = "posix"

    def __init__(self, executable: str | None = None):
        resolved = executable or shutil.which("bash") or shutil.which("sh")
        if not resolved:
            raise RuntimeError("bash or sh is required for the POSIX local environment backend.")
        super().__init__(resolved)
        self.dialect = "bash" if Path(resolved).name.lower().startswith("bash") else "posix-sh"

    def argv(self, command: str) -> list[str]:
        option = "-lc" if self.dialect == "bash" else "-c"
        return [self.executable, option, command]

    def popen_kwargs(self) -> dict[str, Any]:
        return {"start_new_session": True}

    def terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)


def get_shell_backend(name: ShellBackendName = "auto") -> ShellBackend:
    """Resolve an explicit backend or the native default for this platform."""
    if name == "auto":
        name = "powershell" if os.name == "nt" else "posix"
    if name == "powershell":
        return PowerShellBackend()
    if name == "posix":
        return PosixShellBackend()
    raise ValueError(f"Unknown shell backend: {name}")


__all__ = [
    "PosixShellBackend",
    "PowerShellBackend",
    "ShellBackend",
    "ShellBackendName",
    "get_shell_backend",
]
