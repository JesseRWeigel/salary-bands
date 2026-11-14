"""Turn LCA disclosure rows into the published aggregate table.

Every number this produces carries the count it was computed from. A median over four
filings and a median over four thousand are not the same kind of claim, and printing them
in the same column with no n is how a spreadsheet becomes misinformation.

Row selection, in order, each step counted in meta.filters:

  1. VISA_CLASS = H-1B.            E-3 and H-1B1 are separate programmes with their own
                                    wage rules and are not pooled in.
  2. CASE_STATUS = Certified.      Denied, Withdrawn and Certified-Withdrawn filings are
                                    counted and excluded; a withdrawn filing is not
                                    evidence that anyone was paid anything.
  3. FULL_TIME_POSITION = Y.       Annualising a part-time hourly rate at 2080 hours would
                                    invent a full-time salary that nobody offered.
  4. The wage annualises.          See bands/wage.py. Reasons for refusal are counted.

One filing is one observation. TOTAL_WORKER_POSITIONS can exceed one, and this code does
NOT weight by it, because a filing for eight positions states one wage floor and weighting
it eight times would let a single large filing move a median. The distribution of that
field is reported so the choice can be argued with.

Percentiles use linear interpolation between the two nearest ranks of the sorted values,
index = p * (n - 1). Money is rounded with floor(x + 0.5), which is stated because Python's
built-in round() is banker's rounding and would disagree with an independent checker on
exact halves.
"""

from __future__ import annotations

import collections
import datetime
import hashlib
import json
import math
import random
from dataclasses import dataclass

from . import metro, roles, wage
from .disclosure import WAGE_NOTE
from .xlsxread import records

MIN_N_EMPLOYER = 25      # an employer row is published only at or above this many filings
MIN_N_METRO = 40
MIN_N_CELL = 10          # employer x family, and family x level, cells
TOP_TITLES = 12
TOP_EMPLOYERS = 25
EXCEL_EPOCH = datetime.date(1899, 12, 30)

CORP_SUFFIXES = (
    "INCORPORATED", "INC", "LLC", "L L C", "LLP", "LP", "PLC", "CORPORATION", "CORP",
    "COMPANY", "LIMITED", "LTD", "PVT", "PRIVATE", "GMBH", "NV", "SA", "AG", "PC", "PA",
    "USA", "US", "AMERICA", "AMERICAS", "NORTH AMERICA", "TECHNOLOGIES", "TECHNOLOGY",
)


def money(x: float) -> int:
    """Round half up, always. Stated explicitly so a second implementation can match it."""
    return int(math.floor(x + 0.5))


def percentile(sorted_values: list[float], p: float) -> float:
    n = len(sorted_values)
    if n == 0:
        raise ValueError("percentile of an empty list")
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] * (hi - k) + sorted_values[hi] * (k - lo)


def band(values: list[float]) -> dict:
    """The published five-number summary plus n. Input need not be sorted."""
    s = sorted(values)
    return {
        "n": len(s),
        "p10": money(percentile(s, 0.10)),
        "p25": money(percentile(s, 0.25)),
        "p50": money(percentile(s, 0.50)),
        "p75": money(percentile(s, 0.75)),
        "p90": money(percentile(s, 0.90)),
    }


def canonical_employer(name: str) -> str:
    """Uppercase, drop punctuation, then strip trailing corporate suffixes.

    'IQVIA Inc.' and 'IQVIA INC' become 'IQVIA'. Only TRAILING suffixes are stripped and
    only repeatedly from the end, so 'US BANK' keeps its US and 'AMAZON.COM SERVICES LLC'
    becomes 'AMAZON COM SERVICES'. The variants merged into each published employer are
    listed in the output so the merge can be inspected.
    """
    up = "".join(ch if ch.isalnum() else " " for ch in str(name).upper())
    toks = up.split()
    changed = True
    while changed and len(toks) > 1:
        changed = False
        for suf in CORP_SUFFIXES:
            parts = suf.split()
            if len(toks) > len(parts) and toks[-len(parts):] == parts:
                toks = toks[:-len(parts)]
                changed = True
                break
    return " ".join(toks)


def excel_date(serial: str) -> str | None:
    try:
        return (EXCEL_EPOCH + datetime.timedelta(days=int(float(serial)))).isoformat()
    except (TypeError, ValueError):
        return None


@dataclass
class Row:
    case: str
    annual: float
    annual_to: float | None
    unit: str
    family: str
    level: str
    title: str
    employer: str
    employer_raw: str
    metro: str
    city: str
    state: str
    soc: str
    positions: int


