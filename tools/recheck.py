#!/usr/bin/env python3
"""An independent recomputation of every published figure, from the raw filings.

This file imports nothing from `bands`. It has its own spreadsheet reader (raw expat
handlers rather than ElementTree iterparse), its own unit table typed from the DOL record
layout rather than copied, its own title rule written from the prose specification in
bands/roles.py rather than from its code, its own percentile arithmetic and its own
employer canonicalisation. The only thing it shares is data: bands/metros.json, which is a
lookup table and not logic.

Why it exists: a validator that compares a published page against the script that produced
it cannot catch a bug in that script, because both sides are the same derivation. The only
check that can is a second derivation from the raw rows. An aggregator that undercounts,
that annualises with the wrong factor, or that lets a decoy title into a bucket will
disagree with this file, and disagreeing is a failure.

Three ways to run it:

  --fixture                     recompute fixtures/fixture.xlsx and compare against
                                fixtures/expected.json, the hand-written ground truth.
                                Runs offline; this is what the test suite exercises.
  --source F.xlsx --against data/aggregates.json --write data/recheck.json
                                recompute every claim in the published aggregate from the
                                raw download and record the result.
  --compare data/aggregates.json data/recheck.json
                                compare the published aggregate against the recorded
                                independent recomputation. Runs offline.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import xml.parsers.expat
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

# ---------------------------------------------------------------------------
# spreadsheet reading, via raw expat handlers
# ---------------------------------------------------------------------------

SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


class HostileXml(Exception):
    pass


def _new_parser():
    p = xml.parsers.expat.ParserCreate(namespace_separator="|")

    def refuse(*_a):
        raise HostileXml("the workbook declares an entity or a DTD")

    p.EntityDeclHandler = refuse
    p.StartDoctypeDeclHandler = refuse
    p.ExternalEntityRefHandler = refuse
    return p


def _read_shared(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    strings: list[str] = []
    state = {"in_si": False, "in_t": False, "buf": []}

    def start(name, _attrs):
        if name == f"{SHEET_NS}|si":
            state["in_si"] = True
            state["buf"] = []
        elif name == f"{SHEET_NS}|t" and state["in_si"]:
            state["in_t"] = True

    def chars(data):
        if state["in_t"]:
            state["buf"].append(data)

    def end(name):
        if name == f"{SHEET_NS}|t":
            state["in_t"] = False
        elif name == f"{SHEET_NS}|si":
            state["in_si"] = False
            strings.append("".join(state["buf"]))

    p = _new_parser()
    p.StartElementHandler = start
    p.CharacterDataHandler = chars
    p.EndElementHandler = end
    with zf.open("xl/sharedStrings.xml") as fh:
        p.ParseFile(fh)
    return strings


def _letters_to_index(ref: str) -> int:
    total = 0
    for ch in ref:
        if not ch.isalpha():
            break
        total = total * 26 + (ord(ch.upper()) - 64)
    return total - 1


def read_rows(path):
    """Yield every sheet row as a dict of column letter index -> text."""
    with zipfile.ZipFile(path) as zf:
        shared = _read_shared(zf)
        sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
        if not sheets:
            raise SystemExit(f"{path}: no worksheet part")
        target = sorted(sheets)[0]

        pending: list[dict] = []
        cur = {"row": None, "col": None, "type": None, "buf": [], "in_v": False,
               "in_t": False}

        def start(name, attrs):
            if name == f"{SHEET_NS}|row":
                cur["row"] = {}
            elif name == f"{SHEET_NS}|c":
                cur["col"] = _letters_to_index(attrs.get("r", ""))
                cur["type"] = attrs.get("t")
                cur["buf"] = []
            elif name == f"{SHEET_NS}|v":
                cur["in_v"] = True
            elif name == f"{SHEET_NS}|t":
                cur["in_t"] = True

        def chars(data):
            if cur["in_v"] or cur["in_t"]:
                cur["buf"].append(data)

        def end(name):
            if name == f"{SHEET_NS}|v":
                cur["in_v"] = False
            elif name == f"{SHEET_NS}|t":
                cur["in_t"] = False
            elif name == f"{SHEET_NS}|c":
                text = "".join(cur["buf"])
                if cur["type"] == "s":
                    text = shared[int(text)] if text.strip() else ""
                if cur["row"] is not None and cur["col"] is not None and cur["col"] >= 0:
                    cur["row"][cur["col"]] = text
            elif name == f"{SHEET_NS}|row":
                pending.append(cur["row"])
                cur["row"] = None

        p = _new_parser()
        p.StartElementHandler = start
        p.CharacterDataHandler = chars
        p.EndElementHandler = end
        with zf.open(target) as fh:
            # Parse in chunks so a 500 MB sheet does not have to be materialised, handing
            # finished rows out as they complete.
            while True:
                chunk = fh.read(1 << 20)
                p.Parse(chunk, not chunk)
                while pending:
                    yield pending.pop(0)
                if not chunk:
                    break


def read_records(path):
    it = read_rows(path)
    header_row = next(it)
    header = {i: name.strip() for i, name in header_row.items()}
    for row in it:
        # The real sheet carries 445,109 trailing rows whose cells exist only to hold a
        # style and have no value at all. They are not filings and must not be counted as
        # rows read; without this the row count comes out 4.7x too high.
        if not any(v.strip() for v in row.values()):
            continue
        rec = {}
        for i, name in header.items():
            rec[name] = row.get(i, "")
        yield rec


# ---------------------------------------------------------------------------
# the rules, re-derived from the written specification
# ---------------------------------------------------------------------------

PERIODS_IN_A_YEAR = {
    "YEAR": 1,
    "MONTH": 12,
    "BI-WEEKLY": 26,
    "WEEK": 52,
    "HOUR": 40 * 52,        # 2080, written as the 40-hour week it comes from
}
UNIT_SPELLINGS = {
    "Y": "YEAR", "YR": "YEAR", "YEARLY": "YEAR", "ANNUAL": "YEAR", "ANNUALLY": "YEAR",
    "M": "MONTH", "MTH": "MONTH", "MONTHLY": "MONTH",
    "BW": "BI-WEEKLY", "B": "BI-WEEKLY", "BIWEEKLY": "BI-WEEKLY", "BI WEEKLY": "BI-WEEKLY",
    "W": "WEEK", "WK": "WEEK", "WEEKLY": "WEEK",
    "H": "HOUR", "HR": "HOUR", "HOURLY": "HOUR",
}
LOWEST_LEGAL_ANNUAL = 7.25 * 40 * 52
HIGHEST_PLAUSIBLE_ANNUAL = 10_000_000.0


def to_annual(amount_text: str, unit_text: str):
    """Return (annual, unit, reason). Reason is 'ok' or why the row cannot be used."""
    unit = " ".join(str(unit_text).upper().split())
    unit = UNIT_SPELLINGS.get(unit, unit)
    cleaned = str(amount_text).strip().replace(",", "").replace("$", "")
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    if not cleaned:
        return None, (unit if unit in PERIODS_IN_A_YEAR else None), "blank_wage"
    try:
        amount = float(cleaned)
    except ValueError:
        return None, (unit if unit in PERIODS_IN_A_YEAR else None), "nonnumeric_wage"
    if unit not in PERIODS_IN_A_YEAR:
        return None, None, ("blank_unit" if not str(unit_text).strip() else "unknown_unit")
    annual = amount * PERIODS_IN_A_YEAR[unit]
    if annual < LOWEST_LEGAL_ANNUAL:
        return None, unit, "below_federal_minimum"
    if annual > HIGHEST_PLAUSIBLE_ANNUAL:
        return None, unit, "above_ceiling"
    return annual, unit, "ok"


WORD_SUBSTITUTIONS = {
    "SR": ["SENIOR"], "SNR": ["SENIOR"], "JR": ["JUNIOR"], "MGR": ["MANAGER"],
    "ENGR": ["ENGINEER"], "ENGG": ["ENGINEER"], "ENG": ["ENGINEER"], "DEV": ["DEVELOPER"],
    "SW": ["SOFTWARE"], "SDE": ["SOFTWARE", "DEVELOPMENT", "ENGINEER"],
    "SWE": ["SOFTWARE", "ENGINEER"], "SDET": ["SDET"],
    "ML": ["MACHINE", "LEARNING"], "MLE": ["MACHINE", "LEARNING", "ENGINEER"],
    "AI": ["ARTIFICIAL", "INTELLIGENCE"], "SRE": ["SITE", "RELIABILITY", "ENGINEER"],
    "QA": ["QA"], "IT": ["IT"], "SYS": ["SYSTEMS"], "ADMIN": ["ADMINISTRATOR"],
    "ARCH": ["ARCHITECT"], "SCI": ["SCIENTIST"], "PRIN": ["PRINCIPAL"],
    "ASSOC": ["ASSOCIATE"], "ASST": ["ASSISTANT"],
}
RANK_WORDS = [("DISTINGUISHED", "Distinguished"), ("FELLOW", "Fellow"),
              ("PRINCIPAL", "Principal"), ("STAFF", "Staff"), ("LEAD", "Lead"),
              ("SENIOR", "Senior"), ("JUNIOR", "Junior/entry"), ("ENTRY", "Junior/entry"),
              ("GRADUATE", "Junior/entry"), ("INTERN", "Junior/entry")]
RANK_NUMBERS = {"I": "Level I", "1": "Level I", "II": "Level II", "2": "Level II",
                "III": "Level III", "3": "Level III", "IV": "Level IV", "4": "Level IV",
                "V": "Level V", "5": "Level V"}
BOSS_WORDS = {"MANAGER", "DIRECTOR", "HEAD", "VP", "PRESIDENT", "CHIEF", "SUPERVISOR",
              "CTO", "CEO"}
TECHNICAL_WORDS = {"SOFTWARE", "ENGINEER", "ENGINEERING", "DEVELOPER", "DEVELOPMENT",
                   "DATA", "MACHINE", "PLATFORM", "INFRASTRUCTURE", "DEVOPS", "SYSTEMS",
                   "TECHNOLOGY", "TECHNICAL"}


def words_of(title: str) -> list[str]:
    letters = "".join(c if (c.isalnum() and c.isascii()) else " " for c in str(title).upper())
    result: list[str] = []
    for word in letters.split():
        result += WORD_SUBSTITUTIONS.get(word, [word])
    return result


def rank_of(title: str) -> str:
    words = words_of(title)
    for word, label in RANK_WORDS:
        if word in words:
            return label
    for word in words:
        if word in RANK_NUMBERS:
            return RANK_NUMBERS[word]
    return "Unstated"


def bucket_of(title: str) -> str:
    w = set(words_of(title))

    def all_of(*need):
        return all(x in w for x in need)

    if (w & BOSS_WORDS) and "PRODUCT" not in w:
        return "Engineering management" if (w & TECHNICAL_WORDS) else "unmatched"
    if "SALES" in w:
        return "unmatched"
    if "SDET" in w or "QA" in w or "TEST" in w or all_of("QUALITY", "ASSURANCE"):
        return "QA / test engineer"

    learning = all_of("MACHINE", "LEARNING")
    artificial = all_of("ARTIFICIAL", "INTELLIGENCE")
    if learning and "ENGINEER" in w:
        return "Machine learning engineer"
    if (learning or artificial) and ("SCIENTIST" in w or "RESEARCHER" in w):
        return "Applied / research scientist (ML, AI)"
    if all_of("DATA", "SCIENTIST"):
        return "Data scientist"
    if all_of("DATA", "ENGINEER"):
        return "Data engineer"
    if all_of("DATA", "ANALYST"):
        return "Data analyst"
    if ("DEVOPS" in w or all_of("SITE", "RELIABILITY") or all_of("PLATFORM", "ENGINEER")
            or all_of("INFRASTRUCTURE", "ENGINEER") or all_of("CLOUD", "ENGINEER")):
        return "DevOps / SRE / platform engineer"
    if ("SECURITY" in w or "CYBERSECURITY" in w) and "ENGINEER" in w:
        return "Security engineer"
    if "SOFTWARE" in w and (w & {"ENGINEER", "ENGINEERING", "DEVELOPER", "DEVELOPMENT",
                                 "ARCHITECT", "PROGRAMMER"}):
        return "Software engineer"
    if "SCIENTIST" in w and ("RESEARCH" in w or "APPLIED" in w):
        return "Research / applied scientist (unspecified)"
    if all_of("PRODUCT", "MANAGER"):
        return "Product manager"
    return "unmatched"


TRAILING_NOISE = ["INCORPORATED", "INC", "LLC", "L L C", "LLP", "LP", "PLC", "CORPORATION",
                  "CORP", "COMPANY", "LIMITED", "LTD", "PVT", "PRIVATE", "GMBH", "NV",
                  "SA", "AG", "PC", "PA", "USA", "US", "AMERICA", "AMERICAS",
                  "NORTH AMERICA", "TECHNOLOGIES", "TECHNOLOGY"]


def short_employer(raw: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in str(raw).upper()).split()
    while len(words) > 1:
        for noise in TRAILING_NOISE:
            tail = noise.split()
            if len(words) > len(tail) and words[-len(tail):] == tail:
                words = words[:-len(tail)]
                break
        else:
            break
    return " ".join(words)


_METROS = None


def metro_for(city: str, state: str) -> str:
    global _METROS
    if _METROS is None:
        _METROS = json.loads((ROOT / "bands" / "metros.json").read_text())["map"]
    return _METROS.get(f"{' '.join(str(city).upper().split())}|{str(state).upper().strip()}",
                       "Other / not mapped")


def quantile(values: list[float], fraction: float) -> float:
    """Same specification as the aggregator, arrived at with different arithmetic."""
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("no values")
    if count == 1:
        return float(ordered[0])
    position = fraction * (count - 1)
    below = int(position // 1)
    remainder = position - below
    if remainder == 0:
        return float(ordered[below])
    step = ordered[below + 1] - ordered[below]
    return float(ordered[below]) + step * remainder


def dollars(x: float) -> int:
    whole = int(x + 0.5) if x >= 0 else -int(-x + 0.5)
    return whole


def summarise(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p10": dollars(quantile(values, 0.10)),
        "p25": dollars(quantile(values, 0.25)),
        "p50": dollars(quantile(values, 0.50)),
        "p75": dollars(quantile(values, 0.75)),
        "p90": dollars(quantile(values, 0.90)),
    }


# ---------------------------------------------------------------------------
# recomputation
# ---------------------------------------------------------------------------

class Tally:
    def __init__(self):
        self.by_family: dict[str, list[float]] = {}
        self.by_family_employer: dict[tuple, list[float]] = {}
        self.by_family_metro: dict[tuple, list[float]] = {}
        self.by_family_rank: dict[tuple, list[float]] = {}
        self.by_employer: dict[str, list[float]] = {}
        self.by_metro: dict[str, list[float]] = {}
        self.counts: dict[str, int] = {}
        self.rows_read = 0
        self.rows_kept = 0
        self.mapped = 0
        self.per_case: dict[str, dict] = {}
        self.outcome: dict[str, str] = {}
        self.duplicate_rejections = 0

    def bump(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def recompute(paths) -> Tally:
    t = Tally()
    seen = set()
    for path in paths:
        for rec in read_records(path):
            t.rows_read += 1
            case = rec.get("CASE_NUMBER", "").strip()
            t.bump("visa:" + rec.get("VISA_CLASS", ""))
            if rec.get("VISA_CLASS", "").strip() != "H-1B":
                t.bump("filter:not_h1b")
                t.outcome[case] = "not_h1b"
                continue
            t.bump("status:" + rec.get("CASE_STATUS", ""))
            if rec.get("CASE_STATUS", "").strip() != "Certified":
                t.bump("filter:not_certified")
                t.outcome[case] = "not_certified"
                continue
            if rec.get("FULL_TIME_POSITION", "").strip().upper() != "Y":
                t.bump("filter:not_full_time")
                t.outcome[case] = "not_full_time"
                continue
            if case and case in seen:
                t.bump("filter:duplicate_case_number")
                t.duplicate_rejections += 1
                continue
            if case:
                seen.add(case)

            annual, unit, why = to_annual(rec.get("WAGE_RATE_OF_PAY_FROM", ""),
                                          rec.get("WAGE_UNIT_OF_PAY", ""))
            t.bump("unit:" + (unit or "(unreadable)"))
            t.bump("outcome:" + why)
            if annual is None:
                t.bump("filter:wage_unconvertible")
                t.outcome[case] = why
                continue
            t.outcome[case] = "ok"
            t.rows_kept += 1

            title = rec.get("JOB_TITLE", "").strip()
            fam = bucket_of(title)
            rank = rank_of(title)
            emp = short_employer(rec.get("EMPLOYER_NAME", ""))
            met = metro_for(rec.get("WORKSITE_CITY", ""), rec.get("WORKSITE_STATE", ""))
            if met != "Other / not mapped":
                t.mapped += 1

            t.per_case[case] = {"annual": int(annual), "unit": unit, "family": fam,
                                "level": rank, "employer": emp, "metro": met}
            t.by_family.setdefault(fam, []).append(annual)
            t.by_family_employer.setdefault((fam, emp), []).append(annual)
            t.by_family_metro.setdefault((fam, met), []).append(annual)
            t.by_family_rank.setdefault((fam, rank), []).append(annual)
            t.by_employer.setdefault(emp, []).append(annual)
            t.by_metro.setdefault(met, []).append(annual)
    return t


def claims_from(t: Tally) -> dict:
    """Flatten everything this checker computed into comparable key -> number pairs."""
    out: dict[str, int] = {"rows_read": t.rows_read, "rows_kept": t.rows_kept,
                           "metro_mapped_rows": t.mapped}
    for key, n in t.counts.items():
        out["count/" + key] = n
    for fam, vals in t.by_family.items():
        for k, v in summarise(vals).items():
            out[f"family/{fam}/{k}"] = v
    for (fam, emp), vals in t.by_family_employer.items():
        for k, v in summarise(vals).items():
            out[f"family_employer/{fam}/{emp}/{k}"] = v
    for (fam, met), vals in t.by_family_metro.items():
        for k, v in summarise(vals).items():
            out[f"family_metro/{fam}/{met}/{k}"] = v
    for (fam, rank), vals in t.by_family_rank.items():
        for k, v in summarise(vals).items():
            out[f"family_level/{fam}/{rank}/{k}"] = v
    for emp, vals in t.by_employer.items():
        for k, v in summarise(vals).items():
            out[f"employer/{emp}/{k}"] = v
    for met, vals in t.by_metro.items():
        for k, v in summarise(vals).items():
            out[f"metro/{met}/{k}"] = v
    return out


def claims_published(agg: dict) -> dict:
    """Every number the published aggregate asserts, in the same flat key space."""
    out: dict[str, int] = {
        "rows_read": agg["meta"]["rows_read"],
        "rows_kept": agg["meta"]["rows_kept"],
        "metro_mapped_rows": agg["meta"]["metro_mapped_rows"],
    }
    for name, n in agg["meta"]["filters"].items():
        out[f"count/filter:{name}"] = n
    for name, n in agg["meta"]["wage_conversion_outcomes"].items():
        out[f"count/outcome:{name}"] = n
    for name, n in agg["meta"]["wage_unit_counts_after_status_filters"].items():
        out[f"count/unit:{name}"] = n
    for name, n in agg["meta"]["case_status_of_h1b_rows"].items():
        out[f"count/status:{name}"] = n
    for name, n in agg["meta"]["visa_class_of_all_rows"].items():
        out[f"count/visa:{name}"] = n
    keys = ("n", "p10", "p25", "p50", "p75", "p90")
    for fam in agg["families"]:
        for k in keys:
            out[f"family/{fam['family']}/{k}"] = fam[k]
        for e in fam["employers"]:
            for k in keys:
                out[f"family_employer/{fam['family']}/{e['name']}/{k}"] = e[k]
        for m in fam["metros"]:
            for k in keys:
                out[f"family_metro/{fam['family']}/{m['name']}/{k}"] = m[k]
        for lv in fam["levels"]:
            for k in keys:
                out[f"family_level/{fam['family']}/{lv['name']}/{k}"] = lv[k]
    for e in agg["employers_all_families"]:
        for k in keys:
            out[f"employer/{e['name']}/{k}"] = e[k]
    for m in agg["metros_all_families"]:
        for k in keys:
            out[f"metro/{m['name']}/{k}"] = m[k]
    return out


def compare(published: dict, independent: dict) -> list[str]:
    problems = []
    missing = [k for k in published if k not in independent]
    for k in sorted(missing)[:20]:
        problems.append(f"the published file asserts {k} and this checker found no such group")
    for k, want in sorted(published.items()):
        if k in independent and independent[k] != want:
            problems.append(f"{k}: published {want}, independently recomputed {independent[k]}")
    return problems


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def run_fixture() -> int:
    expected = json.loads((ROOT / "fixtures" / "expected.json").read_text())
    t = recompute([ROOT / "fixtures" / "fixture.xlsx"])
    problems = []

    def want(label, got, exp):
        if got != exp:
            problems.append(f"{label}: recomputed {got}, ground truth {exp}")

    want("rows_read", t.rows_read, expected["rows_read"])
    want("rows_kept", t.rows_kept, expected["rows_kept"])
    want("trace", t.outcome, expected["trace"])
    want("duplicate rejections", t.duplicate_rejections, expected["duplicate_case_rejections"])
    want("kept rows", t.per_case, expected["kept_rows"])
    want("metro_mapped_rows", t.mapped, expected["metro_mapped_rows"])
    for name, n in expected["filters"].items():
        want(f"filter {name}", t.counts.get("filter:" + name, 0), n)
    for name, n in expected["wage_conversion_outcomes"].items():
        want(f"outcome {name}", t.counts.get("outcome:" + name, 0), n)
    for name, n in expected["wage_unit_counts_after_status_filters"].items():
        want(f"unit {name}", t.counts.get("unit:" + name, 0), n)
    for fam, exp in expected["families"].items():
        got = summarise(t.by_family.get(fam, []))
        want(f"family {fam}", {k: got[k] for k in exp}, exp)
    for emp, exp in expected["software_engineer_by_employer"].items():
        got = summarise(t.by_family_employer.get(("Software engineer", emp), []))
        want(f"software engineer at {emp}", {k: got[k] for k in exp}, exp)
    for met, exp in expected["software_engineer_by_metro"].items():
        got = summarise(t.by_family_metro.get(("Software engineer", met), []))
        want(f"software engineer in {met}", {k: got[k] for k in exp}, exp)
    for lvl, exp in expected["software_engineer_by_level"].items():
        got = summarise(t.by_family_rank.get(("Software engineer", lvl), []))
        want(f"software engineer at level {lvl}", {k: got[k] for k in exp}, exp)
    for emp, exp in expected["employers_all_families"].items():
        got = summarise(t.by_employer.get(emp, []))
        want(f"employer {emp}", {k: got[k] for k in exp}, exp)
    for met, exp in expected["metros_all_families"].items():
        got = summarise(t.by_metro.get(met, []))
        want(f"metro {met}", {k: got[k] for k in exp}, exp)

    for p in problems:
        print("  FAIL  " + p)
    if problems:
        print(f"{len(problems)} disagreement(s) with fixtures/expected.json")
        return 1
    print(f"  ok    independent recompute of the fixture matches expected.json on "
          f"{t.rows_read} rows, {t.rows_kept} kept, {len(t.by_family)} families")
    return 0


def run_source(a) -> int:
    agg = json.loads(pathlib.Path(a.against).read_text())
    t = recompute([pathlib.Path(p) for p in a.source])
    mine = claims_from(t)
    theirs = claims_published(agg)
    problems = compare(theirs, mine)
    payload = {
        "_": ("Written by tools/recheck.py, which shares no code with bands/. Every value "
              "here was recomputed from the raw filings by a second implementation. "
              "scripts/verify.sh compares this against data/aggregates.json on every run, "
              "which is how the published numbers stay checked when the raw download is "
              "not present."),
        "source_sha256": [s["sha256"] for s in agg["meta"]["sources"]],
        "claims": {k: mine[k] for k in sorted(theirs) if k in mine},
    }
    if a.write:
        pathlib.Path(a.write).write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    for p in problems[:40]:
        print("  FAIL  " + p)
    if problems:
        print(f"{len(problems)} disagreement(s) between the aggregator and this checker")
        return 1
    print(f"  ok    independently recomputed {len(theirs)} published figures from "
          f"{t.rows_read} raw rows; all agree")
    return 0


def run_compare(a) -> int:
    agg = json.loads(pathlib.Path(a.compare[0]).read_text())
    rec = json.loads(pathlib.Path(a.compare[1]).read_text())
    theirs = claims_published(agg)
    mine = rec["claims"]
    if [s["sha256"] for s in agg["meta"]["sources"]] != rec["source_sha256"]:
        print("  FAIL  the recheck was run against a different source file than the "
              "aggregate was built from")
        return 1
    problems = compare(theirs, mine)
    for p in problems[:40]:
        print("  FAIL  " + p)
    if problems:
        print(f"{len(problems)} disagreement(s)")
        return 1
    if len(mine) < len(theirs):
        print(f"  FAIL  the recheck covers only {len(mine)} of {len(theirs)} published figures")
        return 1
    print(f"  ok    all {len(theirs)} published figures match the independent recomputation")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", action="store_true")
    ap.add_argument("--source", nargs="+")
    ap.add_argument("--against", default="data/aggregates.json")
    ap.add_argument("--write", default="")
    ap.add_argument("--compare", nargs=2)
    a = ap.parse_args(argv)
    if a.fixture:
        return run_fixture()
    if a.compare:
        return run_compare(a)
    if a.source:
        return run_source(a)
    ap.error("give --fixture, --source or --compare")


if __name__ == "__main__":
    sys.exit(main())
