#!/usr/bin/env python3
"""Build docs/index.html from data/aggregates.json.

Every number is read from the aggregates at build time; verify rebuilds the page and compares it
byte for byte.

Form. These are distributions, not single values, so every row shows p10 through p90 as a range
bar rather than a median with a bar chart. A median alone is the most misleading way to present
pay data: it hides that the same title at the same company spans a factor of two.

`n` is printed beside every single figure, without exception. A median over 13 filings and a
median over 22,355 must never look alike, and the only reliable way to stop that is to refuse to
draw one without its count.
"""
from __future__ import annotations

import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def esc(s):
    return html.escape(str(s), quote=True)


def money(x):
    return f"${x:,.0f}" if x is not None else "n/a"


CSS = """
:root{
  color-scheme:light;
  --bg:#fcfbf8; --panel:#f2efe7; --ink:#15130e; --ink2:#544f45; --ink3:#8a857a;
  --rule:#e2ded2; --bar:#1f6f4a; --bar2:#8fbfa6; --accent:#a8541e;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){
  color-scheme:dark;
  --bg:#12140f; --panel:#1c1f18; --ink:#f2f2e8; --ink2:#bfbfae; --ink3:#87877a;
  --rule:#2e3227; --bar:#4cb083; --bar2:#2c5c46; --accent:#e0925c;}}
:root[data-theme=dark]{
  color-scheme:dark;
  --bg:#12140f; --panel:#1c1f18; --ink:#f2f2e8; --ink2:#bfbfae; --ink3:#87877a;
  --rule:#2e3227; --bar:#4cb083; --bar2:#2c5c46; --accent:#e0925c;}
:root[data-theme=light]{
  color-scheme:light;
  --bg:#fcfbf8; --panel:#f2efe7; --ink:#15130e; --ink2:#544f45; --ink3:#8a857a;
  --rule:#e2ded2; --bar:#1f6f4a; --bar2:#8fbfa6; --accent:#a8541e;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.62 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:900px;margin:0 auto;padding:0 1.25rem}
header{padding:3.4rem 0 2rem}
.eyebrow{font:700 .7rem/1 var(--mono);letter-spacing:.15em;text-transform:uppercase;
  color:var(--accent);margin:0 0 .9rem}
h1{font-size:clamp(1.85rem,5vw,2.9rem);line-height:1.04;letter-spacing:-.026em;margin:0 0 1rem;
  font-weight:760;text-wrap:balance;max-width:18ch}
.lede{font-size:1.05rem;color:var(--ink2);max-width:64ch;margin:0 0 1.3rem}
section{padding:2.5rem 0;border-top:1px solid var(--rule)}
h2{font-size:1.3rem;margin:0 0 .5rem;letter-spacing:-.014em;text-wrap:balance}
h3{font-size:1rem;margin:1.6rem 0 .5rem}
p,li{color:var(--ink2);max-width:68ch}
strong{color:var(--ink)}
a{color:var(--bar)}
code{font-family:var(--mono);font-size:.87em}
p code,li code,td code{background:var(--panel);border:1px solid var(--rule);border-radius:3px;
  padding:.05em .3em}
.rows{margin:1rem 0}
.r{display:grid;grid-template-columns:minmax(9rem,15rem) 1fr 4.5rem;gap:.7rem;align-items:center;
  padding:.26rem 0}
.r .lbl{font-size:.85rem;color:var(--ink);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.r .band{position:relative;display:block;height:1.25rem;background:var(--panel);
  border:1px solid var(--rule);border-radius:3px;overflow:hidden}
.r .whisk{position:absolute;top:50%;transform:translateY(-50%);height:2px;background:var(--bar2)}
.r .box{position:absolute;top:2px;bottom:2px;background:var(--bar2);border-radius:2px}
.r .med{position:absolute;top:0;bottom:0;width:2px;background:var(--bar)}
.r .n{font:.74rem/1 var(--mono);color:var(--ink3);text-align:right;
  font-variant-numeric:tabular-nums}
.scale{display:grid;grid-template-columns:minmax(9rem,15rem) 1fr 4.5rem;gap:.7rem;
  font:.7rem/1 var(--mono);color:var(--ink3);margin-top:.3rem}
.scale .ticks{display:flex;justify-content:space-between}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.85rem;min-width:520px}
th,td{text-align:left;padding:.4rem .55rem;border-bottom:1px solid var(--rule)}
th{color:var(--ink);font-weight:650}
td{color:var(--ink2);font-variant-numeric:tabular-nums}
td.name{font-variant-numeric:normal}
.note{background:var(--panel);border:1px solid var(--rule);border-left:3px solid var(--accent);
  border-radius:0 6px 6px 0;padding:.75rem .9rem;margin:1rem 0}
.note p{margin:.3rem 0}
footer{padding:2.3rem 0 4rem;color:var(--ink3);font-size:.84rem;border-top:1px solid var(--rule)}
#theme{font:inherit;font-size:.8rem;padding:.35rem .7rem;border:1px solid var(--rule);
  background:var(--panel);color:var(--ink2);border-radius:4px;cursor:pointer}
#theme:focus-visible{outline:2px solid var(--bar);outline-offset:2px}
"""


