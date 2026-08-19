from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TCodeCommand:
    axis: str
    value: float
    interval_ms: int = 0


_COMMAND = re.compile(r"^([A-Za-z][0-9])([0-9]+)(?:I([0-9]+))?$")
_SCAN_COMMAND = re.compile(r"([A-Za-z][0-9])([0-9]+?)(?:I([0-9]+))?(?=[A-Za-z][0-9]|[\s,;|]|$)")


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

