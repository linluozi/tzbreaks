"""tzbreaks - enumerate and classify time-zone discontinuities, with no silent guessing."""

from .core import (
    AmbiguousLocalTime,
    Break,
    NonexistentLocalTime,
    Planned,
    PlanResult,
    WallTime,
    breaks,
    classify,
    plan,
)

__all__ = [
    "AmbiguousLocalTime",
    "Break",
    "NonexistentLocalTime",
    "Planned",
    "PlanResult",
    "WallTime",
    "breaks",
    "classify",
    "plan",
]
__version__ = "1.0.0"
