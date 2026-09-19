"""Demo fixture: the same service with its logging done right.

Every line carries only operational data or opaque identifiers, including
the kinds of line a keyword scanner would false-alarm on (``email`` in a
variable name that is masked, ``card`` in a brand label, a hashed user key).
Ground truth lives in ``expected.json``.
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def onboard(customer_id: str, email: str) -> None:
    logger.info("onboarding started customer_id=%s", customer_id)
    logger.info("welcome email queued for %s", mask_email(email))
    logger.debug("kyc check passed customer_id=%s provider=onfido", customer_id)


def charge(customer_id: str, card_pan: str, amount_cents: int, txn_id: str) -> None:
    card_brand = "visa" if card_pan.startswith("4") else "other"
    logger.info("charging %d cents txn_id=%s card_brand=%s", amount_cents, txn_id, card_brand)
    try:
        _authorize(card_pan, amount_cents)
    except RuntimeError as exc:
        logger.error("authorization failed txn_id=%s reason=%s", txn_id, type(exc).__name__)
        raise
    logger.info("charge settled txn_id=%s latency_ms=%d", txn_id, 42)


def audit(user_key: str, action: str) -> None:
    digest = hashlib.sha256(user_key.encode()).hexdigest()[:12]
    logger.warning("audit action=%s user_hash=%s", action, digest)
    logger.info("audit batch flushed rows=%d table=%s", 128, "audit_events")


def _authorize(pan: str, amount_cents: int) -> None:
    if amount_cents > 500_000:
        raise RuntimeError("limit exceeded")
