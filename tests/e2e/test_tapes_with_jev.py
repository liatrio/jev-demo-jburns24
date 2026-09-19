"""End-to-end: play every tape through Jev and hold it to the ground truth.

Deterministic by construction: in the default ``replay`` mode every gateway
response comes from ``tests/cassettes`` (real recorded Jev answers), so this
runs offline in the pre-commit hook. ``JEV_DEMO_MODE=record`` refreshes.

Following jev-ultrafast, the *code* judges the model here: verdicts are scored
against labels the model never saw, and every answer is checked against the
typed contract before it counts.
"""

from __future__ import annotations

import pytest

from jev_demo.evaluators import JevEvaluator, jev_questions, validate_choice
from jev_demo.gateway import Gateway
from jev_demo.models import Pattern, Tape
from jev_demo.runner import build_states, play_tape, score
from jev_demo.tapes import load_all

pytestmark = pytest.mark.e2e

TAPES = load_all()

# Minimum bar Jev must clear per tape. Recall is over the tape's fraud rows;
# max_fp bounds false alarms on legitimate rows.
EXPECTATIONS: dict[str, dict[str, float]] = {
    "T00-clean": {"min_recall": 1.0, "max_fp": 4},
    "T01-ato": {"min_recall": 0.75, "max_fp": 4},
    "T02-cardtest": {"min_recall": 0.80, "max_fp": 4},
    "T03-structuring": {"min_recall": 0.75, "max_fp": 4},
    "T04-travel": {"min_recall": 1.0, "max_fp": 4},
    "T05-mule": {"min_recall": 0.75, "max_fp": 4},
}


@pytest.fixture(params=TAPES, ids=[t.tape_id for t in TAPES])
def tape(request: pytest.FixtureRequest) -> Tape:
    return request.param


async def test_jev_flags_the_known_situation(gateway: Gateway, tape: Tape) -> None:
    result = await play_tape(tape, [JevEvaluator(gateway)])
    verdicts = result.verdicts["jev"]
    assert all(v.error is None for v in verdicts), [v.error for v in verdicts if v.error]

    m = score(tape, verdicts)
    exp = EXPECTATIONS[tape.tape_id]
    assert m.recall >= exp["min_recall"], f"recall {m.recall:.2f} on {tape.tape_id}"
    assert m.fp <= exp["max_fp"], f"{m.fp} false positives on {tape.tape_id}"


async def test_jev_answers_honour_the_typed_contract(gateway: Gateway, tape: Tape) -> None:
    ev = JevEvaluator(gateway)
    criteria = jev_questions()["pattern"]["criteria"]
    for state in build_states(tape):
        v = await ev.classify(state)
        assert v.error is None
        assert v.raw is not None
        validate_choice(v.raw["answers"]["pattern"], criteria)
        assert v.fraud_probability is not None and 0.0 <= v.fraud_probability <= 1.0
        assert v.pattern in set(Pattern)
        assert v.is_fraud == (v.fraud_probability >= 0.5)


async def test_jev_names_the_pattern_on_true_positives(gateway: Gateway, tape: Tape) -> None:
    if tape.pattern == Pattern.NONE:
        pytest.skip("clean tape has no fraud pattern to name")
    result = await play_tape(tape, [JevEvaluator(gateway)])
    m = score(tape, result.verdicts["jev"])
    assert m.pattern_accuracy is not None
    assert m.pattern_accuracy >= 0.5, f"pattern accuracy {m.pattern_accuracy:.2f}"


async def test_replay_is_bit_for_bit_deterministic(gateway: Gateway, tape: Tape) -> None:
    ev = JevEvaluator(gateway)
    states = build_states(tape)[:5]
    first = [(await ev.classify(s)).model_dump(exclude={"latency_ms"}) for s in states]
    second = [(await ev.classify(s)).model_dump(exclude={"latency_ms"}) for s in states]
    assert first == second


@pytest.mark.live
async def test_live_jev_is_repeatable_on_identical_state(gateway: Gateway) -> None:
    """Empirical repeatability check; TypeSafe documents calibration, not determinism."""
    tape = next(t for t in TAPES if t.tape_id == "T01-ato")
    state = build_states(tape)[24]
    ev = JevEvaluator(gateway)
    probs = {round((await ev.classify(state)).fraud_probability or -1, 2) for _ in range(3)}
    assert len(probs) == 1, f"jev gave differing probabilities for identical state: {probs}"
