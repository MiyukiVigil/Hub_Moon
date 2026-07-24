#!/usr/bin/env python3
"""
Moondrop DAC USB HID Control Script
Reverse engineered from https://hub.moondroplab.tech/

Dependencies:
    pip install hidapi

Usage:
    python3 moondrop_control.py --list
    python3 moondrop_control.py --info
    python3 moondrop_control.py --get-peq
    python3 moondrop_control.py --set-pregain -3.5
    python3 moondrop_control.py --set-peq 0 peaking 1000 -3.0 1.0
"""

import sys
import os
import json
import math
import time
import struct
import argparse
try:
    import hid
except ImportError:
    print("Error: The 'hidapi' package is required. Install it using: pip install hidapi")
    sys.exit(1)

# Default vendor ID for Moondrop
MOONDROP_VID = 0x35D8

# Device registry, mirrored from the official web app's own table (vendor 0x35D8).
# These all drive PEQ by writing Q2.30 biquad coefficients, which is what this
# tool implements.
SUPPORTED_DEVICES = {
    0x011B: "Rays",
    0x011C: "Marigold",
    0x011D: "DAWN PRO2",
    0x011E: "AG Rays",
    0x0120: "DHA15",
    0x012A: "INN Deco75-DH Audio",
    0x012B: "Deco Audio System",
    0x43DA: "MOONRIVER 3",
    0x98D3: "FreeDSP Pro",
    0x98D4: "FreeDSP Mini",
    0x98D5: "E.S. combo",
}

# Recognised but not driveable. The Old Fashioned does not use biquad coefficients
# at all: it writes PEQ through device registers (EQ_REG_BASE 38, WRITE_REG 87)
# as int8 gain x10 / uint16 frequency / int16 Q x1000, and reports
# supportPreGain=false and supportGlobalGain=false. Every command this tool sends
# would be meaningless to it, so refuse rather than corrupt its flash.
UNSUPPORTED_DEVICES = {
    0x0122: ("Old Fashioned", "uses a register-based PEQ protocol this tool does not implement"),
}

# Commands definition (from Dt in JS)
CMD_WRITE = 1
CMD_READ = 128

SUB_SAVE_EQ_TO_FLASH = 1
SUB_ACTIVE_EQ = 15
SUB_UPDATE_EQ = 9
SUB_UPDATE_EQ_COEFF_TO_REG = 10
SUB_DAC_OFFSET = 3
SUB_PRE_GAIN = 35
SUB_SAVE_OFFSET_TO_FLASH = 4
SUB_CLEAR_FLASH = 5
SUB_FIRMWARE_VERSION = 12
SUB_ENTER_UPGRADE_MODE = 255

# Filter types mapping
FILTER_TYPES = {
    "disabled": 0,
    "low_shelf": 1,
    "peaking": 2,
    "high_shelf": 3,
    "low_pass": 4,
    "high_pass": 5
}
REV_FILTER_TYPES = {v: k for k, v in FILTER_TYPES.items()}

# ---------------------------------------------------------------------------
# Moondrop Hub community preset library
#
# The official web app carries a public library of user-made PEQ curves. Read
# access needs no account, no key and no token: every GET below answers
# unauthenticated. Publishing, liking and favouriting do need a login, and this
# tool deliberately implements none of them — it reads.
#
# The library is not called "presets" anywhere in the app; the resource is
# `peq-configs`, which is why it is easy to miss.
# ---------------------------------------------------------------------------
HUB_API = "https://cdn-service.moondroplab.tech/api/v1"
HUB_CDN = "https://cdn.moondroplab.tech"
HUB_CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "hub_moon")
HUB_CACHE_TTL = 24 * 3600
HUB_UA = "hub_moon/1.0 (+https://hubmoon.miyukivigil.tech)"

# product_id -> the Hub's productUUID for that device.
#
# This table cannot be derived at runtime and has to be hardcoded here. The API's
# own products/all lists 102 products and reports pid:null and vid:null for every
# single one of them, so nothing served by the API connects a plugged-in DAC to its
# presets. The link exists only inside the web app's JS bundle, where each device
# class carries a static config: vv[] maps Class->pid, `let Su=Js` aliases it, and
# Ve(Js,"config",{productUUID}) holds the uuid. These 12 are that join, resolved.
#
# Old Fashioned is included: it has 75 presets of its own and they are readable,
# even though this tool refuses to *write* to that device (register protocol).
PRODUCT_UUIDS = {
    0x011B: "23d46ee2-3926-4c84-8b40-2fc6f08e12f0",  # Rays
    0x011C: "395619a3-3442-419a-a598-94f9f1d4ef4b",  # Marigold
    0x011D: "069eed07-c968-427e-9243-32663ad6eb25",  # DAWN PRO2
    0x011E: "c0a224c7-b6e1-4245-9f30-7f5233e082a5",  # AG Rays
    0x0120: "9f1fd925-4122-477d-a142-9ae6f931773e",  # DHA15
    0x0122: "97778394-2d4b-49da-bd45-416213b2baff",  # Old Fashioned (read-only here)
    0x012A: "b30e3988-a9f9-432c-b41d-9d413ecb86d4",  # INN Deco75-DH Audio
    0x012B: "175b4b8f-2853-4bfe-8c91-5c6c5430d020",  # Deco Audio System
    0x43DA: "22a34fac-c91d-465a-ad5c-1898c773fff6",  # MOONRIVER 3
    0x98D3: "3a3ebcb5-7605-4d1f-9e27-3fd3d8a3af0e",  # FreeDSP Pro
    0x98D4: "666c7b9f-46fc-42a7-83ff-dd1c9f2c5f8b",  # FreeDSP Mini
    0x98D5: "7ba57e23-b6e5-4ffb-9398-da8639eaddad",  # E.S. combo
}

# The Hub's filterType strings -> our FILTER_TYPES keys.
#
# Only the 2nd-order variants reach the wire. The app's own lookup is
#   Ta={DISABLED:0,LOW_SHELF_2:1,PEAKING:2,HIGH_SHELF_2:3,LOW_PASS_2:4,HIGH_PASS_2:5}
# — identical to FILTER_TYPES above — and its writer is
#   if (u && u in Ta) o[33]=Ta[u]; else o[33]=Ta.PEAKING
# so anything not in that table is written as PEAKING by the official app. We
# reproduce that rather than invent a better answer: guessing a shelf where the
# vendor writes a peak would make our curve differ from what the preset's author
# actually heard when they published it.
#
# The field is optional and its absence is the COMMON case, not an edge case: in a
# 40-preset sample of the DAWN PRO2 library, 128 of 320 bands carried no filterType
# at all (the rest: 188 PEAKING, 2 LOW_SHELF_2, 2 HIGH_SHELF_2). The bundle also
# defines 1st-order names (LOW_SHELF_1, HIGH_PASS_1, ...) which are absent from Ta
# and would therefore coerce to peaking — but none appeared in that sample, so
# whether any published preset uses one is UNVERIFIED.
HUB_FILTER_TYPES = {
    "DISABLED": "disabled",
    "LOW_SHELF_2": "low_shelf",
    "PEAKING": "peaking",
    "HIGH_SHELF_2": "high_shelf",
    "LOW_PASS_2": "low_pass",
    "HIGH_PASS_2": "high_pass",
}


