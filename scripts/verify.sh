#!/usr/bin/env bash
# Verification for salary-bands.
#
# The output is a set of salary percentiles that somebody might negotiate against, so the bar is
# "could this number be wrong without anyone noticing". The layers are aimed at that:
#
#   1  the unit suite, every assertion paired with a negative control
#   2  tools/recheck.py, which imports nothing from bands/ and recomputes the published figures
#      from the raw rows
#   3  that independence checked by inspecting the import graph, not by grepping
#   4  the wage-unit conversion, checked exactly on a fixture holding every unit
#   5  the permutation control: shuffle the employer labels and the spread must collapse
#   6  the page rebuilt and compared byte for byte
#   7  sabotages, each proved to have applied before its result counts
#   8  the README's numbers regenerated from the aggregates
#   9  hygiene, including that the 79 MB raw download is never tracked
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"
PY=python3

pass=0; fail=0
ok()  { printf '  ok    %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n' "$1"; fail=$((fail+1)); }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "0. environment"
$PY --version | sed 's/^/        /'
ok "python present"

echo
echo "1. unit suite"
# -t . because the tests use relative imports; without it discovery fails with an ImportError
# that says nothing about whether the code works.
if $PY -m unittest discover -s tests -t . >"$TMP/unit.log" 2>&1; then
  ok "$(grep -oE 'Ran [0-9]+ tests' "$TMP/unit.log" | grep -oE '[0-9]+') unit tests passed"
else
  bad "unit suite"; grep -E 'FAIL:|Error|assert' "$TMP/unit.log" | head -20 | sed 's/^/        /'
fi

echo
echo "2. the fixture recomputed by code that shares nothing with the aggregator"
if $PY tools/recheck.py --fixture >"$TMP/recheck.log" 2>&1; then
  tail -2 "$TMP/recheck.log" | sed 's/^/        /'
  ok "independent recompute agrees on the fixture"
else
  bad "the independent recompute disagrees"; tail -6 "$TMP/recheck.log" | sed 's/^/        /'
fi

echo
echo "3. the checker really is independent"
if $PY - <<'PY' >"$TMP/imports.log" 2>&1; then
import ast, pathlib, sys
tree = ast.parse(pathlib.Path("tools/recheck.py").read_text())
bad = []
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        bad += [a.name for a in node.names if a.name.split(".")[0] == "bands"]
    elif isinstance(node, ast.ImportFrom):
        if (node.module or "").split(".")[0] == "bands":
            bad.append(node.module)
if bad:
    sys.exit(f"recheck.py imports from bands/: {bad}")
print("recheck.py imports nothing from bands/")
PY
  sed 's/^/        /' "$TMP/imports.log"; ok "the checker shares no code with the aggregator"
else
  bad "$(cat "$TMP/imports.log")"
fi

echo
echo "4. every wage unit converts to the same annual figure"
# The single most dangerous field in this data. An hourly wage read as annual is off by 2,080x,
# and the error is silent: the number still looks like a salary.
if $PY - <<'PY' >"$TMP/wage.log" 2>&1; then
import sys
sys.path.insert(0, ".")
from bands.wage import annualise
cases = [("100", "Hour", 208000.0), ("4000", "Week", 208000.0),
         ("8000", "Bi-Weekly", 208000.0), ("17333.3333", "Month", 208000.0),
         ("208000", "Year", 208000.0)]
for value, unit, want in cases:
    c = annualise(value, unit)
    assert c.reason == "ok", f"{value} {unit} was dropped: {c.reason}"
    assert abs(c.annual - want) < 1.0, f"{value} {unit} -> {c.annual}, expected {want}"
# The control: an unknown unit must be REFUSED, not guessed. Guessing is exactly how an hourly
# wage silently becomes a salary 2,080 times too large.
u = annualise("100", "Fortnight")
assert u.annual is None and u.reason == "unknown_unit", u
# And a value below the federal minimum is dropped rather than published as a real band.
low = annualise("1", "Hour")
assert low.annual is None and low.reason == "below_federal_minimum", low
print(f"{len(cases)} units convert exactly; unknown units and sub-minimum wages are refused")
PY
  sed 's/^/        /' "$TMP/wage.log"; ok "wage-unit conversion is exact and refuses the unknown"
else
  bad "wage conversion is wrong"; tail -5 "$TMP/wage.log" | sed 's/^/        /'
fi

echo
echo "5. the permutation control: employer identity carries real signal"
# Shuffle which employer each filing belongs to and the spread of employer medians must collapse.
# If it does not, the published spread is an artefact of sample sizes rather than a fact about
# who pays what, and every employer figure on the page is noise.
if $PY - <<'PY' >"$TMP/perm.log" 2>&1; then
import json
a = json.load(open("data/aggregates.json"))
c = a["employer_permutation_control"]
real = c["real_p10_to_p90_spread_of_employer_medians"]
shuf = c["shuffled_p10_to_p90_spread_of_employer_medians"]
assert real > 0 and shuf > 0, c
ratio = real / shuf
assert ratio > 2.0, (
    f"the real spread ({real:,.0f}) is only {ratio:.1f}x the shuffled one ({shuf:,.0f}); "
    "employer identity is not carrying enough signal to publish per-employer bands")
print(f"real spread {real:,.0f} vs shuffled {shuf:,.0f} = {ratio:.1f}x, "
      f"over {c['employers_published']} employers (seed {c['seed']})")
PY
  sed 's/^/        /' "$TMP/perm.log"; ok "shuffling employers collapses the spread, as it must"
else
  bad "the permutation control does not separate signal from noise"
  tail -4 "$TMP/perm.log" | sed 's/^/        /'
fi

