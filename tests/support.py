import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "fixture.xlsx"
HOSTILE = ROOT / "fixtures" / "hostile.xlsx"
EXPECTED = json.loads((ROOT / "fixtures" / "expected.json").read_text(encoding="utf-8"))
FIXTURE_MIN_N = 2
