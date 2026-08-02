#!/usr/bin/env python3
"""Build fixtures/fixture.xlsx and fixtures/hostile.xlsx, deterministically.

The fixture is 26 filings whose correct aggregates were worked out by hand and written
into fixtures/expected.json. That file is the ground truth for the whole suite: the
pipeline is checked against it, and the independent checker is checked against it too. If
either agrees with the other but not with expected.json, both are wrong and the suite says
so, which is the point of not deriving expected.json from either of them.

The workbook is written by hand rather than by openpyxl because the reader has to survive
the encodings a real DOL file uses, and only a hand-written workbook can guarantee all of
them appear:

  * shared strings, the normal case
  * a rich-text shared string, split into two runs mid-word
  * an inline string (t="inlineStr")
  * a numeric cell with no t attribute
  * a formula cell with a cached string result (t="str")
  * a row that OMITS a cell entirely rather than writing it empty

Zip entry timestamps are pinned so two runs produce a byte-identical file.
"""

from __future__ import annotations

import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"

COLUMNS = [
    "CASE_NUMBER", "CASE_STATUS", "DECISION_DATE", "VISA_CLASS", "JOB_TITLE", "SOC_CODE",
    "FULL_TIME_POSITION", "TOTAL_WORKER_POSITIONS", "EMPLOYER_NAME", "WORKSITE_CITY",
    "WORKSITE_STATE", "WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_TO", "WAGE_UNIT_OF_PAY",
]

C, D, W, N = "Certified", "Denied", "Certified - Withdrawn", "Withdrawn"
H1B, E3 = "H-1B", "E-3 Australian"
DEC = "45930"   # 2025-09-30

