"""Saved profiles: the store, and the promise that a profile is an exported file."""
import json
import os

import pytest

from gui import profiles as P


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(P.mc, "config_dir", lambda: str(tmp_path))
    return tmp_path


def bands(n=8):
    return [{"index": i, "type": "low_shelf" if i == 0 else "peaking",
             "frequency": 100 * (i + 1), "gain": 1.5 * i - 2, "q": 0.7 + i * 0.1}
            for i in range(n)]


def test_save_then_load():
    P.save("Late night", bands(), -4.2, global_gain=1.0, device="DAWN PRO2", slot=7)
    got = P.load_all()
    assert len(got) == 1
    assert got[0]["name"] == "Late night"
    assert got[0]["pregain"] == -4.2
    assert got[0]["count"] == 8


def test_a_profile_is_a_valid_export():
    """The whole point of the format choice: Import must be able to read one."""
    path = P.save("Export shaped", bands(), -3.0, device="DAWN PRO2")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    assert isinstance(raw["filters"], list) and len(raw["filters"]) == 8
    assert "pregain" in raw and "device_name" in raw
    for f in raw["filters"]:
        assert set(f) == {"index", "type", "frequency", "gain", "q"}


def test_saving_the_same_name_replaces_it():
    P.save("One", bands(8), 0.0)
    P.save("One", bands(4), -1.0)
    got = P.load_all()
    assert len(got) == 1 and got[0]["count"] == 4


def test_newest_first():
    import time
    P.save("older", bands(), 0.0)
    time.sleep(1.05)                       # the stamp has one-second resolution
    P.save("newer", bands(), 0.0)
    assert [p["name"] for p in P.load_all()] == ["newer", "older"]


@pytest.mark.parametrize("name,expect", [
    ("Late night", "late-night"),
    ('Bass?? / "boost": v2 *', "bass-boost-v2"),
    ("   ", "profile"),
    ("///", "profile"),
    ("HD 600 — oratory1990", "hd-600-oratory1990"),
])
def test_slugs_are_portable_filenames(name, expect):
    """Windows will not accept a colon or a quote in a filename, and these files are
    meant to be copyable between machines."""
    got = P.slug(name)
    assert got == expect
    assert not (set(got) & set('<>:"/\\|?*'))


def test_a_corrupt_file_does_not_take_the_list_down(store):
    P.save("good", bands(), 0.0)
    os.makedirs(P.store_dir(), exist_ok=True)
    with open(os.path.join(P.store_dir(), "broken.json"), "w") as fh:
        fh.write("{not json")
    with open(os.path.join(P.store_dir(), "empty.json"), "w") as fh:
        fh.write('{"filters": []}')
    assert [p["name"] for p in P.load_all()] == ["good"]


def test_loading_an_empty_store_is_not_an_error():
    assert P.load_all() == []


def test_delete():
    P.save("gone", bands(), 0.0)
    assert P.delete("gone") is True
    assert P.load_all() == []


def test_deleting_something_absent_is_success():
    """The caller wanted it gone. It is gone."""
    assert P.delete("never existed") is True


def test_long_names_are_bounded():
    P.save("x" * 500, bands(), 0.0)
    got = P.load_all()[0]
    assert len(got["name"]) <= P.MAX_NAME
    assert len(os.path.basename(got["path"])) < 120


def test_summary_warns_when_a_profile_is_wider_than_the_device():
    P.save("from a bigger dac", bands(10), 0.0)
    line = P.summary(P.load_all()[0], band_count=8)
    assert "10 bands" in line


def test_summary_names_what_matters():
    P.save("Named", bands(), -4.2, device="DAWN PRO2")
    line = P.summary(P.load_all()[0], 8)
    assert "8 bands" in line and "-4.2" in line and "DAWN PRO2" in line