def load(paths: list, trace: list | None = None) -> tuple[list[Row], dict]:
    """Read every source file, apply the filters, and return kept rows plus the meta counts.

    If `trace` is a list, one (CASE_NUMBER, outcome) pair is appended for every row read,
    where outcome is "ok" or the name of the step that rejected it. fixtures/expected.json
    carries the hand-written trace for the fixture, which is how a row silently changing
    bucket gets caught rather than averaging out inside a percentile.
    """
    filters = collections.Counter()
    status_counts = collections.Counter()
    visa_counts = collections.Counter()
    unit_counts = collections.Counter()
    drop_reasons = collections.Counter()
    positions_counts = collections.Counter()
    seen_cases: set[str] = set()
    dates: list[str] = []
    kept: list[Row] = []
    total = 0
    missing_fields = collections.Counter()

    for path in paths:
        for rec in records(path):
            total += 1
            visa_counts[rec.get("VISA_CLASS", "")] += 1
            case = rec.get("CASE_NUMBER", "").strip()

            def reject(reason: str, count_as: str | None = None) -> None:
                filters[count_as or reason] += 1
                if trace is not None:
                    trace.append((case, reason))

            if rec.get("VISA_CLASS", "").strip() != "H-1B":
                reject("not_h1b")
                continue
            status_counts[rec.get("CASE_STATUS", "")] += 1
            if rec.get("CASE_STATUS", "").strip() != "Certified":
                reject("not_certified")
                continue
            if rec.get("FULL_TIME_POSITION", "").strip().upper() != "Y":
                reject("not_full_time")
                continue
            if case and case in seen_cases:
                reject("duplicate_case_number")
                continue
            if case:
                seen_cases.add(case)

            conv = wage.annualise(rec.get("WAGE_RATE_OF_PAY_FROM", ""),
                                 rec.get("WAGE_UNIT_OF_PAY", ""))
            unit_counts[conv.unit or "(unreadable)"] += 1
            drop_reasons[conv.reason] += 1
            if conv.annual is None:
                # The trace names the exact reason; meta.filters counts them together, and
                # meta.wage_conversion_outcomes breaks them out.
                reject(conv.reason, count_as="wage_unconvertible")
                continue

            to = wage.annualise(rec.get("WAGE_RATE_OF_PAY_TO", ""),
                                rec.get("WAGE_UNIT_OF_PAY", ""))
            title = rec.get("JOB_TITLE", "").strip()
            role = roles.classify(title)
            d = excel_date(rec.get("DECISION_DATE", ""))
            if d:
                dates.append(d)
            try:
                positions = int(float(rec.get("TOTAL_WORKER_POSITIONS", "1") or 1))
            except ValueError:
                positions = 1
            positions_counts[min(positions, 10)] += 1

            city = rec.get("WORKSITE_CITY", "").strip()
            state = rec.get("WORKSITE_STATE", "").strip().upper()
            if trace is not None:
                trace.append((case, "ok"))
            for field in ("CASE_NUMBER", "JOB_TITLE", "EMPLOYER_NAME", "WORKSITE_CITY",
                          "WORKSITE_STATE", "DECISION_DATE", "WAGE_RATE_OF_PAY_TO"):
                if not str(rec.get(field, "")).strip():
                    missing_fields[field] += 1
            kept.append(Row(
                case=case,
                annual=conv.annual,
                annual_to=to.annual,
                unit=conv.unit,
                family=role.family,
                level=role.level,
                title=" ".join(title.upper().split()),
                employer=canonical_employer(rec.get("EMPLOYER_NAME", "")),
                employer_raw=" ".join(rec.get("EMPLOYER_NAME", "").split()),
                metro=metro.metro_of(city, state),
                city=" ".join(city.upper().split()),
                state=state,
                soc=rec.get("SOC_CODE", "").strip(),
                positions=positions,
            ))

    meta = {
        "missing_fields_kept": dict(sorted(missing_fields.items())),
        "rows_read": total,
        "rows_kept": len(kept),
        "filters": {k: filters[k] for k in sorted(filters)},
        "case_status_of_h1b_rows": dict(status_counts.most_common()),
        "visa_class_of_all_rows": dict(visa_counts.most_common()),
        "wage_unit_counts_after_status_filters": dict(unit_counts.most_common()),
        "wage_conversion_outcomes": dict(drop_reasons.most_common()),
        "total_worker_positions_histogram": {str(k): positions_counts[k]
                                             for k in sorted(positions_counts)},
        "decision_date_min": min(dates) if dates else None,
        "decision_date_max": max(dates) if dates else None,
    }
    return kept, meta


def _group_bands(rows: list[Row], key, min_n: int) -> list[dict]:
    buckets: dict[str, list[float]] = collections.defaultdict(list)
    for r in rows:
        buckets[key(r)].append(r.annual)
    out = []
    for name, vals in buckets.items():
        if len(vals) < min_n:
            continue
        b = band(vals)
        b["name"] = name
        out.append(b)
    out.sort(key=lambda b: (-b["p50"], b["name"]))
    return out


