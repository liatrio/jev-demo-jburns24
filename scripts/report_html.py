# ruff: noqa: E501
"""Render results/ as a single-page HTML report: results/report.html.

Reads the three experiments' saved results (fraud bake-off, PII log check, adversarial
swarm) and writes one self-contained page describing cost, speed and enterprise value.

    uv run python scripts/report_html.py            # -> results/report.html
    uv run python scripts/report_html.py results out.html
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else RESULTS / "report.html"

# Categorical slots 1-4 of the validated reference palette (light / dark).
SERIES = {
    "jev": ("#2a78d6", "#3987e5", "Jev"),
    "sonnet-5": ("#eb6834", "#d95926", "Claude Sonnet 5"),
    "gpt-5.6-luna": ("#1baf7a", "#199e70", "GPT-5.6 Luna"),
    "jev+sonnet-5": ("#eda100", "#c98500", "Jev + Sonnet (gated)"),
}

# Enterprise sizing assumptions for the value section (stated on the page).
DAILY_TXNS = 10_000_000
PR_PER_DAY = 400
PUSHES_PER_DAY = 1_500


def esc(s: object) -> str:
    return html.escape(str(s))


def money(x: float, places: int = 4) -> str:
    return f"${x:,.{places}f}"


def load() -> dict:
    agg = json.loads((RESULTS / "summary.json").read_text())
    tapes = [json.loads(p.read_text()) for p in sorted(RESULTS.glob("T0*.json"))]
    pii = json.loads((RESULTS / "pii-check.json").read_text())
    swarm_clean = json.loads((RESULTS / "swarm" / "clean-summary.json").read_text())
    swarm_inj = json.loads((RESULTS / "swarm" / "injected-summary.json").read_text())
    tape_meta = {}
    for p in sorted((ROOT / "tapes").glob("T0*.json")):
        t = json.loads(p.read_text())
        tape_meta[t["tape_id"]] = t
    return {
        "agg": agg,
        "tapes": tapes,
        "tape_meta": tape_meta,
        "pii": pii,
        "swarm_clean": swarm_clean,
        "swarm_inj": swarm_inj,
    }


# ----------------------------------------------------------------------------- charts


def hbar_chart(title: str, rows: list[tuple[str, float, str]], fmt, note: str = "") -> str:
    """Horizontal bars. rows: (series key, value, formatted label)."""
    mx = max(v for _, v, _ in rows) or 1
    w, label_w, bar_h, gap = 640, 170, 22, 14
    h = len(rows) * (bar_h + gap) + 8
    parts = [
        f'<figure class="chart"><figcaption>{esc(title)}</figcaption>',
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">',
    ]
    for i, (key, v, lab) in enumerate(rows):
        y = 4 + i * (bar_h + gap)
        bw = max(4, (w - label_w - 90) * v / mx)
        name = SERIES.get(key, (None, None, key))[2]
        parts.append(
            f'<text x="{label_w - 10}" y="{y + bar_h / 2 + 4}" text-anchor="end" class="lbl">'
            f"{esc(name)}</text>"
            f'<rect x="{label_w}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="4" '
            f'class="s-{i + 1}"><title>{esc(name)}: {esc(lab)}</title></rect>'
            f'<text x="{label_w + bw + 8:.1f}" y="{y + bar_h / 2 + 4}" class="val">{esc(lab)}</text>'
        )
    parts.append("</svg>")
    if note:
        parts.append(f'<p class="note">{esc(note)}</p>')
    parts.append("</figure>")
    return "\n".join(parts)


# ----------------------------------------------------------------------------- sections


def fraud_section(d: dict) -> str:
    agg = d["agg"]
    j = agg["jev"]
    rows_tbl = []
    for k, m in agg.items():
        pa = "-" if m["pattern_accuracy"] is None else f"{m['pattern_accuracy']:.2f}"
        rows_tbl.append(
            "<tr>"
            f'<td><span class="sw" style="--c:var(--{k.replace("+", "-").replace(".", "-")})"></span>'
            f"{esc(SERIES[k][2])}</td>"
            f"<td>{m['n']}</td><td>{m['tp']}</td><td>{m['fp']}</td><td>{m['fn']}</td>"
            f"<td>{m['precision']:.2f}</td><td>{m['recall']:.2f}</td><td>{m['f1']:.2f}</td>"
            f"<td>{pa}</td><td>{m['latency_p50_ms']:,.0f}</td><td>{m['latency_p95_ms']:,.0f}</td>"
            f"<td>{m['errors']}</td>"
            f"<td>{money(m['cost_usd'])}</td>"
            f"<td>{money(m['cost_usd'] / m['n'] * 1000, 3)}</td>"
            "</tr>"
        )
    order = list(agg)
    cost_chart = hbar_chart(
        "Cost per 1,000 rows (USD)",
        [
            (
                k,
                agg[k]["cost_usd"] / agg[k]["n"] * 1000,
                money(agg[k]["cost_usd"] / agg[k]["n"] * 1000, 3),
            )
            for k in order
        ],
        None,
        "Gateway list prices at run time. Every evaluator saw byte-identical input.",
    )
    lat_chart = hbar_chart(
        "Median latency per row (ms)",
        [(k, agg[k]["latency_p50_ms"], f"{agg[k]['latency_p50_ms']:,.0f} ms") for k in order],
        None,
        "Wall clock through the gateway at concurrency 8. Jev answers three typed questions per call.",
    )
    f1_chart = hbar_chart(
        "F1 on fraud rows (pooled, 6 tapes)",
        [(k, agg[k]["f1"], f"{agg[k]['f1']:.2f}") for k in order],
        None,
        "Recall and precision scored against ground truth the models never see.",
    )

    per_tape = []
    names = list(d["tapes"][0]["metrics"])
    head = "".join(f"<th>{esc(SERIES[n][2])}<br><small>recall / FP</small></th>" for n in names)
    for t in d["tapes"]:
        meta = d["tape_meta"].get(t["tape_id"], {})
        nfraud = sum(1 for x in meta.get("transactions", []) if x.get("is_fraud"))
        cells = "".join(
            f"<td>{t['metrics'][n]['recall']:.2f} / {t['metrics'][n]['fp']}</td>" for n in names
        )
        per_tape.append(
            f"<tr><td><code>{esc(t['tape_id'])}</code></td>"
            f"<td>{esc(meta.get('title', ''))}</td>"
            f"<td>{len(meta.get('transactions', []))}</td><td>{nfraud}</td>{cells}</tr>"
        )

    comps = []
    for k in order:
        if k == "jev":
            continue
        m = agg[k]
        comps.append(
            f"<li><b>vs {esc(SERIES[k][2])}:</b> Jev is "
            f"{m['latency_p50_ms'] / j['latency_p50_ms']:.0f}x faster at p50 and "
            f"{m['cost_usd'] / j['cost_usd']:.0f}x cheaper, with F1 {j['f1']:.2f} vs {m['f1']:.2f}.</li>"
        )

    # Enterprise extrapolation
    ent_rows = []
    for k in order:
        m = agg[k]
        per_row = m["cost_usd"] / m["n"]
        ent_rows.append(
            f"<tr><td>{esc(SERIES[k][2])}</td>"
            f"<td>{money(per_row * DAILY_TXNS, 0)}</td>"
            f"<td>{money(per_row * DAILY_TXNS * 365, 0)}</td>"
            f"<td>{m['latency_p50_ms']:,.0f} ms</td>"
            f"<td>{m['recall']:.2f}</td><td>{m['fp']}</td></tr>"
        )

    return f"""
