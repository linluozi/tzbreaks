# How the scanner works

`zoneinfo` exposes no transition table, only `utcoffset(instant)`. Two ways to
enumerate discontinuities:

1. **Exhaustive grid** — ask the offset every minute and refine any change by
   bisection. Sound by construction, and what `benchmark/naive_reference.py`
   does. Cost: one query per minute of range.
2. **Coarse grid + bisection** (delivered) — ask the offset every `step`
   (default 6 h); when two adjacent samples disagree, bisect the interval down
   to one second and record the transition. Cost: `range/step` queries plus
   ~17 per transition.

For a transition at instant `T` the offsets `before`/`after` define the affected
wall interval, half-open:

* gap (`after > before`): `[T+before, T+after)` does not exist on the wall clock;
* overlap (`after < before`): `[T+after, T+before)` occurs twice.

`classify()` does not use the scanner: it resolves the wall time under `fold=0`
and `fold=1`, keeps the interpretations that round-trip back to the same wall
time, and only then looks up the responsible `Break` (an O(1)-ish window scan)
for the bounds.

## Coverage is a parameter, not a proof

A pair of transitions that both fall inside one grid cell *and* restore the
original offset is invisible to the coarse grid. That is not hypothetical for
historical data, so the repository ships three independent checks instead of
assuming it away:

1. `tests/test_core.py::test_oracle_minute_grid_equivalence` — 6 zones × 2024,
   compared against the exhaustive minute grid (3.1 M sampled minutes).
2. `tests/test_core.py::test_step_insensitivity_over_wide_history` — 12 zones ×
   1970–2036, `step=6h` vs `step=1h`, identical break lists.
3. `python -m tzbreaks audit --zones all --start 2020-01-01T00:00:00Z
   --end 2036-01-01T00:00:00Z` — 6,530 transitions over all 599 zones, each
   re-probed at `T-1s` and `T`; measured 14.7 s, 0 mismatches.

Callers who need a different guarantee pass `step=`. The `audit` subcommand
re-verifies every transition it reports, so a stale or unusual tz database
shows up as `mismatch > 0` rather than a silent wrong answer.

## Restoring a wall time

`plan()` maps a nonexistent wall time to the first existing instant after the
gap (`shift_forward`) and records the shift, e.g. New York 2024-03-10 02:30 →
07:00Z with `shifted forward by 0:30:00`. Ambiguous times resolve to `fold=0`
or `fold=1` under `earlier`/`later`, and `strict` raises rather than choosing.
