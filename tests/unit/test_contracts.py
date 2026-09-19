"""Answer-contract and parsing tests. Offline, no gateway.

``validate_choice`` is a port of jev-ultrafast's contract: the model's answer
is checked against what we offered, never trusted.
"""

from __future__ import annotations

import math

import pytest

from jev_demo.evaluators import (
    JevAnswerError,
    _extract_json,
    jev_questions,
    llm_system_prompt,
    validate_choice,
)
from jev_demo.gateway import request_key
from jev_demo.models import Pattern

CRITERIA = {"a": "first", "b": "second", "c": "third"}


def good() -> dict:
    return {"choice": "b", "probabilities": {"a": 0.1, "b": 0.7, "c": 0.2}, "confidence": 0.8}


def test_valid_answer_passes() -> None:
    validate_choice(good(), CRITERIA)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda a: a.__setitem__("choice", "zzz"), "not in offered"),
        (lambda a: a["probabilities"].pop("c"), "keys do not match"),
        (lambda a: a["probabilities"].__setitem__("d", 0.0), "keys do not match"),
        (lambda a: a["probabilities"].__setitem__("b", math.nan), "finite"),
        (lambda a: a["probabilities"].__setitem__("b", -0.1), "finite"),
        (lambda a: a["probabilities"].__setitem__("b", 0.9), "sum to"),
        (lambda a: a.__setitem__("choice", "a"), "argmax"),
        (lambda a: a.__setitem__("confidence", 1.7), "out of range"),
    ],
)
def test_invalid_answers_are_rejected(mutate, message) -> None:
    a = good()
    mutate(a)
    with pytest.raises(JevAnswerError, match=message):
        validate_choice(a, CRITERIA)


def test_rounded_tie_accepts_either_argmax() -> None:
    """The gateway rounds to 2 decimals, so 0.5/0.5 ties happen; both choices are argmaxes."""
    tie = {"choice": "c", "probabilities": {"a": 0.0, "b": 0.5, "c": 0.5}}
    validate_choice(tie, CRITERIA)
    tie["choice"] = "b"
    validate_choice(tie, CRITERIA)


def test_questions_cover_every_pattern() -> None:
    q = jev_questions()
    assert set(q["pattern"]["criteria"]) == {p.value for p in Pattern}
    assert q["is_fraud"]["type"] == "boolean"
    assert len(q["risk"]["criteria"]) >= 2


def test_llm_prompt_lists_every_pattern() -> None:
    prompt = llm_system_prompt()
    for p in Pattern:
        assert p.value in prompt


@pytest.mark.parametrize(
    "text",
    [
        '{"is_fraud": true}',
        'Sure! {"is_fraud": true} hope that helps',
        '```json\n{"is_fraud": true}\n```',
    ],
)
def test_extract_json_is_forgiving(text: str) -> None:
    assert _extract_json(text) == {"is_fraud": True}


def test_extract_json_rejects_empty() -> None:
    with pytest.raises(ValueError):
        _extract_json("")


def test_request_key_is_order_independent() -> None:
    assert request_key("/p", {"a": 1, "b": 2}) == request_key("/p", {"b": 2, "a": 1})
    assert request_key("/p", {"a": 1}) != request_key("/q", {"a": 1})
