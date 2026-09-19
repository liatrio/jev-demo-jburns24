"""Evaluators: Jev (System One) and generative LLMs behind one interface.

Both families receive the *same* state object per transaction: the account
profile, the last N transactions the bank has already seen for that account,
and the transaction under review. Jev is asked typed questions in a single
speculative fan-out call (verdict, pattern, risk). LLMs are asked for the same
fields as a JSON object. Ground truth never reaches either.
"""

from __future__ import annotations

import json
import math
import time
from abc import ABC, abstractmethod
from typing import Any

from .gateway import Gateway, GatewayResponse
from .models import PATTERN_DESCRIPTIONS, Pattern, Verdict

JEV_MODEL = "typesafe-ai/jev"
SONNET_MODEL = "anthropic/claude-sonnet-5"
GPT_MODEL = "openai/gpt-5.6-luna"

# USD per token, from the gateway's /v1/models listing (2026-09-19).
PRICING: dict[str, tuple[float, float]] = {
    JEV_MODEL: (0.000000042, 0.0),
    SONNET_MODEL: (0.000002, 0.00001),
    GPT_MODEL: (0.0000002, 0.0000012),
}

FRAUD_THRESHOLD = 0.5


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return input_tokens * pin + output_tokens * pout


class Evaluator(ABC):
    name: str
    model: str

    def __init__(self, gateway: Gateway) -> None:
        self.gw = gateway

    @abstractmethod
    async def classify(self, state: dict[str, Any]) -> Verdict: ...

    def _error(self, txn_id: str, exc: Exception, started: float) -> Verdict:
        return Verdict(
            txn_id=txn_id,
            evaluator=self.name,
            is_fraud=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}"[:400],
        )


# ------------------------------------------------------------------- Jev
class JevAnswerError(ValueError):
    """The model returned an answer that violates the typed contract."""


def validate_choice(answer: dict[str, Any], criteria: dict[str, str]) -> None:
    """Port of jev-ultrafast's ``validate_choice`` contract.

    The chosen option must be one we offered, probabilities must cover exactly
    the offered options, be finite, in [0, 1], sum to ~1, and the choice must be
    the argmax. Anything else is rejected rather than trusted.
    """
    choice = answer.get("choice")
    probs = answer.get("probabilities")
    if choice not in criteria:
        raise JevAnswerError(f"choice {choice!r} not in offered options")
    if not isinstance(probs, dict) or set(probs) != set(criteria):
        raise JevAnswerError("probability keys do not match offered options")
    vals = list(probs.values())
    if any(not isinstance(v, int | float) or not math.isfinite(v) or v < 0 or v > 1 for v in vals):
        raise JevAnswerError("probabilities must be finite numbers in [0, 1]")
    if abs(sum(vals) - 1.0) > 0.02:
        raise JevAnswerError(f"probabilities sum to {sum(vals):.3f}, expected 1")
    if max(probs, key=probs.get) != choice:  # type: ignore[arg-type]
        raise JevAnswerError("choice is not the argmax of probabilities")
    conf = answer.get("confidence")
    if conf is not None and not (0 <= conf <= 1):
        raise JevAnswerError(f"confidence {conf} out of range")


def jev_questions() -> dict[str, dict[str, Any]]:
    """Speculative fan-out: every question evaluated in parallel in one call."""
    return {
        "is_fraud": {
            # TypeSafe calls this primitive "noul"; the gateway dialect spells it "boolean".
            "type": "boolean",
            "instructions": (
                "The transaction under review is fraudulent or is part of a fraud or "
                "money-laundering scheme, judged against the account profile and the "
                "recent history the bank has already seen."
            ),
        },
        "pattern": {
            "type": "choice",
            "instructions": "Which situation best describes the transaction under review?",
            "criteria": {p.value: d for p, d in PATTERN_DESCRIPTIONS.items()},
        },
        "risk": {
            "type": "score",
            "instructions": "How much investigation does the transaction under review warrant?",
            "criteria": [
                "Routine; approve automatically",
                "Mildly unusual; log and monitor",
                "Suspicious; hold and review",
                "Almost certainly fraud; block and alert",
            ],
        },
    }


