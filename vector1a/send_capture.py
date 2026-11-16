"""Opt-in capture of the primary Restim send path, stamped with media time.

A wall-clock frame log cannot be aligned to a ``.events.yml`` timeline.
Media position exists only inside Vector at send time, so this log is written
there: one line per primary send, carrying the media ``position_ms`` that the
events engine used.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

SAMPLE_AXES = ("V0", "P0", "P1", "S1", "Vp")


def default_log_dir() -> Path:
    if sys.platform.startswith("win"):
        return Path.home() / "AppData" / "Local" / "Vector1A" / "send-capture"
    return Path.home() / ".local" / "share" / "Vector1A" / "send-capture"


def session_log_path(log_dir: Path | None = None) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return (log_dir or default_log_dir()) / f"session-{stamp}.log"


def format_axis(value: float | None) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def format_active(active) -> str:
    if not active:
        return "-"
    if isinstance(active, str):
        names = [active]
    else:
        names = [str(name).strip() for name in active if str(name).strip()]
    if not names:
        return "-"
    return ",".join(names)


def format_sample(media_ms: int | None, events_on: bool, active,
                  axes: dict[str, float | None]) -> str:
    """Render the media-keyed payload of one capture line (no wall stamp)."""
    media = "-" if media_ms is None else str(int(media_ms))
    fields = " ".join(
        f"{axis}={format_axis(axes.get(axis))}" for axis in SAMPLE_AXES)
    return (f"media_ms={media}  events_on={int(bool(events_on))}  "
            f"active={format_active(active)}  {fields}")


def parse_sample(line: str) -> dict[str, str]:
    """Split a capture line into its ``key=value`` fields (ignores the stamp)."""
    fields: dict[str, str] = {}
    for token in line.split():
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        fields[key] = value
    return fields


class SendCaptureLog:
    """Append-only session log; every write is flushed immediately.

    Mirrors the reliability of the proxy's capture log (flush per line, writes
    never raise into the send path) without depending on the proxy package.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8", newline="\n")
        self.write_meta(f"session_start path={self.path}")

    def write_meta(self, text: str) -> None:
        self._write(f"META  {text}")

    def write_sample(self, media_ms: int | None = None, events_on: bool = False,
                     active=(), **axes: float | None) -> None:
        self._write(format_sample(media_ms, events_on, active, axes))

    def _write(self, payload: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with self._lock:
            if self._closed:
                return
            try:
                self._file.write(f"{stamp}  {payload}\n")
                self._file.flush()
            except Exception:
                return

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                self._file.write(f"{stamp}  META  session_end\n")
                self._file.flush()
            except Exception:
                pass
            try:
                self._file.close()
            except Exception:
                pass
