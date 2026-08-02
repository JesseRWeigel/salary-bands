"""Bucket a free-text JOB_TITLE into a role family and a level.

JOB_TITLE is whatever the employer typed. FY2025 Q4 has 33,840 distinct strings across
118,580 filings, including "SOFTWARE ENGINEER", "Sr. Software Engineer", "SOFTWARE DEV
ENGINEER II" and "MANAGER JC50 - COMPUTER SYSTEMS ENGINEERS/ARCHITECTS". Any bucketing is
a judgement call, so the rule is written out in full below and the aggregate output
publishes, for every bucket, how many rows it caught and the raw titles that fed it. A
reader who disagrees with a merge can see exactly what was merged.

THE RULE, in order. The first clause that matches wins.

1. Normalise. Uppercase, then replace every character outside A-Z and 0-9 with a space and
   collapse runs of spaces. "Sr. Software Engineer, II" and "SR SOFTWARE ENGINEER II"
   become the same token list.
2. Expand token aliases (SR to SENIOR, SDE to SOFTWARE DEVELOPMENT ENGINEER, ML to MACHINE
   LEARNING, and so on; the full map is ALIASES below). One token can expand to several.
3. Pull out the level. PRINCIPAL, STAFF, LEAD, SENIOR, JUNIOR/ENTRY, then a roman or arabic
   numeral I to V. First one found in that precedence order is the level; the rest of the
   tokens stay in place so "SENIOR SOFTWARE ENGINEER" still buckets as software.
4. Reject clauses, checked BEFORE any family clause:
   - a management token (MANAGER, DIRECTOR, HEAD, VP, PRESIDENT, CHIEF, SUPERVISOR) sends
     the row to "Engineering management" if it also carries an engineering word, otherwise
     to "unmatched". A median for an IC family must not contain managers.
   - SALES sends the row to "unmatched". "SOFTWARE SALES ENGINEER" contains both SOFTWARE
     and ENGINEER and is not a software engineering job.
   - QA, SDET, TEST, or QUALITY ASSURANCE sends the row to "QA / test engineer", a family
     of its own rather than into software engineering.
5. Family clauses, in this order:
   - Machine learning engineer: MACHINE and LEARNING present, plus ENGINEER.
   - Applied / research scientist (ML): MACHINE+LEARNING or ARTIFICIAL+INTELLIGENCE, plus
     SCIENTIST or RESEARCHER.
   - Data scientist: DATA and SCIENTIST.
   - Data engineer: DATA and ENGINEER.
   - Data analyst: DATA and ANALYST.
   - DevOps / SRE / platform: DEVOPS, or SITE+RELIABILITY, or PLATFORM+ENGINEER, or
     INFRASTRUCTURE+ENGINEER, or CLOUD+ENGINEER.
   - Security engineer: SECURITY or CYBERSECURITY, plus ENGINEER.
   - Software engineer: SOFTWARE present, plus one of ENGINEER, ENGINEERING, DEVELOPER,
     DEVELOPMENT, ARCHITECT, PROGRAMMER.
   - Research / applied scientist (unspecified): SCIENTIST plus RESEARCH or APPLIED,
     where the title does not say machine learning. Amazon's "Applied Scientist" is an ML
     job and lands here, because the title alone does not say so; that is a known merge.
   - Product manager: PRODUCT and MANAGER. (Reached only when clause 4 sent it here
     deliberately; see MANAGEMENT_EXEMPT.)
   - Anything else is "unmatched" and is counted, never quietly assigned.

What this rule deliberately does NOT do is merge across families by SOC code. SOC_CODE is
the government's occupation code and is coarser than the title (a machine learning engineer
and a back-end engineer share 15-1252.00), so it cannot separate the buckets this project
exists to separate. It is reported alongside as a sanity check, not used for bucketing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

# One raw token expands to this tuple of tokens. Applied once, not recursively.
ALIASES: dict[str, tuple[str, ...]] = {
    "SR": ("SENIOR",),
    "SNR": ("SENIOR",),
    "JR": ("JUNIOR",),
    "MGR": ("MANAGER",),
    "ENGR": ("ENGINEER",),
    "ENGG": ("ENGINEER",),
    "ENG": ("ENGINEER",),
    "DEV": ("DEVELOPER",),
    "SW": ("SOFTWARE",),
    "SDE": ("SOFTWARE", "DEVELOPMENT", "ENGINEER"),
    "SWE": ("SOFTWARE", "ENGINEER"),
    "SDET": ("SDET",),
    "ML": ("MACHINE", "LEARNING"),
    "MLE": ("MACHINE", "LEARNING", "ENGINEER"),
    "AI": ("ARTIFICIAL", "INTELLIGENCE"),
    "SRE": ("SITE", "RELIABILITY", "ENGINEER"),
    "QA": ("QA",),
    "IT": ("IT",),
    "SYS": ("SYSTEMS",),
    "ADMIN": ("ADMINISTRATOR",),
    "ARCH": ("ARCHITECT",),
    "SCI": ("SCIENTIST",),
    "PRIN": ("PRINCIPAL",),
    "ASSOC": ("ASSOCIATE",),
    "ASST": ("ASSISTANT",),
}

# Precedence order matters: "SENIOR STAFF ENGINEER" is Staff, not Senior.
LEVEL_TOKENS: tuple[tuple[str, str], ...] = (
    ("DISTINGUISHED", "Distinguished"),
    ("FELLOW", "Fellow"),
    ("PRINCIPAL", "Principal"),
    ("STAFF", "Staff"),
    ("LEAD", "Lead"),
    ("SENIOR", "Senior"),
    ("JUNIOR", "Junior/entry"),
    ("ENTRY", "Junior/entry"),
    ("GRADUATE", "Junior/entry"),
    ("INTERN", "Junior/entry"),
)
NUMERAL_LEVELS: dict[str, str] = {
    "I": "Level I", "1": "Level I",
    "II": "Level II", "2": "Level II",
    "III": "Level III", "3": "Level III",
    "IV": "Level IV", "4": "Level IV",
    "V": "Level V", "5": "Level V",
}

MANAGEMENT_TOKENS = frozenset({
    "MANAGER", "DIRECTOR", "HEAD", "VP", "PRESIDENT", "CHIEF", "SUPERVISOR", "CTO", "CEO",
})
# PRODUCT MANAGER is a management title that is also its own occupation and is reported as
# a comparison family, so it is let past the management reject clause.
MANAGEMENT_EXEMPT = ("PRODUCT",)

ENGINEERING_WORDS = frozenset({
    "SOFTWARE", "ENGINEER", "ENGINEERING", "DEVELOPER", "DEVELOPMENT", "DATA", "MACHINE",
    "PLATFORM", "INFRASTRUCTURE", "DEVOPS", "SYSTEMS", "TECHNOLOGY", "TECHNICAL",
})

UNMATCHED = "unmatched"
FAMILY_ORDER = (
    "Machine learning engineer",
    "Applied / research scientist (ML, AI)",
    "Data scientist",
    "Data engineer",
    "Data analyst",
    "DevOps / SRE / platform engineer",
    "Security engineer",
    "Software engineer",
    "Research / applied scientist (unspecified)",
    "Product manager",
    "QA / test engineer",
    "Engineering management",
    UNMATCHED,
)


@dataclass(frozen=True)
class Role:
    family: str
    level: str
    tokens: tuple[str, ...]


def tokenise(title: str) -> tuple[str, ...]:
    """Uppercase, strip punctuation, expand aliases. Step 1 and 2 of the rule."""
    flat = _NON_ALNUM.sub(" ", str(title).upper()).strip()
    out: list[str] = []
    for tok in flat.split():
        out.extend(ALIASES.get(tok, (tok,)))
    return tuple(out)


def level_of(tokens: tuple[str, ...]) -> str:
    """Step 3. Returns a level name or 'Unstated'."""
    have = set(tokens)
    for token, name in LEVEL_TOKENS:
        if token in have:
            return name
    for tok in tokens:
        if tok in NUMERAL_LEVELS:
            return NUMERAL_LEVELS[tok]
    return "Unstated"


def family_of(tokens: tuple[str, ...]) -> str:
    """Steps 4 and 5. Returns a name from FAMILY_ORDER."""
    have = set(tokens)

    # --- step 4, reject clauses, before any family clause ---
    if have & MANAGEMENT_TOKENS and not any(w in have for w in MANAGEMENT_EXEMPT):
        return "Engineering management" if have & ENGINEERING_WORDS else UNMATCHED
    if "SALES" in have:
        return UNMATCHED
    if "SDET" in have or "QA" in have or "TEST" in have or ("QUALITY" in have and "ASSURANCE" in have):
        return "QA / test engineer"

    # --- step 5, family clauses, first match wins ---
    ml = "MACHINE" in have and "LEARNING" in have
    ai = "ARTIFICIAL" in have and "INTELLIGENCE" in have
    if ml and "ENGINEER" in have:
        return "Machine learning engineer"
    if (ml or ai) and ("SCIENTIST" in have or "RESEARCHER" in have):
        return "Applied / research scientist (ML, AI)"
    if "DATA" in have and "SCIENTIST" in have:
        return "Data scientist"
    if "DATA" in have and "ENGINEER" in have:
        return "Data engineer"
    if "DATA" in have and "ANALYST" in have:
        return "Data analyst"
    if ("DEVOPS" in have
            or ("SITE" in have and "RELIABILITY" in have)
            or ("PLATFORM" in have and "ENGINEER" in have)
            or ("INFRASTRUCTURE" in have and "ENGINEER" in have)
            or ("CLOUD" in have and "ENGINEER" in have)):
        return "DevOps / SRE / platform engineer"
    if ("SECURITY" in have or "CYBERSECURITY" in have) and "ENGINEER" in have:
        return "Security engineer"
    if "SOFTWARE" in have and have & {
            "ENGINEER", "ENGINEERING", "DEVELOPER", "DEVELOPMENT", "ARCHITECT", "PROGRAMMER"}:
        return "Software engineer"
    if "SCIENTIST" in have and ("RESEARCH" in have or "APPLIED" in have):
        return "Research / applied scientist (unspecified)"
    if "PRODUCT" in have and "MANAGER" in have:
        return "Product manager"
    return UNMATCHED


def classify(title: str) -> Role:
    tokens = tokenise(title)
    return Role(family_of(tokens), level_of(tokens), tokens)