def _hub_get(url, timeout=20):
    """One unauthenticated GET. Raises urllib errors; callers turn them into JSON."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": HUB_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def hub_fetch_index(product_uuid, refresh=False, timeout=20):
    """The preset index for a product, cached on disk.

    Worth caching hard: the endpoint has no pagination whatsoever. `?productUuid=`
    is the only parameter it honours — page/pageSize/limit/sortBy are not ignored
    but actively return zero rows — so the smallest possible request is the whole
    index for that device (~3.6 MB for a DAWN PRO2; 31 MB unfiltered). Re-fetching
    that per keystroke would be abusive, hence a day-long TTL and a local search.

    The server also pools by sharedConfigGroupId, so this returns every preset for
    the device's whole family, not just the exact model.

    The timeout is per-socket-operation, not for the whole transfer: it fires after
    `timeout` seconds of SILENCE, so a slow link still completes the ~4 MB while a
    black-holed route gives up promptly. 60s here meant a minute of frozen GUI.
    """
    os.makedirs(HUB_CACHE, exist_ok=True)
    path = os.path.join(HUB_CACHE, f"presets-{product_uuid}.json")
    if not refresh and os.path.exists(path):
        if (time.time() - os.path.getmtime(path)) < HUB_CACHE_TTL:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f), True
            except (json.JSONDecodeError, OSError):
                pass    # corrupt cache is not an error, just a refetch
    body = _hub_get(f"{HUB_API}/peq-configs/all?productUuid={product_uuid}", timeout)
    data = json.loads(body)
    rows = data.get("data") or []
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    os.replace(tmp, path)       # atomic: a killed fetch never leaves a half cache
    return rows, False


def hub_slim(row):
    """Only the fields a browser needs; the raw rows carry duplicate casing variants."""
    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def flat(s):
        # Descriptions are free text and authors format them: 12 of 40 sampled presets
        # embed newlines, one with 24 of them (a hand-drawn frequency table). A caller
        # rendering these as one-liners gets 25 lines drawn over whatever is below, so
        # collapse every run of whitespace to a single space here rather than making
        # each front-end rediscover it. str.split() with no args splits on any
        # whitespace and drops empties, so this also strips.
        return " ".join((s or "").split())

    return {
        "uuid": row.get("uuid", ""),
        "title": flat(row.get("title")),
        "author": flat(row.get("username")),
        "desc": flat(row.get("desc")),
        "downloads": num(row.get("downloadcount")),
        "likes": num(row.get("like")),
        "file": row.get("file") or "",
    }


def hub_resolve_file(preset):
    """preset uuid (or a raw file ref) -> the CDN path holding its curve.

    Tries the local caches first so that clicking a preset you are already looking at
    costs one small CDN fetch and no API call at all.
    """
    if "/" in preset:
        return preset               # already a file ref
    try:
        for fn in os.listdir(HUB_CACHE):
            if not fn.startswith("presets-"):
                continue
            with open(os.path.join(HUB_CACHE, fn), encoding="utf-8") as f:
                for r in json.load(f):
                    if r.get("uuid") == preset:
                        return r.get("file")
    except (OSError, json.JSONDecodeError):
        pass                        # no cache, or a corrupt one — ask the API
    try:
        meta = json.loads(_hub_get(f"{HUB_API}/peq-configs/{preset}"))
        return (meta.get("data") or {}).get("file")
    except Exception:
        return None


def hub_preset_bands(file_ref, band_count=None, timeout=20):
    """Fetch one preset's curve and map it onto our band model.

    The published file is a plain JSON array:
      [{"id":"0","frequency":"105","gain":"2.6","q":"0.7","filterType":"LOW_SHELF_2"}]
    Every field is a *string*, and filterType is optional.
    """
    band_count = band_count or DEFAULT_BANDS    # defined below this block
    raw = json.loads(_hub_get(f"{HUB_CDN}/{file_ref}", timeout))
    bands, dropped = [], 0
    for i, b in enumerate(raw):
        if i >= band_count:
            dropped += 1
            continue
        ft = (b.get("filterType") or "").upper()
        bands.append({
            "index": i,
            "type": HUB_FILTER_TYPES.get(ft, "peaking"),   # see HUB_FILTER_TYPES
            "frequency": float(b.get("frequency") or 1000),
            "gain": float(b.get("gain") or 0),
            "q": float(b.get("q") or 0.7),
            # so a caller can tell the user we reinterpreted their curve
            "coerced": bool(ft) and ft not in HUB_FILTER_TYPES,
        })
    return bands, dropped

# Constants
REPORT_ID = 75  # 0x4B
FS = 96000      # DSP internal sample rate is hardcoded to 96kHz
RESPONSE_TIMEOUT_S = 1.0  # how long to wait for a reply whose echo matches the request

# Every device in SUPPORTED_DEVICES reports peqCount 8 in the app's registry.
# DEVICE_BANDS stays as the override point if a future device differs.
DEVICE_BANDS = {}
DEFAULT_BANDS = 8

# The custom-PEQ profile slot, written into the last byte of an EQ update. Nearly
# every device uses 7; E.S. combo uses 4 (peqIndex in the app's device registry).
#
# Note this is NOT comparable to the active EQ profile that SUB_ACTIVE_EQ reports.
# The app assumes it is (isInPEQMode: readEQIndex() === peqIndex), but a DAWN PRO2
# on firmware 1.5 reports active profile 9 in both its EQ-off and custom-EQ modes,
# while band writes carrying peqIndex 7 are plainly audible in custom-EQ mode. The
# EQ on/off toggle (both volume buttons) is not reflected in any readable register
# we could find, so do not gate writes on the active profile.
DEVICE_PEQ_INDEX = {0x98D5: 4}
DEFAULT_PEQ_INDEX = 7

# Devices whose registry entry sets supportPreGain=false. Writing pre-gain to them
# is not something the official app ever does.
NO_PREGAIN_DEVICES = {0x011B, 0x43DA, 0x98D3}

def band_count(product_id):
    return DEVICE_BANDS.get(product_id, DEFAULT_BANDS)

def peq_profile_index(product_id):
    return DEVICE_PEQ_INDEX.get(product_id, DEFAULT_PEQ_INDEX)

class ShelfSlopeError(ValueError):
    """
    Shelf Q too steep for the requested gain: no such filter exists.
    Carries the ceiling so callers can say what would work.
    """
    def __init__(self, gain, Q, limit):
        self.gain, self.Q, self.limit = gain, Q, limit
        super().__init__(
            f"shelf Q={Q:g} is too steep for a {gain:+.1f} dB shelf (max about Q={limit:.2f}). "
            f"Lower Q or reduce the gain."
        )


def max_shelf_q(gain):
    """
    RBJ's shelf formulas read Q as the shelf slope S, and their shared term
    sqrt((A + 1/A)(1/S - 1) + 2) has no real solution once the slope is too steep
    for the gain -- the radicand goes negative. The ceiling tightens as gain grows:
    about Q=17.6 at 6 dB, Q=5.03 at 12 dB. Returns the largest Q that still solves.
    """
    a = 10 ** (abs(gain) / 40.0)
    s = a + 1.0 / a
    if s <= 2.0:              # unity gain -- every Q solves
        return float('inf')
    return 1.0 / (1.0 - 2.0 / s)


def calculate_biquad(f, gain, Q, filter_type):
    """
    Standard Bristow-Johnson biquad formulas.
    Swapped numerator/denominator as in Moondrop's implementation.
    """
    if filter_type not in FILTER_TYPES or filter_type == "disabled":
        return [0.0]*3, [1.0, 0.0, 0.0]

    # Refuse an impossible shelf up front. Left to itself the sqrt below raises a
    # bare "math domain error", which says nothing about which knob to turn.
    if filter_type in ("low_shelf", "high_shelf"):
        limit = max_shelf_q(gain)
        if Q > limit:
            raise ShelfSlopeError(gain, Q, limit)

    # Convert parameters
    w0 = f * math.pi * 2 / FS
    cos_w0 = math.cos(w0)

    if filter_type == "peaking":
        A = math.sqrt(10 ** (gain / 20))
        alpha = math.sin(w0) / (2 * Q)
        a0 = alpha / A + 1
        num = [1.0, cos_w0 * -2.0 / a0, (1.0 - alpha / A) / a0]
        den = [(alpha * A + 1.0) / a0, cos_w0 * -2.0 / a0, (1.0 - alpha * A) / a0]
        
    elif filter_type == "low_shelf":
        a = 10 ** (gain / 40)
        alpha = math.sin(w0) / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / Q - 1.0) + 2.0)
        a0 = a + 1.0 + (a - 1.0) * cos_w0 + 2.0 * math.sqrt(a) * alpha
        num = [1.0, -2.0 * (a - 1.0 + (a + 1.0) * cos_w0) / a0, (a + 1.0 + (a - 1.0) * cos_w0 - 2.0 * math.sqrt(a) * alpha) / a0]
        den = [a * (a + 1.0 - (a - 1.0) * cos_w0 + 2.0 * math.sqrt(a) * alpha) / a0,
               2.0 * a * (a - 1.0 - (a + 1.0) * cos_w0) / a0,
               a * (a + 1.0 - (a - 1.0) * cos_w0 - 2.0 * math.sqrt(a) * alpha) / a0]
               
    elif filter_type == "high_shelf":
        a = 10 ** (gain / 40)
        alpha = math.sin(w0) / 2.0 * math.sqrt((a + 1.0 / a) * (1.0 / Q - 1.0) + 2.0)
        a0 = a + 1.0 - (a - 1.0) * cos_w0 + 2.0 * math.sqrt(a) * alpha
        num = [1.0, 2.0 * (a - 1.0 - (a + 1.0) * cos_w0) / a0, (a + 1.0 - (a - 1.0) * cos_w0 - 2.0 * math.sqrt(a) * alpha) / a0]
        den = [a * (a + 1.0 + (a - 1.0) * cos_w0 + 2.0 * math.sqrt(a) * alpha) / a0,
               -2.0 * a * (a - 1.0 + (a + 1.0) * cos_w0) / a0,
               a * (a + 1.0 + (a - 1.0) * cos_w0 - 2.0 * math.sqrt(a) * alpha) / a0]
               
    elif filter_type == "low_pass":
        alpha = math.sin(w0) / (2.0 * Q)
        a0 = alpha + 1.0
        num = [1.0, cos_w0 * -2.0 / a0, (1.0 - alpha) / a0]
        den = [(1.0 - cos_w0) / 2.0 / a0, (1.0 - cos_w0) / a0, (1.0 - cos_w0) / 2.0 / a0]
        
    elif filter_type == "high_pass":
        alpha = math.sin(w0) / (2.0 * Q)
        a0 = alpha + 1.0
        num = [1.0, cos_w0 * -2.0 / a0, (1.0 - alpha) / a0]
        den = [(1.0 + cos_w0) / 2.0 / a0, (-1.0 - cos_w0) / a0, (1.0 + cos_w0) / 2.0 / a0]
        
    return num, den

Q30_SCALE = 1073741824  # 2^30
INT32_MIN = -2147483648
INT32_MAX = 2147483647
COEFF_NAMES = ("b0", "b1", "b2", "-a1", "-a2")


class CoefficientOverflowError(ValueError):
    """A biquad coefficient does not fit the firmware's Q2.30 range of [-2, 2)."""

    def __init__(self, name, value):
        self.coeff_name = name
        self.value = value
        super().__init__(f"coefficient {name}={value:.3f} exceeds the Q2.30 range of +/-2.0")


