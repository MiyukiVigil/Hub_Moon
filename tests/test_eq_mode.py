"""Separate the hardware mode from the stored profile and defer live EQ safely."""
import struct
import pytest
import moondrop_control as mc


@pytest.mark.parametrize('mode', [0, 4, 8, 9])
def test_mode_reader_uses_separate_ram_word(mode):
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.supports_eq_mode = True
    calls = []
    def reply(cmd):
        calls.append(cmd)
        return [75, 128, 0, 8] + list(struct.pack('<II', 0x10648c, mode))
    dev.send_command = reply
    assert dev.get_physical_eq_mode() == mode
    assert calls == [[128, 0, 8, 140, 100, 16, 0]]


def test_unknown_firmware_never_reads_mode_ram():
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.send_command = lambda *a: pytest.fail('unverified RAM read')
    assert dev.get_physical_eq_mode() is None


@pytest.mark.parametrize('reply', [[], [75, 128, 0, 8],
    [75, 128, 0, 8] + list(struct.pack('<II', 0x106490, 9)),
    [75, 128, 0, 8] + list(struct.pack('<II', 0x10648c, 255))])
def test_invalid_mode_reply_is_not_treated_as_custom(reply):
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.supports_eq_mode = True
    dev.send_command = lambda *a: reply
    with pytest.raises((OSError, ValueError)):
        dev.get_physical_eq_mode()


def worker():
    pytest.importorskip('slint')
    from gui.bridge import DeviceWorker
    events = []
    w = DeviceWorker(lambda *event: events.append(event))
    class Dac:
        supports_eq_mode = True
        supports_pregain = True
        mode = 8
        def __init__(self): self.writes = []
        def get_physical_eq_mode(self): return self.mode
        def write_peq_index(self, *args): self.writes.append(('band', args))
        def set_pregain(self, *args, **kwargs): self.writes.append(('pregain', args))
        def save_eq_to_flash(self): self.writes.append(('flash', ()))
        def save_offset_to_flash(self): self.writes.append(('offset_flash', ()))
        def close(self): pass
    w._dev = Dac()
    return w, events


def test_red_edits_are_staged_latest_only_and_yellow_applies_without_flash():
    w, events = worker()
    w.write_band(0, 'peak', 1000, 1, 1)
    w.write_band(0, 'peak', 1000, 4, 1)
    w.set_pregain(-5)
    assert w._dev.writes == []
    w._dev.mode = 9
    assert w._flush_staged_eq()
    assert w._dev.writes == [('band', (0, 'peak', 1000, 4, 1)), ('pregain', (-5.0,))]
    assert w._staged_eq == {}


def test_red_save_does_not_claim_success_or_write_flash():
    w, events = worker()
    w.write_band(0, 'peak', 1000, 4, 1)
    w.save_to_flash()
    assert w._dev.writes == []
    assert not any(e[0] == 'saved' for e in events)
    assert any(e[0] == 'error' and 'yellow' in e[1] for e in events)
    w._dev.mode = 9
    w.save_to_flash()
    assert [kind for kind, _ in w._dev.writes] == ['band', 'flash', 'offset_flash']
    assert ('saved', None) in events


def test_red_import_keeps_editor_state_and_does_not_read_old_bands_back():
    w, events = worker()
    w._read_all = lambda: pytest.fail('staged import read stale device bands')
    w.apply_bands([dict(index=0, type='peak', frequency=1000, gain=4, q=1)], -5, None)
    assert w._dev.writes == []
    assert 0 in w._staged_eq
    assert not any(e[0] == 'snapshot' for e in events)


def test_mode_change_during_flush_keeps_remaining_edits_staged():
    w, _ = worker()
    w.write_band(0, 'peak', 1000, 4, 1)
    w.write_band(1, 'peak', 2000, 3, 1)
    w._dev.mode = 9
    original = w._dev.write_peq_index
    def write(*args):
        original(*args)
        w._dev.mode = 8
    w._dev.write_peq_index = write
    assert not w._flush_staged_eq()
    assert list(w._staged_eq) == [1]
    assert len(w._dev.writes) == 1


