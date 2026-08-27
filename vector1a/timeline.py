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
# packet before treating T0 as stale, then hold long enough to cover Vector's
# default look-ahead so send-time file events (including cum → S1) still see
# media position. Hold must stay above engine look-ahead (default 2 s).
TIMELINE_TCODE_DIGITS = 5
TIMELINE_FRESHNESS_SECONDS = 1.0
# After freshness expires, continue reporting last position/progress briefly
# instead of slamming the media volume ramp to floor — and instead of dropping
# custom-event apply (S1 mute) at sample due_at.
TIMELINE_HOLD_SECONDS = 5.0
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

RAMP_LEVEL_KEYS = (
    "floor1", "floor2", "floor3",
    "ceiling1", "ceiling2", "ceiling3",
)
RAMP_LEVEL_LABELS = {
    "floor1": "Floor 1",
    "floor2": "Floor 2",
    "floor3": "Floor 3",
    "ceiling1": "Ceiling 1",
    "ceiling2": "Ceiling 2",
    "ceiling3": "Ceiling 3",
}
# Extra level spinboxes are shown only when a waypoint references them.
EXTRA_RAMP_LEVEL_KEYS = ("floor2", "floor3", "ceiling2", "ceiling3")


@dataclass(frozen=True)
class RampWaypoint:
    """Absolute media time targeting a named ramp level.

    ``curve`` shapes the segment arriving at this waypoint (from the previous
    point). ``None`` means inherit the global / default curve at normalize time.
    The first waypoint's curve is unused until a later point exists.
    """
    time_s: float
    level: str
    curve: str | None = None


def normalize_curve_name(name: str | None, default: str = "Linear") -> str:
    """Return a canonical ramp curve label from ``RAMP_CURVE_NAMES``."""
    fallback = default if default in RAMP_CURVE_NAMES else "Linear"
    raw = str(name or "").strip()
    if not raw:
        return fallback
    key = " ".join(raw.lower().replace("_", " ").split())
    for label in RAMP_CURVE_NAMES:
        if " ".join(label.lower().split()) == key:
            return label
    return fallback


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


def normalize_level_key(level: str | None) -> str:
    key = str(level or "floor1").strip().lower().replace(" ", "").replace("_", "")
    if key in ("ceiling1", "ceiling"):
        return "ceiling1"
    if key == "ceiling2":
        return "ceiling2"
    if key == "ceiling3":
        return "ceiling3"
    if key in ("floor1", "floor"):
        return "floor1"
    if key == "floor2":
        return "floor2"
    if key == "floor3":
        return "floor3"
    return "floor1"


