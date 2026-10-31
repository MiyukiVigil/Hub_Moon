"""ECHO-B coefficient design: RBJ biquads quantised to the CX31988's 24-bit format.

The format itself was confirmed on hardware (issue #1): every band of an untouched
ECHO-B reads back as shift 3, [2**22, 0, 0, 0, 0]. What these pin down is that our
quantisation stays faithful to the float filter, including at the low corners where
plain rounding drifts by most of a dB.
"""
import cmath
import math

import pytest

import moondrop_control as mc


def db_error(f0, gain, q, ftype, fs):
    """Worst gap in dB between the quantised band and the float RBJ biquad."""
    shift, c = mc.echo_b_coefficients(f0, gain, q, ftype, fs)
    s = 2 ** (25 - shift)
    num, den = mc.calculate_biquad(f0, gain, q, ftype, fs)
    worst = 0.0
    for k in range(400):
        f = 20 * 1000 ** (k / 399)
        if f >= 0.49 * fs:
            break
        z = cmath.exp(-2j * math.pi * f / fs)
        got = abs((c[0] + c[1] * z + c[2] * z * z) / s / (1 - c[3] / s * z - c[4] / s * z * z))
        want = abs((den[0] + den[1] * z + den[2] * z * z) / (num[0] + num[1] * z + num[2] * z * z))
        worst = max(worst, abs(20 * math.log10(got / want)))
    return worst


def test_disabled_is_what_an_untouched_device_reads_back():
    assert mc.echo_b_coefficients(1000, 0, 0.7, "disabled", 48000) == (3, [1 << 22, 0, 0, 0, 0])


@pytest.mark.parametrize("ftype", ["peaking", "low_shelf", "high_shelf", "low_pass", "high_pass"])
def test_midband_filters_are_exact(ftype):
    gain = 0 if ftype.endswith("pass") else 6
    assert db_error(1000, gain, 0.7, ftype, 48000) < 0.01


@pytest.mark.parametrize("fs", [44100, 48000])
def test_low_corners_stay_close(fs):
    """20 Hz puts the poles almost on z = 1, where rounding each coefficient alone
    misses the peak by about 0.4 dB at 48 kHz."""
    assert db_error(20, 12, 2.0, "peaking", fs) < 0.15
    assert db_error(31, -6, 1.0, "low_shelf", fs) < 0.1


def test_every_value_fits_24_bits():
    for fs in (44100, 48000, 96000, 192000):
        for f0 in (20, 100, 1000, 10000, 20000):
            for gain in (-12, 12):
                _, c = mc.echo_b_coefficients(f0, gain, 0.7, "peaking", fs)
                assert all(-(1 << 23) <= x < (1 << 23) for x in c)


def test_shift_is_the_smallest_that_fits():
    """A cut keeps every coefficient under 1.0 and gets shift 2; a boost needs b0 > 1.
    Same choices Moondrop's app makes for these bands."""
    assert mc.echo_b_coefficients(16000, -6, 2.0, "peaking", 96000)[0] == 2
    assert mc.echo_b_coefficients(16000, 12, 2.0, "peaking", 96000)[0] == 3


def test_dac_design_still_defaults_to_96k():
    assert mc.calculate_biquad(1000, 3, 0.7, "peaking") == mc.calculate_biquad(1000, 3, 0.7, "peaking", 96000)
