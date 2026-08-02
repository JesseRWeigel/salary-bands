"""Command line entry point.

    python3 -m bands.cli build  --source <file.xlsx> [...] --out data/aggregates.json
    python3 -m bands.cli show   --data data/aggregates.json --family "Software engineer"
    python3 -m bands.cli title  "Sr. Software Engineer II"

`build` is the only command that needs the large download. `show` and `title` work from
committed files alone.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

from . import aggregate, roles


def cmd_build(a) -> int:
    paths = [pathlib.Path(p) for p in a.source]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        print("source file not found: " + ", ".join(str(p) for p in missing), file=sys.stderr)
        print("run:  python3 tools/fetch.py --list", file=sys.stderr)
        return 2
    source_meta = [{
        "file": p.name,
        "bytes": p.stat().st_size,
        "sha256": aggregate.sha256(p),
    } for p in paths]
    if a.min_n:
        aggregate.MIN_N_EMPLOYER = aggregate.MIN_N_METRO = aggregate.MIN_N_CELL = a.min_n
    out = aggregate.build(paths, source_meta)
    text = aggregate.dumps(out)
    if a.out == "-":
        sys.stdout.write(text)
    else:
        pathlib.Path(a.out).write_text(text, encoding="utf-8")
        m = out["meta"]
        print(f"wrote {a.out}: {m['rows_kept']} rows kept of {m['rows_read']} read, "
              f"{len(out['families'])} families, "
              f"{len(out['employers_all_families'])} employers published")
    return 0


def cmd_show(a) -> int:
    import json
    data = json.loads(pathlib.Path(a.data).read_text(encoding="utf-8"))
    fams = data["families"]
    if a.family:
        fams = [f for f in fams if f["family"].lower().startswith(a.family.lower())]
        if not fams:
            print(f"no family matching {a.family!r}", file=sys.stderr)
            return 2
    for f in fams:
        print(f"\n{f['family']}   n={f['n']}")
        print(f"  p10 {f['p10']:>9,}   p25 {f['p25']:>9,}   median {f['p50']:>9,}"
              f"   p75 {f['p75']:>9,}   p90 {f['p90']:>9,}")
        if a.family:
            print("  by employer (n filings):")
            for e in f["employers"][:15]:
                print(f"    {e['name'][:38]:<38} median {e['p50']:>9,}  n={e['n']}")
            print("  by metro:")
            for e in f["metros"][:12]:
                print(f"    {e['name'][:38]:<38} median {e['p50']:>9,}  n={e['n']}")
    return 0


def cmd_title(a) -> int:
    for t in a.title:
        r = roles.classify(t)
        print(f"{t!r}\n  tokens : {' '.join(r.tokens)}\n  family : {r.family}"
              f"\n  level  : {r.level}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bands")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="read disclosure files and write the aggregate JSON")
    b.add_argument("--source", nargs="+", required=True)
    b.add_argument("--out", default="data/aggregates.json")
    b.add_argument("--min-n", type=int, default=0,
                   help="override every publication threshold (fixtures use 2)")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("show", help="print bands from an aggregate JSON")
    s.add_argument("--data", default="data/aggregates.json")
    s.add_argument("--family", default="")
    s.set_defaults(fn=cmd_show)

    t = sub.add_parser("title", help="show how a job title is bucketed")
    t.add_argument("title", nargs="+")
    t.set_defaults(fn=cmd_title)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    raise SystemExit(main())
