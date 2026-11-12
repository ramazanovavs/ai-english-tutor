"""
IELTS band arithmetic used everywhere in the IELTS section.

Python's round() rounds .5 to the nearest EVEN number (round(12.5) == 12), which made an
average of 6.25 become 6.0. These helpers implement the IELTS conventions explicitly.
"""

from __future__ import annotations

import math
from typing import Any


def overall_band(values: list[float]) -> float:
    """
    Overall band from the four skill bands (official IELTS rule):
    an average ending in .25 rounds UP to the next half band, .75 rounds UP to the
    next whole band; .125 rounds down to the whole band, .375/.625 go to .5.
    """
    avg = sum(float(v) for v in values) / len(values)
    base = math.floor(avg + 1e-9)
    frac = avg - base
    if frac < 0.25 - 1e-9:
        return float(base)
    if frac < 0.75 - 1e-9:
        return base + 0.5
    return float(base + 1)


def half_band_down(value: float) -> float:
    """Criterion averages (Writing/Speaking) are reported to the half band below: 6.75 -> 6.5."""
    return math.floor(float(value) * 2 + 1e-9) / 2


def half_band_nearest(value: float) -> float:
    """Nearest half band with .25 / .75 rounding up (never banker's rounding)."""
    return math.floor(float(value) * 2 + 0.5 + 1e-9) / 2


def criterion(value: Any) -> int:
    """Writing and Speaking criteria are awarded in whole bands 0-9."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    return int(max(0, min(9, math.floor(v + 0.5))))


def criteria_band(values: list[Any]) -> float:
    return half_band_down(sum(criterion(v) for v in values) / len(values))


def writing_band(task1: float, task2: float) -> float:
    """Task 2 counts twice as much as Task 1."""
    return half_band_nearest((float(task1) + 2 * float(task2)) / 3)
