"""SCD Relentless→Extract→overload composite for mcb_extract / mcb_extract_4p.

Vendored from funscript-tools for Vector live expand. Hub–spoke motion is baked
at expand time (optional seed). Vector uses primary alpha/beta only (no -2 /
prostate position axes).
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Default SCD span: Relentless start → end of pleasure overload (Ch2).
EXTRACT_BASE_MS = 265367
EXTRACT_MIN_MS = 60_000

# Soft glide ≈ SCD thetaLag 0.9s (capped per dwell).
_SOFT_LAG_MS = 900

# poles @ ρ=1 → α/β with α=(x+1)/2, β=(y+1)/2 from ρ·(cos θ, sin θ)
_ZETA = 2.0 * math.pi / 3.0
_POLE_THETA = {
    "N": 0.0,
    "L": _ZETA,
    "R": 2.0 * _ZETA,
}

# Carrier amp ladder (fraction start → amp level); last runs to 1.0
_AMP_CUES: Tuple[Tuple[float, int], ...] = (
    (0.0, 1),
    (0.073, 2),
    (0.176, 3),
    (0.472, 2),
    (0.530, 1),
    (0.588, 2),
    (0.618, 3),
    (0.647, 2),
    (0.706, 3),
    (0.824, 4),
)

# Pulse Hz segments (start_frac, end_frac, hz)
_PULSE_SEGS: Tuple[Tuple[float, float, int], ...] = (
    (0.0, 0.353, 60),
    (0.353, 0.588, 40),
    (0.588, 0.647, 50),
    (0.647, 0.882, 60),
    (0.882, 1.0, 120),
)

# ~dur (seconds) by fraction start
_DUR_CUES: Tuple[Tuple[float, float], ...] = (
    (0.0, 1.0),
    (0.176, 0.8),
    (0.353, 0.75),
    (0.706, 0.5),
    (0.882, 1.0),
)

_ALPHA_AXES = "alpha"
_BETA_AXES = "beta"
_E_AXES = ("e1", "e2", "e3", "e4")


def _ms(duration_ms: float, frac: float) -> int:
    return int(round(duration_ms * frac))


def _dwell_s_at(frac: float) -> float:
    dwell = _DUR_CUES[0][1]
    for start, value in _DUR_CUES:
        if frac + 1e-12 >= start:
            dwell = value
    return dwell


def _pole_ab(pole: str, rho: float) -> Tuple[float, float]:
    th = _POLE_THETA[pole]
    x = rho * math.cos(th)
    y = rho * math.sin(th)
    return 0.5 + 0.5 * x, 0.5 + 0.5 * y


def _soft_ms(dwell_ms: int) -> int:
    if dwell_ms <= 1:
        return 0
    return max(1, min(_SOFT_LAG_MS, dwell_ms // 2))


def _linear_step(
    axis: str,
    start_offset: int,
    duration_ms: int,
    start_value: float,
    end_value: float,
    *,
    ramp_in_ms: int = 0,
    ramp_out_ms: int = 0,
    mode: str = "overwrite",
) -> Dict[str, Any]:
    return {
        "operation": "apply_linear_change",
        "axis": axis,
        "start_offset": start_offset,
        "params": {
            "start_value": start_value,
            "end_value": end_value,
            "duration_ms": max(1, int(duration_ms)),
            "ramp_in_ms": int(ramp_in_ms),
            "ramp_out_ms": int(ramp_out_ms),
            "mode": mode,
        },
    }


def derive_extract_params(final_params: dict, event_name: str) -> None:
    """Fill proportional pulse/amp segment tokens; enforce min duration."""
    if event_name not in ("mcb_extract", "mcb_extract_4p"):
        return

    try:
        duration_ms = float(final_params.get("duration_ms", 0))
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"Event '{event_name}': invalid duration_ms: {e}"
        ) from e

    if duration_ms < EXTRACT_MIN_MS:
        raise ValueError(
            f"Event '{event_name}': duration_ms ({duration_ms:g}) must be at least "
            f"{EXTRACT_MIN_MS} ms."
        )

    final_params["pulse_width"] = float(final_params.get("pulse_width", 65))
    final_params["ramp_ms"] = int(final_params.get("ramp_ms", 500))

    # Pulse segments → offset_pulse_i / seg_pulse_i / hz_pulse_i
    for i, (f0, f1, hz) in enumerate(_PULSE_SEGS):
        final_params[f"offset_pulse_{i}_ms"] = _ms(duration_ms, f0)
        final_params[f"seg_pulse_{i}_ms"] = max(1, _ms(duration_ms, f1) - _ms(duration_ms, f0))
        final_params[f"hz_pulse_{i}"] = float(hz)

    # Amp segments
    for i, (f0, level) in enumerate(_AMP_CUES):
        f1 = _AMP_CUES[i + 1][0] if i + 1 < len(_AMP_CUES) else 1.0
        final_params[f"offset_amp_{i}_ms"] = _ms(duration_ms, f0)
        final_params[f"seg_amp_{i}_ms"] = max(1, _ms(duration_ms, f1) - _ms(duration_ms, f0))
        final_params[f"amp_{i}"] = level / 100.0


def build_extract_motion_steps(
    final_params: dict,
    event_name: str,
    *,
    rng: Optional[random.Random] = None,
) -> List[Dict[str, Any]]:
    """Bake hub–spoke overwrite steps for the full duration (Apply/expand-time RNG)."""
    if event_name not in ("mcb_extract", "mcb_extract_4p"):
        return []

    duration_ms = int(round(float(final_params["duration_ms"])))
    if rng is None:
        seed = final_params.get("seed", None)
        rng = random.Random(seed) if seed is not None else random.Random()

    if event_name == "mcb_extract":
        return _build_3p_motion(duration_ms, rng, final_params)
    return _build_4p_motion(duration_ms, rng, final_params)


def _build_3p_motion(
    duration_ms: int,
    rng: random.Random,
    final_params: dict,
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    t = 0
    prev_a, prev_b = _pole_ab("N", 0.85)
    first = True
    ramp_ms = int(final_params.get("ramp_ms", 500))

    while t < duration_ms:
        frac = t / duration_ms
        dwell_ms = max(1, int(round(_dwell_s_at(frac) * 1000.0)))
        # Hub then spoke (SCD Pseq hub→spoke, 2 dwells)
        spoke = rng.choice(("L", "R"))
        for pole in ("N", spoke):
            if t >= duration_ms:
                break
            remaining = duration_ms - t
            this_dwell = min(dwell_ms, remaining)
            rho = rng.uniform(0.75, 0.95)
            a, b = _pole_ab(pole, rho)
            soft = 0 if first else _soft_ms(this_dwell)
            first = False

            if soft > 0:
                steps.append(_linear_step(_ALPHA_AXES, t, soft, prev_a, a))
                steps.append(_linear_step(_BETA_AXES, t, soft, prev_b, b))
                hold_off = t + soft
                hold_dur = this_dwell - soft
            else:
                hold_off = t
                hold_dur = this_dwell
                # First segment: blend in from base stroke
                steps.append(
                    _linear_step(
                        _ALPHA_AXES, hold_off, hold_dur, a, a,
                        ramp_in_ms=min(ramp_ms, hold_dur),
                        ramp_out_ms=0,
                    )
                )
                steps.append(
                    _linear_step(
                        _BETA_AXES, hold_off, hold_dur, b, b,
                        ramp_in_ms=min(ramp_ms, hold_dur),
                        ramp_out_ms=0,
                    )
                )
                prev_a, prev_b = a, b
                t += this_dwell
                continue

            if hold_dur > 0:
                steps.append(_linear_step(_ALPHA_AXES, hold_off, hold_dur, a, a))
                steps.append(_linear_step(_BETA_AXES, hold_off, hold_dur, b, b))

            prev_a, prev_b = a, b
            t += this_dwell

        # Refresh dwell after each 2-note burst using time at burst end
        # (already advanced inside loop)

    # Final ramp-out toward center on last soft edge of event
    if steps:
        out = min(int(final_params.get("ramp_ms", 500)), 1500)
        end_t = max(0, duration_ms - out)
        steps.append(_linear_step(_ALPHA_AXES, end_t, out, prev_a, 0.5, ramp_out_ms=0))
        steps.append(_linear_step(_BETA_AXES, end_t, out, prev_b, 0.5, ramp_out_ms=0))

    return steps


def _e_levels(active: str, rho: float) -> Dict[str, float]:
    levels = {ax: 0.0 for ax in _E_AXES}
    levels[active] = float(rho)
    return levels


def _build_4p_motion(
    duration_ms: int,
    rng: random.Random,
    final_params: dict,
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    t = 0
    prev = _e_levels("e1", 0.85)
    first = True
    ramp_ms = int(final_params.get("ramp_ms", 500))
    spokes: Sequence[str] = ("e2", "e3", "e4")

    while t < duration_ms:
        frac = t / duration_ms
        dwell_ms = max(1, int(round(_dwell_s_at(frac) * 1000.0)))
        spoke = rng.choice(tuple(spokes))
        for pad in ("e1", spoke):
            if t >= duration_ms:
                break
            remaining = duration_ms - t
            this_dwell = min(dwell_ms, remaining)
            rho = rng.uniform(0.75, 0.95)
            curr = _e_levels(pad, rho)
            soft = 0 if first else _soft_ms(this_dwell)
            first = False

            if soft > 0:
                for ax in _E_AXES:
                    steps.append(_linear_step(ax, t, soft, prev[ax], curr[ax]))
                hold_off = t + soft
                hold_dur = this_dwell - soft
            else:
                hold_off = t
                hold_dur = this_dwell
                for ax in _E_AXES:
                    steps.append(
                        _linear_step(
                            ax, hold_off, hold_dur, curr[ax], curr[ax],
                            ramp_in_ms=min(ramp_ms, hold_dur) if ax == "e1" else 0,
                        )
                    )
                prev = curr
                t += this_dwell
                continue

            if hold_dur > 0:
                for ax in _E_AXES:
                    steps.append(_linear_step(ax, hold_off, hold_dur, curr[ax], curr[ax]))

            prev = curr
            t += this_dwell

    if steps:
        out = min(int(final_params.get("ramp_ms", 500)), 1500)
        end_t = max(0, duration_ms - out)
        for ax in _E_AXES:
            steps.append(_linear_step(ax, end_t, out, prev[ax], 0.0))

    return steps
