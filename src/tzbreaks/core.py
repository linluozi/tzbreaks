"""Enumerate and classify UTC-offset discontinuities in IANA time zones.

The public surface is deliberately small:

    breaks(zone, start, end, step)   -> list[Break]      (gap / overlap intervals)
    classify(zone, naive_local)      -> WallTime         (unambiguous / ambiguous / nonexistent)
    plan(zone, naive_times, policy)  -> PlanResult       (scheduler-safe resolution)

Every ``Break`` carries the UTC instant of the transition, the offsets on both
sides, and the wall-clock interval that is skipped (gap) or repeated (overlap).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

UTC = timezone.utc
_ONE_SECOND = timedelta(seconds=1)


@dataclass(frozen=True, slots=True)
class Break:
    """One UTC-offset discontinuity.

    ``wall_lo``/``wall_hi`` delimit the affected *naive local* wall interval:
    ``[wall_lo, wall_hi)`` does not exist for a gap and occurs twice for an overlap.
    """

    zone: str
    kind: str                 # "gap" | "overlap"
    utc: datetime             # first UTC instant carrying the new offset
    before: timedelta
    after: timedelta
    wall_lo: datetime         # naive
    wall_hi: datetime         # naive

    @property
    def delta(self) -> timedelta:
        return self.after - self.before

    @property
    def wall_minutes(self) -> float:
        """Width of the affected wall interval, always positive."""
        return (self.wall_hi - self.wall_lo).total_seconds() / 60.0

    def to_dict(self) -> dict:
        return {
            "zone": self.zone,
            "kind": self.kind,
            "utc": self.utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "before_seconds": int(self.before.total_seconds()),
            "after_seconds": int(self.after.total_seconds()),
            "wall_lo": self.wall_lo.isoformat(sep=" "),
            "wall_hi": self.wall_hi.isoformat(sep=" "),
            "wall_minutes": self.wall_minutes,
        }


def _offset(tz: ZoneInfo, instant: datetime) -> timedelta:
    return instant.astimezone(tz).utcoffset()


def _naive(instant: datetime, tz: ZoneInfo) -> datetime:
    return instant.astimezone(tz).replace(tzinfo=None)


def _check_range(start: datetime, end: datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware UTC datetimes")
    if end < start:
        raise ValueError("end must not be earlier than start")
    for value in (start, end):
        if value.utcoffset() != timedelta(0):
            raise ValueError("start and end must be UTC (offset 0)")


def _bisect_second(tz: ZoneInfo, lo: datetime, hi: datetime) -> datetime:
    """First instant in (lo, hi] whose offset differs from offset(lo).

    Requires offset(lo) != offset(hi). Returns a whole-second instant.
    """
    base = _offset(tz, lo)
    while hi - lo > _ONE_SECOND:
        mid = lo + (hi - lo) / 2
        mid = mid.replace(microsecond=0)
        if mid <= lo:
            mid = lo + _ONE_SECOND
        if _offset(tz, mid) == base:
            lo = mid
        else:
            hi = mid
    return hi


def breaks(
    zone: str,
    start: datetime,
    end: datetime,
    step: timedelta = timedelta(hours=6),
) -> list[Break]:
    """Return all offset discontinuities in ``[start, end]``.

    The scan samples the offset on a coarse grid and refines every detected
    change by second-precision bisection. ``step`` bounds the grid: a pair of
    transitions that both fall inside one grid cell *and* restore the original
    offset would be invisible to it, so ``step`` is a coverage parameter, not a
    formality. See docs/algorithm.md and the equivalence evidence in README.
    """
    _check_range(start, end)
    if step <= timedelta(0):
        raise ValueError("step must be positive")
    if step > timedelta(days=30):
        raise ValueError("step must be at most 30 days")
    tz = ZoneInfo(zone)
    out: list[Break] = []
    cursor = start
    prev_off = _offset(tz, cursor)
    guard = 0
    while cursor < end:
        guard += 1
        if guard > 5_000_000:
            raise RuntimeError("scan guard tripped; step too small for the range")
        nxt = min(cursor + step, end)
        off = _offset(tz, nxt)
        if off == prev_off:
            cursor, prev_off = nxt, off
            continue
        instant = _bisect_second(tz, cursor, nxt)
        new_off = _offset(tz, instant)
        if new_off > prev_off:
            kind = "gap"
            wall_lo = (instant + prev_off).replace(tzinfo=None)
            wall_hi = (instant + new_off).replace(tzinfo=None)
        else:
            kind = "overlap"
            wall_lo = (instant + new_off).replace(tzinfo=None)
            wall_hi = (instant + prev_off).replace(tzinfo=None)
        out.append(Break(zone, kind, instant, prev_off, new_off, wall_lo, wall_hi))
        cursor, prev_off = instant, new_off
    return out


@dataclass(frozen=True, slots=True)
class WallTime:
    """Classification of one naive local wall time in one zone."""

    zone: str
    local: datetime
    kind: str                       # "unambiguous" | "ambiguous" | "nonexistent"
    instants: tuple[datetime, ...]  # UTC instants that render this wall time
    offsets: tuple[timedelta, ...]
    break_: Break | None = None     # the discontinuity responsible, when inside one

    def to_dict(self) -> dict:
        return {
            "zone": self.zone,
            "local": self.local.isoformat(sep=" "),
            "kind": self.kind,
            "instants": [i.strftime("%Y-%m-%dT%H:%M:%SZ") for i in self.instants],
            "offsets_seconds": [int(o.total_seconds()) for o in self.offsets],
            "break": self.break_.to_dict() if self.break_ else None,
        }


def _roundtrips(tz: ZoneInfo, instant: datetime, naive: datetime) -> bool:
    return _naive(instant, tz) == naive


def _nearby_break(tz: ZoneInfo, zone: str, naive: datetime) -> Break | None:
    """Locate the discontinuity whose wall interval contains ``naive``."""
    probe = naive.replace(tzinfo=UTC)  # arbitrary anchor; window is wide
    window = timedelta(hours=30)
    for b in breaks(zone, probe - window, probe + window, step=timedelta(hours=1)):
        if b.wall_lo <= naive < b.wall_hi:
            return b
    return None


def classify(zone: str, local: datetime) -> WallTime:
    """Decide whether a naive local time exists once, twice, or not at all."""
    if local.tzinfo is not None:
        raise ValueError("local must be a naive datetime")
    tz = ZoneInfo(zone)
    try:
        f0 = local.replace(tzinfo=tz, fold=0)
        f1 = local.replace(tzinfo=tz, fold=1)
        u0, u1 = f0.astimezone(UTC), f1.astimezone(UTC)
        o0, o1 = f0.utcoffset(), f1.utcoffset()
    except (OverflowError, ValueError) as exc:  # pragma: no cover - year bounds
        raise ValueError(f"cannot resolve {local} in {zone}: {exc}") from exc

    valid = [i for i, u in ((0, u0), (1, u1)) if _roundtrips(tz, u, local)]
    if len(valid) == 2 and u0 != u1:
        return WallTime(zone, local, "ambiguous", (u0, u1), (o0, o1),
                        _nearby_break(tz, zone, local))
    if not valid:
        return WallTime(zone, local, "nonexistent", (u0, u1), (o0, o1),
                        _nearby_break(tz, zone, local))
    if len(valid) == 1:
        i = valid[0]
        u, o = (u0, o0) if i == 0 else (u1, o1)
        return WallTime(zone, local, "unambiguous", (u,), (o,), None)
    return WallTime(zone, local, "unambiguous", (u0,), (o0,), None)


@dataclass(frozen=True, slots=True)
class Planned:
    local: datetime
    kind: str                 # as classify(), plus "skipped"
    utc: datetime | None
    offset: timedelta | None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "local": self.local.isoformat(sep=" "),
            "kind": self.kind,
            "utc": self.utc.strftime("%Y-%m-%dT%H:%M:%SZ") if self.utc else None,
            "offset_seconds": int(self.offset.total_seconds()) if self.offset else None,
            "note": self.note,
        }


@dataclass
class PlanResult:
    zone: str
    policy: str
    rows: list[Planned] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not [r for r in self.rows if r.kind == "skipped"]


POLICIES = ("strict", "shift_forward", "skip", "earlier", "later")


def plan(zone: str, local_times: Iterable[datetime], policy: str = "shift_forward") -> PlanResult:
    """Resolve naive local times to UTC under an explicit ambiguity policy.

    policy:
      * ``strict``        - raise ``AmbiguousLocalTime``/``NonexistentLocalTime``
      * ``shift_forward`` - nonexistent times move to the first existing instant
      * ``skip``          - nonexistent times are reported as skipped (no UTC)
      * ``earlier``       - ambiguous times resolve to the first occurrence
      * ``later``         - ambiguous times resolve to the second occurrence
    """
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}")
    result = PlanResult(zone, policy)
    for local in local_times:
        w = classify(zone, local)
        if w.kind == "unambiguous":
            result.rows.append(Planned(local, "unambiguous", w.instants[0], w.offsets[0]))
            continue
        if w.kind == "ambiguous":
            if policy in ("earlier", "shift_forward", "skip"):
                result.rows.append(Planned(local, "ambiguous", w.instants[0], w.offsets[0],
                                            "earlier occurrence (fold=0)"))
            elif policy == "later":
                result.rows.append(Planned(local, "ambiguous", w.instants[1], w.offsets[1],
                                            "later occurrence (fold=1)"))
            else:
                raise AmbiguousLocalTime(zone, local, w.instants)
            result.warnings.append(
                f"{zone} {local} occurs twice; resolved per policy '{policy}'")
            continue
        # nonexistent
        if policy == "strict":
            raise NonexistentLocalTime(zone, local, w.break_)
        if policy == "skip":
            result.rows.append(Planned(local, "skipped", None, None,
                                       "wall time does not exist in this zone"))
            result.warnings.append(f"{zone} {local} does not exist; skipped")
            continue
        b = w.break_
        if b is None:  # pragma: no cover - defensive
            raise NonexistentLocalTime(zone, local, None)
        first_valid = b.wall_hi  # first wall time after the gap
        instant = (first_valid - b.after).replace(tzinfo=UTC)
        shift = first_valid - local
        result.rows.append(Planned(local, "nonexistent", instant, b.after,
                                   f"shifted forward by {shift}"))
        result.warnings.append(f"{zone} {local} does not exist; shifted forward by {shift}")
    return result


class AmbiguousLocalTime(ValueError):
    def __init__(self, zone: str, local: datetime, instants: Sequence[datetime]):
        super().__init__(f"{local} is ambiguous in {zone}: {[i.isoformat() for i in instants]}")
        self.zone, self.local, self.instants = zone, local, tuple(instants)


class NonexistentLocalTime(ValueError):
    def __init__(self, zone: str, local: datetime, brk: Break | None):
        detail = f"{brk.wall_lo}..{brk.wall_hi}" if brk else "unknown gap"
        super().__init__(f"{local} does not exist in {zone} (gap {detail})")
        self.zone, self.local, self.brk = zone, local, brk
