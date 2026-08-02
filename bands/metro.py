"""Map a worksite city and state onto a metro label.

WORKSITE_CITY is free text too, and the same labour market is split across it: Sunnyvale,
Santa Clara, Mountain View and San Francisco are one hiring market with one wage level, and
treating them as four cities makes every per-city sample small enough to be noise.

The mapping is a committed lookup table, `bands/metros.json`, keyed on `CITY|STATE`. It is
deliberately a table and not a radius rule, because a radius needs coordinates this dataset
does not carry, and a fuzzy rule would quietly absorb cities nobody checked.

Anything not in the table is labelled "Other / not mapped" and counted. The published
output reports the mapped share, so a reader can see how much of the file the metro
breakdown actually speaks for. It is never folded into the nearest metro.

City normalisation is narrow on purpose: uppercase, collapse internal whitespace, drop a
trailing period. "St. Louis" and "ST. LOUIS" match; "Saint Paul" and "St. Paul" are both
listed in the table rather than being merged by a rewrite rule that might also merge
something it should not.
"""

from __future__ import annotations

import json
import pathlib

UNMAPPED = "Other / not mapped"

_TABLE_PATH = pathlib.Path(__file__).resolve().parent / "metros.json"
_table: dict[str, str] | None = None


def table() -> dict[str, str]:
    global _table
    if _table is None:
        _table = json.loads(_TABLE_PATH.read_text())["map"]
    return _table


def normalise_city(city: str) -> str:
    return " ".join(str(city).strip().upper().split())


def metro_of(city: str, state: str) -> str:
    key = f"{normalise_city(city)}|{str(state).strip().upper()}"
    return table().get(key, UNMAPPED)