# case, status, visa, title, full_time, employer, city, state, from, to, unit
ROWS = [
    # --- Software engineer family, ten kept rows, one per interesting wage unit ---
    ("F-001", C, H1B, "Software Engineer",                 "Y", "Acme Inc.",           "San Francisco", "CA", "100000", "140000", "Year"),
    ("F-002", C, H1B, "Senior Software Engineer",          "Y", "Acme Inc.",           "San Francisco", "CA", "200000", "260000", "Year"),
    ("F-003", C, H1B, "SOFTWARE ENGINEER II",              "Y", "ACME INC",            "San Francisco", "CA", "150000", "",       "Year"),
    ("F-004", C, H1B, "Sr. Software Engineer",             "Y", "Acme Inc.",           "Santa Clara",   "CA", "96.00",  "120.00", "Hour"),
    ("F-005", C, H1B, "Software Developer",                "Y", "Bolt Systems LLC",    "Austin",        "TX", "10000",  "12000",  "Month"),
    ("F-006", C, H1B, "Software Development Engineer",     "Y", "Bolt Systems LLC",    "Austin",        "TX", "2000",   "2500",   "Week"),
    ("F-007", C, H1B, "SDE II",                            "Y", "Bolt Systems, L.L.C.", "Austin",       "TX", "5000",   "6000",   "Bi-Weekly"),
    ("F-008", C, H1B, "Software Engineering",              "Y", "Bolt Systems LLC",    "Round Rock",    "TX", "110000", "110000", "Year"),
    ("F-009", C, H1B, "Software Engineer",                 "Y", "Cedar Labs",          "Nowhereville",  "KS", "80.00",  "90.00",  "Hourly"),
    ("F-010", C, H1B, "Software Programmer",               "Y", "Cedar Labs",          "Seattle",       "WA", "140000", "",       "Year"),
    # --- decoys: each would corrupt a family median if the reject clauses failed ---
    ("F-011", C, H1B, "Software Sales Engineer",           "Y", "Acme Inc.",           "San Francisco", "CA", "300000", "",       "Year"),
    ("F-012", C, H1B, "Software Engineering Manager",      "Y", "Acme Inc.",           "San Francisco", "CA", "400000", "",       "Year"),
    ("F-013", C, H1B, "QA Engineer",                       "Y", "Acme Inc.",           "San Francisco", "CA", "90000",  "",       "Year"),
    ("F-014", C, H1B, "Sales Engineer",                    "Y", "Bolt Systems LLC",    "Austin",        "TX", "250000", "",       "Year"),
    # --- rows the wage step must refuse, one per reason ---
    ("F-015", C, H1B, "Software Engineer",                 "Y", "Acme Inc.",           "San Francisco", "CA", "65",     "",       "Year"),
    ("F-016", C, H1B, "Software Engineer",                 "Y", "Acme Inc.",           "San Francisco", "CA", "120000", "",       ""),
    ("F-017", C, H1B, "Software Engineer",                 "Y", "Acme Inc.",           "San Francisco", "CA", "120000", "",       "Fortnight"),
    ("F-018", C, H1B, "Software Engineer",                 "Y", "Acme Inc.",           "San Francisco", "CA", "",       "",       "Year"),
    ("F-019", C, H1B, "Software Engineer",                 "Y", "Acme Inc.",           "San Francisco", "CA", "N/A",    "",       "Year"),
    # --- rows the row filters must refuse, one per filter ---
    ("F-020", D,   H1B, "Software Engineer",               "Y", "Acme Inc.",           "San Francisco", "CA", "500000", "",       "Year"),
    ("F-021", W,   H1B, "Software Engineer",               "Y", "Acme Inc.",           "San Francisco", "CA", "500000", "",       "Year"),
    ("F-022", C,   E3,  "Software Engineer",               "Y", "Acme Inc.",           "San Francisco", "CA", "500000", "",       "Year"),
    ("F-023", C,   H1B, "Software Engineer",               "N", "Acme Inc.",           "San Francisco", "CA", "60.00",  "",       "Hour"),
    ("F-001", C,   H1B, "Software Engineer",               "Y", "Acme Inc.",           "San Francisco", "CA", "999999", "",       "Year"),
    ("F-025", N,   H1B, "Software Engineer",               "Y", "Acme Inc.",           "San Francisco", "CA", "500000", "",       "Year"),
    # --- other families ---
    ("F-026", C, H1B, "Machine Learning Engineer",         "Y", "Cedar Labs",          "Seattle",       "WA", "250000", "",       "Year"),
    ("F-027", C, H1B, "ML Engineer",                       "Y", "Cedar Labs",          "Seattle",       "WA", "230000", "",       "Year"),
    ("F-028", C, H1B, "Senior Machine Learning Engineer",  "Y", "Cedar Labs",          "Bellevue",      "WA", "300000", "",       "Year"),
    ("F-029", C, H1B, "Data Scientist",                    "Y", "Cedar Labs",          "Seattle",       "WA", "180000", "",       "Year"),
    ("F-030", C, H1B, "Data Engineer",                     "Y", "Cedar Labs",          "Seattle",       "WA", "160000", "",       "Year"),
    ("F-031", C, H1B, "Applied Scientist",                 "Y", "Cedar Labs",          "Seattle",       "WA", "260000", "",       "Year"),
]

