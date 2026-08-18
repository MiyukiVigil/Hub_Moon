"""What a curve is called, given what it does.

These are synthetic curves with known intent — a bass shelf is a bass shelf — so they
say whether the rules still mean what they were written to mean. The real library is
the other half of the answer and lives in `tools/classify-index.py`, which prints the
histogram over the actual 1,485 presets; a rule change that keeps every test here green
while filing 80% of that library under one label is still a broken rule.
"""
import pytest

from gui import shapes


def band(i, ftype, freq, gain, q=0.7):
    return {"index": i, "type": ftype, "frequency": freq, "gain": gain, "q": q}


def pad(bands, n=8):
    """Real presets always carry eight bands; the unused ones are disabled."""
    out = list(bands)
    while len(out) < n:
        out.append(band(len(out), "disabled", 1000, 0.0))
    return out


FLAT = pad([])
BASS = pad([band(0, "low_shelf", 100, 6.0)])
DEEP_BASS = pad([band(0, "low_shelf", 60, 8.0)])
VSHAPE = pad([band(0, "low_shelf", 100, 5.0), band(1, "high_shelf", 6000, 5.0)])
WARM = pad([band(0, "low_shelf", 120, 5.0), band(1, "high_shelf", 8000, -5.0)])
BRIGHT = pad([band(0, "high_shelf", 5000, 6.0)])
SIBILANCE = pad([band(0, "high_shelf", 7000, -6.0)])
THIN = pad([band(0, "low_shelf", 120, -6.0)])
NARROW_NOTCH = pad([band(0, "peaking", 3000, -7.0, 4.0)])


@pytest.mark.parametrize("bands,expected", [
    (FLAT, shapes.NEUTRAL),
    (BASS, shapes.BASS),
    (DEEP_BASS, shapes.BASS),
    (VSHAPE, shapes.VSHAPE),
    (WARM, shapes.WARM),
    (BRIGHT, shapes.BRIGHT),
    (SIBILANCE, shapes.TAMED),
    (THIN, shapes.LEAN),
])
def test_a_curve_is_called_what_it_is(bands, expected):
    assert shapes.classify(bands) == expected


def test_a_v_shape_is_not_filed_as_a_bass_boost():
    """It satisfies both rules. The order they are tested in is the whole reason it
    comes out as the one somebody would search for."""
    assert shapes.classify(VSHAPE) == shapes.VSHAPE


def test_a_sharp_notch_is_not_neutral():
    """Region averages are means over a decade, and a 7 dB dip two thirds of an octave
    wide barely moves one. Without the peak test this came out Neutral."""
    assert max(abs(v) for v in shapes.profile(NARROW_NOTCH).values()) < shapes.FLAT_DB
    assert shapes.peak_deviation(NARROW_NOTCH) > shapes.PEAK_DB
    assert shapes.classify(NARROW_NOTCH) != shapes.NEUTRAL


def test_no_bands_at_all_is_not_a_shape():
    assert shapes.classify([]) == shapes.OTHER


def test_every_answer_is_one_of_the_labels():
    """The chips are built from LABELS, so a label outside it is a bucket with no
    chip — presets that can be filed and never found."""
    for bands in (FLAT, BASS, VSHAPE, WARM, BRIGHT, SIBILANCE, THIN, NARROW_NOTCH):
        assert shapes.classify(bands) in shapes.LABELS


def test_a_louder_copy_of_a_curve_is_the_same_shape():
    """Tilt is measured against the mids, so lifting everything by 3 dB is a volume
    change, not a tuning change. This is what stopped a third of the real library
    coming out Bright."""
    louder = [dict(b, gain=b["gain"] + 3.0) if b["type"] != "disabled" else b
              for b in VSHAPE]
    assert shapes.classify(louder) == shapes.classify(VSHAPE)
