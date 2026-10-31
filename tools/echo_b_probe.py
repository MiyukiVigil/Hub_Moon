#!/usr/bin/env python3
"""
Read-only probe for the Moondrop ECHO-B (Conexant/Synaptics "Freeman3" chip).

Checks whether Hub Moon's HID stack (hidapi) can talk to the ECHO-B at all,
before any EQ support is written. It only sends query commands (chip code,
sample rate, EQ mode, and the 9 stored EQ bands) and never changes EQ,
presets or flash. Protocol notes: echo_b_protocol.md.

Dependencies:
    pip install hidapi

Usage:
    python3 echo_b_probe.py            # probe the first ECHO-B found
    python3 echo_b_probe.py --list     # list every Moondrop HID interface
    python3 echo_b_probe.py --dry-run  # print the packets without a device
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
        struct.pack("<%dI" % len(words), *words)


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


def probe(hid, path=None):
    found = list_interfaces(hid)
    info = next((d for d in found if d["path"] == path), None) if path else pick_interface(found)
    if info is None:
        print("\nNo ECHO-B (PID 0x1496-0x149C) found.")
        return 1
    print("\nUsing PID 0x%04x interface %s: %s"
          % (info["product_id"], info["interface_number"], info["path"]))

    dev = hid.device()
    try:
        dev.open_path(info["path"])
    except (IOError, OSError) as e:
        print("Could not open the HID interface: %s" % e)
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

        print("\nChip code (cmd %d):" % CMD_GET_CHIP_CODE)
        res = query(dev, CMD_GET_CHIP_CODE, [1, 0, 0, 0], out_len, in_len)
        if res and len(res) >= 14:
            code = struct.unpack_from("<i", res, 10)[0]
            print("  chip code %d (0x%x)" % (code, code & 0xFFFFFFFF))
        results["chip code"] = res is not None

        hz = None
        print("\nSample rate (cmd %d):" % CMD_GET_SAMPLE_RATE)
        res = query(dev, CMD_GET_SAMPLE_RATE, [SAMPLE_RATE_SUBCMD] + [0] * 12, out_len, in_len)
        if res and len(res) >= 18:
            idx = struct.unpack_from("<i", res, 14)[0]
            hz = FREQ_SAMPLE_RATE[idx] if 0 <= idx < len(FREQ_SAMPLE_RATE) else None
            print("  index %d -> %s" % (idx, "%d Hz" % hz if hz else "unknown"))
        results["sample rate"] = res is not None
        fs = hz if res and hz else 48000

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
        for band in EQ_BANDS:
            print("\nEQ band %d (cmd %d):" % (band, CMD_GET_EQ_BAND))
            res = query(dev, CMD_GET_EQ_BAND, [0, band] + [0] * 11, out_len, in_len)
            if res and len(res) >= 42:
                gain, raw, coeffs = decode_band(res)
                print("  shift %d  raw %s" % (gain, raw))
                print("  b0 %.6f b1 %.6f b2 %.6f a1 %.6f a2 %.6f" % coeffs)
                bands.append((band, describe_band(coeffs, fs)))
            else:
                bands.append((band, "read failed"))
        results["eq bands"] = all(desc != "read failed" for _, desc in bands)

        print("\nStored EQ (evaluated at %d Hz):" % fs)
        for band, desc in bands:
            print("  band %d: %s" % (band, desc))
    finally:
        dev.close()

    print("\nSummary: " + ", ".join(
        "%s %s" % (k, "OK" if v else "FAILED") for k, v in results.items()))
    return 0 if all(results.values()) else 2


def main():
    parser = argparse.ArgumentParser(description="Read-only ECHO-B probe")
    parser.add_argument("--list", action="store_true", help="list Moondrop HID interfaces")
    parser.add_argument("--path", help="HID path to probe (from --list)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the query packets without opening a device")
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
        return 0

    try:
        import hid
    except ImportError:
        print("hidapi is not installed: run  python3 -m pip install hidapi")
        return 1

    if args.list:
        list_interfaces(hid)
        return 0
    return probe(hid, args.path)


if __name__ == "__main__":
    sys.exit(main())
