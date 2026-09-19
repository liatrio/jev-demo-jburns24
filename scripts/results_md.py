"""Render results/*.json as markdown tables (used to fill README and slides-outline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "results"


def money(x: float) -> str:
    return f"${x:.4f}"


def pooled() -> str:
    agg = json.loads((RESULTS / "summary.json").read_text())
    rows = [
        "| evaluator | rows | TP | FP | FN | precision | recall | F1 | pattern acc | "
        "errors | p50 ms | p95 ms | total cost |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for name, m in agg.items():
        pa = "-" if m["pattern_accuracy"] is None else f"{m['pattern_accuracy']:.2f}"
        rows.append(
            f"| {name} | {m['n']} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.2f} | "
            f"{m['recall']:.2f} | {m['f1']:.2f} | {pa} | {m['errors']} | "
            f"{m['latency_p50_ms']:,.0f} | {m['latency_p95_ms']:,.0f} | {money(m['cost_usd'])} |"
        )
    j = agg["jev"]
    mult = []
    for name, m in agg.items():
        if name == "jev":
            continue
        mult.append(
            f"- vs **{name}**: Jev is {m['latency_p50_ms'] / j['latency_p50_ms']:.0f}x faster at "
            f"p50 and {m['cost_usd'] / j['cost_usd']:.0f}x cheaper"
        )
    return "\n".join(rows) + "\n\n" + "\n".join(mult)


def per_tape() -> str:
    files = sorted(p for p in RESULTS.glob("T0*.json"))
    names = list(json.loads(files[0].read_text())["metrics"])
    head = "| tape | " + " | ".join(f"{n} recall / FP" for n in names) + " |"
    sep = "|---|" + "|".join("--:" for _ in names) + "|"
    rows = [head, sep]
    for f in files:
        d = json.loads(f.read_text())
        cells = [f"{d['metrics'][n]['recall']:.2f} / {d['metrics'][n]['fp']}" for n in names]
        rows.append(f"| {d['tape_id']} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


if __name__ == "__main__":
    print("### Pooled across all six tapes (212 rows, 39 fraud)\n")
    print(pooled())
    print("\n### Per tape: recall on fraud rows / false positives\n")
    print(per_tape())
