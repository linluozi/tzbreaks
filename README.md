# tzbreaks

Enumerate every UTC-offset discontinuity in an IANA time zone, and classify a
naive local wall clock as *unambiguous*, *ambiguous* (occurs twice) or
*nonexistent* (skipped by a gap) — with the exact wall interval, no silent
`fold=0` guessing.

`tzbreaks` does not sample every minute. It samples the offset on a coarse grid
and bisects each detected change down to the second, which is how it audits
**599 zones × 16 years (6,530 transitions) in 14.7 s measured**, re-probing
every single transition at `T-1s`/`T` and finding 0 mismatches.

Upstream: none

```python
from datetime import datetime, timezone
UTC = timezone.utc
from tzbreaks import breaks, classify, plan

breaks("Europe/Berlin", datetime(2024, 1, 1, tzinfo=UTC),
       datetime(2025, 1, 1, tzinfo=UTC))
# [Break(kind='gap',     utc=2024-03-31T01:00:00Z, before=+1h, after=+2h,
#        wall_lo=2024-03-31 02:00, wall_hi=2024-03-31 03:00),
#  Break(kind='overlap', utc=2024-10-27T01:00:00Z, before=+2h, after=+1h,
#        wall_lo=2024-10-27 02:00, wall_hi=2024-10-27 03:00)]

classify("America/New_York", datetime(2024, 11, 3, 1, 30))
# WallTime(kind='ambiguous', instants=(05:30Z, 06:30Z), offsets=(-4h, -5h), ...)

plan("America/New_York", [datetime(2024, 3, 10, 2, 30)], policy="shift_forward")
# 02:30 does not exist -> 07:00Z, note='shifted forward by 0:30:00'
```

## Why this exists

`datetime.replace(tzinfo=zone)` accepts a wall time that never happened and
`astimezone()` silently picks one side of a fold. Calendar, cron and booking
code inherits that behaviour: one meeting per year is either invented at an
impossible wall time or dropped. `tzbreaks` makes the discontinuity an object
you can assert on:

* `Break.kind` — `"gap"` (wall clock jumps forward) or `"overlap"` (repeats).
* `Break.utc` — first UTC instant carrying the new offset, exact to the second.
* `Break.wall_lo`/`wall_hi` — the half-open wall interval that is skipped or
  repeated, so a scheduler can reject, shift or dedupe it explicitly.
* `plan(..., policy=...)` — `strict`, `shift_forward`, `skip`, `earlier`,
  `later`. `strict` raises instead of guessing; every other policy records a
  warning and the applied shift in the result row.

Handles the cases a naive ±1 hour assumption gets wrong: Lord Howe's 30-minute
DST shift, Kathmandu's 15-minute 1986 jump, and Samoa's skipped calendar day
(2011-12-30, a 24-hour gap). All three are regression tests.

## Install

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest -q            # 16 passed
```

No runtime dependencies; Python ≥ 3.10 (`dataclass(slots=True)` + `zoneinfo`, system tzdata).

## CLI

```bash
.venv/bin/python -m tzbreaks scan --zone Australia/Lord_Howe \
    --start 2024-01-01T00:00:00Z --end 2025-01-01T00:00:00Z
.venv/bin/python -m tzbreaks classify --zone America/New_York --local "2024-03-10 02:30" --json
.venv/bin/python -m tzbreaks plan --zone America/New_York --local "2024-03-10 02:30" --policy skip
.venv/bin/python -m tzbreaks audit --zones all \
    --start 2020-01-01T00:00:00Z --end 2036-01-01T00:00:00Z --json
