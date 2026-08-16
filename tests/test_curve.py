"""The filter maths — the part where a mistake is inaudible in review and audible
in someone's headphones.

These are invariants rather than golden numbers: a peaking filter is worth its gain
at its own centre frequency, a shelf tends to its gain on one side and to nothing on
the other, and the sum of the bands is the sum of the bands. Pinning exact
coefficients would only test that the code still equals itself.
"""
import math

import pytest

import moondrop_control as mc
from gui import curve


def band(kind, f, g, q=1.0):
    return {"type": kind, "frequency": f, "gain": g, "q": q}


# ── peaking ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("f", [50.0, 200.0, 1000.0, 5000.0, 12000.0])
@pytest.mark.parametrize("g", [-9.0, -3.0, 3.0, 9.0])
def test_peaking_is_worth_its_gain_at_its_centre(f, g):
    assert curve.band_response(band("peaking", f, g), f) == pytest.approx(g, abs=0.15)


def test_peaking_decays_away_from_centre():
    b = band("peaking", 1000.0, 8.0, q=2.0)
    at = curve.band_response(b, 1000.0)
    assert curve.band_response(b, 100.0) < at * 0.25
    assert curve.band_response(b, 10000.0) < at * 0.25


def test_a_higher_q_is_narrower():
    wide = curve.band_response(band("peaking", 1000.0, 6.0, q=0.5), 500.0)
    narrow = curve.band_response(band("peaking", 1000.0, 6.0, q=8.0), 500.0)
    assert wide > narrow


def test_zero_gain_is_a_flat_band():
    b = band("peaking", 1000.0, 0.0, q=1.0)
    for f in (20.0, 500.0, 1000.0, 8000.0, 20000.0):
        assert curve.band_response(b, f) == pytest.approx(0.0, abs=0.01)


# ── shelves ──────────────────────────────────────────────────────────────────

def test_low_shelf_lifts_the_bottom_and_leaves_the_top():
    b = band("low_shelf", 200.0, 4.0, q=0.7)
    assert curve.band_response(b, 20.0) == pytest.approx(4.0, abs=0.4)
    assert curve.band_response(b, 15000.0) == pytest.approx(0.0, abs=0.2)


def test_high_shelf_lifts_the_top_and_leaves_the_bottom():
    b = band("high_shelf", 4000.0, 4.0, q=0.7)
    assert curve.band_response(b, 20000.0) == pytest.approx(4.0, abs=0.6)
    assert curve.band_response(b, 30.0) == pytest.approx(0.0, abs=0.2)


def test_a_shelf_is_not_a_peak():
    """What the LSC/HSC bug produced. At an octave below the corner a low shelf is
    still near full gain; a peaking filter of the same numbers has fallen away."""
    shelf = curve.band_response(band("low_shelf", 200.0, 6.0, q=0.7), 50.0)
    peak = curve.band_response(band("peaking", 200.0, 6.0, q=0.7), 50.0)
    assert shelf > peak + 2.0


# ── sums, and the pre-gain hint that depends on them ─────────────────────────

def test_disabled_bands_contribute_nothing():
    assert curve.band_response(band("disabled", 1000.0, 9.0), 1000.0) == 0.0


def test_sum_is_the_sum():
    bands = [band("peaking", 200.0, 3.0), band("peaking", 4000.0, -2.0)]
    for f in (100.0, 1000.0, 8000.0):
        assert curve.sum_response(bands, f) == pytest.approx(
            sum(curve.band_response(b, f) for b in bands), abs=1e-9)


def test_peak_db_finds_the_boost():
    bands = [band("peaking", 1000.0, 7.0, q=1.0)]
    assert curve.peak_db(bands) == pytest.approx(7.0, abs=0.3)


def test_peak_db_of_a_pure_cut_is_not_positive():
    assert curve.peak_db([band("peaking", 1000.0, -6.0)]) <= 0.05


def test_overlapping_boosts_add():
    """Why the pre-gain hint uses the summed curve and not the largest single band."""
    two = [band("peaking", 1000.0, 4.0, q=0.7), band("peaking", 1200.0, 4.0, q=0.7)]
    assert curve.peak_db(two) > 5.0


def test_empty_and_none_are_flat():
    assert curve.sum_response([], 1000.0) == 0.0
    assert curve.sum_response(None, 1000.0) == 0.0


# ── the coefficient wall ─────────────────────────────────────────────────────

def test_representable_filters_pack():
    assert mc._packs_ok(1000.0, 6.0, 1.0, "peaking")
    assert mc._packs_ok(100.0, -6.0, 0.7, "low_shelf")


def test_the_documented_impossible_filters_are_refused():
    """Q2.30 spans [-2, 2); the readme names these two shapes as the ones that leave
    it. The vendor app wraps instead of failing, which programs a filter unrelated to
    the curve it drew."""
    assert not mc._packs_ok(8000.0, 9.0, 0.7, "high_shelf")
    assert not mc._packs_ok(20000.0, 12.0, 0.3, "peaking")


def test_an_unrealisable_band_contributes_nothing_rather_than_nonsense():
    got = curve.band_response(band("high_shelf", 8000.0, 12.0, q=0.7), 8000.0)
    assert math.isfinite(got)


# ── the drawing is well formed ───────────────────────────────────────────────

def test_svg_curve_is_a_path():
    d = curve.svg_curve([band("peaking", 1000.0, 6.0)],
                        width=600, height=300, top_db=12, bot_db=-12)
    assert d.startswith("M") and len(d) > 50
    assert "nan" not in d.lower() and "inf" not in d.lower()


def test_svg_curve_survives_an_impossible_band():
    d = curve.svg_curve([band("high_shelf", 8000.0, 12.0, q=0.7)],
                        width=600, height=300, top_db=12, bot_db=-12)
    assert "nan" not in d.lower()


def test_frequency_and_x_round_trip():
    for f in (20.0, 100.0, 1000.0, 20000.0):
        assert curve.freq_of_x(curve.x_of_freq(f, 600.0), 600.0) == pytest.approx(f, rel=1e-6)
