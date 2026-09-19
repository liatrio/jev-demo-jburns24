"""End-to-end tests where Jev is the *oracle*.

Two things a fraud demo can get subtly wrong are (1) a tape that does not
actually encode the situation its title claims, and (2) a report that flags
rows but never tells the analyst what is going on. Both are judgement calls a
classic assertion cannot make. Here Jev grades them with typed questions
(the TypeSafe "speculative fan-out" pattern: several questions, one call),
and the test thresholds the calibrated probabilities. Replayed from cassettes
by default, so still deterministic and offline.
"""

from __future__ import annotations

import pytest

from jev_demo.evaluators import JEV_MODEL, JevEvaluator, validate_choice
from jev_demo.gateway import Gateway
from jev_demo.models import PATTERN_DESCRIPTIONS, Pattern, Tape
from jev_demo.report import report_text
from jev_demo.runner import play_tape
from jev_demo.tapes import load_all

pytestmark = pytest.mark.e2e

TAPES = load_all()
# Jev's boolean is a calibrated probability; 0.5 is its natural decision boundary
# (the same threshold the fraud verdict uses). The typed choice/score answers carry
# the stronger assertions.
PASS = 0.5


@pytest.fixture(params=TAPES, ids=[t.tape_id for t in TAPES])
def tape(request: pytest.FixtureRequest) -> Tape:
    return request.param


def _prob(answer: dict) -> float:
    return answer.get("probability", answer.get("noul"))


async def test_tape_encodes_its_stated_situation(gateway: Gateway, tape: Tape) -> None:
    """Fixture-quality gate: does the whole stream really contain the described scheme?"""
    state = {
        "situation_claimed": tape.situation,
        "accounts": [a.model_dump() for a in tape.accounts],
        "transaction_stream": [t.public() for t in tape.transactions],
    }
    questions = {
        "contains_claimed_situation": {
            "type": "boolean",
            "instructions": (
                "The transaction_stream contains the activity described in situation_claimed."
            ),
        },
        "dominant_pattern": {
            "type": "choice",
            "instructions": "Which pattern dominates the suspicious activity, if any?",
            "criteria": {p.value: d for p, d in PATTERN_DESCRIPTIONS.items()},
        },
    }
    resp = await gateway.evaluate(JEV_MODEL, state, questions)
    answers = resp.body["answers"]
    validate_choice(answers["dominant_pattern"], questions["dominant_pattern"]["criteria"])
    assert _prob(answers["contains_claimed_situation"]) >= PASS, answers
    assert answers["dominant_pattern"]["choice"] == tape.pattern.value, answers


async def test_report_is_actionable_for_an_analyst(gateway: Gateway, tape: Tape) -> None:
    """Output-quality gate: Jev reads the report a human would receive and grades it."""
    result = await play_tape(tape, [JevEvaluator(gateway)])
    report = report_text(tape, result, "jev")
    state = {"expected_situation": tape.situation, "report": report}
    questions = {
        "identifies_situation": {
            "type": "boolean",
            "instructions": (
                "The report's flagged transactions and labels make the expected_situation "
                "evident to a bank fraud analyst. If expected_situation says there is no "
                "fraud, this is true when the report flags nothing or only a few rows."
            ),
        },
        "usefulness": {
            "type": "score",
            "instructions": "How useful is this report to an analyst deciding what to do next?",
            "criteria": [
                "Useless: no flags or no context",
                "Weak: flags rows but labels are missing or wrong",
                "Good: flags the right rows with a plausible pattern label",
                "Excellent: complete, correctly labelled, nothing spurious",
            ],
        },
    }
    resp = await gateway.evaluate(JEV_MODEL, state, questions)
    answers = resp.body["answers"]
    assert _prob(answers["identifies_situation"]) >= PASS, report
    assert answers["usefulness"]["score"] >= 2, (answers, report)
    if tape.pattern != Pattern.NONE:
        assert tape.pattern.value in report, "report must carry the pattern label"
