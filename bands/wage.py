"""Turn a filed wage range plus its unit into one annual number, or refuse to.

This is the part of the pipeline that is easiest to get catastrophically wrong. An LCA
states a wage RANGE and a UNIT, and the unit column really does carry all five values in
the same file. FY2025 Q4 alone holds 109,568 Year rows, 8,612 Hour rows, 252 Month, 79
Week and 69 Bi-Weekly. Reading the number and ignoring the unit turns an $80/hour offer
into $80 a year, an error of 2080x, and the result still looks like a plausible column of
numbers because the yearly rows dominate the median.

So every conversion is explicit, every refusal is counted with a reason, and the counts
are published beside the figures.

Two decisions worth arguing with:

* **2080 hours a year.** 40 hours x 52 weeks, the conventional full-time year, with no
  deduction for holidays. It is what the wage-and-hour rules assume and what makes an
  hourly LCA comparable to a salaried one. Everything downstream is restricted to
  FULL_TIME_POSITION = Y, so annualising a part-timer's hourly rate as if it were a full
  year cannot happen here.
* **A floor of $15,080.** That is the federal minimum wage of $7.25 x 2080. A full-time
  filing annualising below it is a data-entry error, most often an hourly rate typed into
  a row marked Year. These are dropped and counted, never silently clamped.
"""

from __future__ import annotations

from dataclasses import dataclass

HOURS_PER_YEAR = 2080
WEEKS_PER_YEAR = 52
BIWEEKLY_PERIODS_PER_YEAR = 26
MONTHS_PER_YEAR = 12

# How many of each period make a year. Multiply the filed rate by this to annualise.
UNIT_FACTOR: dict[str, int] = {
    "YEAR": 1,
    "MONTH": MONTHS_PER_YEAR,
    "BI-WEEKLY": BIWEEKLY_PERIODS_PER_YEAR,
    "WEEK": WEEKS_PER_YEAR,
    "HOUR": HOURS_PER_YEAR,
}

# Spellings seen across OFLC file generations. The FY2025 files use the long forms; older
# ones used single letters. Anything not listed here is an unknown unit and the row is
# dropped rather than guessed at, because guessing "Year" is exactly the 2080x bug.
UNIT_ALIASES: dict[str, str] = {
    "Y": "YEAR", "YR": "YEAR", "YEARLY": "YEAR", "ANNUAL": "YEAR", "ANNUALLY": "YEAR",
    "M": "MONTH", "MTH": "MONTH", "MONTHLY": "MONTH",
    "BW": "BI-WEEKLY", "BI WEEKLY": "BI-WEEKLY", "BIWEEKLY": "BI-WEEKLY",
    "B": "BI-WEEKLY",
    "W": "WEEK", "WK": "WEEK", "WEEKLY": "WEEK",
    "H": "HOUR", "HR": "HOUR", "HOURLY": "HOUR",
}

FEDERAL_MINIMUM_HOURLY = 7.25
FLOOR = FEDERAL_MINIMUM_HOURLY * HOURS_PER_YEAR   # 15080.0
CEILING = 10_000_000.0

DROP_REASONS = (
    "blank_wage",
    "nonnumeric_wage",
    "blank_unit",
    "unknown_unit",
    "below_federal_minimum",
    "above_ceiling",
)


@dataclass(frozen=True)
class Converted:
    annual: float | None
    unit: str | None        # canonical unit, present even when the row is later dropped
    reason: str             # "ok", or one of DROP_REASONS


def canonical_unit(raw: str) -> str | None:
    """Canonical unit name, or None if the string is not a unit this code understands."""
    u = " ".join(str(raw).strip().upper().split())
    if not u:
        return None
    u = UNIT_ALIASES.get(u, u)
    return u if u in UNIT_FACTOR else None


def parse_amount(raw: str) -> float | None:
    """A filed money value as a float. Handles '150,000', '$150000', '75.50', ''."""
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace(",", "").replace("$", "").strip()
    if s.startswith("(") and s.endswith(")"):      # accounting negative
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def annualise(amount_raw: str, unit_raw: str) -> Converted:
    """Convert one filed rate to an annual figure, or say precisely why it cannot be."""
    unit = canonical_unit(unit_raw)
    amount = parse_amount(amount_raw)
    if amount is None:
        blank = not str(amount_raw).strip()
        return Converted(None, unit, "blank_wage" if blank else "nonnumeric_wage")
    if unit is None:
        blank = not str(unit_raw).strip()
        return Converted(None, None, "blank_unit" if blank else "unknown_unit")
    annual = amount * UNIT_FACTOR[unit]
    if annual < FLOOR:
        return Converted(None, unit, "below_federal_minimum")
    if annual > CEILING:
        return Converted(None, unit, "above_ceiling")
    return Converted(annual, unit, "ok")