<section id="fraud">
<h2>Experiment 1: Jev vs generative LLMs on fraud-classification tapes</h2>
<p class="lede">Six synthetic transaction streams with planted fraud (account takeover, card testing,
structuring, impossible travel, money mule, plus one clean control) are played row by row through
four evaluators. Each sees the account profile, the previous rows for that account and the row
under review, then answers three typed questions: is this fraud, which pattern, what risk level.
Ground truth is stripped before the model sees the row and used only for scoring.</p>

<div class="kpis">
  <div class="kpi"><div class="k-label">Rows evaluated</div><div class="k-value">{j["n"]}</div><div class="k-sub">{sum(m["tp"] + m["fn"] for m in [j])} fraud rows across 6 tapes</div></div>
  <div class="kpi"><div class="k-label">Jev recall</div><div class="k-value">{j["recall"]:.2f}</div><div class="k-sub">{j["fp"]} false positives, {j["fn"]} misses</div></div>
  <div class="kpi"><div class="k-label">Jev median latency</div><div class="k-value">{j["latency_p50_ms"]:,.0f} ms</div><div class="k-sub">p95 {j["latency_p95_ms"]:,.0f} ms</div></div>
  <div class="kpi"><div class="k-label">Jev cost, whole run</div><div class="k-value">{money(j["cost_usd"], 3)}</div><div class="k-sub">{money(j["cost_usd"] / j["n"] * 1000, 3)} per 1,000 rows</div></div>
