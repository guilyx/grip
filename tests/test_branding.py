"""The brand palette: colour maths, the checks, and the generated files being current."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def build() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "grip_brand_build", ROOT / "branding" / "build.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_files_are_current_and_checks_pass(build: ModuleType) -> None:
    assert build.main(["--check"]) == 0, "run `python branding/build.py` and commit the result"


def test_every_check_passes(build: ModuleType) -> None:
    results = build.run_checks(build.flat(build.palette()))
    assert results and all(r.ok for r in results), [r.detail for r in results if not r.ok]


def test_wcag_and_apca_reference_values(build: ModuleType) -> None:
    assert build.wcag("#000000", "#ffffff") == pytest.approx(21.0)
    assert build.wcag("#777777", "#777777") == pytest.approx(1.0)
    assert build.apca("#000000", "#ffffff") == pytest.approx(106.04, abs=0.1)
    assert build.apca("#ffffff", "#000000") == pytest.approx(-107.88, abs=0.1)
    assert build.apca("#888888", "#888888") == 0.0


def test_oklab_round_trip(build: ModuleType) -> None:
    for hx in ("#ff8644", "#00a2d2", "#e953a0", "#fdfbf7", "#140f0c"):
        lab = build.linear_to_oklab(build.hex_to_linear(hx))
        rgb = build.oklab_to_linear(lab)
        assert all(abs(a - b) < 1e-6 for a, b in zip(rgb, build.hex_to_linear(hx), strict=True))


def test_gamut_mapping_only_lowers_chroma(build: ModuleType) -> None:
    lightness, chroma, hue = build.gamut_map((0.9, 0.4, 124))
    assert (lightness, hue) == (0.9, 124)
    assert 0 < chroma < 0.4
    assert build.in_gamut(build.oklab_to_linear(build.oklch_to_oklab((lightness, chroma, hue))))


def test_one_lightness_ladder_across_families(build: ModuleType) -> None:
    for family in build.FAMILIES:
        ladder = [family.lch(i)[0] for i in range(len(build.STEPS))]
        assert ladder == list(build.LIGHTNESS)
        assert ladder == sorted(ladder, reverse=True)


def test_brand_and_link_hues_are_complements(build: ModuleType) -> None:
    hues = {f.name: f.hue for f in build.FAMILIES}
    assert math.isclose((hues["sky"] - hues["ember"]) % 360, 180)


def test_no_lime_or_violet(build: ModuleType) -> None:
    """grip must not look like a lime-on-violet product: no family in either hue band."""
    for family in build.FAMILIES:
        if family.chroma < 0.05:
            continue
        assert not 105 <= family.hue <= 135, f"{family.name} is lime"
        assert not 270 <= family.hue <= 320, f"{family.name} is violet"


def test_mark_is_a_g(build: ModuleType) -> None:
    svg = build.mark_svg("#ff8644", "#201e1c")
    assert 'aria-label="grip"' in svg and "<rect" in svg
    # An arc with the large-arc flag set, anticlockwise, then the inward bar.
    assert " 0 1 0 " in svg and " H" in svg
    assert "<rect" not in build.mark_svg("#ff8644", None)
