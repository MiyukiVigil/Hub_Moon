import struct

import pytest

import moondrop_control as mc


def device(trims=(0, 0)):
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.supports_eq_mode = True
    state = list(trims)
    writes = []

    def command(cmd, wait_response=True):
        assert cmd[1:3] == [22, 0]  # Only channel trim; no flash or master gain.
        channel = cmd[3]
        if cmd[0] == mc.CMD_READ:
            return [mc.REPORT_ID] + cmd + [0] + list(struct.pack('>h', state[channel]))
        assert cmd[0] == mc.CMD_WRITE and not wait_response
        assert cmd[4] == 0
        state[channel] = struct.unpack('>h', bytes(cmd[5:7]))[0]
        writes.append((channel, state[channel]))
    dev.send_command = command
    return dev, state, writes


@pytest.mark.parametrize('balance,expected', [(6, (-1536, 0)), (-6, (0, -1536)), (0, (0, 0))])
def test_balance_attenuates_opposite_channel_without_boost(balance, expected):
    dev, state, _ = device()
    assert dev.set_balance(balance) == balance
    assert tuple(state) == expected


def test_changing_sides_quiets_new_side_before_releasing_old_side():
    dev, state, writes = device((-1536, 0))
    assert dev.set_balance(-6) == -6
    assert writes == [(1, -1536), (0, 0)]
    assert state == [0, -1536]


def test_balance_preserves_existing_common_attenuation():
    dev, state, _ = device((-512, -512))
    dev.set_balance(3)
    assert state == [-1280, -512]
    dev.set_balance(0)
    assert state == [-512, -512]


def test_balance_readback_failure_restores_both_original_trims():
    dev, state, writes = device((-512, -512))
    read = dev.get_channel_trims
    calls = 0

    def mismatch():
        nonlocal calls
        calls += 1
        return read() if calls == 1 else (0, 0)
    dev.get_channel_trims = mismatch
    with pytest.raises(OSError, match='did not verify'):
        dev.set_balance(3)
    assert state == [-512, -512]
    assert writes[-2:] == [(0, -512), (1, -512)]


@pytest.mark.parametrize('balance', [-7, 7, float('nan'), float('inf')])
def test_invalid_balance_never_writes(balance):
    dev, _, writes = device()
    with pytest.raises(ValueError):
        dev.set_balance(balance)
    assert writes == []


def test_unknown_firmware_and_insufficient_range_never_write():
    dev, _, writes = device()
    dev.supports_eq_mode = False
    assert dev.get_channel_trims() is None
    with pytest.raises(ValueError):
        dev.set_balance(1)
    assert writes == []
    dev, _, writes = device((-2560, -2560))
    with pytest.raises(ValueError):
        dev.set_balance(6)
    assert writes == []


def test_malformed_channel_echo_and_positive_gain_are_rejected():
    dev, _, _ = device((256, 0))
    with pytest.raises(ValueError):
        dev.get_channel_trims()
    dev.send_command = lambda _: [mc.REPORT_ID, mc.CMD_READ, 22, 0, 1, 0, 0, 0]
    with pytest.raises(OSError):
        dev.get_channel_trims()
