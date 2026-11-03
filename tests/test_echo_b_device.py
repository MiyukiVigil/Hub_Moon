"""ECHO-B driver against a fake device.

The expected bytes are copied from what a real ECHO-B accepted in issue #1
(tools/echo_b_probe.py runs), so a change to the framing shows up here first.
"""
import struct

import pytest

import moondrop_control as mc


def hexbytes(text):
    return bytes.fromhex(text.replace(" ", ""))


# From the --ear-compare run: band 5 of the right channel set flat, EQ enable, bypass off.
FLAT_BAND_5_RIGHT = hexbytes("01 00 0d 00 be 00 00 23 2d b3 01 00 00 00 05 00 00 00 03 00 00 00 "
                             "00 00 40 00") + bytes(36)
EQ_ENABLE = hexbytes("01 00 0d 00 bc 00 00 23 2d b3 01 00 00 00") + bytes(48)
EQ_BYPASS_OFF = hexbytes("01 00 01 00 bb 00 00 23 2d b3 00 00 00 00") + bytes(48)


class FakeEchoB:
    """Answers like the real one: cmd | 0x8000 at bytes 4-5, status at 2-3."""

    def __init__(self, rate_index=5):
        self.rate_index = rate_index
        self.sent = []
        self._reply = None

    def open_path(self, path):
        pass

    def close(self):
        pass

    def write(self, data):
        msg = bytes(data)
        self.sent.append(msg)
        cmd = struct.unpack_from("<H", msg, 4)[0]
        reply = bytearray(msg)
        struct.pack_into("<hH", reply, 2, 0, cmd | 0x8000)
        if cmd == mc.ECHO_B_CMD_GET_PARAM:
            struct.pack_into("<i", reply, 14, self.rate_index)
        self._reply = bytes(reply)
        return len(msg)

    def get_input_report(self, report_id, length):
        reply, self._reply = self._reply, None
        return list(reply) if reply else []

    def read(self, length, timeout=0):
        return []

    def band_writes(self):
        return [m for m in self.sent if struct.unpack_from("<H", m, 4)[0] == mc.ECHO_B_CMD_SET_BAND]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    dev = FakeEchoB()
    monkeypatch.setattr(mc.hid, "device", lambda: dev)
    monkeypatch.setattr(mc, "config_dir", lambda: str(tmp_path))
    return dev


INFO = {"product_id": 0x149B, "path": b"x", "serial_number": "SN1", "interface_number": 3}


def test_open_flat_writes_nothing(fake):
    dev = mc.open_device(INFO)
    assert isinstance(dev, mc.EchoBDevice)
    assert dev.sample_rate == 48000
    assert fake.band_writes() == []
    assert all(dev.read_peq_index(i)["type"] == "disabled" for i in range(9))


def test_band_write_matches_hardware_bytes(fake):
    dev = mc.open_device(INFO)
    fake.sent.clear()
    dev.write_peq_index(4, "disabled", 1000, 0.0, 1.0)
    assert fake.sent[0] == EQ_ENABLE
    assert fake.sent[1] == EQ_BYPASS_OFF
    left, right = fake.sent[2:]
    assert right == FLAT_BAND_5_RIGHT
    assert left == FLAT_BAND_5_RIGHT[:10] + b"\x00" + FLAT_BAND_5_RIGHT[11:]
    # Enable goes out once per session, not before every write.
    dev.write_peq_index(4, "peaking", 1000, -3.0, 1.0)
    assert len(fake.sent) == 6


def test_band_uses_echo_b_design_at_device_rate(fake):
    dev = mc.open_device(INFO)
    dev.write_peq_index(2, "peaking", 1000, -6.0, 1.0)
    shift, ints = mc.echo_b_coefficients(1000, -6.0, 1.0, "peaking", 48000)
    for msg in fake.band_writes():
        words = struct.unpack_from("<8i", msg, 10)
        assert words[1] == 3 and words[2] == shift and list(words[3:]) == ints


def test_pregain_scales_first_band(fake):
    dev = mc.open_device(INFO)
    dev.set_pregain(-6.0, save=False)
    words = struct.unpack_from("<8i", fake.band_writes()[-1], 10)
    assert words[1] == 1
    assert words[3] / 2 ** (25 - words[2]) == pytest.approx(10 ** (-6 / 20), abs=1e-6)


def test_saved_curve_is_written_again_on_connect(fake):
    dev = mc.open_device(INFO)
    dev.write_peq_index(0, "low_shelf", 100, 4.0, 0.7)
    dev.set_pregain(-4.0, save=False)
    dev.save_eq_to_flash()
    dev.close()

    fake.sent.clear()
    again = mc.open_device(INFO)
    assert again.read_peq_index(0)["type"] == "low_shelf"
    assert again.get_pregain() == -4.0
    # Every band on both channels, so a replug comes back to the saved curve.
    assert len(fake.band_writes()) == 18


def test_sample_rate_change_redesigns(fake):
    dev = mc.open_device(INFO)
    dev.write_peq_index(0, "peaking", 1000, -6.0, 1.0)
    fake.sent.clear()
    assert dev.check_sample_rate() is False
    fake.rate_index = 6  # 96 kHz
    assert dev.check_sample_rate() is True
    assert dev.sample_rate == 96000
    shift, ints = mc.echo_b_coefficients(1000, -6.0, 1.0, "peaking", 96000)
    first = struct.unpack_from("<8i", fake.band_writes()[0], 10)
    assert list(first[3:]) == ints


def test_unknown_rate_refuses_to_write(fake):
    fake.rate_index = 99
    dev = mc.open_device(INFO)
    with pytest.raises(ValueError):
        dev.write_peq_index(0, "peaking", 1000, -6.0, 1.0)
    assert dev.read_peq_index(0)["type"] == "disabled"


def test_find_devices_picks_interface_3(monkeypatch):
    rows = [dict(INFO, vendor_id=mc.MOONDROP_VID, interface_number=n, path=str(n).encode())
            for n in (0, 3, 4)]
    monkeypatch.setattr(mc.hid, "enumerate", lambda: rows)
    found = mc.find_devices()
    assert [d["path"] for d in found] == [b"3"]


def test_gui_worker_drives_echo_b(fake, monkeypatch):
    pytest.importorskip("slint")
    from gui.bridge import DeviceWorker
    monkeypatch.setattr(mc, "find_devices", lambda: [INFO])
    events = []
    w = DeviceWorker(lambda *event: events.append(event))
    w.refresh()
    state = next(p for k, p in events if k == "snapshot")[0]
    assert (state["bandCount"], state["dspRate"], state["savesOnHost"]) == (9, 48000, True)
    assert state["activeProfile"] == -1

    w.apply_bands([{"index": 8, "type": "peaking", "frequency": 8000, "gain": -3.0, "q": 2.0}],
                  -2.0, None)
    assert not [p for k, p in events if k == "error"]
    # Band 9 on both channels, then band 1 again carrying the pre-gain.
    assert [struct.unpack_from("<2i", m, 10) for m in fake.band_writes()[-4:]] == \
        [(0, 9), (1, 9), (0, 1), (1, 1)]
    w.save_to_flash()
    assert ("saved", None) in events
    assert mc.open_device(INFO).read_peq_index(8)["gain"] == -3.0

    fake.rate_index = 4  # 44.1 kHz
    w.probe()
    assert w._dev.sample_rate == 44100
