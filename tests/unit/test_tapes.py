"""Tape generator contracts: deterministic, well-formed, ground truth consistent."""

from __future__ import annotations

import json

from jev_demo.models import Pattern
from jev_demo.runner import build_states
from jev_demo.tapes import GENERATORS, TAPES_DIR, generate_all, load_all


def test_generation_is_deterministic() -> None:
    a = [json.dumps(t.model_dump(mode="json"), sort_keys=True) for t in generate_all()]
    b = [json.dumps(t.model_dump(mode="json"), sort_keys=True) for t in generate_all()]
    assert a == b


def test_committed_tapes_match_generators() -> None:
    """tapes/*.json must be exactly what the generators produce (run `task tapes:generate`)."""
    on_disk = {t.tape_id: t.model_dump(mode="json") for t in load_all(TAPES_DIR)}
    fresh = {t.tape_id: t.model_dump(mode="json") for t in generate_all()}
    assert on_disk == fresh


def test_every_generator_has_one_tape() -> None:
    assert len(load_all()) == len(GENERATORS)


def test_ground_truth_is_consistent() -> None:
    for tape in load_all():
        ids = [t.txn_id for t in tape.transactions]
        assert len(ids) == len(set(ids)), "txn ids must be unique"
        timestamps = [t.timestamp for t in tape.transactions]
        assert timestamps == sorted(timestamps), f"{tape.tape_id} must be in time order"
        for t in tape.transactions:
            assert t.is_fraud == (t.pattern != Pattern.NONE)
            if t.is_fraud:
                assert t.pattern == tape.pattern, "fraud rows carry the tape's pattern"
        if tape.pattern == Pattern.NONE:
            assert tape.fraud_count == 0
        else:
            assert tape.fraud_count >= 2


def test_public_view_hides_ground_truth() -> None:
    for tape in load_all():
        for state in build_states(tape):
            blob = json.dumps(state)
            assert "is_fraud" not in blob
            assert '"pattern"' not in blob
            for row in state["recent_history"]:
                assert "is_fraud" not in row


def test_stream_semantics_only_show_the_past() -> None:
    tape = load_all()[1]
    states = build_states(tape)
    seen: dict[str, list[str]] = {}
    for txn, state in zip(tape.transactions, states, strict=True):
        hist_ids = [h["txn_id"] for h in state["recent_history"]]
        assert txn.txn_id not in hist_ids
        assert hist_ids == seen.get(txn.account_id, [])[-12:]
        seen.setdefault(txn.account_id, []).append(txn.txn_id)