def permutation_control(rows: list[Row], seed: int = 20260801) -> dict:
    """One seeded unconditional label shuffle, descriptive only.

    Mixing employer labels also destroys role/location/level composition. A smaller
    shuffled spread therefore does not isolate an employer premium or causal effect.
    This is not a permutation p-value or an uncertainty interval.
    """
    real = _group_bands(rows, lambda r: r.employer, MIN_N_EMPLOYER)
    labels = [r.employer for r in rows]
    rng = random.Random(seed)
    rng.shuffle(labels)
    shuffled_rows = [Row(**{**r.__dict__, "employer": lab}) for r, lab in zip(rows, labels)]
    fake = _group_bands(shuffled_rows, lambda r: r.employer, MIN_N_EMPLOYER)

    def spread(bands_: list[dict]) -> int:
        meds = sorted(b["p50"] for b in bands_)
        if len(meds) < 4:
            return 0
        return money(percentile(meds, 0.90) - percentile(meds, 0.10))

    return {
        "seed": seed,
        "employers_published": len(real),
        "real_p10_to_p90_spread_of_employer_medians": spread(real),
        "shuffled_p10_to_p90_spread_of_employer_medians": spread(fake),
    }


def build(paths: list, source_meta: list[dict]) -> dict:
    rows, meta = load(paths)
    meta["sources"] = source_meta
    meta["wage_field"] = "WAGE_RATE_OF_PAY_FROM"
    meta["hours_per_year"] = wage.HOURS_PER_YEAR
    meta["floor_annual"] = wage.FLOOR
    meta["min_n"] = {"employer": MIN_N_EMPLOYER, "metro": MIN_N_METRO, "cell": MIN_N_CELL}
    meta["percentile_method"] = "linear interpolation at index p*(n-1) over sorted values"
    meta["rounding"] = "floor(x + 0.5)"

    mapped = sum(1 for r in rows if r.metro != metro.UNMAPPED)
    meta["metro_mapped_rows"] = mapped
    meta["metro_mapped_share"] = round(mapped / len(rows), 4) if rows else 0.0

    with_to = [r for r in rows if r.annual_to is not None and r.annual_to > r.annual]
    ratios = sorted(r.annual_to / r.annual for r in with_to)
    meta["offered_range"] = {
        "rows_with_top_of_range_above_bottom": len(with_to),
        "share_of_kept_rows": round(len(with_to) / len(rows), 4) if rows else 0.0,
        "median_top_over_bottom": round(percentile(ratios, 0.5), 3) if ratios else None,
        "note": WAGE_NOTE,
    }

    by_family: dict[str, list[Row]] = collections.defaultdict(list)
    for r in rows:
        by_family[r.family].append(r)

    families = []
    for name in roles.FAMILY_ORDER:
        frows = by_family.get(name, [])
        if not frows:
            continue
        titles = collections.Counter(r.title for r in frows)
        entry = band([r.annual for r in frows])
        entry["family"] = name
        entry["distinct_titles"] = len(titles)
        entry["top_titles"] = [[t, c] for t, c in titles.most_common(TOP_TITLES)]
        entry["levels"] = _group_bands(frows, lambda r: r.level, MIN_N_CELL)
        entry["employers"] = _group_bands(frows, lambda r: r.employer, MIN_N_CELL)[:TOP_EMPLOYERS]
        entry["metros"] = [b for b in _group_bands(frows, lambda r: r.metro, MIN_N_CELL)
                           if b["name"] != metro.UNMAPPED]
        families.append(entry)

    variants: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        variants[r.employer][r.employer_raw] += 1

    employers = _group_bands(rows, lambda r: r.employer, MIN_N_EMPLOYER)
    counts = collections.Counter(r.employer for r in rows)
    for e in employers:
        e["filings"] = counts[e["name"]]
        e["name_variants"] = [[v, c] for v, c in variants[e["name"]].most_common(4)]
    employers.sort(key=lambda b: (-b["filings"], b["name"]))

    unmatched = collections.Counter(r.title for r in by_family.get(roles.UNMATCHED, []))

    return {
        "meta": meta,
        "families": families,
        "employers_all_families": employers[:60],
        "metros_all_families": [b for b in _group_bands(rows, lambda r: r.metro, MIN_N_METRO)
                                if b["name"] != metro.UNMAPPED],
        "unmatched_top_titles": [[t, c] for t, c in unmatched.most_common(25)],
        "employer_permutation_control": permutation_control(rows),
    }


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dumps(obj: dict) -> str:
    return json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