def pack_coefficients(num, den):
    """
    Convert floating point coefficients into Q2.30 format signed 32-bit integers,
    packed in layout [b0, b1, b2, -a1, -a2] (which maps to [den[0], den[1], den[2], -num[1], -num[2]])

    Q2.30 only spans [-2, 2). The feedback terms always fit -- -a1 approaches 2.0
    from below as the corner frequency drops (1.9991 at 10 Hz) but never crosses --
    so overflow is always a numerator problem, in one of two ways:

      b1 < -2 : high_shelf, either from gain above roughly +5 dB, or from a corner
                frequency below roughly 200 Hz at any gain (b1 = -2.0004 at 100 Hz).
      b0 >  2 : any type at high gain + low Q + high frequency, e.g. peaking
                20 kHz +12 dB Q=0.3 (b0 = 2.331) or low_shelf 20 kHz +18 dB Q=0.3.

    The official web app packs via JS bitwise ops, which wrap modulo 2^32 rather
    than failing, so past those points it silently programs a filter unrelated to
    the one it draws. We refuse instead of guessing what the firmware does with a
    wrapped value.
    """
    coeff_floats = [den[0], den[1], den[2], -num[1], -num[2]]
    coeff_ints = [round(c * Q30_SCALE) for c in coeff_floats]

    for name, as_float, as_int in zip(COEFF_NAMES, coeff_floats, coeff_ints):
        if not INT32_MIN <= as_int <= INT32_MAX:
            raise CoefficientOverflowError(name, as_float)

    return struct.pack('<5i', *coeff_ints)


def _packs_ok(freq, gain, Q, filter_type):
    # ShelfSlopeError counts as "doesn't fit" too: bisecting gain at a fixed Q walks
    # into slopes that have no solution, and those gains are just as unusable.
    try:
        pack_coefficients(*calculate_biquad(freq, gain, Q, filter_type))
        return True
    except (CoefficientOverflowError, ShelfSlopeError):
        return False


