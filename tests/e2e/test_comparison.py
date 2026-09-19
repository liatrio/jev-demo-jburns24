"""End-to-end: the headline comparison must hold on the recorded runs.

Latency and token usage are taken from the cassettes (observed at record
time), so this is a regression guard on the demo's claims, not a live
benchmark. If the gap closes, the test fails and the slides must change.
"""

from __future__ import annotations

import pytest

from jev_demo.evaluators import build_evaluators
from jev_demo.gateway import Gateway
from jev_demo.runner import aggregate, play_tape
from jev_demo.tapes import load_all

pytestmark = pytest.mark.e2e

TAPES = load_all()


async def test_jev_is_faster_and_cheaper_than_both_llms(gateway: Gateway) -> None:
    evaluators = build_evaluators(gateway, ["jev", "sonnet", "gpt"])
    results = [await play_tape(t, evaluators) for t in TAPES]
    agg = aggregate(results, TAPES)
    jev = agg["jev"]
    for name in ("sonnet-5", "gpt-5.6-luna"):
        llm = agg[name]
        assert jev.latency_p50_ms * 3 < llm.latency_p50_ms, (
            name,
            jev.latency_p50_ms,
            llm.latency_p50_ms,
        )
        assert jev.cost_usd * 3 < llm.cost_usd, (name, jev.cost_usd, llm.cost_usd)


async def test_jev_accuracy_is_competitive_with_the_best_llm(gateway: Gateway) -> None:
    evaluators = build_evaluators(gateway, ["jev", "sonnet", "gpt"])
    results = [await play_tape(t, evaluators) for t in TAPES]
    agg = aggregate(results, TAPES)
    best_llm_f1 = max(agg["sonnet-5"].f1, agg["gpt-5.6-luna"].f1)
    assert agg["jev"].f1 >= best_llm_f1 - 0.15, {k: round(v.f1, 3) for k, v in agg.items()}
    assert agg["jev"].recall >= 0.8


async def test_hybrid_escalates_only_the_gray_zone(gateway: Gateway) -> None:
    evaluators = build_evaluators(gateway, ["jev", "hybrid"])
    tape = next(t for t in TAPES if t.tape_id == "T01-ato")
    result = await play_tape(tape, evaluators)
    hybrid = result.verdicts["jev+sonnet-5"]
    escalated = [v for v in hybrid if v.reason and v.reason.startswith("escalated")]
    assert 0 < len(escalated) < len(hybrid)
    assert result.metrics["jev+sonnet-5"].cost_usd < result.metrics["jev"].cost_usd * 200