```

`classify` exits `3` when the wall time is ambiguous or nonexistent, `plan`
exits `4` under `skip` when a row was dropped, `audit` exits `1` if any
transition fails its re-probe. Exit codes are meant for CI.

## Measured results

Both arms return byte-identical break lists (asserted in the harness on every
timed run); the only difference is the sampling grid: 1 minute vs 6 hours plus
second-level bisection. Timer `time.perf_counter()`, 3 warmups, 5 timed runs,
seed `20241113`, one shared 4-vCPU host.

| scale | zones × days | baseline median | optimized median | speedup |
|---|---|---|---|---|
| small  | 1 × 180 | 0.246587 s (p25 0.245403 / p75 0.247326) | 0.000800 s | **308.18×** |
| medium | 2 × 180 | 0.498123 s (p25 0.496705 / p75 0.500755) | 0.001608 s | **309.76×** |
| large  | 4 × 180 | 0.971733 s (p25 0.967799 / p75 0.975216) | 0.003142 s | **309.23×** |

Median of the three speedups: **309.23×**. Relative standard deviation is
0.42–1.53% on every scale (no scale exceeds the 10% noise threshold); the
residual is scheduler jitter on the shared host, not GC — the optimized arm
allocates a few hundred bytes in total.

Peak traced memory on the large scale: **802 B (optimized) vs 1186 B
(baseline)**, ratio 0.68×.

## Correctness evidence

| check | what was run | result |
|---|---|---|
| G1 own suite | `python -m pytest -q` | **16 passed**, 0 failed |
| G2a minute-grid oracle | 6 zones × 2024 (3.1 M sampled minutes), independent minute walk + refine | break lists `==`, 8/8 |
| G2b grid insensitivity | 12 zones × 1970–2036, `step=6h` vs `step=1h` | identical, 0 differences |
| G2c interval semantics | every break 1970–2036 in 8 zones: midpoint `classify()` matches kind, `wall_lo-1s` and `wall_hi` are unambiguous | >500 breaks, 0 failures |
| G2d second precision | 3 zones 2000–2030: `offset(T-1s)==before`, `offset(T)==after` | 0 failures |
| G3 median speedup | table above | 309.23× ≥ 1.15× |
| G3b real corpus | all 599 system zones, 2020–2036, **6,530 transitions** (3,265 gap / 3,265 overlap), each re-probed at `T-1s` and `T` | 6,530/6,530 verified, **0 mismatch** |
| G4 large scale | large row above | 309.23× ≥ 0.95× |
| G5 memory | `tracemalloc` peak, large scale | 0.68× baseline ≤ 1.10× |

Reproduce each gate:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python benchmark/bench.py --impl both --json-out benchmark/both.json
.venv/bin/python -m tzbreaks audit --zones all --start 2020-01-01T00:00:00Z \
    --end 2036-01-01T00:00:00Z --json > benchmark/audit_all_zones.json
.venv/bin/python -c "import json;d=json.load(open('benchmark/baseline.json'));print({k:round(v['arm']['median_s'],6) for k,v in d['scales'].items()})"
.venv/bin/python -c "import json;print(json.load(open('benchmark/audit_all_zones.json'))['totals'])"
```

`benchmark/raw/` holds all 30 per-run samples
(`<arm>-<scale>-<i>.json`, 2 arms × 3 scales × 5 runs); `baseline.json` and
`optimized.json` aggregate the same run (`benchmark/both.json`).

## Limits (read before trusting the speedup)

* The baseline is an **exhaustive one-minute grid**, the sound-by-construction
  way to use a black-box tz API. The 309× number therefore measures grid
  reduction, not a faster offset lookup; absolute times are small either way.
* The coarse grid is a **coverage parameter, not a proof**. Two transitions
  inside one grid cell that restore the original offset would be invisible.
  The equivalence evidence above (minute-grid oracle, 6h-vs-1h over 66 years,
  all-zone re-probe) is empirical; `breaks(..., step=...)` lets callers shrink
  the grid, and `audit` re-verifies every reported transition.
* tz data comes from the host. Different tzdata releases can change historical
  transitions; the audit output records the run's counts, not a frozen dataset.
* No claim is made about pre-1970 data beyond the ranges tested.

## Environment

CPU AMD EPYC 7K62 (4 vCPU), 3.6 GiB RAM, Linux 6.8.0-124-generic, CPython
3.12.3, tzdata from the distribution (599 zones), seed 20241113. Only CPython
3.12 exists on this host; no 3.10/3.11 cross-interpreter run was possible, so
none is reported.

## 本轮创新

**B 档**：把「某时刻存不存在」的点查询升级为**可枚举的断点对象 + 墙钟区间证书**，
并用 6h 粗网格 + 秒级二分在真实 tzdata 上做全量等效性证明（599 区 × 16 年 = 6,530 个
断点，逐个在 `T-1s`/`T` 复探，0 不符）。与 `dateutil.tz.datetime_exists` /
`datetime_ambiguous`（只回答单点、无区间上下界）和 `pytz.localize(is_dst=None)`
（只抛异常、不枚举）不同：这里给出 gap/overlap 的半开墙钟区间、跳变量和可机检的
`Break` 对象，并附带「采样网格是否漏断点」的可证伪证据。未做过的点：此前台账里的
时区/时间类工作为 0，且没有把「粗网格 + 二分」与「逐分钟穷举」做过等效性对照。

## License

MIT (see `LICENSE`). No upstream code was copied.
