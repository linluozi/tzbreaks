"""Command line interface: scan | classify | plan | audit."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, available_timezones

from . import core

UTC = timezone.utc


def _utc(text: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"bad UTC datetime: {text!r} (use 2024-03-31T01:00:00Z)")


def _local(text: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"bad local datetime: {text!r} (use 2024-03-10 02:30)")


def _step(text: str) -> timedelta:
    units = {"m": 60, "h": 3600, "d": 86400}
    if not text or text[-1].lower() not in units:
        raise argparse.ArgumentTypeError("step must look like 30m, 6h or 1d")
    try:
        value = int(text[:-1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("step must look like 30m, 6h or 1d") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("step must be positive")
    return timedelta(seconds=value * units[text[-1].lower()])


def _emit(text: str) -> None:
    sys.stdout.write(text + "\n")


def cmd_scan(args: argparse.Namespace) -> int:
    found = core.breaks(args.zone, args.start, args.end, args.step)
    if args.json:
        _emit(json.dumps({"zone": args.zone, "count": len(found),
                          "breaks": [b.to_dict() for b in found]}, indent=2))
        return 0
    _emit(f"{args.zone}: {len(found)} discontinuities in "
          f"[{args.start:%Y-%m-%d}, {args.end:%Y-%m-%d}]")
    _emit(f"{'kind':8} {'utc instant':22} {'before':>8} {'after':>8}  wall interval")
    for b in found:
        _emit(f"{b.kind:8} {b.utc:%Y-%m-%dT%H:%M:%SZ} "
              f"{int(b.before.total_seconds()):>8} {int(b.after.total_seconds()):>8}  "
              f"[{b.wall_lo:%Y-%m-%d %H:%M}, {b.wall_hi:%Y-%m-%d %H:%M})")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    w = core.classify(args.zone, args.local)
    if args.json:
        _emit(json.dumps(w.to_dict(), indent=2))
    else:
        _emit(f"{args.zone} {args.local:%Y-%m-%d %H:%M} -> {w.kind}")
        for inst, off in zip(w.instants, w.offsets):
            _emit(f"  utc {inst:%Y-%m-%dT%H:%M:%SZ}  offset {int(off.total_seconds()):+d}s "
                  f"({off})")
        if w.break_ is not None:
            _emit(f"  {w.break_.kind} wall interval "
                  f"[{w.break_.wall_lo:%Y-%m-%d %H:%M}, {w.break_.wall_hi:%Y-%m-%d %H:%M})")
    return 0 if w.kind == "unambiguous" else 3


def cmd_plan(args: argparse.Namespace) -> int:
    res = core.plan(args.zone, args.local, args.policy)
    if args.json:
        _emit(json.dumps({"zone": res.zone, "policy": res.policy,
                          "ok": res.ok, "warnings": res.warnings,
                          "rows": [r.to_dict() for r in res.rows]}, indent=2))
    else:
        for r in res.rows:
            utc = r.utc.strftime("%Y-%m-%dT%H:%M:%SZ") if r.utc else "-"
            _emit(f"{r.local:%Y-%m-%d %H:%M}  {r.kind:12} {utc:22} {r.note}")
        for w in res.warnings:
            _emit(f"warning: {w}")
    return 0 if res.ok else 4


def cmd_audit(args: argparse.Namespace) -> int:
    zones = sorted(available_timezones()) if args.zones == "all" else args.zones.split(",")
    zones = [z.strip() for z in zones if z.strip()]
    skipped, total = [], {"gap": 0, "overlap": 0, "zones": 0, "verified": 0, "mismatch": 0}
    records = []
    for name in zones:
        try:
            ZoneInfo(name)
        except Exception:
            skipped.append(name)
            continue
        total["zones"] += 1
        found = core.breaks(name, args.start, args.end, args.step)
        for b in found:
            total[b.kind] += 1
            tz = ZoneInfo(name)
            ok = ((b.utc - timedelta(seconds=1)).astimezone(tz).utcoffset() == b.before
                  and b.utc.astimezone(tz).utcoffset() == b.after)
            total["verified" if ok else "mismatch"] += 1
            records.append(b.to_dict() | {"verified": ok})
    if args.json:
        _emit(json.dumps({"start": args.start.isoformat(), "end": args.end.isoformat(),
                          "step_seconds": int(args.step.total_seconds()),
                          "skipped": skipped, "totals": total,
                          "breaks": records}, indent=2))
    else:
        _emit(f"zones {total['zones']}  gap {total['gap']}  overlap {total['overlap']}  "
              f"verified {total['verified']}  mismatch {total['mismatch']}")
        if skipped:
            _emit(f"skipped {len(skipped)}: {', '.join(skipped)}")
    return 0 if total["mismatch"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tzbreaks", description=__doc__)
    p.add_argument("--version", action="version", version="tzbreaks 1.0.0")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="list discontinuities in a UTC range")
    s.add_argument("--zone", required=True)
    s.add_argument("--start", type=_utc, required=True)
    s.add_argument("--end", type=_utc, required=True)
    s.add_argument("--step", type=_step, default=timedelta(hours=6))
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_scan)

    c = sub.add_parser("classify", help="classify one naive local wall time")
    c.add_argument("--zone", required=True)
    c.add_argument("--local", type=_local, required=True)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_classify)

    pl = sub.add_parser("plan", help="resolve naive local times under a policy")
    pl.add_argument("--zone", required=True)
    pl.add_argument("--local", type=_local, action="append", required=True)
    pl.add_argument("--policy", default="shift_forward", choices=core.POLICIES)
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_plan)

    a = sub.add_parser("audit", help="scan many zones and re-probe every transition")
    a.add_argument("--zones", default="all", help='"all" or a comma list')
    a.add_argument("--start", type=_utc, required=True)
    a.add_argument("--end", type=_utc, required=True)
    a.add_argument("--step", type=_step, default=timedelta(hours=6))
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_audit)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, core.AmbiguousLocalTime, core.NonexistentLocalTime) as exc:
        sys.stderr.write(f"tzbreaks: {exc}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