class JevEvaluator(Evaluator):
    name = "jev"
    model = JEV_MODEL

    async def classify(self, state: dict[str, Any]) -> Verdict:
        txn_id = state["transaction_under_review"]["txn_id"]
        started = time.perf_counter()
        questions = jev_questions()
        try:
            resp = await self.gw.evaluate(self.model, state, questions)
            return self._parse(txn_id, resp, questions)
        except Exception as exc:  # noqa: BLE001 - surfaced in the report
            return self._error(txn_id, exc, started)

    def _parse(self, txn_id: str, resp: GatewayResponse, questions: dict) -> Verdict:
        body = resp.body
        answers = body["answers"]
        fraud_ans = answers["is_fraud"]
        # gateway dialect returns {"type":"boolean","probability":p}; native returns {"noul":p}
        p = fraud_ans.get("probability", fraud_ans.get("noul"))
        if p is None or not (0 <= p <= 1):
            raise JevAnswerError(f"bad fraud probability {p!r}")
        pattern_ans = answers["pattern"]
        validate_choice(pattern_ans, questions["pattern"]["criteria"])
        confidence = pattern_ans.get("confidence")
        if confidence is None:
            confidence = (
                body.get("providerMetadata", {})
                .get("typesafe", {})
                .get("confidence", {})
                .get("pattern")
            )
        usage = body.get("usage", {})
        in_tok = usage.get("inputTokens", usage.get("input_tokens", 0))
        out_tok = usage.get("outputTokens", usage.get("output_tokens", 0))
        risk = answers.get("risk", {}).get("score")
        return Verdict(
            txn_id=txn_id,
            evaluator=self.name,
            is_fraud=p >= FRAUD_THRESHOLD,
            fraud_probability=p,
            pattern=Pattern(pattern_ans["choice"]),
            confidence=confidence,
            reason=f"risk_score={risk}",
            latency_ms=resp.latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost_for(self.model, in_tok, out_tok),
            raw={"answers": answers, "model": body.get("model")},
        )


# ------------------------------------------------------------------- LLMs
SYSTEM_PROMPT = """You are a fraud and AML analyst at a large retail bank. You review one
transaction at a time as it arrives in the transaction log, using only the account profile and
the recent history the bank has already seen for that account.

Respond with a single JSON object and nothing else:
{
  "is_fraud": boolean,               // true if fraudulent or part of a fraud/AML scheme
  "fraud_probability": number,       // your calibrated probability in [0, 1]
  "pattern": string,                 // exactly one of: %s
  "reason": string                   // one sentence
}
Pattern definitions:
%s"""


def llm_system_prompt() -> str:
    names = ", ".join(p.value for p in Pattern)
    defs = "\n".join(f"- {p.value}: {d}" for p, d in PATTERN_DESCRIPTIONS.items())
    return SYSTEM_PROMPT % (names, defs)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in LLM output: {text[:120]!r}")
    return json.loads(text[start : end + 1])


