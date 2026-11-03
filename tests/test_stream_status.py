import struct

import pytest

import moondrop_control as mc


def device(rate=96000, bits=24):
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.supports_eq_mode = True
    commands = []

    def command(cmd):
        commands.append(cmd)
        assert cmd[:3] == [mc.CMD_READ, 0, 12]
        address = struct.unpack('<I', bytes(cmd[3:7]))[0]
        data = (struct.pack('<II', rate, bits) if address == 0x11cb34
                else struct.pack('<4h', 0, -3200, -3456, 0))
        return [mc.REPORT_ID] + cmd + list(data)

    dev.send_command = command
    return dev, commands


def test_stream_reads_usb_format_and_independent_channel_gains_without_writes():
    dev, commands = device()
    state = dev.get_stream_status()
    assert state == dict(rate=96000, bits=24, left_db=-12.5, right_db=-13.5, activity=None)
    assert [struct.unpack('<I', bytes(c[3:7]))[0] for c in commands] == [0x11cb34, 0x11cb64]


@pytest.mark.parametrize('rate,bits', [(48000, 32), (44100, 16), (192000, 24)])
def test_stream_format_variants(rate, bits):
    dev, _ = device(rate, bits)
    assert dev.get_stream_status()['rate'] == rate
    assert dev.get_stream_status()['bits'] == bits


def test_unknown_firmware_is_not_read():
    dev, commands = device()
    dev.supports_eq_mode = False
    assert dev.get_stream_status() is None
    assert commands == []


def test_stream_rejects_malformed_or_unrecognized_readback():
    dev, _ = device(12345, 24)
    with pytest.raises(ValueError):
        dev.get_stream_status()
    dev.send_command = lambda _: [mc.REPORT_ID, mc.CMD_READ, 0, 12] + [0] * 60
    with pytest.raises(OSError):
        dev.get_stream_status()
