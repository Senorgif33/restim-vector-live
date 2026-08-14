from __future__ import annotations

import math

ELECTRODE_ORDERS = ("ABCD", "ABDC", "BACD", "ACBD")


def pair_swapped_order(order: str) -> str:
    """Return the discrete destination of a full A/B + C/D pair morph."""
    if order not in ELECTRODE_ORDERS:
        order = "ABCD"
    return order.translate(str.maketrans("ABCD", "BADC"))


def map_electrode_order(values: tuple[float, float, float, float], order: str
                        ) -> tuple[float, float, float, float]:
    """Map logical A-B-C-D path positions onto a physical electrode order."""
    if order not in ELECTRODE_ORDERS:
        order = "ABCD"
    mapped = [0.0] * 4
    for logical_index, physical_label in enumerate(order):
        mapped[ord(physical_label) - ord("A")] = values[logical_index]
    return tuple(mapped)


def pair_morph(values: tuple[float, float, float, float], amount: float
               ) -> tuple[float, float, float, float]:
    """Smoothly morph A<->B and C<->D; endpoints are exact permutations."""
    amount = min(1.0, max(0.0, amount))
    a, b, c, d = values
    return (a + (b - a) * amount, b + (a - b) * amount,
            c + (d - c) * amount, d + (c - d) * amount)


def vertical_crossfade(position: float) -> tuple[float, float, float, float]:
    """Visual-only A-to-D equal-power adjacent crossfade for a 0..1 position."""
    position = min(1.0, max(0.0, position))
    scaled = position * 3.0
    section = min(2, int(scaled))
    progress = scaled - section
    weights = [0.0, 0.0, 0.0, 0.0]
    weights[section] = math.cos(progress * math.pi / 2.0)
    weights[section + 1] = math.sin(progress * math.pi / 2.0)
    return tuple(weights)


def directed_signed(position: float, direction: int = 1, return_depth: float = .30
                    ) -> tuple[tuple[float, float, float, float], int, int]:
    """Smooth adjacent-pair handover along A-B-C-D.

    In each section the first half reverses the adjacent source/return pair.
    The second half holds the destination primary while moving its return from
    the previous electrode to the next.  This avoids non-adjacent A->C roles.
    """
    position = min(1.0, max(0.0, position))
    return_depth = min(1.0, max(0.0, return_depth))
    scaled = position * 3.0

    def state(primary: int, preferred_return: int) -> tuple[float, ...]:
        values = [0.0, 0.0, 0.0, 0.0]
        values[primary] = 1.0
        values[preferred_return] = -return_depth
        return tuple(values)

    if direction >= 0:
        source = min(2, int(scaled))
        destination = source + 1
        progress = scaled - source
        incoming = source - 1 if source > 0 else destination
        outgoing = destination + 1 if destination < 3 else source
    else:
        source = max(1, min(3, math.ceil(scaled)))
        destination = source - 1
        progress = source - scaled
        incoming = source + 1 if source < 3 else destination
        outgoing = destination - 1 if destination > 0 else source

    if progress <= .5:
        start = state(source, destination)
        end = state(destination, source)
        local = progress * 2.0
        role_primary, role_return = source, destination
    else:
        start = state(destination, incoming)
        end = state(destination, outgoing)
        local = (progress - .5) * 2.0
        role_primary, role_return = destination, outgoing
    blend = (1.0 - math.cos(min(1.0, max(0.0, local)) * math.pi)) / 2.0
    values = tuple(a + (b - a) * blend for a, b in zip(start, end))
    return values, role_primary, role_return


def normalize_signed(values: tuple[float, float, float, float]
                     ) -> tuple[float, float, float, float]:
    """Map signed relative potentials into ReStim's 0..1 command range."""
    low, high = min(values), max(values)
    span = high - low
    if span <= 1e-12:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple((value - low) / span for value in values)


def _constrain_restim(values: tuple[float, float, float, float]
                      ) -> tuple[float, float, float, float]:
    """Scalar equivalent of ReStim constrain_4p_amplitudes."""
    a, b, c, d = (min(1.0, max(0.0, value)) for value in values)
    s_a = min(-a + b + c + d, 0.0) / -3.0
    s_b = min(a - b + c + d, 0.0) / -3.0
    s_c = min(a + b - c + d, 0.0) / -3.0
    s_d = min(a + b + c - d, 0.0) / -3.0
    result = [a + s_b + s_c + s_d, b + s_a + s_c + s_d,
              c + s_a + s_b + s_d, d + s_a + s_b + s_c]
    maximum = max(result)
    if maximum < 1.0:
        result = [value + 1.0 - maximum for value in result]
        result[result.index(max(result))] = 1.0
    return tuple(min(1.0, max(0.0, value)) for value in result)


def crossover_blend(progress: float, width: float = 1.0,
                    curve: str = "Cosine", sharpness: float = 1.0) -> float:
    """Shape one adjacent-electrode handover while preserving its endpoints."""
    progress = min(1.0, max(0.0, progress))
    width = min(1.0, max(.05, width))
    start = (1.0 - width) / 2.0
    local = min(1.0, max(0.0, (progress - start) / width))
    name = curve.strip().lower()
    if name == "linear":
        blend = local
    elif name == "ease in":
        blend = local * local
    elif name == "ease out":
        blend = 1.0 - (1.0 - local) ** 2
    elif name in ("s-curve", "s curve"):
        blend = local * local * (3.0 - 2.0 * local)
    else:
        blend = (1.0 - math.cos(local * math.pi)) / 2.0
    sharpness = min(5.0, max(.2, sharpness))
    if abs(sharpness - 1.0) > 1e-12 and 0.0 < blend < 1.0:
        high = blend ** sharpness
        low = (1.0 - blend) ** sharpness
        blend = high / (high + low)
    return blend


def restim_crossfade(position: float, direction: int = 1, return_depth: float = .30,
                     crossover_width: float = 1.0, curve: str = "Cosine",
                     sharpness: float = 1.0
                     ) -> tuple[float, float, float, float]:
    """Cross cell boundaries continuously in ReStim's own E1-E4 space."""
    position = min(1.0, max(0.0, position))
    depth = min(1.0, max(0.0, return_depth))
    neutral = depth / (1.0 + depth)

    def node(primary: int, preferred_return: int) -> tuple[float, ...]:
        values = [neutral] * 4
        values[primary], values[preferred_return] = 1.0, 0.0
        return tuple(values)

    scaled = position * 3.0
    if direction >= 0:
        section = min(2, int(scaled)); progress = scaled - section
        start = node(section, section + 1)
        end = node(section + 1, section + 2 if section < 2 else section)
    else:
        section = max(0, min(2, math.ceil(scaled) - 1)); progress = section + 1 - scaled
        start = node(section + 1, section)
        end = node(section, section - 1 if section > 0 else section + 1)
    blend = crossover_blend(progress, crossover_width, curve, sharpness)
    return _constrain_restim(tuple(a + (b - a) * blend for a, b in zip(start, end)))


def potential_roles(values: tuple[float, float, float, float], primary: int | None = None,
                    preferred_return: int | None = None) -> tuple[str, str]:
    labels = "ABCD"
    return (labels[primary if primary is not None else values.index(max(values))],
            labels[preferred_return if preferred_return is not None else values.index(min(values))])
