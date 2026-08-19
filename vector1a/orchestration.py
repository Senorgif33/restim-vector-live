from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
import subprocess
from typing import Callable

IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class LaunchResult:
    name: str
    launched: bool
    message: str


def port_is_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


class SessionOrchestrator:
    """Launch external session applications without owning signal flow."""

    def __init__(self, status: Callable[[str], None] | None = None) -> None:
        self.status = status or (lambda _text: None)
        self._launched_targets: set[str] = set()

    def launch(self, name: str, target: str) -> LaunchResult:
        target = os.path.expandvars(os.path.expanduser(target.strip().strip('"')))
        if not target:
            return LaunchResult(name, False, f"{name}: no launch target configured")
        normalized = os.path.normcase(os.path.abspath(target))
        if normalized in self._launched_targets:
            return LaunchResult(name, False, f"{name}: already launched by Vector")
        path = Path(target)
        try:
            cwd = str(path.parent) if path.parent else None
            if IS_WINDOWS and path.suffix.lower() == ".exe":
                # ReStim keeps per-instance configuration beside the executable.
                # Launching with an explicit working directory lets independent copies
                # in different folders load their own INI/config instead of inheriting
                # Vector's working directory.
                subprocess.Popen([str(path)], cwd=cwd)
            elif IS_WINDOWS and path.suffix.lower() in {".bat", ".cmd"}:
                subprocess.Popen(["cmd.exe", "/c", str(path)], cwd=cwd)
            elif IS_WINDOWS:
                # .lnk and other ShellExecute targets retain normal Windows behavior.
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen([str(path)], cwd=cwd)
            self._launched_targets.add(normalized)
            result = LaunchResult(name, True, f"{name}: launched {target}")
        except OSError as exc:
            result = LaunchResult(name, False, f"{name}: launch failed: {exc}")
        self.status(result.message)
        return result


def wait_for_port(host: str, port: int, timeout: float = 12.0,
                  poll_interval: float = 0.20) -> bool:
    """Wait until a TCP service is accepting connections, without owning it."""
    import time
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if port_is_open(host, port):
            return True
        time.sleep(max(0.02, float(poll_interval)))
    return port_is_open(host, port)
