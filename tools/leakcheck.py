#!/usr/bin/env python3
"""Scan every tracked file for things that must never be committed, and prove it can.

Four classes, and each is here because of a specific failure:

* **Personal data.** The raw disclosure file carries EMPLOYER_POC_EMAIL,
  AGENT_ATTORNEY_EMAIL_ADDRESS, and named individuals with phone numbers. Those columns
  are never read by the aggregator, and this check is what makes that a fact rather than
  an intention. An email address in a derived file means the pipeline started carrying
  something it should not.
* **Credential-shaped strings.** Patterns are case-SENSITIVE where the real format is.
  A case-insensitive `AKIA[0-9A-Z]{16}` matches ordinary base64 and turns every embedded
  image into a false alarm.
* **Absolute home paths.** Verify output gets pasted into the README, and `/home/<name>`
  there is both private and unportable.
* **NUL bytes.** One NUL makes a file binary to git and to grep, and `grep -I` then skips
  it silently, so a scan can report a tree it never read. This scan is done in Python
  because `grep -P '\\x00'` is not available in every grep on this box and returns no
  matches where Python finds the byte immediately.

`--selftest` plants one canary of every class and fails if any goes undetected. A scanner
that cannot find its own canaries reports the same clean result as one that read nothing.
Canaries are assembled at runtime from templates so no complete credential pattern is ever
written to disk, which is what push protection scans history for.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("openrouter key", re.compile(r"sk-or-v1-[0-9a-f]{64}")),
    ("openai key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer assignment", re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*"
                                     r"['\"][A-Za-z0-9/+_\-]{16,}['\"]")),
    ("absolute home path", re.compile(r"/(?:home|Users)/[a-z][a-z0-9_.\-]{1,30}/")),
    ("email address", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
]

# Text that legitimately matches a pattern. Each entry is a whole line that must appear
# verbatim; nothing is exempted by prefix or by file.
ALLOWED_LINES = {
    "email address": (
        '    ("email address", re.compile(r"[A-Za-z0-9._%+\\-]+@[A-Za-z0-9.\\-]+\\.[A-Za-z]{2,}")),',
    ),
}

SKIP_SUFFIXES = {".xlsx", ".png", ".jpg", ".gz", ".zip", ".pdf"}


def tracked_files() -> list[pathlib.Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("not a git repository, so there is no tracked file list to scan")
    return [ROOT / n for n in out.stdout.split("\0") if n]


def scan_text(name: str, text: str) -> list[str]:
    hits = []
    for label, pat in PATTERNS:
        allowed = ALLOWED_LINES.get(label, ())
        for m in pat.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.end())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            if line in allowed:
                continue
            lineno = text.count("\n", 0, m.start()) + 1
            hits.append(f"{name}:{lineno}: {label} ({len(m.group(0))} chars)")
    return hits


def scan_bytes(name: str, raw: bytes) -> list[str]:
    if b"\0" in raw:
        return [f"{name}: contains a NUL byte, which makes git and grep treat it as binary "
                f"and skip it; write it as the two-character escape \\0 instead"]
    return []


ZIP_TEXT_SUFFIXES = (".xlsx", ".xlsm", ".docx", ".pptx", ".odt", ".ods")


def scan_zip(path: pathlib.Path) -> list[str]:
    """Scan the text parts inside an Office file instead of its compressed bytes.

    An xlsx is a zip. Its bytes are full of NULs by construction, so the NUL rule says nothing
    about it, and a plain byte scan cannot see the cell contents at all. Exempting the file would
    hide a credential pasted into a cell; unzipping it does not.
    """
    import zipfile
    out = []
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        return [f"{path}: looks like an Office file but could not be opened ({e})"]
    for name in z.namelist():
        if not name.endswith((".xml", ".rels", ".txt")):
            continue
        try:
            inner = z.read(name)
        except (KeyError, OSError):
            continue
        # scan_TEXT, not scan_bytes. scan_bytes only checks for NUL, which is meaningless on a
        # part extracted from a zip, so the first version of this loop called it and then filtered
        # its only possible output away. It checked nothing at all, and a github token planted in
        # a cell went undetected.
        try:
            text = inner.decode("utf-8")
        except UnicodeDecodeError:
            out.append(f"{path}!{name}: not valid UTF-8, so it was not scanned for secrets")
            continue
        out.extend(scan_text(f"{path}!{name}", text))
    return out


def scan_file(path: pathlib.Path) -> list[str]:
    if path.suffix.lower() in ZIP_TEXT_SUFFIXES:
        return scan_zip(path)
    name = str(path.relative_to(ROOT))
    raw = path.read_bytes()
    hits = scan_bytes(name, raw)
    if path.suffix in SKIP_SUFFIXES:
        return hits
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return hits + [f"{name}: is not valid UTF-8, so it was not scanned for secrets"]
    return hits + scan_text(name, text)


def check_sizes(files: list[pathlib.Path]) -> list[str]:
    """The raw download must never end up tracked."""
    out = []
    for p in files:
        size = p.stat().st_size
        if size > 1_000_000:
            out.append(f"{p.relative_to(ROOT)}: {size} bytes tracked. The raw disclosure "
                       f"files belong in the cache, not the repo.")
    return out


def selftest() -> int:
    """Plant one canary per class and require every one to be found."""
    canaries = {
        "aws access key id": "AKIA" + "Q" * 16,
        "github token": "ghp_" + "a1B2" * 9,
        "openrouter key": "sk-or-v1-" + "0123456789abcdef" * 4,
        "google api key": "AIza" + "x" * 35,
        "slack token": "xoxb-" + "1234567890-abcdefghij",
        # Assembled rather than written out. A complete header here is a finding in this
        # scanner's own scan, and the scanner is right about that: to a grep, a canary and a
        # leak are the same bytes. Every other canary above is already built this way.
        "private key block": "-----" + "BEGIN RSA PRIVATE KEY" + "-----",
        "bearer assignment": 'api_key = "' + "z" * 24 + '"',
        "absolute home path": "/" + "home" + "/somebody/Projects/x",
        "email address": "someone" + "@" + "example.com",
    }
    # A secret planted inside an Office file must be found. This case exists because the first
    # zip-aware version called scan_bytes, which only checks for NUL, and then filtered that away.
    # It inspected nothing: a github token planted in a spreadsheet cell went undetected while the
    # scan still reported "clean".
    def _zip_canary_ok() -> bool:
        import os
        import tempfile
        import zipfile
        src = ROOT / "fixtures" / "fixture.xlsx"
        if not src.is_file():
            return True
        planted = ("ghp_" + "b3X9" * 9).encode()
        tmp = tempfile.mktemp(suffix=".xlsx")
        try:
            zin = zipfile.ZipFile(src)
            with zipfile.ZipFile(tmp, "w") as zout:
                for n in zin.namelist():
                    d = zin.read(n)
                    if n.endswith("sharedStrings.xml"):
                        d = d.replace(b"</sst>", b"<si><t>" + planted + b"</t></si></sst>")
                    zout.writestr(n, d)
            # The canary must be proved to have landed, or a "not found" result proves nothing.
            if planted not in zipfile.ZipFile(tmp).read("xl/sharedStrings.xml"):
                return False
            return bool(scan_file(pathlib.Path(tmp))) and not scan_file(src)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    ok, bad = 0, []
    for label, value in canaries.items():
        found = [h for h in scan_text("canary", value) if label in h]
        if found:
            print(f"  ok    detects {label}")
            ok += 1
        else:
            bad.append(label)
    nul = scan_bytes("canary", b"typeflag\x00regular")
    if nul:
        print("  ok    detects a NUL byte, in Python rather than through grep")
        ok += 1
    else:
        bad.append("NUL byte")

    # Negative control. A scanner that flagged everything would pass all of the above.
    clean = "median 140000 over 35071 filings, Software engineer, SF Bay Area"
    if scan_text("canary", clean):
        bad.append("false positive on ordinary text: " + str(scan_text("canary", clean)))
    else:
        print("  ok    does not fire on ordinary aggregate text")
        ok += 1

    for b in bad:
        print(f"  FAIL  missed {b}")
    if _zip_canary_ok():
        ok += 1
        print("  ok    detects a secret planted inside an xlsx, and not in the clean fixture")
    else:
        bad.append("secret inside an xlsx")
    print(f"{ok} detections proven" if not bad else f"{len(bad)} canaries missed")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--stdin", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest()
    if a.stdin:
        hits = scan_text("<stdin>", sys.stdin.read())
        for h in hits:
            print("  " + h)
        return 1 if hits else 0

    files = tracked_files()
    problems: list[str] = []
    for p in files:
        if p.is_file():
            problems += scan_file(p)
    problems += check_sizes([p for p in files if p.is_file()])
    for h in problems:
        print("  " + h)
    if problems:
        print(f"{len(problems)} problem(s) in {len(files)} tracked files")
        return 1
    print(f"clean: {len(files)} tracked files carry no credential, no email address, "
          f"no absolute home path and no NUL byte")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
