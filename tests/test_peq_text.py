"""The REW / AutoEQ parser, against real published files.

The fixtures in tests/data are unmodified AutoEQ output, fetched once and committed.
Testing against a hand-written approximation of the format would have missed the exact
bug these tests exist to prevent: AutoEQ writes shelves as `LSC`/`HSC`, which the
1.1.0 importer did not know and silently turned into peaking filters.
"""
import os

import pytest

import moondrop_control as mc


def read(sample_dir, name):
    with open(os.path.join(sample_dir, name), encoding="utf-8") as fh:
        return fh.read()


# ── the bug this module exists for ───────────────────────────────────────────

def test_autoeq_shelves_are_shelves(sample_dir):
    """The regression. Both of the HD 600's shelves must survive as shelves."""
    got = mc.parse_peq_text(read(sample_dir, "autoeq-hd600.txt"))
    kinds = [f["type"] for f in got["filters"]]
    assert kinds[0] == "low_shelf", "LSC must map to a low shelf, not %r" % kinds[0]
    assert kinds[5] == "high_shelf", "HSC must map to a high shelf, not %r" % kinds[5]
    assert kinds.count("peaking") == 8


@pytest.mark.parametrize("token,expect", [
    ("LSC", "low_shelf"), ("LS", "low_shelf"), ("LSQ", "low_shelf"),
    ("HSC", "high_shelf"), ("HS", "high_shelf"), ("HSQ", "high_shelf"),
    ("PK", "peaking"), ("PEQ", "peaking"),
    ("LP", "low_pass"), ("HP", "high_pass"),
])
def test_every_known_shape_token(token, expect):
    text = "Filter 1: ON %s Fc 1000 Hz Gain 3.0 dB Q 1.00" % token
    got = mc.parse_peq_text(text)
    assert got["filters"][0]["type"] == expect
    assert got["filters"][0]["type"] in mc.FILTER_TYPES


def test_unsupported_shapes_are_reported_not_guessed():
    """A notch is not a peak. Refusing loudly beats importing something else."""
    got = mc.parse_peq_text("Filter 1: ON NO Fc 1000 Hz Gain -6.0 dB Q 4.00")
    assert got["filters"] == []
    assert any("notch" in w for w in got["warnings"])


# ── the real files ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["autoeq-hd600.txt", "autoeq-moondrop-aria.txt"])
def test_real_autoeq_file_parses_completely(sample_dir, name):
    got = mc.parse_peq_text(read(sample_dir, name))
    assert len(got["filters"]) == 10, "AutoEQ publishes ten filters per profile"
    assert got["warnings"] == [], got["warnings"]
    assert got["preamp"] < 0, "an AutoEQ preamp is always a cut"
    for f in got["filters"]:
        assert 20.0 <= f["frequency"] <= 20000.0
        assert 0.0 < f["q"] <= 20.0
        assert f["type"] in mc.FILTER_TYPES


def test_hd600_values_exactly(sample_dir):
    got = mc.parse_peq_text(read(sample_dir, "autoeq-hd600.txt"))
    assert got["preamp"] == -6.3
    first = got["filters"][0]
    assert (first["index"], first["frequency"], first["gain"], first["q"]) \
        == (0, 105.0, 6.5, 0.70)


# ── format variants ──────────────────────────────────────────────────────────

def test_off_filters_are_absent():
    got = mc.parse_peq_text(
        "Filter 1: ON PK Fc 100 Hz Gain 3.0 dB Q 1.00\n"
        "Filter 2: OFF PK Fc 200 Hz Gain 3.0 dB Q 1.00\n")
    assert len(got["filters"]) == 1


def test_kilohertz_corner():
    got = mc.parse_peq_text("Filter 1: ON PK Fc 10.5 kHz Gain -2.0 dB Q 1.00")
    assert got["filters"][0]["frequency"] == 10500.0


def test_fixed_slope_shelf_without_q():
    """REW writes `LS 6dB` with no Q at all; it must not crash or drop the filter."""
    got = mc.parse_peq_text("Filter 1: ON LS 6dB Fc 100 Hz Gain 3.0 dB")
    assert got["filters"][0]["type"] == "low_shelf"
    assert got["filters"][0]["q"] == pytest.approx(0.707, abs=1e-3)


def test_rew_extra_whitespace():
    got = mc.parse_peq_text("Filter  1:  ON   PK    Fc   105 Hz   Gain  6.50 dB  Q  0.70")
    assert got["filters"][0]["frequency"] == 105.0


def test_comments_and_blank_lines_ignored():
    got = mc.parse_peq_text("# a comment\n\nPreamp: -1.5 dB\n\n"
                            "Filter 1: ON PK Fc 100 Hz Gain 1.0 dB Q 1.00\n")
    assert got["preamp"] == -1.5 and len(got["filters"]) == 1


def test_junk_input_says_so():
    got = mc.parse_peq_text("this is not an EQ file at all\n")
    assert got["filters"] == []
    assert any("no filter lines" in w for w in got["warnings"])


def test_unreadable_filter_line_is_named():
    got = mc.parse_peq_text("Filter 1: ON PK Fc bananas Hz Gain 1 dB Q 1")
    assert any("line 1" in w for w in got["warnings"])


# ── fitting ten filters into eight bands ─────────────────────────────────────

def test_fit_keeps_the_biggest(sample_dir):
    got = mc.parse_peq_text(read(sample_dir, "autoeq-hd600.txt"))
    kept, dropped = mc.fit_peq_to_bands(got["filters"], 8)
    assert len(kept) == 8 and len(dropped) == 2
    # the two smallest filters in the file, and nothing else
    assert sorted(round(abs(f["gain"]), 1) for f in dropped) == [0.7, 0.9]
    assert min(abs(f["gain"]) for f in kept) >= max(abs(f["gain"]) for f in dropped)


def test_fit_renumbers_onto_slots(sample_dir):
    got = mc.parse_peq_text(read(sample_dir, "autoeq-hd600.txt"))
    kept, _ = mc.fit_peq_to_bands(got["filters"], 8)
    assert [f["index"] for f in kept] == list(range(8))


def test_fit_preserves_file_order(sample_dir):
    """Renumbering must not reorder: the slots follow the file, not the ranking."""
    got = mc.parse_peq_text(read(sample_dir, "autoeq-hd600.txt"))
    kept, _ = mc.fit_peq_to_bands(got["filters"], 8)
    freqs = [f["frequency"] for f in kept]
    assert freqs == [105.0, 125.0, 8445.0, 1298.0, 10000.0, 3158.0, 6639.0, 5433.0]


def test_fit_is_a_no_op_when_it_fits():
    fs = [{"index": i, "type": "peaking", "frequency": 100 * (i + 1),
           "gain": 1.0, "q": 1.0} for i in range(5)]
    kept, dropped = mc.fit_peq_to_bands(fs, 8)
    assert len(kept) == 5 and dropped == []


def test_fit_breaks_gain_ties_on_width():
    """Same height, so the wider filter — the one colouring more of the band — wins."""
    wide = {"index": 0, "type": "peaking", "frequency": 200, "gain": 3.0, "q": 0.5}
    narrow = {"index": 1, "type": "peaking", "frequency": 400, "gain": 3.0, "q": 8.0}
    kept, dropped = mc.fit_peq_to_bands([wide, narrow], 1)
    assert kept[0]["frequency"] == 200
    assert dropped[0]["frequency"] == 400
