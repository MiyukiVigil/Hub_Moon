"""Live volume trim must work on a DAC that never acknowledges writes."""
import moondrop_control as mc
import struct
import pytest


def test_live_volume_write_does_not_wait_for_a_reply():
    class UnacknowledgingDac:
        def __init__(self):
            self.writes = []

        def write(self, packet):
            self.writes.append(bytes(packet))
            return len(packet)

        def read(self, *args):
            raise AssertionError("a live volume write waited for a reply")

    device = mc.MoondropDevice.__new__(mc.MoondropDevice)
    device.device = UnacknowledgingDac()
    device.report_id = mc.REPORT_ID

    device.set_global_gain(-6, save=False)

    # One -6 dB offset write, with no flash command and no blocking read.
    assert len(device.device.writes) == 1
    packet = device.device.writes[0]
    assert len(packet) == 64
    assert packet[:6] == bytes([75, 1, 3, 0, 0, 250])


class VolumeDac:
    def __init__(self):
        self.word = struct.pack('<hH', -3825, 0x1234)
        self.trim = struct.pack('<h', -768)
        self.writes = []
        self.reply = None
        self.refuse_volume_write = False

    def write(self, packet):
        packet = bytes(packet)
        self.writes.append(packet)
        cmd, sub = packet[1:3]
        if cmd == 128 and sub == 0:
            self.reply = packet[:8] + self.word + bytes(52)
        elif cmd == 128 and sub == 3:
            self.reply = packet[:4] + self.trim + bytes(58)
        elif cmd == 1 and sub == 0 and not self.refuse_volume_write:
            self.word = packet[8:12]
        elif cmd == 1 and sub == 3:
            self.trim = packet[4:6]

    def read(self, *args):
        reply, self.reply = self.reply, None
        return list(reply or b'')


def physical_device():
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.device = VolumeDac()
    dev.report_id = 75
    dev.supports_physical_volume = True
    return dev


def test_physical_volume_preserves_neighbor_and_trim_without_flash():
    dev = physical_device()
    assert dev.get_physical_volume() == -3825 / 256
    actual = dev.set_physical_volume(-21)
    assert actual == -5355 / 256
    assert dev.device.word == struct.pack('<hH', -5355, 0x1234)
    assert dev.device.trim == struct.pack('<h', -768)
    writes = [p for p in dev.device.writes if p[1] == 1]
    assert [p[2] for p in writes] == [0, 3]
    assert writes[0][:8] == bytes.fromhex('4b0100082cd21100')


@pytest.mark.parametrize('db', [-61, 1, float('nan'), float('inf')])
def test_invalid_physical_volume_never_accesses_device(db):
    dev = physical_device()
    with pytest.raises(ValueError):
        dev.set_physical_volume(db)
    assert dev.device.writes == []


def test_unverified_firmware_never_accesses_ram():
    dev = physical_device()
    dev.supports_physical_volume = False
    with pytest.raises(ValueError):
        dev.set_physical_volume(-20)
    assert dev.device.writes == []


def test_failed_write_restores_original_word_and_trim():
    dev = physical_device()
    dev.device.refuse_volume_write = True
    with pytest.raises(OSError, match='did not verify'):
        dev.set_physical_volume(-21)
    assert dev.device.word == struct.pack('<hH', -3825, 0x1234)
    assert dev.device.trim == struct.pack('<h', -768)
    assert [p[2] for p in dev.device.writes if p[1] == 1] == [0,3,0,3]


def test_corrupt_volume_state_never_gets_written():
    dev = physical_device()
    dev.device.word = struct.pack('<hH', 500, 2)
    with pytest.raises(OSError, match='verified range'):
        dev.set_physical_volume(-20)
    assert all(p[1] == 128 for p in dev.device.writes)


def test_fractional_trim_never_gets_overwritten_by_gain_refresh():
    dev = physical_device()
    dev.device.trim = struct.pack('<h', -128)
    with pytest.raises(OSError, match='cannot be preserved'):
        dev.set_physical_volume(-20)
    assert all(p[1] == 128 for p in dev.device.writes)


@pytest.mark.parametrize('raw', [-15453, -15513, -15360, 0])
def test_physical_button_end_stops_stay_connected_and_in_slider_range(raw):
    dev = physical_device()
    dev.device.word = struct.pack('<hH', raw, 2)
    assert dev.get_physical_volume() == max(-60, raw / 256)
    assert all(p[1] == 128 for p in dev.device.writes)


def test_slider_can_move_away_from_physical_button_minimum():
    dev = physical_device()
    dev.device.word = struct.pack('<hH', -15453, 2)
    assert -60 < dev.set_physical_volume(-59) < -58
    assert dev.device.word[2:] == struct.pack('<H', 2)


def test_values_beyond_one_button_step_still_reject_writes():
    dev = physical_device()
    dev.device.word = struct.pack('<hH', -15514, 2)
    with pytest.raises(OSError, match='verified range'):
        dev.set_physical_volume(-59)
    assert all(p[1] == 128 for p in dev.device.writes)