# Row index (1-based over ROWS) -> how that row's cells are encoded, so the reader is
# exercised on every representation a real file uses.
#   "rich"   : JOB_TITLE arrives as a two-run rich-text shared string, split mid-word
#   "inline" : WAGE_UNIT_OF_PAY arrives as t="inlineStr"
#   "number" : WAGE_RATE_OF_PAY_FROM arrives as a bare numeric cell, no t attribute
#   "formula": EMPLOYER_NAME arrives as t="str" with a cached result
#   "omit"   : the WAGE_RATE_OF_PAY_TO cell is left out of the row entirely
ENCODINGS = {1: "rich", 2: "omit", 4: "inline", 5: "number", 8: "omit", 9: "formula"}


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def col_letter(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        out = chr(65 + rem) + out
    return out


def build_sheet() -> tuple[str, list[str]]:
    """Return the sheet XML and the shared string table it refers to."""
    shared: list[str] = []
    shared_index: dict[str, int] = {}
    rich: set[int] = set()

    def sid(value: str, as_rich: bool = False) -> int:
        key = ("R" if as_rich else "S") + value
        if key not in shared_index:
            shared_index[key] = len(shared)
            shared.append(value)
            if as_rich:
                rich.add(len(shared) - 1)
        return shared_index[key]

    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             "<sheetData>"]

    cells = [f'<c r="{col_letter(i)}1" t="s"><v>{sid(name)}</v></c>'
             for i, name in enumerate(COLUMNS)]
    lines.append('<row r="1">' + "".join(cells) + "</row>")

    for n, row in enumerate(ROWS, start=1):
        (case, status, visa, title, ft, employer, city, state, wfrom, wto, unit) = row
        values = dict(zip(COLUMNS, [case, status, DEC, visa, title, "15-1252.00", ft, "1",
                                    employer, city, state, wfrom, wto, unit]))
        how = ENCODINGS.get(n, "")
        out = []
        for i, name in enumerate(COLUMNS):
            v = values[name]
            ref = f"{col_letter(i)}{n + 1}"
            if name == "WAGE_RATE_OF_PAY_TO" and how == "omit":
                continue                                  # cell is absent, not empty
            if v == "":
                continue                                  # a real file omits empties too
            if name == "JOB_TITLE" and how == "rich":
                out.append(f'<c r="{ref}" t="s"><v>{sid(v, as_rich=True)}</v></c>')
            elif name == "WAGE_UNIT_OF_PAY" and how == "inline":
                out.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc(v)}</t></is></c>')
            elif name == "WAGE_RATE_OF_PAY_FROM" and how == "number":
                out.append(f'<c r="{ref}"><v>{esc(v)}</v></c>')
            elif name == "EMPLOYER_NAME" and how == "formula":
                out.append(f'<c r="{ref}" t="str"><f>CONCATENATE(A1)</f><v>{esc(v)}</v></c>')
            else:
                out.append(f'<c r="{ref}" t="s"><v>{sid(v)}</v></c>')
        lines.append(f'<row r="{n + 1}">' + "".join(out) + "</row>")

    lines.append("</sheetData></worksheet>")
    sst = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
           f'count="{len(shared)}" uniqueCount="{len(shared)}">']
    for i, s in enumerate(shared):
        if i in rich:
            cut = max(1, len(s) // 2)
            sst.append(f"<si><r><t>{esc(s[:cut])}</t></r><r><t>{esc(s[cut:])}</t></r></si>")
        else:
            sst.append(f'<si><t xml:space="preserve">{esc(s)}</t></si>')
    sst.append("</sst>")
    return "".join(lines), "".join(sst)


CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
    "</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)
WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="LCA" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
    "</Relationships>"
)


def write_xlsx(path: pathlib.Path, sheet_xml: str, shared_xml: str) -> None:
    parts = {
        "[Content_Types].xml": CONTENT_TYPES,
        "_rels/.rels": ROOT_RELS,
        "xl/workbook.xml": WORKBOOK,
        "xl/_rels/workbook.xml.rels": WORKBOOK_RELS,
        "xl/sharedStrings.xml": shared_xml,
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts.items():
            # Fixed timestamp: two runs must produce the same bytes.
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, body)


HOSTILE_SHEET = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE worksheet [\n"
    '  <!ENTITY a "boomboomboomboomboomboomboomboom">\n'
    '  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    '  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
    "]>\n"
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&c;</t></is></c></row>'
    "</sheetData></worksheet>"
)


def main() -> int:
    FIXTURES.mkdir(exist_ok=True)
    sheet, shared = build_sheet()
    write_xlsx(FIXTURES / "fixture.xlsx", sheet, shared)
    write_xlsx(FIXTURES / "hostile.xlsx", HOSTILE_SHEET, shared)

    # A CSV twin of the same rows. Nothing in the pipeline reads it; it exists so a human
    # can diff the fixture against expected.json without unzipping anything.
    csv_lines = [",".join(COLUMNS)]
    for row in ROWS:
        (case, status, visa, title, ft, employer, city, state, wfrom, wto, unit) = row
        vals = [case, status, DEC, visa, title, "15-1252.00", ft, "1", employer, city,
                state, wfrom, wto, unit]
        csv_lines.append(",".join('"' + v.replace('"', '""') + '"' for v in vals))
    (FIXTURES / "fixture.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    print(f"fixture.xlsx: {len(ROWS)} rows, "
          f"{(FIXTURES / 'fixture.xlsx').stat().st_size} bytes")
    print(f"hostile.xlsx: {(FIXTURES / 'hostile.xlsx').stat().st_size} bytes")
    expected = FIXTURES / "expected.json"
    if expected.exists():
        json.loads(expected.read_text())
        print("expected.json parses; it is hand-written and is NOT regenerated here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
