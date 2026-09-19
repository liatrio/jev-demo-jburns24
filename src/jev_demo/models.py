"""Domain models: transactions, tapes, verdicts and per-tape results."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Pattern(StrEnum):
    """Known fraud situations a tape can encode. ``NONE`` means legitimate."""

    NONE = "none"
    ACCOUNT_TAKEOVER = "account_takeover"
    CARD_TESTING = "card_testing"
    STRUCTURING = "structuring"
    IMPOSSIBLE_TRAVEL = "impossible_travel"
    MULE_ACCOUNT = "mule_account"


PATTERN_DESCRIPTIONS: dict[Pattern, str] = {
    Pattern.NONE: "Legitimate activity consistent with the account's history",
    Pattern.ACCOUNT_TAKEOVER: (
        "Credentials or device compromised: new device/country, then high-value "
        "card-not-present spend or transfers far outside the customer's history"
    ),
    Pattern.CARD_TESTING: (
        "Stolen card being probed: a burst of tiny online authorizations at many "
        "unrelated merchants within minutes, often followed by one large purchase"
    ),
    Pattern.STRUCTURING: (
        "Cash deposits deliberately kept just under the 10,000 USD reporting "
        "threshold, repeated across days or branches"
    ),
    Pattern.IMPOSSIBLE_TRAVEL: (
        "Two card-present transactions in locations too far apart to travel "
        "between in the elapsed time"
    ),
    Pattern.MULE_ACCOUNT: (
        "Account receiving inbound transfers from many unrelated senders and "
        "forwarding almost all of it out within hours"
    ),
}


class Transaction(BaseModel):
    """One row of a bank's transaction log, plus hidden ground truth."""

    txn_id: str
    timestamp: str  # ISO-8601, UTC
    account_id: str
    amount_usd: float
    currency: str = "USD"
    channel: Literal["card_present", "card_not_present", "ach", "wire", "cash", "p2p"]
    direction: Literal["debit", "credit"]
    merchant: str
    merchant_category: str
    city: str
    country: str
    device_id: str | None = None
    counterparty: str | None = None
    # ---- ground truth (never shown to evaluators) ----
    is_fraud: bool
    pattern: Pattern = Pattern.NONE

    def public(self) -> dict:
        """The record an evaluator is allowed to see."""
        return self.model_dump(exclude={"is_fraud", "pattern"})


class AccountProfile(BaseModel):
    """What the bank already knows about the customer before the tape starts."""

    account_id: str
    customer_segment: str
    home_city: str
    home_country: str
    typical_monthly_spend_usd: float
    typical_txn_usd: float
    usual_merchant_categories: list[str]
    known_devices: list[str]
    account_age_days: int


class Tape(BaseModel):
    """A stream of transactions encoding a known situation."""

    tape_id: str
    title: str
    situation: str = Field(description="Plain-English description of what the tape encodes")
    pattern: Pattern
    seed: int
    accounts: list[AccountProfile]
    transactions: list[Transaction]

    @property
    def fraud_count(self) -> int:
        return sum(t.is_fraud for t in self.transactions)


class Verdict(BaseModel):
    """What an evaluator says about one transaction."""

    txn_id: str
    evaluator: str
    is_fraud: bool
    fraud_probability: float | None = None
    pattern: Pattern | None = None
    confidence: float | None = None
    reason: str | None = None
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict | None = None
    error: str | None = None


class Metrics(BaseModel):
    evaluator: str
    n: int
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float
    recall: float
    f1: float
    accuracy: float
    pattern_accuracy: float | None
    errors: int
    latency_p50_ms: float
    latency_p95_ms: float
    latency_total_ms: float
    cost_usd: float
    input_tokens: int
    output_tokens: int


class TapeResult(BaseModel):
    tape_id: str
    verdicts: dict[str, list[Verdict]]  # evaluator -> verdicts (in tape order)
    metrics: dict[str, Metrics]