class LLMEvaluator(Evaluator):
    def __init__(
        self, gateway: Gateway, name: str, model: str, reasoning_effort: str | None = None
    ) -> None:
        super().__init__(gateway)
        self.name = name
        self.model = model
        self.reasoning_effort = reasoning_effort

    async def classify(self, state: dict[str, Any]) -> Verdict:
        txn_id = state["transaction_under_review"]["txn_id"]
        started = time.perf_counter()
        messages = [
            {"role": "system", "content": llm_system_prompt()},
            {"role": "user", "content": json.dumps(state, indent=1)},
        ]
        try:
            # Reasoning tokens count against max_tokens on the gateway; leave headroom so
            # the visible JSON is never truncated.
            resp = await self.gw.chat(
                self.model, messages, max_tokens=2000, reasoning_effort=self.reasoning_effort
            )
            return self._parse(txn_id, resp)
        except Exception as exc:  # noqa: BLE001
            return self._error(txn_id, exc, started)

    def _parse(self, txn_id: str, resp: GatewayResponse) -> Verdict:
        body = resp.body
        content = body["choices"][0]["message"]["content"] or ""
        parsed = _extract_json(content)
        usage = body.get("usage", {})
        in_tok = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        pattern_raw = str(parsed.get("pattern", "none")).strip().lower()
        try:
            pattern = Pattern(pattern_raw)
        except ValueError:
            pattern = None
        prob = parsed.get("fraud_probability")
        prob = float(prob) if isinstance(prob, int | float) else None
        return Verdict(
            txn_id=txn_id,
            evaluator=self.name,
            is_fraud=bool(parsed.get("is_fraud", False)),
            fraud_probability=prob,
            pattern=pattern,
            confidence=None,
            reason=str(parsed.get("reason", ""))[:300],
            latency_ms=resp.latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost_for(self.model, in_tok, out_tok),
            raw={"content": content[:600]},
        )


# ------------------------------------------------------------------ hybrid
class ConfidenceGatedEvaluator(Evaluator):
    """Jev first; escalate to an LLM only when Jev is unsure.

    TypeSafe's "confidence-gated routing" pattern. The gray zone is the band of
    fraud probability around the threshold; outside it Jev's answer stands.
    """

    name = "jev+escalate"
    model = JEV_MODEL

    def __init__(
        self, gateway: Gateway, fallback: LLMEvaluator, gray_zone: tuple[float, float] = (0.3, 0.7)
    ) -> None:
        super().__init__(gateway)
        self.primary = JevEvaluator(gateway)
        self.fallback = fallback
        self.gray_zone = gray_zone
        self.name = f"jev+{fallback.name}"

    async def classify(self, state: dict[str, Any]) -> Verdict:
        first = await self.primary.classify(state)
        p = first.fraud_probability
        if (
            first.error is None
            and p is not None
            and not (self.gray_zone[0] <= p <= self.gray_zone[1])
        ):
            return first.model_copy(
                update={"evaluator": self.name, "reason": f"jev decided ({first.reason})"}
            )
        second = await self.fallback.classify(state)
        return second.model_copy(
            update={
                "evaluator": self.name,
                "latency_ms": first.latency_ms + second.latency_ms,
                "input_tokens": first.input_tokens + second.input_tokens,
                "output_tokens": first.output_tokens + second.output_tokens,
                "cost_usd": first.cost_usd + second.cost_usd,
                "reason": f"escalated (jev p={p}): {second.reason}",
            }
        )


# ---------------------------------------------------------------- registry
def build_evaluators(
    gateway: Gateway, names: list[str], effort: str | None = None
) -> list[Evaluator]:
    out: list[Evaluator] = []
    for n in names:
        n = n.strip().lower()
        if n == "jev":
            out.append(JevEvaluator(gateway))
        elif n in ("sonnet", "sonnet-5", "claude"):
            out.append(LLMEvaluator(gateway, "sonnet-5", SONNET_MODEL, effort))
        elif n in ("gpt", "luna", "gpt-5.6-luna"):
            out.append(LLMEvaluator(gateway, "gpt-5.6-luna", GPT_MODEL, effort))
        elif n in ("hybrid", "jev+sonnet"):
            out.append(
                ConfidenceGatedEvaluator(
                    gateway, LLMEvaluator(gateway, "sonnet-5", SONNET_MODEL, effort)
                )
            )
        else:
            raise ValueError(f"unknown evaluator {n!r}; choose from jev, sonnet, gpt, hybrid")
    return out


DEFAULT_EVALUATORS = ["jev", "sonnet", "gpt"]
