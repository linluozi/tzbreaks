"""Tests for tzbreaks: known facts, interval semantics, and an independent oracle."""

from __future__ import annotations

import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from tzbreaks import AmbiguousLocalTime, NonexistentLocalTime, breaks, classify, plan

UTC = timezone.utc
ONE = timedelta(seconds=1)


def off(tz, instant):
    return instant.astimezone(tz).utcoffset()


def oracle_breaks(zone, start, end):
    """Independent reference: walk a one-minute grid, then refine to the second."""
    tz = ZoneInfo(zone)
    found = []
    t = start
    prev = off(tz, t)
    while t < end:
        nxt = min(t + timedelta(minutes=1), end)
        cur = off(tz, nxt)
        if cur != prev:
            lo, hi = t, nxt
            while hi - lo > ONE:
                mid = (lo + (hi - lo) / 2).replace(microsecond=0)
                if mid <= lo:
                    mid = lo + ONE
                if off(tz, mid) == prev:
                    lo = mid
                else:
                    hi = mid
            found.append((hi, prev, cur))
            t, prev = hi, cur
        else:
            t, prev = nxt, cur
    return found


# ---------------------------------------------------------------- known facts

def test_berlin_2024_pair():
    got = breaks("Europe/Berlin", datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC))
    assert [b.kind for b in got] == ["gap", "overlap"]
    gap, over = got
    assert gap.utc == datetime(2024, 3, 31, 1, 0, tzinfo=UTC)
    assert (gap.before, gap.after) == (timedelta(hours=1), timedelta(hours=2))
    assert (gap.wall_lo, gap.wall_hi) == (datetime(2024, 3, 31, 2, 0), datetime(2024, 3, 31, 3, 0))
    assert over.utc == datetime(2024, 10, 27, 1, 0, tzinfo=UTC)
    assert (over.wall_lo, over.wall_hi) == (datetime(2024, 10, 27, 2, 0), datetime(2024, 10, 27, 3, 0))
    assert over.wall_minutes == 60.0


def test_half_hour_shift_lord_howe():
    gap, = [b for b in breaks("Australia/Lord_Howe",
                              datetime(2024, 9, 1, tzinfo=UTC),
                              datetime(2024, 11, 1, tzinfo=UTC))]
    assert gap.kind == "gap" and gap.utc == datetime(2024, 10, 5, 15, 30, tzinfo=UTC)
    assert gap.wall_minutes == 30.0
    assert gap.wall_lo == datetime(2024, 10, 6, 2, 0)


def test_quarter_hour_shift_kathmandu_1986():
    gap, = breaks("Asia/Kathmandu", datetime(1985, 12, 1, tzinfo=UTC),
                  datetime(1986, 2, 1, tzinfo=UTC))
    assert gap.kind == "gap" and gap.wall_minutes == 15.0
    assert (gap.before, gap.after) == (timedelta(hours=5, minutes=30),
                                       timedelta(hours=5, minutes=45))


def test_samoa_skipped_day():
    gap, = breaks("Pacific/Apia", datetime(2011, 12, 1, tzinfo=UTC),
                  datetime(2012, 1, 1, tzinfo=UTC))
    assert gap.kind == "gap" and gap.wall_minutes == 1440.0
    assert gap.wall_lo == datetime(2011, 12, 30, 0, 0)
    assert gap.wall_hi == datetime(2011, 12, 31, 0, 0)
    w = classify("Pacific/Apia", datetime(2011, 12, 30, 12, 0))
    assert w.kind == "nonexistent" and w.break_ == gap


# ------------------------------------------------------------ classification

def test_classify_new_york_folds():
    amb = classify("America/New_York", datetime(2024, 11, 3, 1, 30))
    assert amb.kind == "ambiguous"
    assert amb.instants == (datetime(2024, 11, 3, 5, 30, tzinfo=UTC),
                            datetime(2024, 11, 3, 6, 30, tzinfo=UTC))
    assert amb.offsets == (timedelta(hours=-4), timedelta(hours=-5))
    assert amb.break_.wall_lo == datetime(2024, 11, 3, 1, 0)

    missing = classify("America/New_York", datetime(2024, 3, 10, 2, 30))
    assert missing.kind == "nonexistent"
    assert missing.break_.wall_lo == datetime(2024, 3, 10, 2, 0)

    plain = classify("America/New_York", datetime(2024, 3, 10, 4, 0))
    assert plain.kind == "unambiguous" and plain.instants == (datetime(2024, 3, 10, 8, 0, tzinfo=UTC),)


