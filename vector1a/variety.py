from __future__ import annotations

import math


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
