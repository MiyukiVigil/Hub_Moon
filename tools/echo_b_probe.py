#!/usr/bin/env python3
"""
Probe for the Moondrop ECHO-B (Conexant/Synaptics "Freeman3" chip).

Checks whether Hub Moon's HID stack (hidapi) can talk to the ECHO-B at all,
before any EQ support is written. A plain run only sends query commands (chip
code, sample rate, EQ mode, and the 9 EQ bands of each channel) and changes nothing.

--write-test is the one exception: it live-sets EQ band 9 to a -6 dB dip at
1 kHz, reads it back, asks you to unplug and replug, reads it again, then sets
the band back to flat with the same command the Moondrop app uses to reset it.
It never touches flash (cmd 220); a replug undoes it (confirmed on hardware).

--listen-test makes the change unmistakable (a 400 Hz low-pass) and widens it
step by step until you hear it, then puts every band back as it was. --band-map
puts the same low-pass on one band at a time and asks whether you hear it and
in which ears. --find-right does the same on other band slots (read first,
skipped unless they hold a filter, then put back) to find the right channel.
--right-check repeats --find-right in separate steps, since the right ear went
loud after it, and reads every band and setting if that happens again.
These four read bands 1-9 of both channels first and write back exactly that.
--restore sets both channels back to flat; a replug does the same.
Protocol notes: echo_b_protocol.md.

Dependencies:
    pip install hidapi

Usage:
    python3 echo_b_probe.py            # probe the first ECHO-B found
    python3 echo_b_probe.py --list     # list every Moondrop HID interface
    python3 echo_b_probe.py --dry-run  # print the packets without a device
    python3 echo_b_probe.py --write-test  # temporary one-band write, then restore
    python3 echo_b_probe.py --listen-test # find what makes a live write audible
    python3 echo_b_probe.py --band-map    # which bands and channels are live
    python3 echo_b_probe.py --find-right  # where the right channel's bands are
    python3 echo_b_probe.py --right-check # what makes the right ear loud
    python3 echo_b_probe.py --restore     # set both channels back to flat
"""

import sys
import math
import cmath
import time
import struct
import argparse

MOONDROP_VID = 0x35D8
FREEMAN3_PIDS = range(0x1496, 0x149C + 1)  # res/xml/devicelist.xml "Freeman3"
HID_INTERFACE = 3
REPORT_ID = 1
DEFAULT_REPORT_LEN = 64  # used when the report descriptor cannot be read

REPLY_TIMEOUT_S = 1.0
REPLY_POLL_S = 0.005

CMD_GET_CHIP_CODE = 259
CMD_GET_SAMPLE_RATE = 346  # CONTROL_MGR_GET_PARAMETER_VALUE
SAMPLE_RATE_SUBCMD = 62
EQ_MODE_SUBCMD = 90
CMD_GET_EQ_CONFIG = 442    # sample-rate index the stored EQ was designed for
CMD_GET_EQ_BAND = 446      # [0, band] -> Gain byte at 18, B0 B1 B2 A0 A1 at 22..41
EQ_BANDS = range(1, 10)
CMD_EQ_BYPASS = 187        # [0] -> EQ CFG bypass off, sent once with 188
CMD_EQ_ENABLE = 188        # [1, 0...]
CMD_SET_EQ_BAND = 190      # [0, band, Gain, B0, B1, B2, A0, A1, 0...], live
TEST_BAND = 9              # live band 4, the last one the app's UI reaches
TEST_FILTER = (1000, -6.0, 1.0)  # Hz, dB, Q: a cut, so nothing can clip
FLAT_BAND = (3, [1 << 22, 0, 0, 0, 0])  # what the app writes to reset a band
LISTEN_FILTER = (400, 0.707)  # Hz, Q: low-pass, music sounds like it is behind a wall
FREQ_SAMPLE_RATE = [8000, 16000, 24000, 32000, 44100, 48000, 96000, 192000,
                    384000, 882000, 176000]


def caf_id(name):
    """CAF module id of a four-letter module name, e.g. "CTRL"."""
    c = [ord(ch) - 32 for ch in name]
    return (c[0] << 8) | (c[1] << 14) | (c[2] << 20) | (c[3] << 26)


CTRL_MODULE = caf_id("CTRL")


def build_message(cmd, words):
    """[report id 1, 0x00] + u32 header + u32 module + n data words, little-endian."""
    header = (len(words) & 0xFF) | (cmd << 16)
    return bytes([REPORT_ID, 0]) + struct.pack("<II", header, CTRL_MODULE) + \
        struct.pack("<%dI" % len(words), *(w & 0xFFFFFFFF for w in words))


def hexdump(data):
    return " ".join("%02x" % b for b in data)


def s24(value):
    """Sign-extend the low 24 bits, as the device stores 24-bit coefficients."""
    value &= 0xFFFFFF
    return value - (1 << 24) if value & 0x800000 else value


