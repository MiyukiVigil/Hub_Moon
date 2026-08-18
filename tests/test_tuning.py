"""Where a curve came from, and whether that claim survives contact with a device.

The fingerprint is the load-bearing part. It has to be stable across the rounding a
DAC does on the way back — or every reconnect reports "(edited)" against a curve
nobody touched, which is worse than saying nothing at all.
"""
import json

import pytest

import moondrop_control as mc
from gui import tuning


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "config_dir", lambda: str(tmp_path))
    return tmp_path


def band(i, ftype="peaking", freq=1000, gain=3.0, q=0.7):
    return {"index": i, "type": ftype, "frequency": freq, "gain": gain, "q": q}


BANDS = [band(0, "low_shelf", 100, 6.0), band(1), band(2, "disabled", 1000, 0.0)]


def test_the_same_curve_hashes_the_same():
    assert tuning.fingerprint(BANDS) == tuning.fingerprint([dict(b) for b in BANDS])


def test_slot_order_is_not_a_difference():
    """Bands are keyed by their index, so the order they arrive in is not part of what
    the curve is."""
    assert tuning.fingerprint(list(reversed(BANDS))) == tuning.fingerprint(BANDS)


def test_rounding_the_device_performs_is_not_an_edit():
    """The DAC round-trips gain to 0.1 dB and Q to three decimals. Anything finer in
    the hash means a reconnect claims the user edited something."""
    noisy = [dict(b, gain=b["gain"] + 0.0004, q=b["q"] + 0.00002) for b in BANDS]
    assert tuning.fingerprint(noisy) == tuning.fingerprint(BANDS)


def test_a_real_change_is_an_edit():
    changed = [dict(b, gain=b["gain"] + 1.5) if b["index"] == 0 else b for b in BANDS]
    assert tuning.fingerprint(changed) != tuning.fingerprint(BANDS)


def test_a_disabled_band_carries_nothing_but_being_disabled():
    """Its frequency and gain are whatever they were when it was switched off, and
    they are never written to the device. Two identical curves must not hash apart
    over a number neither of them uses."""
    a = [band(0), {"index": 1, "type": "disabled", "frequency": 200, "gain": -9.0,
                   "q": 3.0}]
    b = [band(0), {"index": 1, "type": "disabled", "frequency": 8000, "gain": 4.0,
                   "q": 0.4}]
    assert tuning.fingerprint(a) == tuning.fingerprint(b)


def test_the_label_says_what_it_is_and_when_it_no_longer_fits():
    src = tuning.source("autoeq", "HD 600", BANDS, author="oratory1990")
    assert tuning.describe(src, BANDS) == "AutoEQ · HD 600"
    edited = [dict(b, gain=9.0) if b["index"] == 0 else b for b in BANDS]
    assert tuning.describe(src, edited) == "AutoEQ · HD 600 (edited)"
    assert tuning.edited(src, edited)


def test_nothing_known_claims_nothing():
    """Specifically not "manual" — an unrecorded curve is unknown, not hand-drawn."""
    assert tuning.describe(None, BANDS) == ""


def test_state_survives_a_restart_and_is_per_device():
    src = tuning.source("hub", "aira", BANDS, ident="uuid-1")
    tuning.save_state(0x011D, src)
    assert tuning.load_state(0x011D)["name"] == "aira"
    assert tuning.load_state(0x011B) is None


def test_a_state_file_from_a_future_schema_is_ignored_not_trusted(config):
    (config / "state.json").write_text(
        json.dumps({"schema": 99, "devices": {"0x011D": {"name": "x"}}}))
    assert tuning.load_state(0x011D) is None


def test_a_corrupt_file_is_not_a_crash(config):
    (config / "history.json").write_text("{not json")
    assert tuning.load_history() == []


def test_applying_the_same_curve_twice_moves_one_entry_rather_than_making_two():
    src = tuning.source("hub", "aira", BANDS)
    tuning.remember(src)
    tuning.remember(tuning.source("preset", "Flat", [band(0, "disabled")]))
    entries = tuning.remember(dict(src))
    assert [e["name"] for e in entries] == ["aira", "Flat"]


def test_history_is_capped():
    for i in range(tuning.HISTORY_MAX + 6):
        tuning.remember(tuning.source("preset", "p%d" % i, [band(0, gain=float(i))]))
    entries = tuning.load_history()
    assert len(entries) == tuning.HISTORY_MAX
    assert entries[0]["name"] == "p%d" % (tuning.HISTORY_MAX + 5)      # newest first


def test_an_entry_carries_the_bands_so_it_can_be_re_applied_offline():
    """Re-applying must not need the library that curve came from — it may be gone."""
    src = tuning.source("hub", "aira", BANDS, pregain=-2.5)
    kept = tuning.remember(src)[0]
    assert len(kept["bands"]) == len(BANDS)
    assert kept["pregain"] == -2.5
