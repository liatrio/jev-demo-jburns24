"""Demo fixture: a payments service whose log lines leak personal data.

Deliberately mixes leaking and clean statements so the check has to read
each one, not just count them. Ground truth lives in ``expected.json``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Customer:
    customer_id: str
    full_name: str
    email: str
    phone: str
    ssn: str
    card_pan: str
    ip_address: str


def onboard(customer: Customer) -> None:
    logger.info("onboarding started customer_id=%s", customer.customer_id)
    logger.info("welcome email queued for %s <%s>", customer.full_name, customer.email)
    logger.debug("kyc payload: ssn=%s dob_verified=true", customer.ssn)
    logger.info("onboarding complete customer_id=%s steps=4", customer.customer_id)


def charge(customer: Customer, amount_cents: int, txn_id: str) -> None:
    logger.info("charging %d cents txn_id=%s", amount_cents, txn_id)
    try:
        _authorize(customer.card_pan, amount_cents)
    except RuntimeError as exc:
        logger.error(
            "authorization failed for card %s (%s): %s",
            customer.card_pan,
            customer.full_name,
            exc,
        )
        raise
    logger.warning("high-value charge from ip=%s txn_id=%s", customer.ip_address, txn_id)
    logger.info("charge settled txn_id=%s latency_ms=%d", txn_id, 42)


def reset_password(customer: Customer, new_password: str) -> None:
    logger.info(f"password reset for {customer.email}: new password is {new_password}")
    logger.info("password reset complete customer_id=%s", customer.customer_id)


def _authorize(pan: str, amount_cents: int) -> None:
    if amount_cents > 500_000:
        raise RuntimeError("limit exceeded")
