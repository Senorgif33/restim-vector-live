from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TCodeCommand:
    axis: str
    value: float
    interval_ms: int = 0


_COMMAND = re.compile(r"^([A-Za-z][0-9])([0-9]+)(?:I([0-9]+))?$")


def parse_command(text: str) -> TCodeCommand:
    """Parse the same decimal T-code representation used by ReStim."""
    match = _COMMAND.match(text.strip())
    if not match:
        raise ValueError(f"Invalid T-code command: {text!r}")
    axis, digits, interval = match.groups()
    value = int(digits) / (10 ** len(digits))
    return TCodeCommand(axis.upper(), min(1.0, max(0.0, value)), int(interval or 0))


def parse_message(message: str) -> list[TCodeCommand]:
    commands: list[TCodeCommand] = []
    for token in re.split(r"[\s\r\n]+", message.strip()):
        if token:
            try:
                commands.append(parse_command(token))
            except ValueError:
                pass
    return commands


def format_command(axis: str, value: float, interval_ms: int = 0) -> str:
    scaled = min(9999, max(0, int(value * 10000)))
    suffix = f"I{int(interval_ms)}" if interval_ms > 0 else ""
    return f"{axis.upper()}{scaled:04d}{suffix}"

