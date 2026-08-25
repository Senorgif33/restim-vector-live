from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re
import shlex


@dataclass(frozen=True)
class TCodeCommand:
    axis: str
    value: float
    interval_ms: int = 0


@dataclass(frozen=True)
class EvtTrigger:
    """Live custom-event trigger from an ``EVT`` line (not T-code)."""
    name: str
    params: dict[str, Any]


_COMMAND = re.compile(r"^([A-Za-z][0-9])([0-9]+)(?:I([0-9]+))?$")
_SCAN_COMMAND = re.compile(r"([A-Za-z][0-9])([0-9]+?)(?:I([0-9]+))?(?=[A-Za-z][0-9]|[\s,;|]|$)")
_EVT_PAIR = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def is_evt_line(text: str) -> bool:
    """True when the first token is EVT (case-insensitive)."""
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.split(None, 1)[0]
    return first.upper() == "EVT"


def _parse_evt_scalar(raw: str) -> Any:
    text = raw.strip()
    if (len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'"))):
        return text[1:-1]
    lower = text.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("null", "~"):
        return None
    try:
        if any(char in text for char in ".eE"):
            return float(text)
        return int(text)
    except ValueError:
        return text


def parse_evt_line(text: str) -> EvtTrigger:
    """Parse ``EVT name=… key=value…``. Raises ValueError if malformed."""
    stripped = text.strip()
    if not is_evt_line(stripped):
        raise ValueError(f"Not an EVT line: {text!r}")
    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError as exc:
        raise ValueError(f"Invalid EVT line: {text!r}") from exc
    if not tokens or tokens[0].upper() != "EVT":
        raise ValueError(f"Invalid EVT line: {text!r}")
    name: str | None = None
    params: dict[str, Any] = {}
    for token in tokens[1:]:
        match = _EVT_PAIR.match(token)
        if not match:
            raise ValueError(f"Invalid EVT token {token!r} in {text!r}")
        key, raw_value = match.groups()
        if key == "name":
            name = str(_parse_evt_scalar(raw_value))
            continue
        params[key] = _parse_evt_scalar(raw_value)
    if not name:
        raise ValueError(f"EVT line missing name=: {text!r}")
    return EvtTrigger(name=name, params=params)


def parse_command(text: str) -> TCodeCommand:
    """Parse the same decimal T-code representation used by ReStim."""
    match = _COMMAND.match(text.strip())
    if not match:
        raise ValueError(f"Invalid T-code command: {text!r}")
    axis, digits, interval = match.groups()
    value = int(digits) / (10 ** len(digits))
    return TCodeCommand(axis.upper(), min(1.0, max(0.0, value)), int(interval or 0))


def parse_message(message: str) -> list[TCodeCommand]:
    """Parse a T-code packet.

    MFP output is commonly whitespace separated, but some transports/plugins
    concatenate commands (for example ``L05000L17500V07000``).  Scan the
    packet rather than relying on whitespace so both forms are accepted.
    Unknown text is ignored, matching the listener's previous tolerant
    behaviour.
    """
    commands: list[TCodeCommand] = []
    for match in _SCAN_COMMAND.finditer(message.strip()):
        axis, digits, interval = match.groups()
        value = int(digits) / (10 ** len(digits))
        commands.append(TCodeCommand(
            axis.upper(), min(1.0, max(0.0, value)), int(interval or 0)))
    return commands


def format_command(axis: str, value: float, interval_ms: int = 0) -> str:
    scaled = min(9999, max(0, int(value * 10000)))
    suffix = f"I{int(interval_ms)}" if interval_ms > 0 else ""
    return f"{axis.upper()}{scaled:04d}{suffix}"