def decode_band(res):
    """
    Coefficients of one stored band as floats (b0, b1, b2, a1, a2), plus the raw
    values. The Gain byte is a shift: coefficients are scaled by 2^(25 - Gain),
    and A0/A1 hold -a1/-a2 of H(z) = (b0 + b1/z + b2/z^2) / (1 + a1/z + a2/z^2).
    """
    gain = struct.unpack_from("<b", res, 18)[0]
    raw = [s24(v) for v in struct.unpack_from("<5i", res, 22)]
    scale = 2.0 ** (25 - gain)
    b0, b1, b2, na1, na2 = (v / scale for v in raw)
    return gain, raw, (b0, b1, b2, -na1, -na2)


def peaking_band(freq, gain_db, q, fs):
    """
    RBJ peaking biquad as the ECHO-B stores it: (shift, [B0, B1, B2, A0, A1]),
    using the smallest shift that fits all five in 24 bits. Plain rounding is
    exact enough here; Hub Moon refines it for corners below ~100 Hz.
    """
    a = 10 ** (gain_db / 40)
    w = 2 * math.pi * freq / fs
    alpha, cos_w = math.sin(w) / (2 * q), math.cos(w)
    a0 = 1 + alpha / a
    values = [(1 + alpha * a) / a0, -2 * cos_w / a0, (1 - alpha * a) / a0,
              2 * cos_w / a0, -(1 - alpha / a) / a0]
    return quantise(values)


def low_pass_band(freq, q, fs):
    """RBJ low-pass in the same format. Never boosts, so it cannot clip."""
    w = 2 * math.pi * freq / fs
    alpha, cos_w = math.sin(w) / (2 * q), math.cos(w)
    a0 = 1 + alpha
    b = (1 - cos_w) / 2 / a0
    return quantise([b, 2 * b, b, 2 * cos_w / a0, -(1 - alpha) / a0])


def quantise(values):
    for shift in range(26):
        ints = [round(v * 2 ** (25 - shift)) for v in values]
        if all(-(1 << 23) <= i < (1 << 23) for i in ints):
            return shift, ints
    raise ValueError("filter does not fit 24 bits")


def response_db(coeffs, freq, fs):
    b0, b1, b2, a1, a2 = coeffs
    z1 = cmath.exp(-2j * math.pi * freq / fs)
    h = (b0 + b1 * z1 + b2 * z1 * z1) / (1 + a1 * z1 + a2 * z1 * z1)
    return 20 * math.log10(max(abs(h), 1e-12))


def describe_band(coeffs, fs):
    """Largest boost or cut between 20 Hz and 20 kHz, and where it is."""
    freqs = [20 * 1000 ** (i / 299) for i in range(300)]
    db, freq = max(((response_db(coeffs, f, fs), f) for f in freqs if f < fs / 2),
                   key=lambda p: abs(p[0]))
    if abs(db) < 0.05:
        return "flat"
    return "%+.1f dB around %d Hz" % (db, round(freq))