def max_safe_gain(freq, Q, filter_type, sign, limit=18.0):
    """
    Largest gain magnitude (in the given sign direction) whose coefficients still
    fit Q2.30, found by bisection. Returns None if even a tiny gain overflows.
    """
    if _packs_ok(freq, sign * limit, Q, filter_type):
        return sign * limit
    lo, hi = 0.0, limit
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if _packs_ok(freq, sign * mid, Q, filter_type):
            lo = mid
        else:
            hi = mid
    return sign * lo if lo > 0.05 else None

def find_devices():
    devices = []
    for d in hid.enumerate():
        if d['vendor_id'] == MOONDROP_VID and d['product_id'] in SUPPORTED_DEVICES:
            devices.append(d)
    return devices

def find_unsupported_devices():
    """Moondrop devices we recognise but cannot drive, so we can say so explicitly."""
    return [d for d in hid.enumerate()
            if d['vendor_id'] == MOONDROP_VID and d['product_id'] in UNSUPPORTED_DEVICES]

class MoondropDevice:
    def __init__(self, hid_info):
        self.hid_info = hid_info
        self.device = hid.device()
        self.device.open_path(hid_info['path'])
        self.report_id = REPORT_ID
        self.product_id = hid_info['product_id']
        self.bands = band_count(self.product_id)
        self.peq_index = peq_profile_index(self.product_id)
        self.supports_pregain = self.product_id not in NO_PREGAIN_DEVICES
        # A previous process may have left unread reports queued; clear them so the
        # first read of this session cannot pick up someone else's answer.
        self.drain()

    def close(self):
        self.device.close()

    def send_command(self, cmd_data, wait_response=True):
        # Pad to 63 bytes (report_id will make it 64)
        packet = bytearray(63)
        packet[0:len(cmd_data)] = cmd_data

        # Write report: [report_id] + 63 bytes data
        self.device.write([self.report_id] + list(packet))

        if not wait_response:
            return None

        # A response echoes the command and sub-command it answers, at bytes 1 and
        # 2. Commands that never reply would otherwise leave the next read picking
        # up the PREVIOUS command's report, shifting every subsequent read by one
        # and silently returning another register's data. So keep reading until the
        # echo matches what we asked for, discarding anything stale.
        want_cmd, want_sub = packet[0], packet[1]
        deadline = time.monotonic() + RESPONSE_TIMEOUT_S
        while time.monotonic() < deadline:
            res = self.device.read(64, 200)
            if not res:
                continue
            if len(res) > 2 and res[1] == want_cmd and res[2] == want_sub:
                return res
            # Stale report from an earlier command: drop it and keep looking.
        return None

    def drain(self, max_reports=64):
        """
        Discard any reports still queued, so a later read cannot pick them up.
        Bounded: a device that streams reports must not be able to hang us here.
        """
        for _ in range(max_reports):
            if not self.device.read(64, 1):
                return

    def get_firmware_version(self):
        res = self.send_command([CMD_READ, SUB_FIRMWARE_VERSION, 0])
        if not res:
            return "Unknown"
        payload = bytes(res[4:])
        version = payload.split(b'\x00')[0].decode('utf-8', errors='ignore')
        return version

    def get_active_eq_index(self):
        res = self.send_command([CMD_READ, SUB_ACTIVE_EQ, 0])
        if not res:
            return None
        return res[4]

    def set_active_eq_index(self, index, save=True):
        self.send_command([CMD_WRITE, SUB_ACTIVE_EQ, 0, index])
        if save:
            self.save_eq_to_flash()

    def get_pregain(self):
        res = self.send_command([CMD_READ, SUB_PRE_GAIN, 0])
        if not res:
            return 0.0
        val = struct.unpack('<h', bytes(res[4:6]))[0]
        return val / 256.0

    def set_pregain(self, db, save=True):
        val = round(db * 256)
        val_bytes = struct.pack('<h', val)
        self.send_command([CMD_WRITE, SUB_PRE_GAIN, 0, val_bytes[0], val_bytes[1]])
        if save:
            self.save_offset_to_flash()

    def get_global_gain(self):
        res = self.send_command([CMD_READ, SUB_DAC_OFFSET, 0])
        if not res:
            return 0.0
        val = struct.unpack('<h', bytes(res[4:6]))[0]
        return val / 256.0

    def set_global_gain(self, db, save=True):
        val = round(db * 256)
        val_bytes = struct.pack('<h', val)
        self.send_command([CMD_WRITE, SUB_DAC_OFFSET, 0, val_bytes[0], val_bytes[1]])
        if save:
            self.save_offset_to_flash()

    def save_eq_to_flash(self):
        self.send_command([CMD_WRITE, SUB_SAVE_EQ_TO_FLASH, 0])

    def save_offset_to_flash(self):
        self.send_command([CMD_WRITE, SUB_SAVE_OFFSET_TO_FLASH, 0])

    def read_peq_index(self, index):
        res = self.send_command([CMD_READ, SUB_UPDATE_EQ, 0, 0, index])
        if not res:
            return None
        freq = struct.unpack('<h', bytes(res[28:30]))[0]
        q = struct.unpack('<h', bytes(res[30:32]))[0] / 256.0
        gain = struct.unpack('<h', bytes(res[32:34]))[0] / 256.0
        t_id = res[34]
        t_name = REV_FILTER_TYPES.get(t_id, "unknown")
        
        return {
            "index": index,
            "frequency": freq,
            "q": q,
            "gain": gain,
            "type": t_name
        }

    def write_peq_index(self, index, filter_type, freq, gain, Q):
        if filter_type not in FILTER_TYPES:
            raise ValueError(
                f"Invalid filter type '{filter_type}'. Choose from: {', '.join(FILTER_TYPES)}"
            )
        if not 0 <= index < self.bands:
            raise ValueError(
                f"PEQ slot {index} out of range; this device has {self.bands} bands (0-{self.bands - 1})"
            )

        num, den = calculate_biquad(freq, gain, Q, filter_type)
        try:
            coeff_bytes = pack_coefficients(num, den)
        except CoefficientOverflowError as e:
            ceiling = max_safe_gain(freq, Q, filter_type, 1.0 if gain >= 0 else -1.0)
            if ceiling is not None:
                hint = f" Max safe gain for {filter_type} at {freq:g} Hz / Q={Q:g} is about {ceiling:+.1f} dB."
            else:
                hint = (f" No gain is representable for {filter_type} at {freq:g} Hz / Q={Q:g}; "
                        f"move the corner frequency or change Q.")
            raise ValueError(
                f"{filter_type} @ {gain:+.1f} dB overflows the firmware's Q2.30 coefficient range "
                f"({e.coeff_name}={e.value:.3f}, limit +/-2.0).{hint} "
                f"Refusing to write: the official app wraps here and programs a filter that does not "
                f"match the curve it draws."
            ) from None
        
        cmd = bytearray(63)
        cmd[0] = CMD_WRITE
        cmd[1] = SUB_UPDATE_EQ
        cmd[2] = 0
        cmd[3] = 0
        cmd[4] = index
        cmd[5] = 0
        cmd[6] = 0
        cmd[7:27] = coeff_bytes
        
        cmd[27:29] = struct.pack('<h', int(freq))
        cmd[29:31] = struct.pack('<h', round(Q * 256))
        cmd[31:33] = struct.pack('<h', round(gain * 256))
        cmd[33] = FILTER_TYPES[filter_type]
        cmd[34] = 0
        cmd[35] = self.peq_index  # custom-PEQ profile slot; 7 everywhere except E.S. combo (4)
        
        self.send_command(cmd, wait_response=False)
        
        enable_cmd = [CMD_WRITE, SUB_UPDATE_EQ_COEFF_TO_REG, index, 0, 255, 255, 255]
        self.send_command(enable_cmd, wait_response=False)