def clamp_gain(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def level_gains(floor1: float, ceiling1: float,
                floor2: float = 0.4, ceiling2: float = 1.0,
                floor3: float = 0.4, ceiling3: float = 1.0) -> dict[str, float]:
    return {
        "floor1": clamp_gain(floor1),
        "floor2": clamp_gain(floor2),
        "floor3": clamp_gain(floor3),
        "ceiling1": clamp_gain(ceiling1),
        "ceiling2": clamp_gain(ceiling2),
        "ceiling3": clamp_gain(ceiling3),
    }


def parse_media_time(text: str) -> float:
    """Parse ``h:mm:ss``, ``m:ss``, or plain seconds into absolute media seconds."""
    raw = str(text).strip()
    if not raw:
        raise ValueError("Empty media time")
    if ":" in raw:
        parts = raw.split(":")
        if len(parts) == 2:
            minutes, seconds = parts
            hours = "0"
        elif len(parts) == 3:
            hours, minutes, seconds = parts
        else:
            raise ValueError(f"Invalid media time: {text!r}")
        try:
            total = (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
        except ValueError as exc:
            raise ValueError(f"Invalid media time: {text!r}") from exc
        if total < 0:
            raise ValueError(f"Invalid media time: {text!r}")
        return float(total)
    try:
        total = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid media time: {text!r}") from exc
    if total < 0:
        raise ValueError(f"Invalid media time: {text!r}")
    return total


def format_media_time(seconds: float) -> str:
    """Format absolute media seconds as ``m:ss`` or ``h:mm:ss``."""
    total = max(0.0, float(seconds))
    whole = int(math.floor(total + 1e-9))
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def normalize_waypoints(raw_waypoints: list | tuple | None,
                        default_curve: str = "Linear") -> list[RampWaypoint]:
    """Return waypoints sorted by time only (stable); drop malformed entries."""
    if not raw_waypoints:
        return []
    fallback = normalize_curve_name(default_curve)
    cleaned: list[RampWaypoint] = []
    for item in raw_waypoints:
        try:
            if isinstance(item, RampWaypoint):
                time_s = float(item.time_s)
                level = normalize_level_key(item.level)
                curve = normalize_curve_name(item.curve, fallback)
            elif isinstance(item, dict):
                if "time_s" in item:
                    time_s = float(item["time_s"])
                elif "time" in item:
                    time_s = float(item["time"])
                else:
                    continue
                level = normalize_level_key(item.get("level", "floor1"))
                curve = normalize_curve_name(item.get("curve"), fallback)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                time_s = float(item[0])
                level = normalize_level_key(str(item[1]))
                curve = normalize_curve_name(
                    item[2] if len(item) >= 3 else None, fallback)
            else:
                continue
            if time_s < 0:
                continue
            cleaned.append(RampWaypoint(time_s=time_s, level=level, curve=curve))
        except (TypeError, ValueError):
            continue
    cleaned.sort(key=lambda point: point.time_s)
    return cleaned


def waypoint_levels_used(waypoints: list[RampWaypoint] | list | tuple | None) -> frozenset[str]:
    """Return normalized level keys referenced by waypoints."""
    return frozenset(point.level for point in normalize_waypoints(waypoints))


def export_ramp_waypoints_payload(
        waypoints: list[RampWaypoint] | list | tuple | None,
        floor1: float,
        floor2: float,
        floor3: float,
        ceiling1: float,
        ceiling2: float,
        ceiling3: float,
        curve: str = "Linear",
) -> dict:
    """JSON-serializable ramp waypoint payload (levels + default curve + points)."""
    default_curve = normalize_curve_name(curve)
    return {
        "waypoints": [
            {"time_s": point.time_s, "level": point.level, "curve": point.curve}
            for point in normalize_waypoints(waypoints, default_curve)
        ],
        "floor1": clamp_gain(floor1),
        "floor2": clamp_gain(floor2),
        "floor3": clamp_gain(floor3),
        "ceiling1": clamp_gain(ceiling1),
        "ceiling2": clamp_gain(ceiling2),
        "ceiling3": clamp_gain(ceiling3),
        "curve": default_curve,
    }


def import_ramp_waypoints_payload(data: dict | None) -> tuple[list[RampWaypoint], dict]:
    """Parse an export payload into waypoints and level/curve settings."""
    if not isinstance(data, dict):
        raise ValueError("Ramp waypoint payload must be a JSON object")
    default_curve = normalize_curve_name(data.get("curve") or "Linear")
    waypoints = normalize_waypoints(data.get("waypoints", []), default_curve)
    # Legacy single "floor" key maps to floor1.
    floor1 = data.get("floor1", data.get("floor", 0.4))
    settings = {
        "floor1": clamp_gain(floor1),
        "floor2": clamp_gain(data.get("floor2", floor1)),
        "floor3": clamp_gain(data.get("floor3", floor1)),
        "ceiling1": clamp_gain(data.get("ceiling1", data.get("ceiling", 1.0))),
        "ceiling2": clamp_gain(data.get("ceiling2", 1.0)),
        "ceiling3": clamp_gain(data.get("ceiling3", 1.0)),
        "curve": default_curve,
    }
    return waypoints, settings


VECTOR_RAMP_FUNSCRIPT_META_KEY = "vector1a_media_ramp"
_RAMP_BAKE_STEP_S = 0.1


def format_ofs_time(seconds: float) -> str:
    """Format seconds as OFS bookmark/chapter time ``HH:MM:SS.mmm``."""
    total_ms = int(round(max(0.0, float(seconds)) * 1000.0))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def parse_ofs_time(text: str) -> float:
    """Parse OFS ``HH:MM:SS.mmm`` / ``H:MM:SS.mmm`` (or without millis) to seconds."""
    raw = str(text).strip()
    if not raw:
        raise ValueError("Empty OFS time")
    if ":" not in raw:
        return parse_media_time(raw)
    parts = raw.split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"Invalid OFS time: {text!r}")
    try:
        total = (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
    except ValueError as exc:
        raise ValueError(f"Invalid OFS time: {text!r}") from exc
    if total < 0:
        raise ValueError(f"Invalid OFS time: {text!r}")
    return float(total)


def format_ramp_bookmark_name(level: str, curve: str | None = None) -> str:
    """Human-visible OFS bookmark name; optional curve suffix for fallback import."""
    label = RAMP_LEVEL_LABELS.get(normalize_level_key(level), "Floor 1")
    if curve:
        return f"{label} | {normalize_curve_name(curve)}"
    return label


def parse_ramp_bookmark_name(name: str | None) -> tuple[str, str | None] | None:
    """Return ``(level_key, curve_or_None)`` if *name* looks like a ramp bookmark."""
    raw = str(name or "").strip()
    if not raw:
        return None
    label_part = raw
    curve: str | None = None
    if " | " in raw:
        label_part, curve_part = raw.split(" | ", 1)
        label_part = label_part.strip()
        curve = normalize_curve_name(curve_part.strip())
    label_to_key = {label.lower(): key for key, label in RAMP_LEVEL_LABELS.items()}
    key = label_to_key.get(label_part.lower())
    if key is None:
        compact = label_part.lower().replace(" ", "").replace("_", "")
        aliases = {
            "floor": "floor1",
            "floor1": "floor1",
            "floor2": "floor2",
            "floor3": "floor3",
            "ceiling": "ceiling1",
            "ceiling1": "ceiling1",
            "ceiling2": "ceiling2",
            "ceiling3": "ceiling3",
        }
        key = aliases.get(compact)
    if key is None:
        return None
    return key, curve


def bake_ramp_funscript_actions(
        waypoints: list[RampWaypoint] | list | tuple | None,
        floor1: float,
        ceiling1: float,
        floor2: float = 0.4,
        ceiling2: float = 1.0,
        floor3: float = 0.4,
        ceiling3: float = 1.0,
        curve: str = "Linear",
        end_s: float | None = None,
        step_s: float = _RAMP_BAKE_STEP_S,
) -> list[dict]:
    """Sample the waypoint ramp into funscript ``{at, pos}`` actions (pos 0–100)."""
    default_curve = normalize_curve_name(curve)
    points = normalize_waypoints(waypoints, default_curve)
    if not points:
        return [{"at": 0, "pos": int(round(clamp_gain(floor1) * 100))}]
    last_s = points[-1].time_s
    duration = float(end_s) if end_s is not None else max(last_s * 1.05, last_s + 1.0)
    duration = max(duration, last_s, 0.0)
    step = max(0.02, float(step_s))
    sample_times = {0.0, duration}
    t = 0.0
    while t <= duration + 1e-9:
        sample_times.add(round(t, 6))
        t += step
    for point in points:
        sample_times.add(round(float(point.time_s), 6))
    actions: list[dict] = []
    for time_s in sorted(sample_times):
        if time_s < 0:
            continue
        gain = media_volume_gain_waypoints(
            time_s, points, floor1, ceiling1, floor2, ceiling2, floor3, ceiling3,
            default_curve)
        pos = int(round(clamp_gain(gain) * 100.0))
        pos = min(100, max(0, pos))
        at_ms = int(round(time_s * 1000.0))
        if actions and actions[-1]["at"] == at_ms:
            actions[-1] = {"at": at_ms, "pos": pos}
            continue
        actions.append({"at": at_ms, "pos": pos})
    return actions


def export_ramp_funscript(
        waypoints: list[RampWaypoint] | list | tuple | None,
        floor1: float,
        floor2: float,
        floor3: float,
        ceiling1: float,
        ceiling2: float,
        ceiling3: float,
        curve: str = "Linear",
        end_s: float | None = None,
        step_s: float = _RAMP_BAKE_STEP_S,
) -> dict:
    """OFS-compatible volume funscript with bookmarks + Vector ramp metadata."""
    payload = export_ramp_waypoints_payload(
        waypoints, floor1, floor2, floor3, ceiling1, ceiling2, ceiling3, curve)
    points = normalize_waypoints(payload["waypoints"], payload["curve"])
    bookmarks = [
        {
            "name": format_ramp_bookmark_name(point.level, point.curve),
            "time": format_ofs_time(point.time_s),
        }
        for point in points
    ]
    actions = bake_ramp_funscript_actions(
        points, floor1, ceiling1, floor2, ceiling2, floor3, ceiling3,
        payload["curve"], end_s=end_s, step_s=step_s)
    return {
        "version": "1.0",
        "inverted": False,
        "range": 100,
        "actions": actions,
        "metadata": {
            "creator": "Vector 1A",
            "description": "Media volume ramp (baked actions; bookmarks + "
                           f"{VECTOR_RAMP_FUNSCRIPT_META_KEY} for re-edit)",
            "bookmarks": bookmarks,
            VECTOR_RAMP_FUNSCRIPT_META_KEY: payload,
        },
    }


def import_ramp_from_bookmarks(
        bookmarks: list | tuple | None,
        default_curve: str = "Linear",
) -> list[RampWaypoint]:
    """Build waypoints from OFS-style bookmarks named like Floor/Ceiling levels."""
    if not bookmarks:
        return []
    fallback = normalize_curve_name(default_curve)
    raw: list[RampWaypoint] = []
    for item in bookmarks:
        if not isinstance(item, dict):
            continue
        parsed = parse_ramp_bookmark_name(item.get("name"))
        if parsed is None:
            continue
        level, curve = parsed
        try:
            time_s = parse_ofs_time(str(item.get("time", "")))
        except (TypeError, ValueError):
            continue
        raw.append(RampWaypoint(time_s, level, curve or fallback))
    return normalize_waypoints(raw, fallback)


def import_ramp_funscript(data: dict | None) -> tuple[list[RampWaypoint], dict]:
    """Import ramp from funscript (metadata first), Vector JSON, or OFS bookmarks."""
    if not isinstance(data, dict):
        raise ValueError("Ramp funscript / JSON must be an object")

    # Native Vector JSON payload (no actions wrapper).
    if "waypoints" in data and "actions" not in data:
        return import_ramp_waypoints_payload(data)

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    ramp_meta = metadata.get(VECTOR_RAMP_FUNSCRIPT_META_KEY)
    if isinstance(ramp_meta, dict):
        return import_ramp_waypoints_payload(ramp_meta)

    # Some tools may put the payload at the top level alongside actions.
    if isinstance(data.get(VECTOR_RAMP_FUNSCRIPT_META_KEY), dict):
        return import_ramp_waypoints_payload(data[VECTOR_RAMP_FUNSCRIPT_META_KEY])

    bookmarks = metadata.get("bookmarks")
    if isinstance(bookmarks, list) and bookmarks:
        default_curve = normalize_curve_name(
            metadata.get("curve") or data.get("curve") or "Linear")
        points = import_ramp_from_bookmarks(bookmarks, default_curve)
        if points:
            # Level gains unknown — keep defaults; caller may leave existing UI values.
            settings = {
                "floor1": clamp_gain(data.get("floor1", 0.4)),
                "floor2": clamp_gain(data.get("floor2", data.get("floor1", 0.4))),
                "floor3": clamp_gain(data.get("floor3", data.get("floor1", 0.4))),
                "ceiling1": clamp_gain(data.get("ceiling1", 1.0)),
                "ceiling2": clamp_gain(data.get("ceiling2", 1.0)),
                "ceiling3": clamp_gain(data.get("ceiling3", 1.0)),
                "curve": default_curve,
                "gains_from_bookmarks_only": True,
            }
            return points, settings

    if isinstance(data.get("actions"), list):
        raise ValueError(
            "Funscript has actions but no Vector ramp metadata or Floor/Ceiling "
            "bookmarks; re-export from Vector or add bookmarks for waypoints")
    raise ValueError("Unrecognized ramp import file (need Vector JSON or funscript metadata)")


def media_volume_gain_waypoints(
        position_s: float | None,
        waypoints: list[RampWaypoint] | list | tuple | None,
        floor1: float,
        ceiling1: float,
        floor2: float = 0.4,
        ceiling2: float = 1.0,
        floor3: float = 0.4,
        ceiling3: float = 1.0,
        curve: str = "Linear",
) -> float:
    """Gain from absolute media time and level waypoints.

    Missing position uses Floor 1. Empty/invalid waypoints fall back to Floor 1.
    After the last waypoint, gain holds that level until EOF.
    Each segment A→B uses B's curve (falling back to *curve* when unset).
    """
    default_curve = normalize_curve_name(curve)
    gains = level_gains(floor1, ceiling1, floor2, ceiling2, floor3, ceiling3)
    floor_gain = gains["floor1"]
    points = normalize_waypoints(waypoints, default_curve)
    if not points:
        return floor_gain
    if position_s is None:
        return floor_gain

    position = max(0.0, float(position_s))
    if position <= points[0].time_s:
        return gains[points[0].level]
    if position >= points[-1].time_s:
        return gains[points[-1].level]

    left = points[0]
    right = points[-1]
    for index in range(len(points) - 1):
        candidate_left = points[index]
        candidate_right = points[index + 1]
        if candidate_left.time_s <= position <= candidate_right.time_s:
            left = candidate_left
            right = candidate_right
            break

    span = right.time_s - left.time_s
    if span <= 1e-9:
        return gains[right.level]
    u = (position - left.time_s) / span
    u = min(1.0, max(0.0, u))
    start = gains[left.level]
    end = gains[right.level]
    segment_curve = normalize_curve_name(right.curve, default_curve)
    shaped = ramp_curve(u, segment_curve)
    return clamp_gain(start + (end - start) * shaped)


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