def test_existing_enabled_builtin_mode_can_still_edit_custom_curve():
    w, _ = worker()
    w._dev.mode = 2
    w.write_band(0, 'peak', 1000, 4, 1)
    assert w._dev.writes == [('band', (0, 'peak', 1000, 4, 1))]


def test_failed_mode_read_never_writes_or_claims_save():
    w, events = worker()
    def failed(): raise OSError('Disconnected')
    w._dev.get_physical_eq_mode = failed
    w.save_to_flash([dict(index=0, type='peak', frequency=1000, gain=4, q=1)], -5)
    assert w._dev.writes == []
    assert not any(e[0] == 'saved' for e in events)


def switching_device(mode=8):
    dev = mc.MoondropDevice.__new__(mc.MoondropDevice)
    dev.supports_eq_mode = True
    memory = {0x10648c: struct.pack('<I', mode), 0x11d234: bytes.fromhex('02000900'),
              0x106550: bytes.fromhex('00aabbcc'),
              0x1064ec: bytes([mode == 9, mode, 0, 0])}
    writes = []
    def command(cmd, wait_response=True):
        assert cmd[1] == 0  # No flash-save or profile-select command.
        address = struct.unpack('<I', bytes(cmd[3:7]))[0]
        if cmd[0] == mc.CMD_READ:
            return [75, 128, 0, 8] + list(struct.pack('<I', address)) + list(memory[address])
        assert not wait_response
        word = bytes(cmd[7:11])
        writes.append((address, word))
        memory[address] = word
        if address == 0x106550:
            assert word == bytes.fromhex('03aabbcc')
            profile = struct.unpack('<HH', memory[0x11d234])[1]
            memory[0x1064ec] = bytes([profile == 9, profile, 0, 0])
            memory[address] = bytes.fromhex('00aabbcc')
    dev.send_command = command
    return dev, memory, writes


def test_software_switch_reloads_dsp_and_preserves_custom_profile_and_neighbors():
    dev, memory, writes = switching_device()
    assert dev.set_custom_eq_enabled(True) == 9
    assert memory[0x1064ec] == bytes.fromhex('01090000')
    assert dev.set_custom_eq_enabled(False) == 8
    assert memory[0x1064ec] == bytes.fromhex('00080000')
    assert memory[0x11d234] == bytes.fromhex('02000900')
    assert memory[0x106550] == bytes.fromhex('00aabbcc')
    assert {a for a, _ in writes} == {0x10648c, 0x11d234, 0x106550}


def test_switching_to_current_mode_is_read_only():
    dev, _, writes = switching_device(9)
    assert dev.set_custom_eq_enabled(True) == 9
    assert writes == []


def test_switch_rejects_unverified_configuration_before_mutating_ram():
    dev, memory, writes = switching_device()
    memory[0x11d234] = bytes.fromhex('01000900')
    with pytest.raises(ValueError):
        dev.set_custom_eq_enabled(True)
    assert writes == []


def test_switch_rejects_unknown_firmware_without_accessing_ram():
    dev, _, writes = switching_device()
    dev.supports_eq_mode = False
    dev.send_command = lambda *a, **kw: pytest.fail('Unverified firmware access')
    with pytest.raises(ValueError):
        dev.set_custom_eq_enabled(True)


def test_busy_firmware_prevents_switch_writes():
    dev, memory, writes = switching_device()
    memory[0x106550] = bytes.fromhex('02aabbcc')
    with pytest.raises(OSError):
        dev.set_custom_eq_enabled(True)
    assert writes == []


def test_partial_switch_failure_restores_mode_dsp_and_header():
    dev, memory, _ = switching_device()
    original_write = dev._write_eq_ram_word
    failed = False
    def write(address, word):
        nonlocal failed
        original_write(address, word)
        if address == 0x10648c and word == struct.pack('<I', 9) and not failed:
            failed = True
            raise OSError('Simulated switch failure')
    dev._write_eq_ram_word = write
    with pytest.raises(OSError, match='Simulated switch failure'):
        dev.set_custom_eq_enabled(True)
    assert memory[0x10648c] == struct.pack('<I', 8)
    assert memory[0x1064ec] == bytes.fromhex('00080000')
    assert memory[0x11d234] == bytes.fromhex('02000900')