def run_interactive(dev, name):
    import os
    
    def clear():
        os.system('cls' if os.name == 'nt' else 'clear')
        
    while True:
        clear()
        print("="*65)
        print("         MOONDROP AUDIO DEVICE REAL-TIME TUNING PANEL")
        print("="*65)
        print(f"  Device Name:      {name}")
        try:
            fw = dev.get_firmware_version()
        except Exception:
            fw = "Unknown"
        try:
            profile = dev.get_active_eq_index()
        except Exception:
            profile = "Unknown"
        try:
            pregain = dev.get_pregain()
        except Exception:
            pregain = 0.0
        try:
            global_gain = dev.get_global_gain()
        except Exception:
            global_gain = 0.0
            
        print(f"  Firmware Version: {fw}")
        print(f"  Active Profile:   Profile {profile}")
        print(f"  Pre-Gain (Preamp):{pregain:+.2f} dB")
        print(f"  Global Offset:    {global_gain:+.2f} dB")
        print("-"*65)
        print(f"  {'Slot':<5} | {'Filter Type':<15} | {'Freq (Hz)':<10} | {'Gain (dB)':<10} | {'Q-Factor':<8}")
        print("-"*65)
        
        for idx in range(dev.bands):
            try:
                peq = dev.read_peq_index(idx)
                if peq:
                    print(f"  {peq['index']:<5} | {peq['type']:<15} | {peq['frequency']:<10} | {peq['gain']:+10.2f} | {peq['q']:.3f}")
            except Exception:
                print(f"  {idx:<5} | {'[Error Reading]':<15} | {'-':<10} | {'-':<10} | {'-':<8}")
                
        print("="*65)
        print("  [p] Edit Pre-Gain       [g] Edit Global Offset")
        print("  [e] Select EQ Profile   [f] Edit PEQ Filter Band")
        print("  [b] Backup to JSON      [r] Restore from JSON")
        print("  [q] Quit Tuning Panel")
        print("="*65)
        
        try:
            choice = input("Select option > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break
            
        if choice == 'q':
            break
        elif choice == 'p':
            try:
                val = float(input("Enter Pre-Gain in dB (e.g. -3.5): "))
                print("Programming Pre-Gain...")
                dev.set_pregain(val)
                print("Saved to flash.")
                input("\nPress Enter to return to menu...")
            except Exception as e:
                print(f"\nError: {e}")
                input("Press Enter to return to menu...")
        elif choice == 'g':
            try:
                val = float(input("Enter Global Offset in dB (e.g. 0.0): "))
                print("Programming Global Offset...")
                dev.set_global_gain(val)
                print("Saved to flash.")
                input("\nPress Enter to return to menu...")
            except Exception as e:
                print(f"\nError: {e}")
                input("Press Enter to return to menu...")
        elif choice == 'e':
            try:
                val = int(input("Enter Profile Index (e.g. 0-7, custom PEQ is usually 7): "))
                print("Changing profile index...")
                dev.set_active_eq_index(val)
                print("Profile selected.")
                input("\nPress Enter to return to menu...")
            except Exception as e:
                print(f"\nError: {e}")
                input("Press Enter to return to menu...")
        elif choice == 'f':
            try:
                slot = int(input(f"Select PEQ Slot (0-{dev.bands - 1}): "))
                print("\nTypes: disabled, peaking, low_shelf, high_shelf, low_pass, high_pass")
                f_type = input("Filter Type [peaking]: ").strip().lower()
                if not f_type:
                    f_type = "peaking"
                if f_type not in FILTER_TYPES:
                    print(f"Invalid type '{f_type}'!")
                    input("Press Enter to return to menu...")
                    continue
                freq = float(input("Frequency (Hz): "))
                gain = float(input("Gain (dB): "))
                q = float(input("Q-Factor: "))
                
                print(f"Programming PEQ slot {slot}...")
                dev.write_peq_index(slot, f_type, freq, gain, q)
                dev.save_eq_to_flash()
                print("PEQ slot configured and saved.")
                input("\nPress Enter to return to menu...")
            except Exception as e:
                print(f"\nError: {e}")
                input("Press Enter to return to menu...")
        elif choice == 'b':
            try:
                path = input("Enter backup filename (e.g., profile.json): ").strip()
                if path:
                    import json
                    print("Reading current configurations...")
                    data = {
                        "device_name": name,
                        "pregain": dev.get_pregain(),
                        "global_gain": dev.get_global_gain(),
                        "active_eq_profile": dev.get_active_eq_index(),
                        "filters": []
                    }
                    for idx in range(dev.bands):
                        peq = dev.read_peq_index(idx)
                        if peq:
                            data["filters"].append({
                                "index": peq["index"],
                                "type": peq["type"],
                                "frequency": peq["frequency"],
                                "gain": peq["gain"],
                                "q": peq["q"]
                            })
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=4)
                    print(f"Backup exported to {path}.")
                input("\nPress Enter to return to menu...")
            except Exception as e:
                print(f"\nError: {e}")
                input("Press Enter to return to menu...")
        elif choice == 'r':
            try:
                path = input("Enter JSON backup filename to restore: ").strip()
                if path:
                    import json
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    print("Applying configuration...")
                    if "pregain" in data:
                        dev.set_pregain(data["pregain"], save=False)
                    if "global_gain" in data:
                        dev.set_global_gain(data["global_gain"], save=False)
                    if "filters" in data:
                        for f in data["filters"]:
                            dev.write_peq_index(f["index"], f["type"], f["frequency"], f["gain"], f["q"])
                    if "active_eq_profile" in data:
                        dev.set_active_eq_index(data["active_eq_profile"], save=False)
                        
                    dev.save_eq_to_flash()
                    dev.save_offset_to_flash()
                    print("Backup restored and saved to flash.")
                input("\nPress Enter to return to menu...")
            except Exception as e:
                print(f"\nError: {e}")
                input("Press Enter to return to menu...")