def report_lengths(descriptor):
    """
    Byte length of each (kind, report_id) in a HID report descriptor, where kind
    is "input", "output" or "feature". Only the short items that size reports
    are tracked; that is all we need to pad writes correctly.
    """
    lengths = {}
    report_size = report_count = report_id = 0
    i = 0
    while i < len(descriptor):
        prefix = descriptor[i]
        if prefix == 0xFE:  # long item: size byte, tag byte, data
            i += 3 + (descriptor[i + 1] if i + 1 < len(descriptor) else 0)
            continue
        size = (0, 1, 2, 4)[prefix & 3]
        value = int.from_bytes(bytes(descriptor[i + 1:i + 1 + size]), "little")
        tag_type = prefix & 0xFC
        if tag_type == 0x74:
            report_size = value
        elif tag_type == 0x94:
            report_count = value
        elif tag_type == 0x84:
            report_id = value
        elif tag_type in (0x80, 0x90, 0xB0):
            kind = {0x80: "input", 0x90: "output", 0xB0: "feature"}[tag_type]
            key = (kind, report_id)
            lengths[key] = lengths.get(key, 0) + report_size * report_count
        i += 1 + size
    return {key: (bits + 7) // 8 for key, bits in lengths.items()}


def list_interfaces(hid):
    found = [d for d in hid.enumerate() if d["vendor_id"] == MOONDROP_VID]
    if not found:
        print("No Moondrop HID interfaces found.")
    for d in found:
        print("PID 0x%04x  interface %s  usage_page 0x%04x  usage 0x%04x  %s  %s"
              % (d["product_id"], d["interface_number"], d["usage_page"],
                 d["usage"], d["product_string"], d["path"]))
    return found


def pick_interface(found):
    echo = [d for d in found if d["product_id"] in FREEMAN3_PIDS]
    for d in echo:
        if d["interface_number"] == HID_INTERFACE:
            return d
    # macOS can report interface_number as -1; fall back to the only candidate.
    return echo[0] if echo else None


def wait_reply(dev, in_len):
    """Poll GET_REPORT(input, id 1) until the reply bit is set, as the app does."""
    get_input = getattr(dev, "get_input_report", None)
    deadline = time.monotonic() + REPLY_TIMEOUT_S
    while time.monotonic() < deadline:
        res = None
        if get_input is not None:
            try:
                res = get_input(REPORT_ID, in_len + 1)
            except (IOError, OSError, ValueError):
                res = None
        if not res:
            # Some backends deliver the answer on the interrupt IN pipe instead.
            res = dev.read(in_len + 1, int(REPLY_POLL_S * 1000) or 1)
        if res and len(res) > 5 and res[5] & 0x80:
            return bytes(res)
        time.sleep(REPLY_POLL_S)
    return None


def query(dev, cmd, words, out_len, in_len):
    msg = build_message(cmd, words)
    packet = msg + bytes(max(0, out_len + 1 - len(msg)))
    print("  sent  (%d bytes): %s" % (len(packet), hexdump(packet)))
    written = dev.write(list(packet))
    if written < 0:
        print("  write failed: %s" % dev.error())
        return None
    res = wait_reply(dev, in_len)
    if res is None:
        print("  no reply within %.1f s" % REPLY_TIMEOUT_S)
        return None
    print("  reply (%d bytes): %s" % (len(res), hexdump(res)))
    status = struct.unpack_from("<h", res, 2)[0]
    print("  status %d (%s)" % (status, "ok" if status >= 0 else "error"))
    return res if status >= 0 else None


def open_echo_b(hid, path=None):
    """Open the ECHO-B's HID interface, or print why not and return None."""
    found = list_interfaces(hid)
    info = next((d for d in found if d["path"] == path), None) if path else pick_interface(found)
    if info is None:
        print("\nNo ECHO-B (PID 0x1496-0x149C) found.")
        return None
    print("\nUsing PID 0x%04x interface %s: %s"
          % (info["product_id"], info["interface_number"], info["path"]))

    dev = hid.device()
    try:
        dev.open_path(info["path"])
    except (IOError, OSError) as e:
        print("Could not open the HID interface: %s" % e)
        return None
    return dev


def report_sizes(dev):
    """(output, input) length of report id 1, from the report descriptor."""
    out_len = in_len = DEFAULT_REPORT_LEN - 1
    try:
        descriptor = dev.get_report_descriptor()
        print("Report descriptor (%d bytes): %s" % (len(descriptor), hexdump(descriptor)))
        lengths = report_lengths(descriptor)
        print("Report lengths: %s" % ", ".join(
            "%s id %d = %d" % (k[0], k[1], v) for k, v in sorted(lengths.items())))
        out_len = lengths.get(("output", REPORT_ID), out_len)
        in_len = lengths.get(("input", REPORT_ID), in_len)
    except (AttributeError, IOError, OSError, ValueError) as e:
        print("Report descriptor unavailable (%s); assuming %d-byte reports"
              % (e, DEFAULT_REPORT_LEN))
    return out_len, in_len


def current_sample_rate(dev, out_len, in_len):
    res = query(dev, CMD_GET_SAMPLE_RATE, [SAMPLE_RATE_SUBCMD] + [0] * 12, out_len, in_len)
    if res and len(res) >= 18:
        idx = struct.unpack_from("<i", res, 14)[0]
        hz = FREQ_SAMPLE_RATE[idx] if 0 <= idx < len(FREQ_SAMPLE_RATE) else None
        print("  index %d -> %s" % (idx, "%d Hz" % hz if hz else "unknown"))
        return hz
    return None


def read_band(dev, band, out_len, in_len, copy=0):
    """(shift, raw ints, float coeffs) of one stored band, or None."""
    res = query(dev, CMD_GET_EQ_BAND, [copy, band] + [0] * 11, out_len, in_len)
    if not res or len(res) < 42:
        return None
    gain, raw, coeffs = decode_band(res)
    print("  shift %d  raw %s" % (gain, raw))
    print("  b0 %.6f b1 %.6f b2 %.6f a1 %.6f a2 %.6f" % coeffs)
    return gain, raw, coeffs


def set_band(dev, band, shift, ints, out_len, in_len, copy=0):
    """Word 0 is the channel: 0 = left, 1 = right."""
    words = [copy, band, shift] + list(ints) + [0] * 5
    return query(dev, CMD_SET_EQ_BAND, words, out_len, in_len) is not None


def probe(hid, path=None):
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1

    results = {"open": True}
    try:
        print("Manufacturer: %s" % dev.get_manufacturer_string())
        print("Product:      %s" % dev.get_product_string())

        # The app only accepts the device if string 41 names the firmware family.
        try:
            print("String 41:    %s" % dev.get_indexed_string(41))
        except (IOError, OSError, ValueError) as e:
            print("String 41:    unavailable (%s)" % e)

        out_len, in_len = report_sizes(dev)

        print("\nChip code (cmd %d):" % CMD_GET_CHIP_CODE)
        res = query(dev, CMD_GET_CHIP_CODE, [1, 0, 0, 0], out_len, in_len)
        if res and len(res) >= 14:
            code = struct.unpack_from("<i", res, 10)[0]
            print("  chip code %d (0x%x)" % (code, code & 0xFFFFFFFF))
        results["chip code"] = res is not None

        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        hz = current_sample_rate(dev, out_len, in_len)
        results["sample rate"] = hz is not None
        fs = hz or 48000

        print("\nEQ mode (cmd %d [%d]):" % (CMD_GET_SAMPLE_RATE, EQ_MODE_SUBCMD))
        res = query(dev, CMD_GET_SAMPLE_RATE, [EQ_MODE_SUBCMD] + [0] * 12, out_len, in_len)
        if res and len(res) >= 18:
            print("  EQ mode %d" % struct.unpack_from("<i", res, 14)[0])
        results["eq mode"] = res is not None

        print("\nStored EQ sample rate (cmd %d):" % CMD_GET_EQ_CONFIG)
        res = query(dev, CMD_GET_EQ_CONFIG, [0] * 13, out_len, in_len)
        if res and len(res) >= 14:
            idx = struct.unpack_from("<i", res, 10)[0]
            eq_hz = FREQ_SAMPLE_RATE[idx] if 0 <= idx < len(FREQ_SAMPLE_RATE) else None
            print("  index %d -> %s" % (idx, "%d Hz" % eq_hz if eq_hz else "unknown"))
            fs = eq_hz or fs
        results["eq config"] = res is not None

        bands = []
        for copy, side in ((0, "left"), (1, "right")):
            for band in EQ_BANDS:
                print("\nEQ band %d, %s (cmd %d, word 0 = %d):" % (band, side, CMD_GET_EQ_BAND, copy))
                read = read_band(dev, band, out_len, in_len, copy)
                bands.append((band, side, describe_band(read[2], fs) if read else "read failed"))
        results["eq bands"] = all(desc != "read failed" for _, _, desc in bands)

        print("\nStored EQ (evaluated at %d Hz):" % fs)
        for band, side, desc in bands:
            print("  band %d %-5s: %s" % (band, side, desc))
    finally:
        dev.close()

    summary(results)
    return 0 if all(results.values()) else 2


def ask(prompt):
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


def summary(results):
    print("\nSummary: " + ", ".join(
        "%s %s" % (k, "OK" if v else "FAILED") for k, v in results.items()))


def write_test(hid, path=None):
    """
    Live-set one band, read it back, survive a replug, then put it back to flat.
    Sends exactly what the Moondrop app sends before and during a live EQ
    change (188, 187, 190), and nothing that writes flash.
    """
    freq, gain_db, q = TEST_FILTER
    results = {}
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1
    try:
        out_len, in_len = report_sizes(dev)
        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        fs = current_sample_rate(dev, out_len, in_len) or 48000

        print("\nBand %d before the test (cmd %d):" % (TEST_BAND, CMD_GET_EQ_BAND))
        before = read_band(dev, TEST_BAND, out_len, in_len)
        results["read before"] = before is not None
        if before is None:
            print("\nCould not read the band, so not writing anything.")
            summary(results)
            return 2

        shift, ints = peaking_band(freq, gain_db, q, fs)
        print("\nTest filter: %+.0f dB at %d Hz, Q %.1f, at %d Hz -> shift %d %s"
              % (gain_db, freq, q, fs, shift, ints))

        print("\nEQ enable (cmd %d):" % CMD_EQ_ENABLE)
        enabled = query(dev, CMD_EQ_ENABLE, [1] + [0] * 12, out_len, in_len) is not None
        print("\nEQ bypass off (cmd %d):" % CMD_EQ_BYPASS)
        unbypassed = query(dev, CMD_EQ_BYPASS, [0], out_len, in_len) is not None
        results["eq enable"] = enabled and unbypassed

        print("\nLive set band %d (cmd %d):" % (TEST_BAND, CMD_SET_EQ_BAND))
        results["write"] = set_band(dev, TEST_BAND, shift, ints, out_len, in_len)

        print("\nBand %d after the write (cmd %d):" % (TEST_BAND, CMD_GET_EQ_BAND))
        after = read_band(dev, TEST_BAND, out_len, in_len)
        results["read back matches"] = after is not None and (after[0], after[1]) == (shift, ints)

        heard = ask("\nPlay some music through the Echo-B now. The test is a 6 dB dip around "
                    "1 kHz, so voices and mids should sound a bit quieter or further away.\n"
                    "Did the sound change? [y/n] ")
    finally:
        dev.close()

    ask("\nNow unplug the Echo-B, plug it back in, wait a few seconds, then press Enter. ")
    dev = open_echo_b(hid, path)
    if dev is None:
        print("Could not reopen it. Once it is plugged in, run:  echo_b_probe.py --restore")
        summary(results)
        return 2
    try:
        out_len, in_len = report_sizes(dev)
        print("\nBand %d after replugging (cmd %d):" % (TEST_BAND, CMD_GET_EQ_BAND))
        replug = read_band(dev, TEST_BAND, out_len, in_len)
        if replug is None:
            after_replug = "read failed"
        elif (replug[0], replug[1]) == (shift, ints):
            after_replug = "kept the test filter"
        elif (replug[0], replug[1]) == FLAT_BAND:
            after_replug = "back to flat"
        else:
            after_replug = "something else (%s)" % describe_band(replug[2], fs)

        print("\nSet band %d back to flat (cmd %d):" % (TEST_BAND, CMD_SET_EQ_BAND))
        results["restore"] = set_band(dev, TEST_BAND, FLAT_BAND[0], FLAT_BAND[1], out_len, in_len)
        print("\nBand %d after restoring (cmd %d):" % (TEST_BAND, CMD_GET_EQ_BAND))
        final = read_band(dev, TEST_BAND, out_len, in_len)
        results["flat again"] = final is not None and (final[0], final[1]) == FLAT_BAND
    finally:
        dev.close()

    print("\nHeard a change: %s" % {"y": "yes", "yes": "yes", "n": "no", "no": "no"}.get(heard, heard or "not answered"))
    print("After replugging, band %d %s" % (TEST_BAND, after_replug))
    summary(results)
    return 0 if all(results.values()) else 2


def enable_eq(dev, out_len, in_len):
    print("\nEQ enable (cmd %d):" % CMD_EQ_ENABLE)
    enabled = query(dev, CMD_EQ_ENABLE, [1] + [0] * 12, out_len, in_len) is not None
    print("\nEQ bypass off (cmd %d):" % CMD_EQ_BYPASS)
    return query(dev, CMD_EQ_BYPASS, [0], out_len, in_len) is not None and enabled


def set_bands(dev, bands, copies, band_value, out_len, in_len):
    ok = True
    for copy in copies:
        for band in bands:
            print("\nSet band %d, word 0 = %d (cmd %d):" % (band, copy, CMD_SET_EQ_BAND))
            ok = set_band(dev, band, band_value[0], band_value[1], out_len, in_len, copy) and ok
    return ok


def snapshot(dev, out_len, in_len):
    """{(band, word 0): (shift, raw)} for bands 1-9 of both channels, or None if a read fails."""
    saved = {}
    for copy in (0, 1):
        for band in EQ_BANDS:
            print("\nSave band %d, word 0 = %d (cmd %d):" % (band, copy, CMD_GET_EQ_BAND))
            read = read_band(dev, band, out_len, in_len, copy)
            if read is None:
                return None
            saved[(band, copy)] = (read[0], read[1])
    return saved


def put_back(dev, saved, out_len, in_len):
    """Write every saved slot back and check it reads back the same."""
    ok = True
    for (band, copy), value in saved.items():
        ok = set_bands(dev, [band], [copy], value, out_len, in_len) and ok
    for (band, copy), value in saved.items():
        print("\nBand %d, word 0 = %d now (cmd %d):" % (band, copy, CMD_GET_EQ_BAND))
        final = read_band(dev, band, out_len, in_len, copy)
        ok = ok and final is not None and (final[0], final[1]) == value
    return ok


def listen_test(hid, path=None):
    """
    --write-test showed a live write reads back but was not heard. This makes
    the change unmistakable (a 400 Hz low-pass) and widens it one step at a
    time until it is heard: word 0 = 0 on band 9, then word 0 = 1 too, then
    every band, then EQ enable sent again after the writes. Every band is
    read first and put back as it was at the end; a replug clears it anyway.
    """
    freq, q = LISTEN_FILTER
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1
    heard_at, results = None, {}
    try:
        out_len, in_len = report_sizes(dev)
        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        fs = current_sample_rate(dev, out_len, in_len) or 48000
        lp = low_pass_band(freq, q, fs)
        print("\nListening filter: %d Hz low-pass, Q %.3f, at %d Hz -> shift %d %s"
              % (freq, q, fs, lp[0], lp[1]))
        saved = snapshot(dev, out_len, in_len)
        if saved is None:
            print("\nCould not read every band, so nothing was changed.")
            return 2
        results["eq enable"] = enable_eq(dev, out_len, in_len)

        steps = [
            ("band 9, word 0 = 0", lambda: set_bands(dev, [TEST_BAND], [0], lp, out_len, in_len)),
            ("band 9, word 0 = 1 as well", lambda: set_bands(dev, [TEST_BAND], [1], lp, out_len, in_len)),
            ("bands 1-9, both values of word 0",
             lambda: set_bands(dev, EQ_BANDS, [0, 1], lp, out_len, in_len)),
            ("EQ enable sent again after the writes", lambda: enable_eq(dev, out_len, in_len)),
        ]
        ask("\nStart some music through the Echo-B and leave it playing, then press Enter. ")
        for n, (label, run) in enumerate(steps, 1):
            print("\n=== Step %d: %s ===" % (n, label))
            results["step %d" % n] = run()
            if n == 2:
                print("\nBand 9, word 0 = 1, read back (cmd %d):" % CMD_GET_EQ_BAND)
                read_band(dev, TEST_BAND, out_len, in_len, copy=1)
            answer = ask("\nStep %d: does the music now sound muffled, like it is behind a wall? [y/n] " % n)
            if answer.startswith("y"):
                heard_at = "step %d (%s)" % (n, label)
                break

        print("\n=== Putting every band back as it was ===")
        results["put back as found"] = put_back(dev, saved, out_len, in_len)
        if heard_at:
            back = ask("\nIs the music back to normal now? [y/n] ")
            results["sounds normal again"] = back.startswith("y")
    finally:
        dev.close()

    print("\nFirst heard at: %s" % (heard_at or "never"))
    summary(results)
    return 0 if all(results.values()) else 2


# (band, word 0) pairs for --band-map. Band 9 / word 0 = 0 was heard in --listen-test
# and comes first as the control; the rest ask which bands and which word 0 reach the audio.
MAP_TARGETS = [(9, 0), (9, 1), (5, 0), (1, 0), (4, 0)]


def band_map(hid, path=None):
    """
    --listen-test heard a low-pass on band 9 with word 0 = 0. This puts the same
    low-pass on one (band, word 0) pair at a time and asks whether it is heard and
    in which ears, putting that band back as it was before the next. It answers which
    bands are in the audio path (the app only writes 5-9) and whether word 0 picks
    a channel or something else.
    """
    freq, q = LISTEN_FILTER
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1
    heard, results = {}, {}
    try:
        out_len, in_len = report_sizes(dev)
        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        fs = current_sample_rate(dev, out_len, in_len) or 48000
        lp = low_pass_band(freq, q, fs)
        print("\nListening filter: %d Hz low-pass, Q %.3f, at %d Hz -> shift %d %s"
              % (freq, q, fs, lp[0], lp[1]))
        saved = snapshot(dev, out_len, in_len)
        if saved is None:
            print("\nCould not read every band, so nothing was changed.")
            return 2
        results["eq enable"] = enable_eq(dev, out_len, in_len)

        ask("\nStart some music through the Echo-B and leave it playing, then press Enter. ")
        for band, copy in MAP_TARGETS:
            label = "band %d, word 0 = %d" % (band, copy)
            print("\n=== %s ===" % label)
            results[label] = set_bands(dev, [band], [copy], lp, out_len, in_len)
            answer = ask("\n%s: does the music sound muffled, like it is behind a wall? [y/n] " % label)
            if answer.startswith("y"):
                ears = ask("Muffled in which ears: both, left only or right only? [b/l/r] ")
                heard[label] = {"l": "left only", "r": "right only"}.get(ears[:1], "both")
            else:
                heard[label] = "not heard"
            results[label + " put back"] = set_bands(dev, [band], [copy], saved[(band, copy)], out_len, in_len)

        print("\n=== Putting every band back as it was ===")
        results["put back as found"] = put_back(dev, saved, out_len, in_len)
        back = ask("\nIs the music back to normal now? [y/n] ")
        results["sounds normal again"] = back.startswith("y")
    finally:
        dev.close()

    print("\nWhat was heard:")
    for label, what in heard.items():
        print("  %-20s %s" % (label, what))
    summary(results)
    return 0 if all(results.values()) else 2


# (band, word 0) candidates for the right channel, for --find-right. --band-map heard
# bands 1, 4, 5 and 9 with word 0 = 0 in the left ear only and band 9 / word 0 = 1 not
# at all. The first two are slots the app already writes; the rest are not, so each is
# read first and only written when the device answers with a real filter.
RIGHT_TARGETS = [(5, 1), (1, 1), (9, 2), (9, 3), (10, 0), (14, 0), (18, 0)]


def read_slot(dev, band, copy, out_len, in_len):
    """(shift, raw ints) of a slot, or None unless the reply echoes it and holds a filter."""
    res = query(dev, CMD_GET_EQ_BAND, [copy, band] + [0] * 11, out_len, in_len)
    if not res or len(res) < 42:
        return None
    echoed = struct.unpack_from("<2i", res, 10) == (copy, band)
    gain, raw, _ = decode_band(res)
    print("  shift %d  raw %s%s" % (gain, raw, "" if echoed else "  (slot not echoed)"))
    # b0 = 0 would silence the channel, so a slot like that cannot be in the audio path.
    return (gain, raw) if echoed and raw[0] != 0 and 0 <= gain <= 25 else None


def find_right(hid, path=None):
    """
    --band-map heard every live change in the left ear only. This tries other
    (band, word 0) slots one at a time to find the right channel's bands. Each
    slot is read first and skipped unless the device answers with a real filter;
    after the listen it gets exactly what was read back. It stops at the first
    slot heard on the right, then puts bands 1-9 of both channels back as found.
    """
    freq, q = LISTEN_FILTER
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1
    heard, results = {}, {}
    try:
        out_len, in_len = report_sizes(dev)
        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        fs = current_sample_rate(dev, out_len, in_len) or 48000
        lp = low_pass_band(freq, q, fs)
        print("\nListening filter: %d Hz low-pass, Q %.3f, at %d Hz -> shift %d %s"
              % (freq, q, fs, lp[0], lp[1]))
        saved = snapshot(dev, out_len, in_len)
        if saved is None:
            print("\nCould not read every band, so nothing was changed.")
            return 2
        results["eq enable"] = enable_eq(dev, out_len, in_len)

        ask("\nStart some music through the Echo-B and leave it playing, then press Enter. ")
        for band, copy in RIGHT_TARGETS:
            label = "band %d, word 0 = %d" % (band, copy)
            print("\n=== %s ===" % label)
            print("\nRead first (cmd %d):" % CMD_GET_EQ_BAND)
            before = read_slot(dev, band, copy, out_len, in_len)
            if before is None:
                heard[label] = "skipped, no filter there"
                continue
            results[label] = set_bands(dev, [band], [copy], lp, out_len, in_len)
            answer = ask("\n%s: does the music sound muffled, like it is behind a wall? [y/n] " % label)
            if answer.startswith("y"):
                ears = ask("Muffled in which ears: both, left only or right only? [b/l/r] ")
                heard[label] = {"l": "left only", "r": "right only"}.get(ears[:1], "both")
            else:
                heard[label] = "not heard"
            results[label + " put back"] = set_bands(dev, [band], [copy], before, out_len, in_len)
            if heard[label] in ("right only", "both"):
                break

        print("\n=== Putting every band back as it was ===")
        results["put back as found"] = put_back(dev, saved, out_len, in_len)
        back = ask("\nIs the music back to normal now? [y/n] ")
        results["sounds normal again"] = back.startswith("y")
    finally:
        dev.close()

    print("\nWhat was heard:")
    for label, what in heard.items():
        print("  %-20s %s" % (label, what))
    summary(results)
    return 0 if all(results.values()) else 2


def device_state(dev, out_len, in_len):
    """Data words of each read-only setting query, to compare before and during a problem."""
    state = {}
    for name, cmd, words in (
            ("sample rate", CMD_GET_SAMPLE_RATE, [SAMPLE_RATE_SUBCMD] + [0] * 12),
            ("EQ mode", CMD_GET_SAMPLE_RATE, [EQ_MODE_SUBCMD] + [0] * 12),
            ("stored EQ sample rate", CMD_GET_EQ_CONFIG, [0] * 13)):
        print("\n%s (cmd %d):" % (name[0].upper() + name[1:], cmd))
        res = query(dev, cmd, words, out_len, in_len)
        state[name] = hexdump(res[10:]) if res else "read failed"
    return state


# Right-channel bands for --right-check: 9 was not heard in --band-map, 5 was heard in
# --find-right and was followed by the loud right ear.
CHECK_BANDS = [9, 5]


def right_check(hid, path=None):
    """
    After --find-right the right ear was loud and clipping until a replug, yet the
    plain probe after the replug reads both channels flat, so writing flat changed
    nothing. This repeats that run in separate steps (EQ enable alone, then the
    low-pass on band 9 and band 5 of the right channel, each put back), asking
    after each one whether the right ear has gone loud. If it has, it reads every
    band and setting while the problem is there and compares them with the start.
    """
    freq, q = LISTEN_FILTER
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1
    heard, results, loud_after, changed = {}, {}, None, None
    try:
        out_len, in_len = report_sizes(dev)
        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        fs = current_sample_rate(dev, out_len, in_len) or 48000
        lp = low_pass_band(freq, q, fs)
        print("\nListening filter: %d Hz low-pass, Q %.3f, at %d Hz -> shift %d %s"
              % (freq, q, fs, lp[0], lp[1]))
        saved = snapshot(dev, out_len, in_len)
        if saved is None:
            print("\nCould not read every band, so nothing was changed.")
            return 2
        before = device_state(dev, out_len, in_len)

        ask("\nTurn the volume down a little, start some music through the Echo-B and leave it "
            "playing, then press Enter. ")
        answer = ask("\nNothing has been changed yet. Do both ears sound equally loud and clean? [y/n] ")
        heard["before any change"] = "even" if answer.startswith("y") else "uneven"

        results["eq enable"] = enable_eq(dev, out_len, in_len)
        answer = ask("\nEQ is now switched on, with every band as it was. Is the right ear now "
                     "louder or harsher than the left? [y/n] ")
        heard["EQ enable alone"] = "right loud" if answer.startswith("y") else "no change"
        if answer.startswith("y"):
            loud_after = "EQ enable alone"

        for band in CHECK_BANDS if loud_after is None else []:
            label = "band %d, right" % band
            print("\n=== %s ===" % label)
            results[label] = set_bands(dev, [band], [1], lp, out_len, in_len)
            answer = ask("\n%s: does the music sound muffled, like it is behind a wall? [y/n] " % label)
            if answer.startswith("y"):
                ears = ask("Muffled in which ears: both, left only or right only? [b/l/r] ")
                heard[label] = {"l": "left only", "r": "right only"}.get(ears[:1], "both")
            else:
                heard[label] = "not heard"
            results[label + " put back"] = set_bands(dev, [band], [1], saved[(band, 1)], out_len, in_len)
            answer = ask("\n%s is back as it was. Listen for about 20 seconds and compare the ears.\n"
                         "Is the right ear now louder or harsher than the left? [y/n] " % label)
            heard[label + " put back"] = "right loud" if answer.startswith("y") else "even"
            if answer.startswith("y"):
                loud_after = label
                break

        if loud_after:
            print("\n=== Reading everything while the right ear is loud ===")
            now = snapshot(dev, out_len, in_len)
            now_state = device_state(dev, out_len, in_len)
            changed = [("band %d, word 0 = %d" % slot, saved[slot], now[slot] if now else None)
                       for slot in saved if not now or now[slot] != saved[slot]]
            changed += [(name, before[name], now_state[name])
                        for name in before if now_state[name] != before[name]]
            print("\n=== Writing every band again ===")
            results["put back as found"] = put_back(dev, saved, out_len, in_len)
            answer = ask("\nEvery band has been written again. Is the right ear still loud? [y/n] ")
            heard["after writing every band again"] = "still loud" if answer.startswith("y") else "even"
        else:
            print("\n=== Putting every band back as it was ===")
            results["put back as found"] = put_back(dev, saved, out_len, in_len)
    finally:
        dev.close()

    print("\nWhat was heard:")
    for label, what in heard.items():
        print("  %-32s %s" % (label, what))
    if loud_after:
        print("\nRight ear went loud after: %s" % loud_after)
        print("Changed since the start (what, before, while loud):")
        for what, was, now in changed or [("nothing", "", "")]:
            print("  %s  %s  ->  %s" % (what, was, now))
        print("\nIf it is still loud, unplug and replug the Echo-B.")
    summary(results)
    return 0 if all(results.values()) else 2


def restore(hid, path=None):
    """Set both channels' bands back to flat, which is how both read at power-up."""
    dev = open_echo_b(hid, path)
    if dev is None:
        return 1
    try:
        out_len, in_len = report_sizes(dev)
        ok = set_bands(dev, EQ_BANDS, [0, 1], FLAT_BAND, out_len, in_len)
        flat = True
        for copy in (0, 1):
            print("\nBand %d, word 0 = %d now (cmd %d):" % (TEST_BAND, copy, CMD_GET_EQ_BAND))
            final = read_band(dev, TEST_BAND, out_len, in_len, copy)
            flat = flat and final is not None and (final[0], final[1]) == FLAT_BAND
    finally:
        dev.close()
    print("\nIf it still sounds wrong, unplug and replug the Echo-B.")
    summary({"restore": ok, "flat again": flat})
    return 0 if ok and flat else 2


def main():
    parser = argparse.ArgumentParser(description="ECHO-B probe (read-only unless --write-test or --restore)")
    parser.add_argument("--list", action="store_true", help="list Moondrop HID interfaces")
    parser.add_argument("--path", help="HID path to probe (from --list)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the query packets without opening a device")
    parser.add_argument("--write-test", action="store_true",
                        help="temporarily change EQ band %d, then set it back to flat" % TEST_BAND)
    parser.add_argument("--listen-test", action="store_true",
                        help="temporarily muffle the sound in widening steps, then put every band back")
    parser.add_argument("--band-map", action="store_true",
                        help="temporarily muffle one band at a time to find which bands and channels are live")
    parser.add_argument("--find-right", action="store_true",
                        help="temporarily muffle other band slots one at a time to find the right channel")
    parser.add_argument("--right-check", action="store_true",
                        help="repeat --find-right in separate steps to find what makes the right ear loud")
    parser.add_argument("--restore", action="store_true",
                        help="set both channels' EQ bands back to flat")
    args = parser.parse_args()

    if args.dry_run:
        print("CTRL module id 0x%08x" % CTRL_MODULE)
        print("chip code:   %s" % hexdump(build_message(CMD_GET_CHIP_CODE, [1, 0, 0, 0])))
        print("sample rate: %s" % hexdump(
            build_message(CMD_GET_SAMPLE_RATE, [SAMPLE_RATE_SUBCMD] + [0] * 12)))
        print("eq mode:     %s" % hexdump(
            build_message(CMD_GET_SAMPLE_RATE, [EQ_MODE_SUBCMD] + [0] * 12)))
        print("eq config:   %s" % hexdump(build_message(CMD_GET_EQ_CONFIG, [0] * 13)))
        print("eq band 1:   %s" % hexdump(build_message(CMD_GET_EQ_BAND, [0, 1] + [0] * 11)))
        shift, ints = peaking_band(*TEST_FILTER, 48000)
        print("eq enable:   %s" % hexdump(build_message(CMD_EQ_ENABLE, [1] + [0] * 12)))
        print("eq bypass:   %s" % hexdump(build_message(CMD_EQ_BYPASS, [0])))
        print("test band:   %s" % hexdump(build_message(
            CMD_SET_EQ_BAND, [0, TEST_BAND, shift] + ints + [0] * 5)))
        print("flat band:   %s" % hexdump(build_message(
            CMD_SET_EQ_BAND, [0, TEST_BAND, FLAT_BAND[0]] + FLAT_BAND[1] + [0] * 5)))
        lp = low_pass_band(*LISTEN_FILTER, 48000)
        print("listen band: %s" % hexdump(build_message(
            CMD_SET_EQ_BAND, [1, TEST_BAND, lp[0]] + lp[1] + [0] * 5)))
        return 0

    try:
        import hid
    except ImportError:
        print("hidapi is not installed: run  python3 -m pip install hidapi")
        return 1

    if args.list:
        list_interfaces(hid)
        return 0
    if args.restore:
        return restore(hid, args.path)
    if args.write_test or args.listen_test or args.band_map or args.find_right or args.right_check:
        test = (right_check if args.right_check else find_right if args.find_right
                else band_map if args.band_map
                else listen_test if args.listen_test else write_test)
        try:
            return test(hid, args.path)
        except KeyboardInterrupt:
            print("\nStopped. Unplug and replug the Echo-B to undo every change"
                  "\n(live writes do not survive that).")
            return 130
    return probe(hid, args.path)


if __name__ == "__main__":
    sys.exit(main())
