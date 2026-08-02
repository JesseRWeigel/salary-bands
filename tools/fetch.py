#!/usr/bin/env python3
"""Download an OFLC LCA disclosure file into a cache OUTSIDE this repository.

The files are 75 to 145 MB each. They are public data that anyone can re-download, they
change when DOL republishes a quarter, and a repository that carries one is a repository
where the 0.4 MB of actual work is invisible next to it. So the cache lives at

    $SALARY_BANDS_CACHE, or ${XDG_CACHE_HOME:-~/.cache}/thousand-salary-bands

and .gitignore refuses anything ending in .xlsx outside fixtures/.

    python3 tools/fetch.py --list
    python3 tools/fetch.py FY2025_Q4 FY2026_Q1
    python3 tools/fetch.py --path FY2025_Q4      # print where it would live

Each quarterly file holds the cases DECIDED in that quarter, not the ones received. The
FY2025 Q4 file is decisions from 2025-07-01 to 2025-09-30 and holds filings received as
far back as 2020.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import sys
import urllib.request

BASE = "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs"
PERFORMANCE_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"

# Quarters this project has been run against, with the size seen at download time. A new
# quarter needs no code change: any FYnnnn_Qn is accepted and the URL is built from it.
KNOWN = {
    "FY2025_Q4": 79134156,
    "FY2026_Q1": 75609372,
}


def cache_dir() -> pathlib.Path:
    env = os.environ.get("SALARY_BANDS_CACHE")
    if env:
        return pathlib.Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return pathlib.Path(base) / "thousand-salary-bands"


def name_for(quarter: str) -> str:
    return f"LCA_Disclosure_Data_{quarter}.xlsx"


def path_for(quarter: str) -> pathlib.Path:
    return cache_dir() / name_for(quarter)


def fetch(quarter: str) -> int:
    dest = path_for(quarter)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"{quarter}: already cached, {dest.stat().st_size} bytes")
        return 0
    url = f"{BASE}/{name_for(quarter)}"
    print(f"{quarter}: downloading {url}")
    tmp = dest.with_suffix(".partial")
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as out:
            if r.status != 200:
                print(f"  HTTP {r.status}", file=sys.stderr)
                return 1
            digest = hashlib.sha256()
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        print(f"  download failed: {e}", file=sys.stderr)
        print(f"  the quarter may not be published yet; check {PERFORMANCE_PAGE}",
              file=sys.stderr)
        return 1
    tmp.rename(dest)
    size = dest.stat().st_size
    print(f"  {size} bytes, sha256 {digest.hexdigest()}")
    if quarter in KNOWN and size != KNOWN[quarter]:
        print(f"  NOTE: {size} bytes, but this project was built against {KNOWN[quarter]}. "
              f"DOL republishes quarters; your aggregates will differ from the committed ones.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("quarter", nargs="*", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--path", action="store_true", help="print the cache path and exit")
    a = ap.parse_args(argv)

    if a.list:
        print(f"cache directory: {cache_dir()}")
        print(f"index page:      {PERFORMANCE_PAGE}")
        for q, size in KNOWN.items():
            p = path_for(q)
            print(f"  {q}  {'cached' if p.exists() else 'not cached':<10} {size:>10} bytes "
                  f"expected  {BASE}/{name_for(q)}")
        return 0
    if not a.quarter:
        ap.error("name at least one quarter, for example FY2025_Q4, or pass --list")
    if a.path:
        for q in a.quarter:
            print(path_for(q))
        return 0
    rc = 0
    for q in a.quarter:
        rc |= fetch(q)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
