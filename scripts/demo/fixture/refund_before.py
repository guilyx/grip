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


def refund(charge: Charge, amount_cents: int, currency: str, gateway) -> str:
    if currency != charge.currency:
        raise RefundError("currency mismatch")
    if amount_cents > charge.amount_cents - charge.refunded_cents:
        raise RefundError("amount exceeds refundable balance")
    refund_id = gateway.refund(charge.id, amount_cents)
    charge.refunded_cents += amount_cents
    return refund_id
