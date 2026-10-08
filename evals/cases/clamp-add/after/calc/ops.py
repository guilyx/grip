"""Arithmetic helpers."""


def add(a: float, b: float, *, clamp: float | None = None) -> float:
    """Return the sum of two numbers, capped at ``clamp`` when one is given.

    >>> add(3, 4, clamp=5)
    5
    """
    total = a + b
    if clamp is None:
        return total
    return min(total, clamp)
