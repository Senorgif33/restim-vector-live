from __future__ import annotations

import math


def speed_linked_depth(speed_percent: float, full_depth_speed: float = 35.0) -> float:
    """Smooth 0..1 variation depth: none at rest, full at configured speed."""
    full = max(1.0, float(full_depth_speed))
    progress = min(1.0, max(0.0, float(speed_percent) / full))
    return progress * progress * (3.0 - 2.0 * progress)


def rolling_value(elapsed_seconds: float, cycle_minutes: float,
                  minimum: float, maximum: float) -> float:
    """Cosine cycle: maximum -> minimum -> maximum with soft turnarounds."""
    period = max(1.0, cycle_minutes * 60.0)
    phase = (elapsed_seconds % period) / period
    blend = (1.0 + math.cos(phase * math.tau)) / 2.0
    return minimum + (maximum - minimum) * blend


def rolling_offset(elapsed_seconds: float, cycle_minutes: float) -> float:
    """Smooth -1..+1 modulation starting at zero."""
    period = max(1.0, cycle_minutes * 60.0)
    return math.sin((elapsed_seconds % period) / period * math.tau)


def fit_range_for_travel(low: float, high: float, travel: float = .20) -> tuple[float, float]:
    """Preserve width where possible while reserving symmetric travel room."""
    low, high = sorted((max(0.0, low), min(1.0, high)))
    width = min(1.0 - 2 * travel, high - low)
    center = min(1.0 - travel - width / 2,
                 max(travel + width / 2, (low + high) / 2))
    return round(center - width / 2, 4), round(center + width / 2, 4)
