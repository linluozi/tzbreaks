"""Baseline scanner: exhaustive one-minute grid, then second-level refinement.

This is the sound-by-construction reference used for differential evidence and
for the benchmark's baseline arm. It is intentionally slow: it asks the tz
database for the offset at every minute of the requested span.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ONE = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)


def _off(tz, instant):
    return instant.astimezone(tz).utcoffset()


def naive_breaks(zone, start, end):
    """Return (utc_instant, before, after) tuples for every offset change."""
    tz = ZoneInfo(zone)
    out = []
    t = start
    prev = _off(tz, t)
    while t < end:
        nxt = min(t + MINUTE, end)
        cur = _off(tz, nxt)
        if cur != prev:
            lo, hi = t, nxt
            while hi - lo > ONE:
                mid = (lo + (hi - lo) / 2).replace(microsecond=0)
                if mid <= lo:
                    mid = lo + ONE
                if _off(tz, mid) == prev:
                    lo = mid
                else:
                    hi = mid
            out.append((hi, prev, cur))
            t, prev = hi, cur
        else:
            t, prev = nxt, cur
    return out