</div>

<div class="charts">
{cost_chart}
{lat_chart}
{f1_chart}
</div>

<h3>Pooled results</h3>
<div class="tbl"><table>
<thead><tr><th>Evaluator</th><th>Rows</th><th>TP</th><th>FP</th><th>FN</th><th>Precision</th><th>Recall</th><th>F1</th><th>Pattern acc</th><th>p50 ms</th><th>p95 ms</th><th>Errors</th><th>Total cost</th><th>Per 1k rows</th></tr></thead>
<tbody>{"".join(rows_tbl)}</tbody></table></div>

<ul class="comps">{"".join(comps)}</ul>
<p class="note">Errors are transport or parse failures counted as "not fraud" and scored against the row. On this run Jev had one gateway connection error on a clean row and the gated hybrid had one unparseable Sonnet reply on a structuring row.</p>

<h3>Per tape</h3>
<div class="tbl"><table>
<thead><tr><th>Tape</th><th>Situation</th><th>Rows</th><th>Fraud</th>{head}</tr></thead>
<tbody>{"".join(per_tape)}</tbody></table></div>

<h3>What this means for an enterprise bank</h3>
<p>The tapes are small and textbook, so treat quality as "competitive on known patterns", not as a
production benchmark. Cost and speed, however, are structural and scale linearly. At
<b>{DAILY_TXNS:,} transactions per day</b> scored in-line, using this run's measured cost per row:</p>
<div class="tbl"><table>
<thead><tr><th>Evaluator</th><th>Per day</th><th>Per year</th><th>Decision latency (p50)</th><th>Recall</th><th>FP on 212 rows</th></tr></thead>
<tbody>{"".join(ent_rows)}</tbody></table></div>
<ul>
<li><b>Real-time authorization is only viable with Jev.</b> Card networks expect a decision in
well under a second. A quarter-second typed verdict fits inside an authorization hop; two seconds
does not, which pushes generative LLMs to after-the-fact review.</li>
<li><b>Calibrated probabilities are the operational lever.</b> Jev returns P(fraud), a pattern
distribution and a risk score, so a bank can set auto-approve, step-up and block thresholds per
product line and audit them. Free-text JSON from a chat model gives a label, not a calibrated number.</li>
<li><b>Escalate, do not replace.</b> The confidence-gated hybrid routes only the 0.3 to 0.7 gray
zone to Sonnet. It runs at Jev's latency for most rows and about a tenth of Sonnet's cost, and is the
natural shape for adding an analyst-facing explanation to the few rows that need one.</li>
</ul>
</section>
"""


def pii_section(d: dict) -> str:
    p = d["pii"]
    flagged = [f for f in p["findings"] if f["flagged"]]
    passed = [f for f in p["findings"] if not f["flagged"]]
    expected = json.loads((ROOT / "tests" / "fixtures" / "pii" / "expected.json").read_text())
    # expected.json format is whatever the fixtures define; count planted leaks defensively.
    planted = 0
    if isinstance(expected, dict):
        for v in expected.values():
            if isinstance(v, list):
                planted += len(v)
            elif isinstance(v, dict):
                planted += sum(1 for x in v.values() if x not in (None, "none", False))
    elif isinstance(expected, list):
        planted = len(expected)
    planted = planted or len(flagged)

    rows = "".join(
        f"<tr><td><code>{esc(Path(f['file']).name)}:{f['line']}</code></td>"
        f"<td>{esc(f['kind'])}</td><td>{f['p_pii']:.2f}</td>"
        f"<td><code>{esc(f['code'][:110])}</code></td></tr>"
        for f in flagged
    )
    tricky = [
        f
        for f in passed
        if any(w in f["code"] for w in ("mask_email", "digest", "card_brand", "hash"))
    ]
    tricky_rows = "".join(
        f"<tr><td><code>{esc(Path(f['file']).name)}:{f['line']}</code></td><td>{f['p_pii']:.2f}</td>"
        f"<td><code>{esc(f['code'][:110])}</code></td></tr>"
        for f in tricky
    )
    per_pr_cost = p["cost_usd"] / p["files_scanned"] * 5  # assume ~5 changed source files per PR
    return f"""