echo
echo "6. the page is rebuildable and matches its committed data"
if [ -f docs/index.html ]; then cp docs/index.html "$TMP/page.html"; fi
if $PY scripts/build_docs.py >"$TMP/docs.log" 2>&1; then
  sed 's/^/        /' "$TMP/docs.log"
  if [ -f "$TMP/page.html" ] && cmp -s docs/index.html "$TMP/page.html"; then
    ok "docs/index.html is exactly what the committed aggregates produce"
  elif [ -f "$TMP/page.html" ]; then
    bad "docs/index.html differs from a fresh build"; cp "$TMP/page.html" docs/index.html
  else
    ok "docs/index.html built (no previous copy)"
  fi
else
  bad "the page could not be built"; tail -5 "$TMP/docs.log" | sed 's/^/        /'
fi

echo
echo "7. self-attack"
attack() {
  local name="$1" file="$2" old="$3" new="$4"
  local dir="$TMP/attack-$name"
  rm -rf "$dir"; mkdir -p "$dir"
  tar -cf - --exclude=.git --exclude=__pycache__ -C "$ROOT" . | tar -xf - -C "$dir"
  if ! $PY - "$dir/$file" "$old" "$new" <<'PY'
import sys, pathlib
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path); t = p.read_text()
if old not in t:
    sys.exit(f"SABOTAGE DID NOT APPLY: {old!r} absent from {path}")
if old == new:
    sys.exit("SABOTAGE IS A NO-OP")
p.write_text(t.replace(old, new, 1))
PY
  then
    bad "sabotage \"$name\" did not apply, so it proves nothing"; return
  fi
  local urc rrc
  set +e
  ( cd "$dir" && $PY -m unittest discover -s tests -t . ) >"$TMP/$name-unit.log" 2>&1; urc=$?
  ( cd "$dir" && $PY tools/recheck.py --fixture ) >"$TMP/$name-re.log" 2>&1; rrc=$?
  set -e
  printf '        sabotage "%s": unit exit %s, independent-recheck exit %s\n' "$name" "$urc" "$rrc"
  if [ "$urc" -ne 0 ] || [ "$rrc" -ne 0 ]; then
    ok "sabotage \"$name\" is caught"
  else
    bad "sabotage \"$name\" passed everything, so nothing tests this behaviour"
  fi
}

attack "hourly-read-as-annual" "bands/wage.py" \
  '"HOUR": HOURS_PER_YEAR,' '"HOUR": 1,'
attack "unknown-unit-guessed-as-yearly" "bands/wage.py" \
  "    return u if u in UNIT_FACTOR else None" "    return u if u in UNIT_FACTOR else \"YEAR\""
attack "denied-filings-counted" "bands/aggregate.py" \
  'if rec.get("CASE_STATUS", "").strip() != "Certified":' \
  'if False and rec.get("CASE_STATUS", "").strip() != "Certified":'

echo
echo "8. the README's numbers come from the aggregates"
if $PY - <<'PY' >"$TMP/readme.log" 2>&1; then
import json, pathlib
text = pathlib.Path("README.md").read_text()
assert "TODO" not in text and "FIXME" not in text, "README contains a placeholder"
a = json.load(open("data/aggregates.json"))
m = a["meta"]
for n in (m["rows_read"], m["rows_kept"]):
    assert f"{n:,}" in text or str(n) in text, f"README does not carry the row count {n}"
c = a["employer_permutation_control"]
assert f"{c['real_p10_to_p90_spread_of_employer_medians']:,}" in text, \
    "README does not carry the permutation control's real spread"
print(f"README carries the row counts ({m['rows_read']:,} read, {m['rows_kept']:,} kept) "
      f"and the permutation control")
PY
  sed 's/^/        /' "$TMP/readme.log"; ok "README numbers regenerated and match"
else
  bad "README numbers are stale"; tail -5 "$TMP/readme.log" | sed 's/^/        /'
fi

echo
echo "9. no secrets, no personal data, and the raw download is not tracked"
if $PY tools/leakcheck.py --selftest >"$TMP/self.log" 2>&1; then
  tail -1 "$TMP/self.log" | sed 's/^/        /'
  ok "the leak checker can detect its own canaries, including one inside an xlsx"
else
  bad "the leak checker cannot detect its canaries, so a clean scan means nothing"
  tail -5 "$TMP/self.log" | sed 's/^/        /'
fi
if $PY tools/leakcheck.py >"$TMP/leak.log" 2>&1; then
  tail -1 "$TMP/leak.log" | sed 's/^/        /'
  ok "no credential, email, home path or NUL byte in any tracked file"
else
  bad "the leak checker found something"; tail -6 "$TMP/leak.log" | sed 's/^/        /'
fi
if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  # `|| true` on the loop, and the test written so the loop's exit status is never the result of
  # its LAST size comparison. Without it the loop returns 1 whenever the final file happens to be
  # small, which under `pipefail` killed the script silently after the last ok line and produced
  # exit 1 with every check passing and no summary printed.
  big=$(git -C "$ROOT" ls-files | while read -r f; do
          if [ -f "$f" ]; then
            s=$(stat -c%s "$f" 2>/dev/null || echo 0)
            if [ "$s" -gt 2000000 ]; then echo "$f ($s bytes)"; fi
          fi
        done || true)
  if [ -z "$big" ]; then ok "no tracked file over 2 MB; the 79 MB download stays out"
  else bad "a large file is tracked"; printf '%s\n' "$big" | head -3 | sed 's/^/        /'; fi
  pyc=$(git -C "$ROOT" ls-files | grep -c '__pycache__' || true)
  if [ "$pyc" = "0" ]; then ok "no build output tracked"
  else bad "$pyc __pycache__ file(s) tracked"; fi
else
  bad "not a git repo"
fi

echo
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ] || { echo "VERIFY FAILED"; exit 1; }
echo "VERIFY OK"