def band_rows(items, lo, hi, label_key="name"):
    """p10-p90 whisker, p25-p75 box, p50 line. n always printed."""
    span = (hi - lo) or 1
    def x(v):
        return max(0.0, min(100.0, (v - lo) / span * 100))
    out = ["<div class='rows'>"]
    for it in items:
        p10, p25, p50, p75, p90 = (it["p10"], it["p25"], it["p50"], it["p75"], it["p90"])
        out.append(
            f"<div class='r'><span class='lbl' title='{esc(it[label_key])}'>"
            f"{esc(it[label_key])}</span>"
            f"<span class='band' role='img' aria-label='"
            f"p10 {money(p10)}, median {money(p50)}, p90 {money(p90)}, n {it['n']}'>"
            f"<span class='whisk' style='left:{x(p10):.2f}%;width:{x(p90) - x(p10):.2f}%'></span>"
            f"<span class='box' style='left:{x(p25):.2f}%;width:{x(p75) - x(p25):.2f}%'></span>"
            f"<span class='med' style='left:{x(p50):.2f}%'></span>"
            f"</span><span class='n'>n={it['n']:,}</span></div>")
    out.append("</div>")
    out.append(f"<div class='scale'><span></span><span class='ticks'>"
               f"<span>{money(lo)}</span><span>{money((lo + hi) / 2)}</span>"
               f"<span>{money(hi)}</span></span><span></span></div>")
    return "".join(out)