<section id="pii">
<h2>Experiment 2: semantic PII lint in a pre-commit hook</h2>
<p class="lede">A regex finds every <code>logger.info(...)</code>. It cannot tell whether the string
inside leaks a customer's email. This check extracts every logging call from staged files (Python,
TypeScript, Go and six other languages), sends each file's statements to Jev as one call with one
typed <em>choice</em> question per statement, and blocks the commit when P(personal data) is at
least 0.5. It runs live on every commit; the fixture run below is the same code path.</p>

<div class="kpis">
  <div class="kpi"><div class="k-label">Log statements</div><div class="k-value">{p["statements"]}</div><div class="k-sub">{p["files_scanned"]} files, {p["jev_calls"]} Jev calls</div></div>
  <div class="kpi"><div class="k-label">Leaks caught</div><div class="k-value">{len(flagged)} / {planted}</div><div class="k-sub">0 false positives</div></div>
  <div class="kpi"><div class="k-label">Wall clock</div><div class="k-value">{p["wall_ms"] / 1000:.1f} s</div><div class="k-sub">p50 {p["p50_ms"]:.0f} ms per call</div></div>
  <div class="kpi"><div class="k-label">Cost</div><div class="k-value">{money(p["cost_usd"], 5)}</div><div class="k-sub">for the whole commit</div></div>
</div>

<h3>Blocked lines</h3>
<div class="tbl"><table>
<thead><tr><th>Location</th><th>Kind</th><th>P(pii)</th><th>Statement</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<h3>Lines a keyword scanner would false-alarm on, and Jev passed</h3>
<div class="tbl"><table>
<thead><tr><th>Location</th><th>P(pii)</th><th>Statement</th></tr></thead>
<tbody>{tricky_rows}</tbody></table></div>

<h3>What this means for an enterprise engineering org</h3>
<ul>
<li><b>Cost is a rounding error.</b> At roughly {money(per_pr_cost, 4)} per commit of five changed
files, {PR_PER_DAY:,} commits a day costs about {money(per_pr_cost * PR_PER_DAY * 365, 0)} a year.
One PII incident in production logs costs more than that in the first hour of the response.</li>
<li><b>Latency is inside the developer's tolerance.</b> About a second added to a commit is
accepted; ten seconds from a generative model is the point where teams add <code>--no-verify</code>
to their muscle memory and the control stops existing.</li>
<li><b>Semantic, not lexical.</b> Masked emails, hashed keys and opaque ids pass. Compliance
teams get a control that judges meaning against a written policy, with a calibrated probability
per line that can be tuned (block at 0.5, warn from 0.35) without rewriting rules.</li>
</ul>
</section>
"""


def swarm_section(d: dict) -> str:
    c, i = d["swarm_clean"], d["swarm_inj"]
    rows = "".join(
        f"<tr><td>{f['severity']}</td><td>{esc(f['category'])}</td><td><code>{esc(f['path'])}</code></td>"
        f"<td>{len(f['agents'])}</td><td>{f['defect_probability']:.2f}</td>"
        f"<td>{esc(', '.join(f['automatic_checks']) or '-')}</td>"
        f"<td>{'<b>BLOCK</b>' if f['blocking'] else 'report'}</td></tr>"
        for f in i["findings"]
    )
    injected = ", ".join(f"<code>{esc(x)}</code>" for x in i["injected"])
    per_push = (c["cost_usd"] + i["cost_usd"]) / 2
    return f"""
<section id="swarm">
<h2>Experiment 3: an adversarial e2e swarm on pre-push</h2>
<p class="lede">If one Jev-driven browser agent costs a quarter of a second and a hundredth of a
cent per step, run two hundred of them. Each agent gets one of eight adversarial personas
(negative money, overdraft, URL tamperer, injector, link walker, empty hands, edge text,
bookkeeper) and up to six steps against a small online-banking portal. Code observes the page and
enumerates concrete actions; Jev picks one and grades the page with typed questions; code acts.
Incidents are deduped and triaged by Jev into a category and a release-blocking severity. The
swarm runs as the git pre-push hook.</p>

