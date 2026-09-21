"""Refunds against the payment gateway."""

from dataclasses import dataclass


class RefundError(Exception):
    """Raised when a refund cannot be issued."""


@dataclass
class Charge:
    id: str
    amount_cents: int
    currency: str
    refunded_cents: int = 0


def refund(
    charge: Charge,
    amount_cents: int,
    currency: str,
    gateway,
    *,
    idempotency_key: str | None = None,
) -> str:
    if currency != charge.currency:
        raise RefundError("currency mismatch")
    if idempotency_key and (existing := gateway.find_refund(idempotency_key)):
        return existing  # a retry of a refund that already went through
    remaining = charge.amount_cents - charge.refunded_cents
    if amount_cents > remaining:
        raise RefundError(f"amount {amount_cents} exceeds refundable balance {remaining}")
    refund_id = gateway.refund(charge.id, amount_cents, idempotency_key=idempotency_key)
    charge.refunded_cents += amount_cents
    return refund_id
