#!/usr/bin/env python3
"""Benchmark the delivered scanner against the exhaustive minute-grid reference.

    python benchmark/bench.py --impl optimized --json-out benchmark/optimized.json
    python benchmark/bench.py --impl baseline  --json-out benchmark/baseline.json

Both arms return the same breaks; the only difference is the sampling grid
(6 h + bisection vs 1 min + bisection). Raw samples are written per run.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "benchmark"))

from naive_reference import naive_breaks  # noqa: E402
from tzbreaks import breaks as fast_breaks  # noqa: E402

UTC = timezone.utc
SEED = 20241113
STEP = timedelta(hours=6)
WARMUP = 3
REPEAT = 5

ZONES = ["America/New_York", "Europe/Berlin", "Australia/Lord_Howe", "Asia/Kathmandu",
         "Pacific/Apia", "Pacific/Chatham", "America/Santiago", "Europe/Dublin"]

SCALES = {
    "small":  {"zones": ZONES[:1], "start": datetime(2024, 3, 1, tzinfo=UTC),
               "end": datetime(2024, 8, 28, tzinfo=UTC)},
    "medium": {"zones": ZONES[:2], "start": datetime(2024, 3, 1, tzinfo=UTC),
               "end": datetime(2024, 8, 28, tzinfo=UTC)},
    "large":  {"zones": ZONES[:4], "start": datetime(2024, 3, 1, tzinfo=UTC),
               "end": datetime(2024, 8, 28, tzinfo=UTC)},
}


def scans(impl, scale):
    cfg = SCALES[scale]
    out = []
    for zone in cfg["zones"]:
        if impl == "baseline":
            out.append(naive_breaks(zone, cfg["start"], cfg["end"]))
        else:
            out.append([(b.utc, b.before, b.after)
                        for b in fast_breaks(zone, cfg["start"], cfg["end"], step=STEP)])
    return out


def env():
    try:
        cpu = subprocess.run(["lscpu"], capture_output=True, text=True).stdout
        model = next((l.split(":", 1)[1].strip() for l in cpu.splitlines()
                      if l.startswith("Model name")), "unknown")
        threads = next((l.split(":", 1)[1].strip() for l in cpu.splitlines()
                        if l.startswith("CPU(s):")), "unknown")
    except Exception:
        model, threads = "unknown", "unknown"
    mem = "unknown"
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                mem = line.split(":", 1)[1].strip()
    except Exception:
        pass
    return {"cpu_model": model, "cpu_threads": threads, "mem_total": mem,
            "python": sys.version.split()[0], "platform": platform.platform(),
            "seed": SEED, "timer": "time.perf_counter", "grid": STEP.total_seconds()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["baseline", "optimized", "both"], default="both")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--raw-dir", default=str(ROOT / "benchmark" / "raw"))
    ap.add_argument("--scales", default="small,medium,large")
    ap.add_argument("--repeat", type=int, default=REPEAT)
    ap.add_argument("--warmup", type=int, default=WARMUP)
    args = ap.parse_args()
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    random.seed(SEED)
    impls = ["baseline", "optimized"] if args.impl == "both" else [args.impl]
    summary = {"env": env(), "step_seconds": int(STEP.total_seconds()),
               "warmup": args.warmup, "repeat": args.repeat,
               "command": "python benchmark/bench.py --impl %s" % args.impl,
               "scales": {}}

    for scale in args.scales.split(","):
        cfg = SCALES[scale]
        ref = scans("baseline", scale)
        fast = scans("optimized", scale)
        equivalent = ref == fast
        nbreaks = sum(len(x) for x in ref)
        entry = {"zones": len(cfg["zones"]), "days": (cfg["end"] - cfg["start"]).days,
                 "breaks": nbreaks, "equivalent_to_baseline": equivalent,
                 "minutes_scanned": int((cfg["end"] - cfg["start"]).total_seconds() // 60)
                                     * len(cfg["zones"]), "arms": {}}
        for impl in impls:
            fn = naive_breaks if impl == "baseline" else (
                lambda z, s, e: [(b.utc, b.before, b.after)
                                 for b in fast_breaks(z, s, e, step=STEP)])
            times = []
            for i in range(args.warmup):
                for z in cfg["zones"]:
                    fn(z, cfg["start"], cfg["end"])
            for i in range(args.repeat):
                t0 = time.perf_counter()
                out = [fn(z, cfg["start"], cfg["end"]) for z in cfg["zones"]]
                dt = time.perf_counter() - t0
                times.append(dt)
                assert out == ref, f"{impl} disagrees with reference on {scale}"
                (raw_dir / f"{impl}-{scale}-{i}.json").write_text(json.dumps({
                    "impl": impl, "scale": scale, "sample": i, "seconds": dt,
                    "zones": cfg["zones"], "start": cfg["start"].isoformat(),
                    "end": cfg["end"].isoformat(), "breaks": sum(len(o) for o in out),
                    "python": sys.version.split()[0], "seed": SEED, "step_seconds": 21600,
                }, indent=2) + "\n")
            q = statistics.quantiles(times, n=4) if len(times) > 1 else [times[0]] * 3
            med = statistics.median(times)
            entry["arms"][impl] = {
                "median_s": med, "p25_s": q[0], "p75_s": q[2], "min_s": min(times),
                "max_s": max(times),
                "rsd_pct": 100.0 * statistics.pstdev(times) / med if med else 0.0,
                "samples_s": times,
            }
        if len(impls) == 2 and entry["arms"]["baseline"]["median_s"] > 0:
            entry["speedup_median"] = entry["arms"]["baseline"]["median_s"] / \
                                      entry["arms"]["optimized"]["median_s"]
        summary["scales"][scale] = entry
        print(f"{scale}: zones={entry['zones']} days={entry['days']} "
              f"breaks={nbreaks} equivalent={equivalent} " +
              " ".join(f"{k}={v['median_s']:.4f}s(rsd {v['rsd_pct']:.1f}%)"
                       for k, v in entry["arms"].items()))

    # peak memory on the largest scale, both arms, one run each
    big = SCALES["large"]
    peaks = {}
    for impl in impls:
        fn = naive_breaks if impl == "baseline" else (
            lambda z, s, e: [(b.utc, b.before, b.after)
                             for b in fast_breaks(z, s, e, step=STEP)])
        tracemalloc.start()
        for z in big["zones"]:
            fn(z, big["start"], big["end"])
        peaks[impl] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    summary["peak_traced_bytes_large"] = peaks
    if "baseline" in peaks and "optimized" in peaks and peaks["optimized"]:
        summary["peak_ratio"] = peaks["baseline"] / peaks["optimized"]
    summary["pytest"] = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"], cwd=ROOT,
        capture_output=True, text=True).stdout.strip().splitlines()[-1:] or [""]
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, indent=2) + "\n")
        print("wrote", args.json_out)
    return 0 if all(e["equivalent_to_baseline"] for e in summary["scales"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