def table(items, label, key="name"):
    rows = "".join(
        f"<tr><td class='name'>{esc(i[key])}</td><td>{i['n']:,}</td>"
        f"<td>{money(i['p10'])}</td><td>{money(i['p25'])}</td><td><strong>{money(i['p50'])}"
        f"</strong></td><td>{money(i['p75'])}</td><td>{money(i['p90'])}</td></tr>"
        for i in items)
    return (f"<div class='scroll'><table><thead><tr><th>{esc(label)}</th><th>n</th><th>p10</th>"
            f"<th>p25</th><th>median</th><th>p75</th><th>p90</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


def main():
    a = json.loads((ROOT / "data" / "aggregates.json").read_text(encoding="utf-8"))
    m = a["meta"]
    fams = sorted([f for f in a["families"] if f.get("family") != "unmatched"],
                  key=lambda x: -x["n"])
    swe = next((f for f in fams if f["family"] == "Software engineer"), fams[0])
    levels = sorted([l for l in swe.get("levels", []) if l["n"] >= 50],
                    key=lambda x: -(x["p50"] or 0))
    metros = sorted(a["metros_all_families"], key=lambda x: -(x["p50"] or 0))[:14]
    employers = sorted([e for e in a["employers_all_families"] if e["n"] >= 100],
                       key=lambda x: -(x["p50"] or 0))[:20]
    ctrl = a["employer_permutation_control"]
    ratio = (ctrl["real_p10_to_p90_spread_of_employer_medians"]
             / ctrl["shuffled_p10_to_p90_spread_of_employer_medians"])

    fam_rows = [{"name": f["family"], **{k: f[k] for k in ("n", "p10", "p25", "p50", "p75", "p90")}}
                for f in fams[:12]]
    lo = min(r["p10"] for r in fam_rows)
    hi = max(r["p90"] for r in fam_rows)

    src = m["sources"][0]
    units = m["wage_unit_counts_after_status_filters"]
    filt = m["filters"]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>What US employers actually filed they would pay</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ctext y='13' font-size='14'%3E%F0%9F%92%B5%3C/text%3E%3C/svg%3E">
<meta name="description" content="Salary bands built from US Department of Labor H-1B disclosure
filings: real per-employer wages an employer legally committed to, not self-reported survey data.">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="eyebrow">Catalog task CAREER-010</p>
  <h1>What employers actually filed they would pay</h1>
  <p class="lede">A US employer sponsoring an H-1B must file the wage it is offering, by job
  title and worksite, and the Department of Labor publishes every filing. It is a large sample of
  real compensation with none of the self-report bias of a salary aggregator, because a company
  that files a number is legally committing to it.</p>
  <p class="lede"><strong>{m['rows_kept']:,}</strong> filings kept from
  <strong>{m['rows_read']:,}</strong> read, decided between {esc(m['decision_date_min'])} and
  {esc(m['decision_date_max'])}.</p>
  <button type="button" id="theme">Theme: auto</button>
</header>

<section>
  <h2>By role family</h2>
  <p>Each bar is a distribution, not a number: the thin line spans p10 to p90, the block is p25 to
  p75, and the dark mark is the median. <code>n</code> is printed on every row without exception,
  because a median over 13 filings and one over 22,000 should never look alike.</p>
  {band_rows(fam_rows, lo, hi)}
  {table([{"name": f["family"], **f} for f in fams[:12]], "role family")}
</section>

<section>
  <h2>Seniority inside one family</h2>
  <p>Levels parsed out of the free-text job title for <strong>{esc(swe['family'])}</strong>
  ({swe['n']:,} filings across {swe['distinct_titles']:,} distinct title strings). A level is only
  shown where at least 50 filings carry it.</p>
  {table(levels, "level")}
  <p>The <code>Unstated</code> row is the largest by far, which is the honest shape of this data:
  most filed titles carry no level at all, so the level bands describe the minority that do.</p>
</section>

<section>
  <h2>By metro</h2>
  {table(metros, "metro")}
  <p>{m['metro_mapped_share'] * 100:.0f}% of filings mapped to a metro
  ({m['metro_mapped_rows']:,} rows). The rest name a worksite this build could not resolve and are
  excluded from this table only, not from the role or employer figures.</p>
</section>

<section>
  <h2>By employer</h2>
  <p>Every employer with at least 100 filings, sorted by median. Name variants are merged, and the
  variants that were merged are recorded in the data.</p>
  {table(employers, "employer")}
</section>

<section>
  <h2>The control that makes the employer table meaningful</h2>
  <p>Employer medians differ. The question is whether they differ because employers pay
  differently, or because a table of {ctrl['employers_published']} groups with uneven sample sizes
  will always show a spread.</p>
  <p>So the employer labels were shuffled at random and the whole table recomputed. If the spread
  survived the shuffle it would be an artefact of the grouping and nothing else.</p>
  <div class="note">
    <p><strong>Real spread of employer medians (p10 to p90):
    {money(ctrl['real_p10_to_p90_spread_of_employer_medians'])}</strong></p>
    <p>Shuffled: {money(ctrl['shuffled_p10_to_p90_spread_of_employer_medians'])},
    a factor of {ratio:.1f} smaller (seed {ctrl['seed']}).</p>
  </div>
  <p>The spread collapses, so employer identity is carrying real signal and the table above is
  about who pays what rather than about how the groups were cut.</p>
</section>

<section>
  <h2>What this number is not</h2>
  <p><strong>It is a floor, not an offer.</strong> The field used is
  <code>{esc(m['wage_field'])}</code>, the bottom of the filed range and the figure the employer
  commits to. Actual offers are routinely higher, and the median filed range top is
  {m['offered_range']['median_top_over_bottom']:.2f}x the bottom. Every figure here understates
  what was paid.</p>
  <p><strong>It is not total compensation.</strong> Equity and bonus are not in this data at all,
  which matters most at exactly the companies whose medians look highest.</p>
  <p><strong>It is sponsored roles only.</strong> These are filings for foreign workers, which is
  a particular slice of hiring and not a random sample of a company's engineers.</p>

  <h3>Wage units, the field most likely to be wrong</h3>
  <p>Filings state a unit, and reading an hourly wage as an annual one is a silent error of
  2,080x. The units present after filtering:</p>
  <div class="scroll"><table><thead><tr><th>unit</th><th>filings</th></tr></thead><tbody>
    {''.join(f"<tr><td class='name'>{esc(k)}</td><td>{v:,}</td></tr>"
             for k, v in sorted(units.items(), key=lambda kv: -kv[1]))}
  </tbody></table></div>
  <p>An unrecognised unit is refused rather than assumed to be yearly, and
  {m['wage_conversion_outcomes'].get('above_ceiling', 0)} rows were dropped for converting to an
  implausible figure. Rows dropped by the filters before that:
  {', '.join(f"{k.replace('_', ' ')} {v:,}" for k, v in sorted(filt.items()))}.</p>
</section>

<footer>
  <p>Source: <code>{esc(src['file'])}</code>, {src['bytes'] / 1e6:.0f} MB, sha256
  <code>{esc(src['sha256'][:16])}…</code>, US Department of Labor OFLC disclosure data. The raw
  file is not committed; the aggregates are. Every number here is generated from
  <code>data/aggregates.json</code> at build time. Source at
  <a href="https://github.com/JesseRWeigel/salary-bands">salary-bands</a>, part of
  <a href="https://github.com/JesseRWeigel/722-things-to-build">722 things to build</a>.</p>
</footer>
</div>
<script>
(function () {{
  var modes = ['auto', 'light', 'dark'], i = 0;
  document.getElementById('theme').addEventListener('click', function () {{
    i = (i + 1) % 3;
    if (modes[i] === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', modes[i]);
    this.textContent = 'Theme: ' + modes[i];
  }});
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    text = main()
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}, {len(text):,} bytes")
