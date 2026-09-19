"""Play a tape through evaluators and score the outcome."""

from __future__ import annotations

import asyncio
import statistics
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from .evaluators import Evaluator
from .models import AccountProfile, Metrics, Pattern, Tape, TapeResult, Transaction, Verdict

HISTORY_WINDOW = 12


def build_states(tape: Tape, window: int = HISTORY_WINDOW) -> list[dict[str, Any]]:
    """Walk the tape in order, emitting the state an evaluator sees for each row.

    This is the "stream" semantics: at row *i* the bank knows the account
    profile and the previous transactions on that account, nothing after.
    """
    profiles: dict[str, AccountProfile] = {a.account_id: a for a in tape.accounts}
    history: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=window))
    states: list[dict[str, Any]] = []
    for txn in tape.transactions:
        profile = profiles[txn.account_id]
        states.append(
            {
                "account_profile": profile.model_dump(),
                "recent_history": list(history[txn.account_id]),
                "transaction_under_review": txn.public(),
            }
        )
        history[txn.account_id].append(txn.public())
    return states


def score(tape: Tape, verdicts: list[Verdict]) -> Metrics:
    truth: dict[str, Transaction] = {t.txn_id: t for t in tape.transactions}
    tp = fp = tn = fn = 0
    pattern_hits = 0
    pattern_total = 0
    for v in verdicts:
        t = truth[v.txn_id]
        if v.is_fraud and t.is_fraud:
            tp += 1
        elif v.is_fraud and not t.is_fraud:
            fp += 1
        elif not v.is_fraud and t.is_fraud:
            fn += 1
        else:
            tn += 1
        if t.is_fraud and v.is_fraud:
            pattern_total += 1
            pattern_hits += int(v.pattern == t.pattern)
    n = len(verdicts)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    lat = sorted(v.latency_ms for v in verdicts) or [0.0]
    return Metrics(
        evaluator=verdicts[0].evaluator if verdicts else "?",
        n=n,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        accuracy=(tp + tn) / n if n else 0.0,
        pattern_accuracy=pattern_hits / pattern_total if pattern_total else None,
        errors=sum(1 for v in verdicts if v.error),
        latency_p50_ms=statistics.median(lat),
        latency_p95_ms=lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))],
        latency_total_ms=sum(lat),
        cost_usd=sum(v.cost_usd for v in verdicts),
        input_tokens=sum(v.input_tokens for v in verdicts),
        output_tokens=sum(v.output_tokens for v in verdicts),
    )


async def play_tape(
    tape: Tape,
    evaluators: list[Evaluator],
    *,
    on_verdict: Callable[[Verdict], None] | None = None,
) -> TapeResult:
    states = build_states(tape)

    async def run_one(ev: Evaluator) -> list[Verdict]:
        async def go(state: dict[str, Any]) -> Verdict:
            v = await ev.classify(state)
            if on_verdict:
                on_verdict(v)
            return v

        return list(await asyncio.gather(*(go(s) for s in states)))

    per_eval = await asyncio.gather(*(run_one(ev) for ev in evaluators))
    verdicts = {ev.name: vs for ev, vs in zip(evaluators, per_eval, strict=True)}
    metrics = {name: score(tape, vs) for name, vs in verdicts.items()}
    return TapeResult(tape_id=tape.tape_id, verdicts=verdicts, metrics=metrics)


def aggregate(results: list[TapeResult], tapes: list[Tape]) -> dict[str, Metrics]:
    """Micro-average metrics across tapes (pooled confusion matrix)."""
    pooled: dict[str, list[Verdict]] = defaultdict(list)
    for r in results:
        for name, vs in r.verdicts.items():
            pooled[name].extend(vs)
    all_txns = {t.txn_id: t for tape in tapes for t in tape.transactions}
    out: dict[str, Metrics] = {}
    for name, vs in pooled.items():
        pooled_tape = Tape(
            tape_id="ALL",
            title="all tapes",
            situation="pooled",
            pattern=Pattern.NONE,
            seed=0,
            accounts=[],
            transactions=[all_txns[v.txn_id] for v in vs],
        )
        out[name] = score(pooled_tape, vs)
    return out
