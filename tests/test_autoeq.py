"""The AutoEQ catalogue: searching it, addressing it, and fitting what it returns.

No network — conftest forbids it — so the index is a small stand-in and the profile
text comes from the committed fixtures. What is being tested is the arithmetic of
finding and reshaping a profile, which is where a mistake would put the wrong curve
on someone's DAC.
"""
import json
import os

import pytest

from gui import autoeq as A

INDEX = {
    "schema": 1,
    "base": "https://raw.example.invalid/results/",
    "suffix": " ParametricEQ.txt",
    "count": 7,
    "models": [
        ["Sennheiser HD 600", "oratory1990", "over-ear"],
        ["Sennheiser HD 600", "crinacle", "GRAS 43AG-7 over-ear"],
        ["Sennheiser HD 600", "Bakkwatan", "over-ear"],
        ["Sennheiser HD 650", "oratory1990", "over-ear"],
        ["Moondrop Aria", "crinacle", "711 in-ear"],
        ["HIFIMAN Sundara", "Super Review", "over-ear"],
        ["Beyerdynamic DT 990", "Rtings", "HMS II.3 over-ear"],
    ],
}


def test_rows_are_dicts():
    got = A.rows(INDEX)
    assert got[0] == {"model": "Sennheiser HD 600", "source": "oratory1990",
                      "form": "over-ear"}


# ── searching ────────────────────────────────────────────────────────────────

def test_search_is_case_and_space_insensitive():
    for q in ["hd 600", "HD 600", "  hd   600 "]:
        assert any(r["model"] == "Sennheiser HD 600" for r in A.search(INDEX, q))


def test_a_prefix_beats_a_substring():
    got = A.search(INDEX, "sennheiser")
    assert got[0]["model"].startswith("Sennheiser")


def test_word_start_matches():
    """"aria" has to find "Moondrop Aria" even though the name does not start with it."""
    got = A.search(INDEX, "aria")
    assert got and got[0]["model"] == "Moondrop Aria"


def test_ties_fall_back_to_the_source_preference():
    """Several people measured the HD 600 and they disagree. The order is a stated
    preference, not a verdict — but it must be the stated one."""
    got = [r for r in A.search(INDEX, "Sennheiser HD 600")
           if r["model"] == "Sennheiser HD 600"]
    assert [r["source"] for r in got] == ["oratory1990", "crinacle", "Bakkwatan"]


def test_an_empty_query_returns_the_catalogue():
    assert len(A.search(INDEX, "")) == len(INDEX["models"])


def test_a_miss_returns_nothing_rather_than_everything():
    assert A.search(INDEX, "definitely not a headphone") == []


def test_the_limit_is_honoured():
    assert len(A.search(INDEX, "", limit=3)) == 3


def test_search_can_find_by_source():
    assert all(r["source"] == "Rtings" or "rtings" in r["model"].lower()
               for r in A.search(INDEX, "rtings"))


# ── addressing ───────────────────────────────────────────────────────────────

def test_profile_url_is_built_from_the_triple():
    row = {"model": "Sennheiser HD 600", "source": "oratory1990", "form": "over-ear"}
    assert A.profile_url(INDEX, row) == (
        "https://raw.example.invalid/results/oratory1990/over-ear/"
        "Sennheiser%20HD%20600/Sennheiser%20HD%20600%20ParametricEQ.txt")


def test_profile_url_escapes_the_awkward_names():
    """Real entries carry brackets, plus signs and ampersands — "HIFIMAN Sundara
    (Dekoni sheepskin Earpads)", "Bruel & Kjaer 5128 in-ear", "EARS + 711 over-ear"."""
    row = {"model": "HIFIMAN Sundara (Dekoni Earpads)", "source": "oratory1990",
           "form": "EARS + 711 over-ear"}
    url = A.profile_url(INDEX, row)
    for raw in (" ", "(", ")", "+"):
        assert raw not in url.split("/results/")[1], "%r was not escaped" % raw


def test_the_shipped_index_matches_what_the_code_expects():
    """The generated catalogue and the reader have to agree about the schema."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "packaging", "autoeq-index.json")
    if not os.path.exists(path):
        pytest.skip("catalogue not generated in this checkout")
    with open(path, encoding="utf-8") as fh:
        real = json.load(fh)
    assert real["count"] == len(real["models"]) > 5000
    assert all(len(m) == 3 for m in real["models"])
    url = A.profile_url(real, A.rows(real)[0])
    assert url.startswith("https://") and url.endswith("ParametricEQ.txt")


# ── fitting ──────────────────────────────────────────────────────────────────

def sample(name):
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", name),
              encoding="utf-8") as fh:
        return fh.read()


def test_fit_fills_every_slot():
    bands, preamp, dropped, warnings = A.fit(sample("autoeq-hd600.txt"), 8)
    assert [b["index"] for b in bands] == list(range(8))
    assert len(dropped) == 2 and warnings == []
    assert preamp == -6.3


def test_fit_pads_short_profiles_with_disabled_bands():
    """Unused slots must be written as disabled, or the tail of the previous curve
    survives underneath the new one."""
    bands, _, _, _ = A.fit("Preamp: 0 dB\nFilter 1: ON PK Fc 1000 Hz Gain 3 dB Q 1", 8)
    assert bands[0]["type"] == "peaking"
    assert all(b["type"] == "disabled" for b in bands[1:])
    assert len(bands) == 8


def test_fit_keeps_the_shelves():
    bands, _, _, _ = A.fit(sample("autoeq-hd600.txt"), 8)
    kinds = {b["type"] for b in bands}
    assert "low_shelf" in kinds and "high_shelf" in kinds


def test_fit_respects_a_smaller_device():
    bands, _, dropped, _ = A.fit(sample("autoeq-hd600.txt"), 5)
    assert len(bands) == 5 and len(dropped) == 5


def test_autoeq_is_credited():
    assert "Jaakko Pasanen" in A.CREDIT and "MIT" in A.CREDIT