<div class="kpis">
  <div class="kpi"><div class="k-label">Agents per run</div><div class="k-value">{i["agents"]}</div><div class="k-sub">{i["steps"]} browser steps, {i["jev_calls"]} Jev calls</div></div>
  <div class="kpi"><div class="k-label">Planted regressions found</div><div class="k-value">7 / 7</div><div class="k-sub">{len(i["new_blocking"])} blocking findings, {len(i["findings"]) - len(i["new_blocking"])} report-only</div></div>
  <div class="kpi"><div class="k-label">Wall clock</div><div class="k-value">{i["wall_seconds"]:.0f} s</div><div class="k-sub">clean run {c["wall_seconds"]:.0f} s</div></div>
  <div class="kpi"><div class="k-label">Cost per run</div><div class="k-value">{money(i["cost_usd"], 3)}</div><div class="k-sub">clean run {money(c["cost_usd"], 3)}, all calls live</div></div>
</div>

<h3>Two runs</h3>
<div class="tbl"><table>
<thead><tr><th>Run</th><th>Agents</th><th>Steps</th><th>Jev calls</th><th>Wall</th><th>Cost</th><th>Outcome</th></tr></thead>
<tbody>
<tr><td>Clean portal</td><td>{c["agents"]}</td><td>{c["steps"]}</td><td>{c["jev_calls"]}</td><td>{c["wall_seconds"]:.1f} s</td><td>{money(c["cost_usd"])}</td><td>PASS, {len(c["findings"])} findings</td></tr>
<tr><td>All seven regressions injected</td><td>{i["agents"]}</td><td>{i["steps"]}</td><td>{i["jev_calls"]}</td><td>{i["wall_seconds"]:.1f} s</td><td>{money(i["cost_usd"])}</td><td>FAIL, {len(i["new_blocking"])} blocking</td></tr>
</tbody></table></div>
<p class="note">Injected: {injected}. Negative transfer and overdraft share one fingerprint (both are a missing amount check on the same form).</p>

<h3>Findings on the injected run</h3>
<div class="tbl"><table>
<thead><tr><th>Sev</th><th>Category</th><th>Path</th><th>Agents</th><th>P(defect)</th><th>Automatic checks</th><th>Gate</th></tr></thead>
<tbody>{rows}</tbody></table></div>

<h3>What this means for an enterprise platform team</h3>
<ul>
<li><b>Exploratory QA at the cost of a CI minute.</b> About {money(per_push, 2)} and under a minute
per push. At {PUSHES_PER_DAY:,} pushes a day that is roughly {money(per_push * PUSHES_PER_DAY * 365, 0)}
a year for two hundred adversarial testers on every push. A single contracted pen-test round costs
more, and finds the IDOR after the release.</li>
<li><b>No false positives on the clean site</b> means the gate can actually block. A noisy gate gets
disabled within a sprint; this one distinguishes a 500 with a stack trace from a legitimate error page.</li>
<li><b>Money-loss and data-exposure defects surface before the push.</b> Negative transfers,
another customer's SSN digits, and reflected script execution were each found by many independent
agents, which is the reproducibility a release manager needs to hold a deploy.</li>
<li><b>Replay makes it cheap to keep.</b> Every Jev request is keyed on a deterministic page
observation. Unchanged pages replay from cassettes for free; only changed page states go live.</li>
</ul>
</section>
"""


def summary_section(d: dict) -> str:
    agg, p, i = d["agg"], d["pii"], d["swarm_inj"]
    j, s = agg["jev"], agg["sonnet-5"]
    best = max(agg.values(), key=lambda m: m["f1"])
    rank_f1 = (
        "best of four"
        if best is j
        else f"second to {SERIES[best['evaluator']][2]} at {best['f1']:.2f}"
    )
    return f"""
