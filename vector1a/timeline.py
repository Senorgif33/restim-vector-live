from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time

from .routing import AxisSegment
from .tcode import TCodeCommand

TIMELINE_SCALE_SECONDS = 10000.0
# Timeline axes use 5-digit T-code on the MFP device (Output precision = 5).
# At this scale that yields ~0.1 s position steps; allow ~1 s without a new
# packet before treating T0 as stale, then briefly hold for the volume ramp.
TIMELINE_TCODE_DIGITS = 5
TIMELINE_FRESHNESS_SECONDS = 1.0
# After freshness expires, continue reporting last position/progress briefly
# instead of slamming the media volume ramp to floor.
TIMELINE_HOLD_SECONDS = 2.0
RAMP_CURVE_NAMES = (
    "Linear",
    "Exponential",
    "Logarithmic",
    "Smoothstep",
    "Smootherstep",
    "Power2",
    "Late Kick",
    "Plateau Rise",
)
_RAMP_CURVE_K = 3.0
_LATE_KICK_AT = 0.75
_PLATEAU_HOLD = 0.35


@dataclass(frozen=True)
class TimelineState:
    position_s: float | None
    duration_s: float | None
    progress: float | None
    position_ms: int | None
    fresh: bool
    held: bool = False

    @property
    def usable(self) -> bool:
        """True while live or briefly holding last known media time."""
        return self.fresh or self.held


def decode_timeline_seconds(encoded: float,
                            scale_seconds: float = TIMELINE_SCALE_SECONDS) -> float:
    scale = max(1.0, float(scale_seconds))
    return min(1.0, max(0.0, float(encoded))) * scale


def ramp_curve(progress: float, name: str = "Linear") -> float:
    """Map media progress 0..1 through a named ramp curve with exact endpoints."""
    t = min(1.0, max(0.0, float(progress)))
    key = str(name).strip().lower().replace("_", " ")
    key = " ".join(key.split())
    if key == "exponential":
        k = _RAMP_CURVE_K
        return (math.exp(k * t) - 1.0) / (math.exp(k) - 1.0)
    if key == "logarithmic":
        k = _RAMP_CURVE_K
        return math.log(1.0 + (math.exp(k) - 1.0) * t) / k
    if key == "smoothstep":
        return t * t * (3.0 - 2.0 * t)
    if key == "smootherstep":
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
    if key in ("power2", "power 2", "power n=2", "power n2"):
        return t * t
    if key == "late kick":
        kick = _LATE_KICK_AT
        if t <= kick:
            return 0.12 * (t / kick)
        u = (t - kick) / (1.0 - kick)
        return 0.12 + 0.88 * (u * u)
    if key in ("plateau rise", "plateau then rise"):
        hold = _PLATEAU_HOLD
        if t <= hold:
            return 0.05 * (t / hold)
        u = (t - hold) / (1.0 - hold)
        return 0.05 + 0.95 * (u * u * (3.0 - 2.0 * u))
    return t


def media_volume_gain(progress: float | None, floor: float, ceiling: float,
                      curve: str = "Linear") -> float:
    """Return volume multiplier from media percent, floor, ceiling and curve.

    Missing progress uses *floor* (safe under-drive). Ceiling is raised to floor
    when mis-ordered.
    """
    low = min(1.0, max(0.0, float(floor)))
    high = min(1.0, max(0.0, float(ceiling)))
    high = max(low, high)
    if progress is None:
        return low
    shaped = ramp_curve(progress, curve)
    return low + (high - low) * shaped


class MediaTimeline:
    """Decode MFP absolute timeline axes on Vector's delayed sample timeline."""

    def __init__(self, position_axis: str = "T0", duration_axis: str = "T1",
                 scale_seconds: float = TIMELINE_SCALE_SECONDS,
                 history_seconds: float = 15.0,
                 clock=time.monotonic) -> None:
        self.clock = clock
        self.history_seconds = max(5.0, float(history_seconds))
        self._lock = threading.RLock()
        self._tracks: dict[str, list[AxisSegment]] = {}
        self.configure(position_axis, duration_axis, scale_seconds)

    def configure(self, position_axis: str, duration_axis: str,
                  scale_seconds: float = TIMELINE_SCALE_SECONDS) -> None:
        with self._lock:
            self.position_axis = str(position_axis or "T0").strip().upper() or "T0"
            self.duration_axis = str(duration_axis or "T1").strip().upper() or "T1"
            self.scale_seconds = max(1.0, float(scale_seconds))

    def timeline_axes(self) -> frozenset[str]:
        with self._lock:
            return frozenset({self.position_axis, self.duration_axis})

    def receive(self, command: TCodeCommand, received_at: float | None = None) -> None:
        received_at = self.clock() if received_at is None else float(received_at)
        axis = command.axis.upper()
        with self._lock:
            if axis not in (self.position_axis, self.duration_axis):
                return
            previous = self._value_at_locked(axis, received_at)
            if previous is None:
                previous = command.value
            end_at = received_at + max(0, command.interval_ms) / 1000.0
            segment = AxisSegment(received_at, received_at, end_at,
                                  previous, command.value)
            track = self._tracks.setdefault(axis, [])
            track.append(segment)
            cutoff = received_at - self.history_seconds
            while len(track) > 1 and track[1].received_at < cutoff:
                track.pop(0)

    def snapshot(self, at_time: float,
                 freshness_seconds: float = TIMELINE_FRESHNESS_SECONDS,
                 hold_seconds: float = TIMELINE_HOLD_SECONDS) -> TimelineState:
        freshness = max(0.05, float(freshness_seconds))
        hold = max(freshness, float(hold_seconds))
        with self._lock:
            position_enc = self._value_at_locked(self.position_axis, at_time)
            duration_enc = self._value_at_locked(self.duration_axis, at_time)
            position_seen = self._latest_received_at_or_before_locked(
                self.position_axis, at_time)
            age = (None if position_seen is None
                   else at_time - position_seen)
            fresh = age is not None and 0.0 <= age <= freshness
            held = (not fresh) and age is not None and 0.0 <= age <= hold
            usable = fresh or held
            position_s = (None if position_enc is None or not usable
                          else decode_timeline_seconds(position_enc, self.scale_seconds))
            duration_s = (None if duration_enc is None or not usable
                          else decode_timeline_seconds(duration_enc, self.scale_seconds))
            if duration_s is not None and duration_s <= 0.0:
                duration_s = None
            progress = None
            position_ms = None
            if position_s is not None:
                position_ms = int(round(position_s * 1000.0))
            if usable and position_s is not None and duration_s is not None:
                progress = min(1.0, max(0.0, position_s / duration_s))
            return TimelineState(
                position_s=position_s,
                duration_s=duration_s,
                progress=progress,
                position_ms=position_ms,
                fresh=fresh,
                held=held,
            )

    def _latest_received_at_or_before_locked(self, axis: str,
                                             at_time: float) -> float | None:
        track = self._tracks.get(axis)
        if not track:
            return None
        latest = None
        for segment in track:
            if segment.received_at <= at_time:
                latest = segment.received_at
            else:
                break
        return latest

    def _value_at_locked(self, axis: str, at_time: float) -> float | None:
        track = self._tracks.get(axis)
        if not track:
            return None
        chosen: AxisSegment | None = None
        for segment in track:
            if segment.received_at <= at_time:
                chosen = segment
            else:
                break
        if chosen is None:
            return track[0].start_value
        return chosen.value_at(at_time)
