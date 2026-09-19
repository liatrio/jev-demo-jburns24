"""Rich terminal rendering of tape results."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import Metrics, Tape, TapeResult, Verdict

console = Console()


def fmt_usd(x: float) -> str:
    return f"${x:,.5f}" if x < 0.01 else f"${x:,.4f}"


def metrics_table(title: str, metrics: dict[str, Metrics]) -> Table:
    t = Table(title=title, show_lines=False, expand=False)
    for col in (
        "evaluator",
        "n",
        "TP",
        "FP",
        "FN",
        "precision",
        "recall",
        "F1",
        "pattern acc",
        "errors",
        "p50 ms",
        "p95 ms",
        "cost",
    ):
        t.add_column(col, justify="right" if col != "evaluator" else "left")
    for name, m in metrics.items():
        t.add_row(
            name,
            str(m.n),
            str(m.tp),
            str(m.fp),
            str(m.fn),
            f"{m.precision:.2f}",
            f"{m.recall:.2f}",
            f"{m.f1:.2f}",
            "-" if m.pattern_accuracy is None else f"{m.pattern_accuracy:.2f}",
            str(m.errors),
            f"{m.latency_p50_ms:,.0f}",
            f"{m.latency_p95_ms:,.0f}",
            fmt_usd(m.cost_usd),
        )
    return t


def tape_header(tape: Tape) -> Panel:
    body = (
        f"[bold]{tape.title}[/bold]  ({tape.tape_id})\n"
        f"{tape.situation}\n\n"
        f"transactions: {len(tape.transactions)}   fraud rows: {tape.fraud_count}   "
        f"pattern: {tape.pattern.value}"
    )
    return Panel(body, title="TAPE", expand=False)


def disagreements(tape: Tape, result: TapeResult, limit: int = 12) -> Table:
    names = list(result.verdicts)
    by_txn: dict[str, dict[str, Verdict]] = {}
    for name, vs in result.verdicts.items():
        for v in vs:
            by_txn.setdefault(v.txn_id, {})[name] = v
    t = Table(title=f"Rows where evaluators disagree or miss (first {limit})", expand=False)
    t.add_column("txn")
    t.add_column("truth")
    for n in names:
        t.add_column(n)
    t.add_column("amount", justify="right")
    t.add_column("merchant")
    shown = 0
    for txn in tape.transactions:
        vs = by_txn.get(txn.txn_id, {})
        flags = [vs[n].is_fraud if n in vs else None for n in names]
        wrong = any(f is not None and f != txn.is_fraud for f in flags)
        if not wrong and len(set(flags)) <= 1:
            continue
        cells = []
        for n in names:
            v = vs.get(n)
            if v is None:
                cells.append("-")
                continue
            ok = v.is_fraud == txn.is_fraud
            colour = "green" if ok else "red"
            label = "FRAUD" if v.is_fraud else "ok"
            prob = f" {v.fraud_probability:.2f}" if v.fraud_probability is not None else ""
            cells.append(
                f"[{colour}]{label}{prob}[/{colour}]" if not v.error else "[yellow]ERR[/yellow]"
            )
        t.add_row(
            txn.txn_id,
            "[bold red]FRAUD[/bold red]" if txn.is_fraud else "ok",
            *cells,
            f"{txn.amount_usd:,.2f}",
            f"{txn.merchant} ({txn.city}, {txn.country})",
        )
        shown += 1
        if shown >= limit:
            break
    if shown == 0:
        t.add_row("(none)", "", *[""] * len(names), "", "")
    return t


def report_text(tape: Tape, result: TapeResult, evaluator: str) -> str:
    """Plain-text analyst report for one evaluator's run of a tape.

    This is the artifact a downstream case-management system or a human
    analyst would receive, and it is what the jev-judged e2e tests grade.
    """
    m = result.metrics[evaluator]
    truth = {t.txn_id: t for t in tape.transactions}
    flagged = [v for v in result.verdicts[evaluator] if v.is_fraud]
    lines = [
        f"FRAUD REVIEW REPORT - tape {tape.tape_id} ({tape.title})",
        f"evaluator: {evaluator}   transactions reviewed: {m.n}   flagged: {len(flagged)}",
        "",
        "Flagged transactions:",
    ]
    for v in flagged:
        t = truth[v.txn_id]
        prob = f"p={v.fraud_probability:.2f}" if v.fraud_probability is not None else ""
        pattern = v.pattern.value if v.pattern else "unclassified"
        lines.append(
            f"- {t.timestamp} {t.txn_id} {t.account_id} {t.direction} {t.amount_usd:,.2f} USD "
            f"{t.channel} at {t.merchant} ({t.city}, {t.country}) -> {pattern} {prob}"
        )
    if not flagged:
        lines.append("- none")
    return "\n".join(lines)


def summary_line(metrics: dict[str, Metrics]) -> str:
    if "jev" not in metrics:
        return ""
    j = metrics["jev"]
    parts = []
    for name, m in metrics.items():
        if name == "jev":
            continue
        speed = m.latency_p50_ms / j.latency_p50_ms if j.latency_p50_ms else float("inf")
        cost = m.cost_usd / j.cost_usd if j.cost_usd else float("inf")
        parts.append(f"vs {name}: jev is {speed:,.0f}x faster (p50) and {cost:,.0f}x cheaper")
    return "   ".join(parts)