def check_stream_status():
    import os
    import glob
    import re
    
    asound_path = "/proc/asound"
    if not os.path.exists(asound_path):
        print("Error: ALSA /proc/asound directory not found. This feature is Linux-only.")
        return
        
    try:
        with open(os.path.join(asound_path, "cards"), 'r', encoding='utf-8') as f:
            cards_content = f.read()
    except Exception as e:
        print(f"Error reading ALSA cards: {e}")
        return
        
    branded = re.findall(r'(\d+)\s+\[([^\]]+)\]:\s*USB-Audio\s*-\s*.*?(?:MOONDROP|DAWN|FreeDSP|Rays|Moonriver)', cards_content, re.IGNORECASE)

    if branded:
        moondrop_cards = branded
    else:
        # Fallback to any USB-Audio card
        moondrop_cards = re.findall(r'(\d+)\s+\[([^\]]+)\]:\s*USB-Audio', cards_content, re.IGNORECASE)
        if not moondrop_cards:
            print("No USB Audio cards detected in /proc/asound/cards.")
            return

    print("Detected USB Audio hardware stream interfaces:")
    for card_idx, card_id in moondrop_cards:
        card_dir = os.path.join(asound_path, f"card{card_idx}")
        stream_files = glob.glob(os.path.join(card_dir, "stream*"))
        
        for sf in stream_files:
            try:
                with open(sf, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                print("="*65)
                print(f" Card {card_idx} [{card_id}] Stream: {os.path.basename(sf)}")
                print("="*65)
                
                # Parse Status
                status_match = re.search(r'Status:\s*(\w+)', content)
                status = status_match.group(1) if status_match else "Unknown"
                print(f"  Playback Status:   {status.upper()}")
                
                if status.lower() == "running":
                    # Stay on the one line: a permissive class here swallows the
                    # following "Feedback Format" line into the rate.
                    freq_match = re.search(r'Momentary freq\s*=\s*([^\r\n]+)', content)
                    freq = freq_match.group(1).strip() if freq_match else "Unknown"
                    
                    format_match = re.search(r'Format:\s*([\w_]+)', content)
                    fmt = format_match.group(1) if format_match else "Unknown"
                    
                    channels_match = re.search(r'Channels:\s*(\d+)', content)
                    channels = channels_match.group(1) if channels_match else "Unknown"
                    
                    print(f"  Hardware Rate:     {freq}")
                    print(f"  Bit Format:        {fmt}")
                    print(f"  Active Channels:   {channels}")
                else:
                    print("  No audio stream is currently playing.")
                    
                print("-"*65)
                print("  Supported Formats & Sample Rates on this interface:")
                # Each altset block starts at "Format:" and carries its own Channels/
                # Rates further down, separated by Endpoint/Bits lines -- so split on
                # the Format: boundaries rather than expecting the three to be adjacent.
                for blk in re.split(r'(?=\bFormat:)', content)[1:]:
                    f_fmt = re.search(r'Format:\s*([\w_]+)', blk)
                    f_ch = re.search(r'Channels:\s*(\d+)', blk)
                    f_rates = re.search(r'Rates:\s*([\d,\s]+)', blk)
                    if not (f_fmt and f_ch and f_rates):
                        continue
                    bits = re.search(r'Bits:\s*(\d+)', blk)
                    bit_str = f", {bits.group(1)}-bit" if bits else ""
                    rates = " ".join(f_rates.group(1).split())
                    print(f"    - {f_fmt.group(1)} ({f_ch.group(1)} ch{bit_str}): {rates} Hz")
                print("="*65)
            except Exception as e:
                print(f"Error reading stream file {sf}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Moondrop DAC HID Control Tool")
    parser.add_argument("--list", action="store_true", help="List connected Moondrop devices")
    parser.add_argument("--info", action="store_true", help="Print information about connected device")
    parser.add_argument("-i", "--interactive", action="store_true", help="Start the interactive terminal dashboard tuning panel")
    parser.add_argument("--get-peq", action="store_true", help="Read all PEQ slots")
    parser.add_argument("--set-pregain", type=float, help="Set Pre-Gain (in dB)")
    parser.add_argument("--set-globalgain", type=float, help="Set Global/DAC Gain offset (in dB)")
    parser.add_argument("--set-eq-index", type=int, help="Select active EQ preset profile index")
    parser.add_argument("--set-peq", nargs=5, metavar=('INDEX', 'TYPE', 'FREQ', 'GAIN', 'Q'), 
                        help="Configure a PEQ slot (e.g., 0 peaking 1000 -3.0 1.0)")
    parser.add_argument("--export-json", metavar="FILE.json", help="Export/Backup DAC configuration to a JSON file")
    parser.add_argument("--import-json", metavar="FILE.json", help="Import/Restore DAC configuration from a JSON file")
    parser.add_argument("--import-rew", metavar="FILE.txt", help="Import EQ configurations from a REW text file")
    parser.add_argument("--stream-status", action="store_true", help="Diagnose current hardware-level audio stream status (sample rate/format)")
    parser.add_argument("--json", action="store_true", help="Dump full device state (info + all PEQ bands) as JSON on stdout, for GUIs")
    parser.add_argument("--registry", action="store_true",
                        help="Dump the device registry (vendor ID + supported/unsupported product IDs) "
                             "as JSON. Touches no hardware, so a GUI can identify a DAC from its USB "
                             "IDs without opening the device")
    parser.add_argument("--presets", action="store_true",
                        help="List community PEQ presets for a device from the Moondrop Hub library "
                             "as JSON. Needs --pid (or a connected device) and the network; touches "
                             "no hardware when --pid is given")
    parser.add_argument("--preset", metavar="UUID",
                        help="Fetch one community preset by uuid and print its bands as JSON, mapped "
                             "onto this tool's band model. Touches no hardware")
    parser.add_argument("--pid", metavar="HEX",
                        help="Product ID (e.g. 011d) to use for --presets instead of probing the bus")
    parser.add_argument("--search", metavar="TERM",
                        help="With --presets: keep only presets whose title, author or description "
                             "contains TERM (case-insensitive)")
    parser.add_argument("--limit", type=int, default=200,
                        help="With --presets: cap results (default 200, 0 = no cap)")
    parser.add_argument("--refresh", action="store_true",
                        help="With --presets: bypass the local cache and refetch the index")
    parser.add_argument("--no-flash", action="store_true", help="Apply live to the DSP but skip the flash write (for GUI live-preview; use --save-flash to persist)")
    parser.add_argument("--save-flash", action="store_true", help="Persist the current EQ + offsets to device flash")
    parser.add_argument("--gui", action="store_true", help="Launch the desktop EQ GUI (needs PySide6)")

    args = parser.parse_args()

    # Desktop GUI. Lazy-imported so the CLI keeps its "two dependencies, one file"
    # promise -- PySide6 is only pulled in when someone actually asks for --gui.
    if args.gui:
        # let it run from any cwd: the gui package sits next to this file
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        try:
            from gui.app import main as gui_main
        except ImportError as e:
            print("The GUI needs PySide6:  pip install -r gui/requirements.txt\n(%s)" % e, file=sys.stderr)
            sys.exit(1)
        sys.exit(gui_main())

    # Registry dump. Deliberately the FIRST thing handled and deliberately
    # hardware-free: this exists so a front-end can recognise a DAC from the USB
    # IDs it already has (PipeWire hands them over in alsa.components as
    # "USB<vid>:<pid>") WITHOUT opening the device. That matters -- two processes
    # must never hold the hidraw at once, and a passive indicator has no business
    # taking the device away from whatever is actually driving it. It also keeps
    # this file the single source of truth for the registry: nothing downstream
    # needs to hardcode a product ID.
    if args.registry:
        print(json.dumps({
            "vendor_id": f"{MOONDROP_VID:04x}",
            "supported": {f"{pid:04x}": name for pid, name in sorted(SUPPORTED_DEVICES.items())},
            "unsupported": {f"{pid:04x}": {"name": name, "reason": reason}
                            for pid, (name, reason) in sorted(UNSUPPORTED_DEVICES.items())},
            # which devices have a community preset library, and under which uuid
            "product_uuids": {f"{pid:04x}": u for pid, u in sorted(PRODUCT_UUIDS.items())},
        }, indent=2))
        sys.exit(0)

    # ---- community presets ----
    # Network only. Neither of these opens the DAC, which matters: the GUI serialises
    # every device call through one queue because two processes sharing one hidraw pick
    # up each other's replies, and browsing a library has no business joining that queue.
    if args.presets or args.preset:
        def die(msg):
            print(json.dumps({"ok": False, "error": msg}))
            sys.exit(1)

        if args.preset:
            ref = hub_resolve_file(args.preset)
            if not ref:
                die(f"could not resolve preset {args.preset}")
            try:
                bands, dropped = hub_preset_bands(ref, DEFAULT_BANDS)
            except Exception as e:
                die(f"could not fetch preset: {e}")
            print(json.dumps({"ok": True, "bands": bands, "dropped": dropped,
                              "coerced": sum(1 for b in bands if b["coerced"])}, indent=2))
            sys.exit(0)

        if args.pid:
            try:
                pid = int(args.pid, 16)
            except ValueError:
                die(f"--pid must be hex, e.g. 011d (got {args.pid!r})")
        else:
            found = find_devices() or find_unsupported_devices()
            if not found:
                die("no Moondrop device connected (pass --pid to browse without one)")
            pid = found[0]["product_id"]
        uuid = PRODUCT_UUIDS.get(pid)
        if not uuid:
            die(f"no community preset library known for product {pid:04x}")
        try:
            rows, cached = hub_fetch_index(uuid, refresh=args.refresh)
        except Exception as e:
            die(f"could not reach the Moondrop Hub library: {e}")
        out = [hub_slim(r) for r in rows]
        if args.search:
            q = args.search.lower()
            out = [p for p in out
                   if q in p["title"].lower() or q in p["author"].lower() or q in p["desc"].lower()]
        out.sort(key=lambda p: (-p["downloads"], -p["likes"]))
        total = len(out)
        if args.limit and args.limit > 0:
            out = out[:args.limit]
        print(json.dumps({"ok": True, "product_uuid": uuid, "cached": cached,
                          "total": total, "shown": len(out), "presets": out}, indent=2))
        sys.exit(0)

    if args.stream_status:
        check_stream_status()
        sys.exit(0)

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    devices = find_devices()
    unsupported = find_unsupported_devices()
    if args.list:
        print("Connected Moondrop Devices:")
        if not devices and not unsupported:
            print("  None found.")
        for d in devices:
            prod_str = d.get('product_string')
            name = prod_str if prod_str else SUPPORTED_DEVICES.get(d['product_id'], "Unknown Moondrop Device")
            path_str = d['path'].decode() if isinstance(d['path'], bytes) else str(d['path'])
            print(f"  - {name} [Vendor ID: 0x{d['vendor_id']:04X}, Product ID: 0x{d['product_id']:04X}] (Path: {path_str})")
        for d in unsupported:
            dev_name, reason = UNSUPPORTED_DEVICES[d['product_id']]
            print(f"  - {dev_name} [Product ID: 0x{d['product_id']:04X}] -- NOT SUPPORTED: {reason}")
        sys.exit(0)

    if not devices:
        if unsupported:
            dev_name, reason = UNSUPPORTED_DEVICES[unsupported[0]['product_id']]
            msg = f"{dev_name} is connected, but it {reason}."
        else:
            msg = "No connected Moondrop device found."
        if args.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"Error: {msg}")
        sys.exit(1)

    dev_info = devices[0]
    prod_str = dev_info.get('product_string')
    name = prod_str if prod_str else SUPPORTED_DEVICES.get(dev_info['product_id'], "Unknown Moondrop Device")
    if not args.json:
        print(f"Opening device: {name}...")
    try:
        dev = MoondropDevice(dev_info)
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"Failed to open device: {e}")
            print("Tip: You may need root permissions (sudo) or udev rules to access HID devices.")
        sys.exit(1)

    try:
        if args.json:
            # A GUI needs the same three facts the CLI warns about in prose:
            # which slot custom PEQ lives on, whether it is the one playing, and
            # whether pre-gain is worth offering at all.
            # Deliberately no "in_peq_mode": comparing the active profile against
            # peq_index is what the official app does, and it does not hold. A DAWN
            # PRO2 on firmware 1.5 reports active profile 9 in both its EQ-off and
            # custom-EQ modes, yet band writes are plainly audible in custom-EQ mode.
            # Emitting that comparison only teaches a UI to warn about a non-problem.
            data = {
                "ok": True,
                "device_name": name,
                "product_id": dev_info['product_id'],
                "firmware": dev.get_firmware_version(),
                "active_eq_profile": dev.get_active_eq_index(),
                "peq_index": dev.peq_index,
                "supports_pregain": dev.supports_pregain,
                "pregain": dev.get_pregain(),
                "global_gain": dev.get_global_gain(),
                "bands": dev.bands,
                "filters": []
            }
            for idx in range(dev.bands):
                peq = dev.read_peq_index(idx)
                if peq:
                    data["filters"].append(peq)
            print(json.dumps(data))

        elif args.interactive:
            run_interactive(dev, name)

        else:
            # Each flag below is an independent action rather than one arm of an
            # elif chain, so combinations compose instead of silently dropping all
            # but the first. --save-flash is applied last so that
            # `--set-peq ... --no-flash --save-flash` previews then persists.
            if args.info:
                print(f"Device Name:      {name}")
                print(f"Firmware Version: {dev.get_firmware_version()}")
                print(f"Active EQ Profile: {dev.get_active_eq_index()}")
                print(f"Pre-Gain:         {dev.get_pregain():.2f} dB")
                print(f"Global Gain:      {dev.get_global_gain():.2f} dB")
                print(f"PEQ Bands:        {dev.bands}")

            if args.get_peq:
                print("Reading PEQ Configuration (DSP runs at 96 kHz internal sample rate):")
                print(f"{'Slot':<5} | {'Type':<12} | {'Freq (Hz)':<10} | {'Gain (dB)':<10} | {'Q':<5}")
                print("-" * 52)
                for idx in range(dev.bands):
                    peq = dev.read_peq_index(idx)
                    if peq:
                        print(f"{peq['index']:<5} | {peq['type']:<12} | {peq['frequency']:<10} | {peq['gain']:<10.2f} | {peq['q']:.3f}")

            if args.set_pregain is not None:
                if not dev.supports_pregain:
                    print(f"Warning: {name} reports no pre-gain support in the official app's "
                          f"registry; this write may do nothing.")
                print(f"Setting pre-gain to {args.set_pregain:.2f} dB...")
                dev.set_pregain(args.set_pregain, save=not args.no_flash)
                print("Applied." if args.no_flash else "Saved.")

            if args.set_globalgain is not None:
                print(f"Setting global gain to {args.set_globalgain:.2f} dB...")
                dev.set_global_gain(args.set_globalgain, save=not args.no_flash)
                print("Applied." if args.no_flash else "Saved.")

            if args.set_eq_index is not None:
                print(f"Selecting EQ profile index {args.set_eq_index}...")
                dev.set_active_eq_index(args.set_eq_index, save=not args.no_flash)
                print("Applied." if args.no_flash else "Saved.")

            if args.set_peq:
                slot = int(args.set_peq[0])
                filter_type = args.set_peq[1].lower()
                freq = float(args.set_peq[2])
                gain = float(args.set_peq[3])
                q = float(args.set_peq[4])

                print(f"Writing PEQ Slot {slot}: {filter_type} @ {freq}Hz, {gain}dB, Q={q}...")
                dev.write_peq_index(slot, filter_type, freq, gain, q)
                if not args.no_flash:
                    dev.save_eq_to_flash()
                print("Applied (live)." if args.no_flash else "Done and saved to Flash.")

            if args.export_json:
                print(f"Reading device state to export to {args.export_json}...")
                data = {
                    "device_name": name,
                    "pregain": dev.get_pregain(),
                    "global_gain": dev.get_global_gain(),
                    "active_eq_profile": dev.get_active_eq_index(),
                    "filters": []
                }
                for idx in range(dev.bands):
                    peq = dev.read_peq_index(idx)
                    if peq:
                        data["filters"].append({
                            "index": peq["index"],
                            "type": peq["type"],
                            "frequency": peq["frequency"],
                            "gain": peq["gain"],
                            "q": peq["q"]
                        })
                with open(args.export_json, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4)
                print(f"Exported successfully to {args.export_json}")

            if args.import_json:
                print(f"Loading configuration from {args.import_json}...")
                with open(args.import_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Validate every band before writing any, so a bad file fails
                # cleanly instead of leaving the DAC half-configured.
                for f in data.get("filters", []):
                    if f["type"] not in FILTER_TYPES:
                        print(f"Error: filter {f['index']} has unsupported type '{f['type']}'. "
                              f"Choose from: {', '.join(FILTER_TYPES)}")
                        sys.exit(1)
                    if not 0 <= f["index"] < dev.bands:
                        print(f"Error: filter index {f['index']} is out of range; "
                              f"this device has {dev.bands} bands (0-{dev.bands - 1}).")
                        sys.exit(1)

                if "pregain" in data:
                    print(f"Setting pre-gain to {data['pregain']:.2f} dB...")
                    dev.set_pregain(data["pregain"], save=False)

                if "global_gain" in data:
                    print(f"Setting global gain to {data['global_gain']:.2f} dB...")
                    dev.set_global_gain(data["global_gain"], save=False)

                for f in data.get("filters", []):
                    print(f"Writing PEQ Slot {f['index']}: {f['type']} @ {f['frequency']}Hz, "
                          f"{f['gain']}dB, Q={f['q']}...")
                    dev.write_peq_index(f["index"], f["type"], f["frequency"], f["gain"], f["q"])

                if "active_eq_profile" in data:
                    print(f"Selecting EQ profile index {data['active_eq_profile']}...")
                    dev.set_active_eq_index(data["active_eq_profile"], save=False)

                if args.no_flash:
                    print("Import applied (live) -- not written to flash.")
                else:
                    dev.save_eq_to_flash()
                    dev.save_offset_to_flash()
                    print("Import completed and configurations saved to Flash.")

            if args.import_rew:
                import re
                print(f"Parsing REW configuration file: {args.import_rew}...")
                with open(args.import_rew, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                filters = []
                preamp = 0.0
                rew_types = {
                    "PK": "peaking",
                    "LS": "low_shelf",
                    "HS": "high_shelf",
                    "LP": "low_pass",
                    "HP": "high_pass"
                }

                for line in lines:
                    line = line.strip()
                    preamp_match = re.match(r'(?:Preamp|Pre-gain|Pre-amp):\s*([\d\.-]+)\s*dB', line, re.IGNORECASE)
                    if preamp_match:
                        preamp = float(preamp_match.group(1))
                        continue

                    filter_match = re.match(r'Filter\s+(\d+):\s*(ON|OFF)\s+([a-zA-Z0-9_]+)\s+Fc\s+([\d\.]+)\s*Hz\s+Gain\s+([\d\.-]+)\s*dB\s+Q\s+([\d\.]+)', line, re.IGNORECASE)
                    if filter_match:
                        idx = int(filter_match.group(1)) - 1
                        state = filter_match.group(2).upper()
                        t_rew = filter_match.group(3).upper()
                        freq = float(filter_match.group(4))
                        gain = float(filter_match.group(5))
                        q = float(filter_match.group(6))

                        t_mapped = rew_types.get(t_rew, "peaking")
                        if state == "OFF":
                            t_mapped = "disabled"

                        filters.append({
                            "index": idx,
                            "type": t_mapped,
                            "frequency": freq,
                            "gain": gain,
                            "q": q
                        })

                filters = sorted(filters, key=lambda x: x["index"])
                usable = [f for f in filters if 0 <= f["index"] < dev.bands]
                if len(usable) < len(filters):
                    print(f"Note: {len(filters) - len(usable)} filter(s) from the REW file fall outside "
                          f"this device's {dev.bands} bands and will be ignored.")

                if preamp != 0.0:
                    print(f"Setting Pre-Gain to {preamp:.2f} dB...")
                    dev.set_pregain(preamp, save=False)

                for f in usable:
                    print(f"Writing PEQ Slot {f['index']}: {f['type']} @ {f['frequency']}Hz, {f['gain']}dB, Q={f['q']:.3f}...")
                    dev.write_peq_index(f["index"], f["type"], f["frequency"], f["gain"], f["q"])

                configured_indices = {f["index"] for f in usable}
                for idx in range(dev.bands):
                    if idx not in configured_indices:
                        print(f"Disabling unused PEQ Slot {idx}...")
                        dev.write_peq_index(idx, "disabled", 1000.0, 0.0, 1.0)

                if args.no_flash:
                    print("Import applied (live) -- not written to flash.")
                else:
                    dev.save_eq_to_flash()
                    dev.save_offset_to_flash()
                    print("Import completed and configurations saved to Flash.")

            if args.save_flash:
                print("Persisting EQ + offsets to flash...")
                dev.save_eq_to_flash()
                dev.save_offset_to_flash()
                print("Saved to flash.")

    except ValueError as e:
        # Bad filter type / out-of-range slot: report it plainly rather than
        # dumping a traceback at someone tuning their DAC.
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        dev.close()

if __name__ == '__main__':
    main()