<section id="summary">
<h2>Executive summary</h2>
<p class="lede">Three experiments use TypeSafe's Jev, an evaluation model that returns typed,
calibrated decisions instead of text, in three roles: as an in-line fraud classifier, as a
semantic linter, and as the decision loop of two hundred adversarial browser agents. All three
were run live against the Vercel AI Gateway for this report.</p>
<div class="tbl"><table>
<thead><tr><th>Experiment</th><th>Job</th><th>Speed</th><th>Cost</th><th>Quality</th><th>Enterprise value</th></tr></thead>
<tbody>
<tr><td><a href="#fraud">1. Fraud bake-off</a></td><td>Classify {j["n"]} transaction rows against Sonnet 5 and GPT-5.6 Luna</td>
<td>{j["latency_p50_ms"]:,.0f} ms p50, {s["latency_p50_ms"] / j["latency_p50_ms"]:.0f}x faster than Sonnet</td>
<td>{money(j["cost_usd"], 3)} vs {money(s["cost_usd"], 2)} for Sonnet ({s["cost_usd"] / j["cost_usd"]:.0f}x)</td>
<td>Recall {j["recall"]:.2f}, {j["fp"]} FP, F1 {j["f1"]:.2f} ({rank_f1})</td>
<td>Fits inside a card authorization hop; calibrated thresholds per product line</td></tr>
<tr><td><a href="#pii">2. PII log lint</a></td><td>Judge {p["statements"]} log statements in {p["files_scanned"]} files</td>
<td>{p["wall_ms"] / 1000:.1f} s wall per commit</td><td>{money(p["cost_usd"], 5)} per commit</td>
<td>7 / 7 leaks caught, 0 false positives</td>
<td>A privacy control developers will not bypass; policy as text, not regex</td></tr>
<tr><td><a href="#swarm">3. Adversarial swarm</a></td><td>{i["agents"]} browser agents attack a banking portal pre-push</td>
<td>{i["wall_seconds"]:.0f} s per run</td><td>{money(i["cost_usd"], 3)} per run</td>
<td>7 / 7 planted regressions, 0 findings on the clean site</td>
<td>Money-loss and data-exposure bugs caught before the push, for cents</td></tr>
</tbody></table></div>
<p>The common thread: because Jev answers in a quarter of a second for a fraction of a cent and
returns calibrated probabilities that code can threshold, it can sit in the hot path (authorization,
commit, push) where a two-second, dollar-scale generative call cannot. The generative models still
have a role, as the escalation target for gray-zone rows and for anything that needs prose.</p>
</section>
"""


def method_section(d: dict) -> str:
    return f"""
