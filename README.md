# salary-bands

**[Open the live page](https://jesserweigel.github.io/salary-bands/)**

A US employer sponsoring an H-1B must file the wage it is offering, by job title and worksite, and
the Department of Labor publishes every filing. It is a large sample of real compensation with none
of the self-report bias of a salary aggregator, because a company that files a number is legally
committing to it.

**171,617 filings kept from 201,700 read**, decided between 2025-07-01 and 2025-12-30.

Catalog task CAREER-010.

## What it says

Software engineer, 35,071 filings across 2,770 distinct title strings:

| percentile | filed wage |
|---|---|
| p25 | $113,000 |
| median | $140,000 |
| p75 | $178,700 |
| p90 | $213,512 |

By level, where the title states one (at least 50 filings each):

| level | n | median | p90 |
|---|---|---|---|
| Staff | 1,459 | $182,994 | $250,059 |
| Principal | 1,023 | $165,000 | $235,080 |
| Level III | 1,553 | $160,805 | $220,684 |
| Level II | 2,392 | $158,100 | $190,000 |

By metro, all families:

| metro | n | median |
|---|---|---|
| SF Bay Area, CA | 22,355 | $185,000 |
| Seattle, WA | 10,957 | $166,966 |
| New York City metro | 14,752 | $145,470 |

`n` is printed beside every figure on the page, without exception. A median over 13 filings and a
median over 22,355 must never look alike, and the only reliable way to prevent that is to refuse to
draw one without its count.

## The control that makes the employer table mean anything

Employer medians differ. The question is whether they differ because employers pay differently, or
because a table of 836 groups with uneven sample sizes will show a spread no matter what.

So the employer labels were shuffled at random and the whole table recomputed:

```
real spread of employer medians (p10 to p90)   $100,669
shuffled                                        $23,217     4.3x smaller
```

The spread collapses, so employer identity is carrying real signal and the table is about who pays
what rather than about how the groups were cut. `verify.sh` re-checks that ratio and fails below
2x, because below that the per-employer bands should not be published at all.

## What the number is not

**It is a floor, not an offer.** The field is `WAGE_RATE_OF_PAY_FROM`, the bottom of the filed
range and the figure the employer commits to. The median filed range top is 1.22x the bottom, so
every figure here understates what was actually paid.

**It is not total compensation.** Equity and bonus are absent entirely, which matters most at
exactly the companies whose medians look highest.

**It is sponsored roles only.** These are filings for foreign workers, a particular slice of
hiring rather than a random sample of a company's engineers.

**Most titles carry no level.** The `Unstated` bucket is the largest by far, so the level bands
describe the minority of filings that state one.

## The field most likely to be silently wrong

Filings state a wage unit, and reading an hourly wage as an annual one is an error of 2,080x that
still produces a number looking like a salary. After filtering:

```
YEAR 161,981   HOUR 9,356   MONTH 265   WEEK 108   BI-WEEKLY 80
```

An unrecognised unit is **refused**, not assumed to be yearly. `verify.sh` converts one wage in
every unit and requires all five to land on the same annual figure, then requires an unknown unit
and a sub-minimum wage to be rejected. One of the sabotages makes `canonical_unit` fall back to
`YEAR`, and it must be caught.

## Use

```bash
# Aggregate from a disclosure file. The raw download is ~79 MB and is never committed.
python3 -m bands.cli build --source LCA_Disclosure_Data_FY2025_Q4.xlsx --out data/aggregates.json

# Print bands from the committed aggregates. No download needed.
python3 -m bands.cli show --family "Software engineer"

# See how a job title is bucketed, and why.
python3 -m bands.cli title "Sr. Software Dev Engineer II"

# Recompute the fixture with code that shares nothing with the aggregator.
python3 tools/recheck.py --fixture
```

## Verify

```
$ bash scripts/verify.sh
...
VERIFY OK
```

Nine layers:

1. The unit suite, every assertion paired with a negative control.
2. `tools/recheck.py`, which **imports nothing from `bands/`** and recomputes the fixture's
   aggregates from the raw rows.
3. That independence checked by inspecting the import graph with `ast`, not by grepping.
4. Wage-unit conversion checked exactly, in every unit, plus the refusals.
5. The permutation control above, with a hard floor on the ratio.
6. The page rebuilt and compared byte for byte.
7. Three sabotages, each proved to have applied before its result counts.
8. This README's numbers regenerated from the aggregates.
9. Hygiene: an independent leak checker with 12 proven canaries, no tracked file over 2 MB so the
   79 MB download can never sneak in, and no build output tracked.

## A blind spot found in the leak checker itself

The fixtures are `.xlsx` files, which are zips, so they are full of NUL bytes by construction and
the checker flagged them as binary. Exempting them would have been the easy fix and a real hole: a
credential pasted into a spreadsheet cell would then never be scanned.

So the checker unzips them and scans the XML parts instead. The first version of that did **not
work** — it called `scan_bytes`, which only checks for NUL, then filtered that one result away, so
it inspected nothing and reported clean. A github token planted in a cell went undetected.

It was caught by insisting on the negative control: plant a token, prove the plant landed, and
require the scanner to find it. That case is now canary 12 in `--selftest`, and it proves the plant
landed before judging the result, because a canary that never arrived proves nothing either way.

## Layout

```
bands/wage.py           unit conversion and the refusals
bands/roles.py          title to role family and level
bands/metro.py          worksite to metro
bands/xlsxread.py       a minimal xlsx reader, no dependency
bands/aggregate.py      filters, percentiles, the permutation control
tools/recheck.py        independent recompute, sharing no code with the above
tools/leakcheck.py      12 proven canaries, zip-aware
scripts/verify.sh       nine layers
data/aggregates.json    the published figures
fixtures/               synthetic xlsx with known-correct aggregates
tests/                  56 tests, each with a negative control
```

Python 3.9+, no dependencies. Source data is public US Department of Labor OFLC disclosure data.

Part of [722 things to build](https://github.com/JesseRWeigel/722-things-to-build).
