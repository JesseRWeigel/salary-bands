"""Interpretation shared by CLI and HTML; never infer payroll from application rows."""
WAGE_NOTE = ("WAGE_RATE_OF_PAY_FROM is the lower endpoint of the filed wage range, "
             "annualised using fixed unit factors. These application records do not establish "
             "actual pay, employment, or total compensation.")
COMPARISON_NOTE = ("Employer comparisons are unadjusted associations, not an employer pay premium. "
                   "Role, level, location, date and selection mix can explain differences. "
                   "No matched comparisons or causal effects are estimated.")

def lines(meta):
    sources = meta.get("sources", [])
    thresholds = meta.get("min_n", {})
    missing = meta.get("missing_fields_kept")
    return [
        WAGE_NOTE,
        "Source: US Department of Labor OFLC LCA application disclosure; " +
        "; ".join(str(s.get("file", "unknown source")) for s in sources or [{}]),
        "Kept-row decision dates (not employment dates): " +
        str(meta.get("decision_date_min") or "unknown") + " to " +
        str(meta.get("decision_date_max") or "unknown"),
        f"Cohort: {meta.get('rows_kept', 0)} filings kept of {meta.get('rows_read', 0)} rows read; "
        "H-1B, Certified, full-time Y; duplicate nonblank case numbers removed; "
        "one filing is one observation, not one worker. No worker-count weighting.",
        "Wages: unknown units/missing or invalid lower wages rejected; annualisation uses "
        "2080 hours, 52 weeks, 26 bi-weeks or 12 months. "
        f"Annual floor {meta.get('floor_annual', 'unknown')}; ceiling 10000000. "
        "Missing/invalid range tops do not remove a valid lower wage.",
        "Exclusions (sequential counts): " + str(meta.get("filters", {})),
        "Publication minimum n: " + str(thresholds) + "; n counts filings, not independent people.",
        "Missing fields among kept rows: " + (str(missing) if missing is not None else
        "not recorded in this historical aggregate; no counts inferred."),
        "Missing titles/locations are not imputed: titles may be unmatched/levels Unstated; "
        "unmapped metros excluded only from metro tables. Blank case IDs cannot be deduplicated. "
        "Blank employers may form a blank-name group; inspect before comparing.",
        COMPARISON_NOTE,
        "Percentiles describe this selected filing cohort, not confidence intervals. Small cohorts "
        "are unstable; repeated filings and sponsorship selection limit generalisation. "
        "No uncertainty interval or population-pay estimate is claimed.",
    ]