def test_classify_edges():
    with pytest.raises(ValueError):
        classify("UTC", datetime(2024, 1, 1, tzinfo=UTC))
    w = classify("UTC", datetime(2024, 1, 1, 12, 0))
    assert w.kind == "unambiguous" and w.instants[0] == datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def test_every_break_interval_semantics():
    """Every reported wall interval must be exactly the set of bad wall times."""
    zones = ["America/New_York", "Europe/Berlin", "Australia/Lord_Howe", "Asia/Kathmandu",
             "Pacific/Apia", "Pacific/Chatham", "America/Santiago", "Europe/Dublin"]
    checked = 0
    for zone in zones:
        for b in breaks(zone, datetime(1970, 1, 1, tzinfo=UTC), datetime(2036, 1, 1, tzinfo=UTC)):
            mid = b.wall_lo + (b.wall_hi - b.wall_lo) / 2
            mid = mid.replace(microsecond=0)
            w = classify(zone, mid)
            assert w.kind == ("nonexistent" if b.kind == "gap" else "ambiguous"), (zone, b)
            if b.kind == "gap":
                assert classify(zone, b.wall_lo).kind == "nonexistent"
                assert classify(zone, b.wall_hi).kind == "unambiguous"  # first valid wall time
            else:
                assert classify(zone, b.wall_lo).kind == "ambiguous"
                assert classify(zone, b.wall_hi).kind == "unambiguous"  # first wall time after
            assert classify(zone, b.wall_lo - ONE).kind == "unambiguous"
            checked += 1
    assert checked > 500, checked


def test_transition_precision_one_second():
    for zone in ["Europe/Berlin", "America/Santiago", "Pacific/Chatham"]:
        tz = ZoneInfo(zone)
        for b in breaks(zone, datetime(2000, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC)):
            assert off(tz, b.utc - ONE) == b.before
            assert off(tz, b.utc) == b.after


# --------------------------------------------------- independent oracle diff

def test_oracle_minute_grid_equivalence():
    zones = ["America/New_York", "Europe/Berlin", "Australia/Lord_Howe",
             "Asia/Kathmandu", "Pacific/Apia", "Pacific/Chatham"]
    start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)
    total = 0
    for zone in zones:
        fast = [(b.utc, b.before, b.after) for b in breaks(zone, start, end)]
        slow = oracle_breaks(zone, start, end)
        assert fast == slow, zone
        total += len(fast)
    assert total >= 8


def test_step_insensitivity_over_wide_history():
    """A 6h grid must find the same breaks as a 1h grid across 66 years."""
    zones = ["America/New_York", "Europe/Berlin", "Europe/Lisbon", "Africa/Casablanca",
             "Asia/Tehran", "America/Santiago", "Australia/Lord_Howe", "Pacific/Apia",
             "Pacific/Chatham", "Asia/Kathmandu", "Europe/Moscow", "America/Havana"]
    start, end = datetime(1970, 1, 1, tzinfo=UTC), datetime(2036, 1, 1, tzinfo=UTC)
    for zone in zones:
        coarse = breaks(zone, start, end, step=timedelta(hours=6))
        fine = breaks(zone, start, end, step=timedelta(hours=1))
        assert [(b.utc, b.before, b.after) for b in coarse] == \
               [(b.utc, b.before, b.after) for b in fine], zone


# ------------------------------------------------------------------- planning

