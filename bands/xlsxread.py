"""A streaming .xlsx row reader built only on the standard library.

Why not openpyxl: the offline verification suite has to run from a clean checkout on a box
where `pip install` into the system Python is blocked by PEP 668. A dependency that the
verify script cannot install is a verify script that skips a section, and a skipped section
reports the same success as one that ran.

The DOL disclosure file is 79 MB zipped and 501 MB of sheet XML, so nothing here holds the
sheet in memory. Shared strings are loaded once (15 MB) and the sheet is walked with
`iterparse`, clearing each row element as it is consumed.

Three details that silently corrupt a row if you get them wrong, all exercised by
fixtures/fixture.xlsx:

* A cell with an empty value is usually **omitted entirely** rather than written as empty.
  Reading cells positionally therefore shifts every later column left. Cells carry an `r`
  reference like `M12345`, and the column letters in it are the only reliable index.
* A string can arrive three ways: `t="s"` (index into sharedStrings), `t="inlineStr"`
  (`<is><t>` nested in the cell), or `t="str"` (a formula's cached string in `<v>`).
  Treating everything as shared turns "Hour" into whatever string happens to sit at index 0.
* A shared string can be rich text, `<si><r><t>Soft</t></r><r><t>ware</t></r></si>`.
  Reading only the first `<t>` truncates it.
"""

from __future__ import annotations

import re
import zipfile
from typing import Iterator
from xml.etree import ElementTree as ET

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_COL_RE = re.compile(r"^([A-Z]+)")


class XmlRejected(ValueError):
    """The document tried to declare a DTD or an entity."""


_GUARD_BYTES = 65536


class _Guarded:
    """A read-only stream that has already had its prolog inspected.

    defusedxml is the usual answer for hostile XML and is not available here: this package
    is deliberately stdlib-only so the verify suite runs on a system Python that blocks
    `pip install`. Hooking expat's entity handlers is the other route, and the C accelerated
    `ET.XMLParser` in CPython 3.12 exposes no `.parser` attribute to hook, so that path
    silently is not available either.

    Both attacks that matter for a zip someone else produced, external entity expansion
    (XXE) and entity self-reference (billion laughs), require an entity declaration, and XML
    permits those only inside a `doctypedecl`, which must appear before the root element.
    So refusing any part whose prolog contains `<!DOCTYPE` or `<!ENTITY` is sufficient. A
    legitimate spreadsheet part never carries one, and inside sheet data a literal `<` is
    always escaped, so this cannot fire on real content.

    Proven by `tests/test_xlsxread.py::test_doctype_rejected`, which builds a workbook with
    a billion-laughs DTD and asserts the reader raises rather than expands it.
    """

    def __init__(self, fh):
        head = fh.read(_GUARD_BYTES)
        for bad in (b"<!DOCTYPE", b"<!ENTITY"):
            if bad in head:
                raise XmlRejected(
                    f"XML part declares {bad.decode()[2:].lower()}; refusing to parse it"
                )
        self._head = head
        self._rest = fh
        self._off = 0

    def read(self, n=-1):
        if self._off >= len(self._head):
            return self._rest.read(n)
        if n is None or n < 0:
            out = self._head[self._off:] + self._rest.read()
            self._off = len(self._head)
            return out
        out = self._head[self._off:self._off + n]
        self._off += len(out)
        if len(out) < n:
            out += self._rest.read(n - len(out))
        return out


def _checked(data: bytes) -> bytes:
    for bad in (b"<!DOCTYPE", b"<!ENTITY"):
        if bad in data[:_GUARD_BYTES]:
            raise XmlRejected(f"XML part declares {bad.decode()[2:].lower()}; refusing to parse it")
    return data


def col_index(ref: str) -> int:
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26. Raises on a reference with no column letters."""
    m = _COL_RE.match(ref)
    if not m:
        raise ValueError(f"cell reference {ref!r} has no column letters")
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        info = zf.getinfo("xl/sharedStrings.xml")
    except KeyError:
        return []
    out: list[str] = []
    with zf.open(info) as fh:
        for event, el in ET.iterparse(_Guarded(fh), events=("end",)):
            if el.tag == NS_MAIN + "si":
                # Concatenate every <t> below this <si>, which is what makes rich text
                # survive. itertext() would also pick up <rPh> phonetic runs, which are
                # duplicated pronunciation hints and must not be included.
                out.append("".join(t.text or "" for t in el.iter(NS_MAIN + "t")))
                el.clear()
    return out


def _sheet_path(zf: zipfile.ZipFile, sheet: str | None) -> str:
    wb = ET.fromstring(_checked(zf.read("xl/workbook.xml")))
    rels = ET.fromstring(_checked(zf.read("xl/_rels/workbook.xml.rels")))
    target_by_id = {r.get("Id"): r.get("Target") for r in rels.iter(NS_PKG_REL + "Relationship")}
    sheets = list(wb.iter(NS_MAIN + "sheet"))
    if not sheets:
        raise ValueError("workbook declares no sheets")
    chosen = sheets[0]
    if sheet is not None:
        for s in sheets:
            if s.get("name") == sheet:
                chosen = s
                break
        else:
            names = ", ".join(s.get("name", "?") for s in sheets)
            raise KeyError(f"no sheet named {sheet!r}; workbook has: {names}")
    target = target_by_id[chosen.get(NS_REL + "id")]
    if target.startswith("/"):
        return target.lstrip("/")
    return "xl/" + target.lstrip("./")


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        node = cell.find(NS_MAIN + "is")
        return "" if node is None else "".join(t.text or "" for t in node.iter(NS_MAIN + "t"))
    v = cell.find(NS_MAIN + "v")
    if v is None or v.text is None:
        return ""
    raw = v.text
    if kind == "s":
        idx = int(raw)
        if idx >= len(shared):
            raise IndexError(f"shared string index {idx} out of range ({len(shared)} strings)")
        return shared[idx]
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    # kind in (None, "n", "str", "e"): the cached text is already what we want. A stored
    # number keeps its literal form, so 74000 stays "74000" and does not become "74000.0".
    return raw


def rows(path, sheet: str | None = None) -> Iterator[list[str]]:
    """Yield each sheet row as a list of cell strings, padded to the widest row so far."""
    with zipfile.ZipFile(path) as zf:
        shared = _shared_strings(zf)
        with zf.open(_sheet_path(zf, sheet)) as fh:
            width = 0
            for event, el in ET.iterparse(_Guarded(fh), events=("end",)):
                if el.tag != NS_MAIN + "row":
                    continue
                out: list[str] = []
                pos = 0
                for cell in el.findall(NS_MAIN + "c"):
                    ref = cell.get("r")
                    at = col_index(ref) if ref else pos
                    if at < len(out):
                        raise ValueError(f"cells out of order at {ref}")
                    out.extend([""] * (at - len(out)))
                    out.append(_cell_text(cell, shared))
                    pos = at + 1
                width = max(width, len(out))
                out.extend([""] * (width - len(out)))
                el.clear()
                yield out


def records(path, sheet: str | None = None) -> Iterator[dict[str, str]]:
    """Yield each data row as a dict keyed by the header row's column names."""
    it = rows(path, sheet)
    try:
        header = [h.strip() for h in next(it)]
    except StopIteration:
        return
    for r in it:
        if not any(c.strip() for c in r):
            continue
        rec = {}
        for i, name in enumerate(header):
            if name:
                rec[name] = r[i] if i < len(r) else ""
        yield rec
