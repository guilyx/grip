from calc.ops import add


def test_add_plain() -> None:
    assert add(3, 4) == 7


def test_clamp_above_sum_is_noop() -> None:
    assert add(3, 4, clamp=10) == 7


def test_clamp_below_sum_caps() -> None:
    assert add(3, 4, clamp=5) == 5