def test_plan_policies():
    gap = datetime(2024, 3, 10, 2, 30)
    shifted = plan("America/New_York", [gap], "shift_forward").rows[0]
    assert shifted.utc == datetime(2024, 3, 10, 7, 0, tzinfo=UTC)
    assert shifted.note == "shifted forward by 0:30:00"

    skipped = plan("America/New_York", [gap], "skip")
    assert skipped.rows[0].kind == "skipped" and skipped.rows[0].utc is None
    assert not skipped.ok

    with pytest.raises(NonexistentLocalTime):
        plan("America/New_York", [gap], "strict")

    amb = datetime(2024, 11, 3, 1, 30)
    earlier = plan("America/New_York", [amb], "earlier").rows[0]
    later = plan("America/New_York", [amb], "later").rows[0]
    assert earlier.utc == datetime(2024, 11, 3, 5, 30, tzinfo=UTC)
    assert later.utc == datetime(2024, 11, 3, 6, 30, tzinfo=UTC)
    with pytest.raises(AmbiguousLocalTime):
        plan("America/New_York", [amb], "strict")
    with pytest.raises(ValueError):
        plan("America/New_York", [amb], "nonsense")


def test_plan_mixed_daily_series():
    """A 02:30 daily series loses exactly one wall time per spring-forward."""
    times = [datetime(2024, 3, d, 2, 30) for d in range(8, 13)]
    res = plan("America/New_York", times, "skip")
    assert [r.kind for r in res.rows] == ["unambiguous", "unambiguous", "skipped",
                                          "unambiguous", "unambiguous"]
    assert len(res.warnings) == 1


# ------------------------------------------------------------ input guards

def test_argument_validation():
    with pytest.raises(ValueError):
        breaks("UTC", datetime(2024, 1, 1), datetime(2024, 2, 1))
    with pytest.raises(ValueError):
        breaks("UTC", datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        breaks("UTC", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC),
               step=timedelta(0))
    with pytest.raises(ValueError):
        breaks("UTC", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC),
               step=timedelta(days=40))
    assert breaks("UTC", datetime(2024, 1, 1, tzinfo=UTC),
                  datetime(2024, 1, 1, tzinfo=UTC)) == []


def test_determinism_and_json_shape():
    a = [b.to_dict() for b in breaks("Europe/Berlin", datetime(2024, 1, 1, tzinfo=UTC),
                                     datetime(2024, 12, 31, tzinfo=UTC))]
    b = [b.to_dict() for b in breaks("Europe/Berlin", datetime(2024, 1, 1, tzinfo=UTC),
                                     datetime(2024, 12, 31, tzinfo=UTC))]
    assert a == b and a[0]["kind"] == "gap" and a[0]["utc"].endswith("Z")


# ---------------------------------------------------------------------- CLI

def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "tzbreaks", *args],
                          capture_output=True, text=True, timeout=120)


def test_cli_scan_and_classify():
    r = run_cli("scan", "--zone", "Europe/Berlin", "--start", "2024-01-01T00:00:00Z",
                "--end", "2025-01-01T00:00:00Z", "--json")
    assert r.returncode == 0, r.stderr
    import json
    payload = json.loads(r.stdout)
    assert payload["count"] == 2 and payload["breaks"][0]["kind"] == "gap"

    r = run_cli("classify", "--zone", "America/New_York", "--local", "2024-03-10 02:30")
    assert r.returncode == 3 and "nonexistent" in r.stdout

    r = run_cli("classify", "--zone", "America/New_York", "--local", "2024-03-10 04:00")
    assert r.returncode == 0 and "unambiguous" in r.stdout


def test_cli_audit_and_plan():
    r = run_cli("audit", "--zones", "Europe/Berlin,America/New_York",
                "--start", "2024-01-01T00:00:00Z", "--end", "2025-01-01T00:00:00Z", "--json")
    assert r.returncode == 0, r.stderr
    import json
    totals = json.loads(r.stdout)["totals"]
    assert totals == {"gap": 2, "overlap": 2, "zones": 2, "verified": 4, "mismatch": 0}

    r = run_cli("plan", "--zone", "America/New_York", "--local", "2024-03-10 02:30",
                "--policy", "skip", "--json")
    assert r.returncode == 4 and json.loads(r.stdout)["rows"][0]["utc"] is None

    r = run_cli("scan", "--zone", "UTC", "--start", "nope", "--end", "2025-01-01T00:00:00Z")
    assert r.returncode == 2