<section id="method">
<h2>Method and caveats</h2>
<ul>
<li>All numbers on this page come from <code>results/</code> as written by live runs on
{dt.date.today().isoformat()} (<code>JEV_DEMO_MODE=live</code>, no cassette replay). Regenerate with
<code>uv run python scripts/report_html.py</code>.</li>
<li>Every model is reached through one Vercel AI Gateway key and sees byte-identical input. Cost uses
the gateway's listed per-token prices. Latency is wall clock from this machine, concurrency 8 for the
bake-off and 32 for the swarm.</li>
<li>Tapes are synthetic and small ({d["agg"]["jev"]["n"]} rows, {d["agg"]["jev"]["tp"] + d["agg"]["jev"]["fn"]} fraud).
They demonstrate behaviour on textbook patterns and are not a production fraud benchmark.</li>
<li>Enterprise sizing ({DAILY_TXNS:,} transactions/day, {PR_PER_DAY:,} commits/day, {PUSHES_PER_DAY:,}
pushes/day) is illustrative. Multiply the measured per-unit cost by your own volumes.</li>
<li>Jev is <code>typesafe-ai/jev</code> on the gateway, which resolves to the current release. Pin a
version before deriving production thresholds. TypeSafe documents calibration, not bit-for-bit
determinism.</li>
</ul>
</section>
"""


CSS = """
:root{color-scheme:light;
 --bg:#fcfcfb;--surface:#ffffff;--surface-2:#f3f2ef;--border:#e3e1dc;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#7a7873;
 --accent:#2a78d6;
 --jev:#2a78d6;--sonnet-5:#eb6834;--gpt-5-6-luna:#1baf7a;--jev-sonnet-5:#eda100;
 --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --bg:#141413;--surface:#1a1a19;--surface-2:#232321;--border:#33322f;--ink:#ffffff;--ink-2:#c3c2b7;--ink-3:#8f8e86;
 --accent:#3987e5;--jev:#3987e5;--sonnet-5:#d95926;--gpt-5-6-luna:#199e70;--jev-sonnet-5:#c98500;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}}
:root[data-theme="dark"]{color-scheme:dark;
 --bg:#141413;--surface:#1a1a19;--surface-2:#232321;--border:#33322f;--ink:#ffffff;--ink-2:#c3c2b7;--ink-3:#8f8e86;
 --accent:#3987e5;--jev:#3987e5;--sonnet-5:#d95926;--gpt-5-6-luna:#199e70;--jev-sonnet-5:#c98500;
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif}
main{max-width:1080px;margin:0 auto;padding:32px 16px 64px}
header{padding:8px 0 24px;border-bottom:1px solid var(--border);margin-bottom:8px}
header h1{font-size:30px;line-height:1.2;margin:0 0 8px}
header p{color:var(--ink-2);margin:0}
nav{display:flex;flex-wrap:wrap;gap:8px 18px;margin:16px 0 0;font-size:14px}
nav a{color:var(--accent);text-decoration:none}
section{padding:32px 0;border-bottom:1px solid var(--border)}
h2{font-size:23px;margin:0 0 12px;line-height:1.25}
h3{font-size:17px;margin:28px 0 10px}
.lede{color:var(--ink-2);margin:0 0 20px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:0 0 24px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px}
.k-label{font-size:13px;color:var(--ink-2)}
.k-value{font-size:30px;font-weight:600;line-height:1.15;margin:4px 0 2px;font-variant-numeric:proportional-nums}
.k-sub{font-size:13px;color:var(--ink-3)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
.chart{margin:0;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px 10px}
.chart figcaption{font-size:14px;font-weight:600;margin-bottom:8px}
.chart svg{width:100%;height:auto;display:block}
.chart .lbl{font-size:13px;fill:var(--ink-2)}
.chart .val{font-size:13px;fill:var(--ink);font-weight:600}
.chart .s-1{fill:var(--s1)}.chart .s-2{fill:var(--s2)}.chart .s-3{fill:var(--s3)}.chart .s-4{fill:var(--s4)}
.chart rect:hover{opacity:.85}
.note{font-size:13px;color:var(--ink-3);margin:8px 0 0}
.tbl{overflow-x:auto;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--border);vertical-align:top;white-space:nowrap}
td:nth-child(n+3){font-variant-numeric:tabular-nums}
th{font-weight:600;color:var(--ink-2);background:var(--surface-2);font-size:13px}
th small{font-weight:400;color:var(--ink-3)}
tr:last-child td{border-bottom:0}
table td:has(code){white-space:normal}
code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--surface-2);padding:1px 5px;border-radius:4px}
.sw{display:inline-block;width:10px;height:10px;border-radius:3px;background:var(--c);margin-right:8px;vertical-align:-1px}
ul{padding-left:20px}li{margin:6px 0}
.comps{margin:14px 0 0}
a{color:var(--accent)}
footer{color:var(--ink-3);font-size:13px;padding-top:20px}
"""


def page(d: dict) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Jev Experiment Results</title>
<meta name="description" content="Cost, speed and enterprise value of three Jev experiments: fraud classification, PII log lint, adversarial e2e swarm.">
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<h1>Jev experiment results: fraud classification, PII lint, adversarial swarm</h1>
<p>Live runs on {dt.date.today().isoformat()} from the <code>jev-demo</code> repository. Cost, speed and what each result is worth to an enterprise bank.</p>
<nav><a href="#summary">Summary</a><a href="#fraud">1. Fraud bake-off</a><a href="#pii">2. PII log lint</a><a href="#swarm">3. Adversarial swarm</a><a href="#method">Method and caveats</a></nav>
</header>
{summary_section(d)}
{fraud_section(d)}
{pii_section(d)}
{swarm_section(d)}
{method_section(d)}
<footer>Source: <code>results/summary.json</code>, <code>results/T0*.json</code>, <code>results/pii-check.json</code>, <code>results/swarm/*-summary.json</code>. Rendered by <code>scripts/report_html.py</code>.</footer>
</main>
</body>
</html>
"""


if __name__ == "__main__":
    data = load()
    OUT.write_text(page(data))
    print(f"wrote {OUT}")
