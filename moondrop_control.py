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

# ---------------------------------------------------------------------------
# Universal (software) EQ via PipeWire
#
# The PEQ above is the DAC's own hardware DSP, so it only exists on the devices
# in SUPPORTED_DEVICES. The same filters can be run in software instead, which
# works with ANY output -- another brand's DAC, laptop speakers, Bluetooth --
# at the cost of a little CPU. PipeWire's built-in filter-chain module takes
# biquads with exactly the Freq/Q/Gain parameters we already carry, so the
# translation is direct.
#
# Two things differ from the hardware path, both in software's favour:
#   * No Q2.30 limit. Software biquads are floating point, so the shelf gains
#     the DAC has to refuse are fine here.
#   * Not tied to 96 kHz. PipeWire recomputes per graph rate, so we emit the
#     filter parameters rather than baked coefficients.
# ---------------------------------------------------------------------------

PIPEWIRE_LABELS = {
    "peaking": "bq_peaking",
    "low_shelf": "bq_lowshelf",
    "high_shelf": "bq_highshelf",
    "low_pass": "bq_lowpass",
    "high_pass": "bq_highpass",
}

REW_TYPES = {"PK": "peaking", "LS": "low_shelf", "HS": "high_shelf",
             "LP": "low_pass", "HP": "high_pass"}


def parse_rew(path):
    """Parse a REW / AutoEQ 'ParametricEQ.txt'. Returns (filters, preamp_db)."""
    import re as _re
    with open(path, 'r', encoding='utf-8') as fh:
        lines = fh.readlines()

    filters = []
    preamp = 0.0
    for line in lines:
        line = line.strip()
        pm = _re.match(r'(?:Preamp|Pre-gain|Pre-amp):\s*([\d\.-]+)\s*dB', line, _re.IGNORECASE)
        if pm:
            preamp = float(pm.group(1))
            continue
        fm = _re.match(r'Filter\s+(\d+):\s*(ON|OFF)\s+([a-zA-Z0-9_]+)\s+Fc\s+([\d\.]+)\s*Hz\s+'
                       r'Gain\s+([\d\.-]+)\s*dB\s+Q\s+([\d\.]+)', line, _re.IGNORECASE)
        if fm:
            t = REW_TYPES.get(fm.group(3).upper(), "peaking")
            if fm.group(2).upper() == "OFF":
                t = "disabled"
            filters.append({
                "index": int(fm.group(1)) - 1,
                "type": t,
                "frequency": float(fm.group(4)),
                "gain": float(fm.group(5)),
                "q": float(fm.group(6)),
            })
    return filters, preamp


def build_pipewire_config(filters, preamp=0.0, node_name="eq", description="Universal EQ"):
    """
    Render a libpipewire-module-filter-chain config: a virtual sink that applies
    these bands to whatever it is routed to.

    The graph is mono (one In, one Out); filter-chain instantiates it per channel,
    so it EQs both sides of a stereo stream identically. Nodes are linked
    explicitly in series -- filter-chain does not chain them implicitly.
    """
    nodes, links, prev = [], [], None

    if abs(preamp) > 1e-9:
        # No biquad does plain gain, so scale the samples: dB -> linear multiplier.
        mult = 10 ** (preamp / 20.0)
        nodes.append(f'                {{ type = builtin name = preamp label = linear '
                     f'control = {{ "Mult" = {mult:.6f} "Add" = 0.0 }} }}')
        prev = "preamp"

    for f in filters:
        if f["type"] == "disabled":
            continue
        label = PIPEWIRE_LABELS.get(f["type"])
        if label is None:
            continue
        name = f"eq_band_{f['index']}"
        nodes.append(f'                {{ type = builtin name = {name} label = {label} '
                     f'control = {{ "Freq" = {float(f["frequency"]):g} "Q" = {float(f["q"]):g} '
                     f'"Gain" = {float(f["gain"]):g} }} }}')
        if prev is not None:
            links.append(f'                {{ output = "{prev}:Out" input = "{name}:In" }}')
        prev = name

    if not nodes:
        # An empty graph is invalid; a unity-gain node keeps the sink usable.
        nodes.append('                { type = builtin name = passthrough label = linear '
                     'control = { "Mult" = 1.0 "Add" = 0.0 } }')

    nodes_s = "\n".join(nodes)
    links_s = "\n".join(links)
    links_block = f"            links = [\n{links_s}\n            ]\n" if links else ""

    return (
        "# PipeWire software EQ -- generated by moondrop_control.py\n"
        "#\n"
        "# Install:   cp this file to ~/.config/pipewire/pipewire.conf.d/\n"
        "# Activate:  systemctl --user restart pipewire pipewire-pulse\n"
        f"# Then select the \"{description}\" sink as your output; it feeds your real device.\n"
        "# Remove:    delete the file and restart pipewire again.\n"
        "#\n"
        "# This is software EQ: it applies to ANY output device, not just a Moondrop DAC.\n"
        "context.modules = [\n"
        "{   name = libpipewire-module-filter-chain\n"
        "    args = {\n"
        f"        node.description = \"{description}\"\n"
        f"        media.name       = \"{description}\"\n"
        "        filter.graph = {\n"
        "            nodes = [\n"
        f"{nodes_s}\n"
        "            ]\n"
        f"{links_block}"
        "        }\n"
        "        # Channel config belongs at args level, not inside capture/playback props:\n"
        "        # that is what makes filter-chain replicate the mono graph onto both\n"
        "        # channels. (Matches /usr/share/pipewire/filter-chain/sink-eq6.conf.)\n"
        "        audio.channels = 2\n"
        "        audio.position = [ FL FR ]\n"
        "        capture.props = {\n"
        f"            node.name   = \"effect_input.{node_name}\"\n"
        "            media.class = Audio/Sink\n"
        "        }\n"
        "        playback.props = {\n"
        f"            node.name    = \"effect_output.{node_name}\"\n"
        "            node.passive = true\n"
        "        }\n"
        "    }\n"
        "}\n"
        "]\n"
    )


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
    parser.add_argument("--to-pipewire", metavar="OUT.conf",
                        help="Write a PipeWire filter-chain config: software EQ that works with ANY "
                             "output device, not just a Moondrop DAC")
    parser.add_argument("--from-json", metavar="FILE.json",
                        help="Source bands from an exported JSON instead of a connected device "
                             "(lets --to-pipewire run with no Moondrop DAC attached)")
    parser.add_argument("--from-rew", metavar="FILE.txt",
                        help="Source bands from a REW/AutoEQ file instead of a connected device "
                             "(lets --to-pipewire run with no Moondrop DAC attached)")
    parser.add_argument("--pw-name", metavar="NAME", default="Universal EQ",
                        help="Sink name shown for --to-pipewire (default: 'Universal EQ')")
    parser.add_argument("--json", action="store_true", help="Dump full device state (info + all PEQ bands) as JSON on stdout, for GUIs")
    parser.add_argument("--no-flash", action="store_true", help="Apply live to the DSP but skip the flash write (for GUI live-preview; use --save-flash to persist)")
    parser.add_argument("--save-flash", action="store_true", help="Persist the current EQ + offsets to device flash")
    
    args = parser.parse_args()

    if args.stream_status:
        check_stream_status()
        sys.exit(0)

    # Device-free path: converting a saved config to a software EQ needs no DAC at
    # all, which is the entire point -- this is how someone with an unsupported
    # device gets the same curves.
    if args.to_pipewire and (args.from_json or args.from_rew):
        if args.from_rew:
            filters, preamp = parse_rew(args.from_rew)
            src = args.from_rew
        else:
            import json
            with open(args.from_json, 'r', encoding='utf-8') as fh:
                d = json.load(fh)
            filters = d.get("filters", [])
            preamp = d.get("pregain", 0.0)
            src = args.from_json
        filters = sorted(filters, key=lambda x: x["index"])
        conf = build_pipewire_config(filters, preamp, description=args.pw_name)
        with open(args.to_pipewire, 'w', encoding='utf-8') as fh:
            fh.write(conf)
        active = [f for f in filters if f["type"] != "disabled"]
        print(f"Read {len(active)} active band(s) + {preamp:+.2f} dB pre-gain from {src}")
        print(f"Wrote PipeWire filter-chain config to {args.to_pipewire}")
        print(f"\n  cp {args.to_pipewire} ~/.config/pipewire/pipewire.conf.d/")
        print(f"  systemctl --user restart pipewire pipewire-pulse")
        print(f"\nThen pick the \"{args.pw_name}\" sink as your output. This is software EQ:")
        print("it works with any DAC, headphones, or speakers -- no Moondrop hardware needed.")
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
            import json
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
            import json
            print(json.dumps({"ok": False, "error": str(e)}))
        else:
            print(f"Failed to open device: {e}")
            print("Tip: You may need root permissions (sudo) or udev rules to access HID devices.")
        sys.exit(1)

    try:
        if args.json:
            import json
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

            if args.to_pipewire:
                print(f"Reading device bands to build a software EQ config...")
                filters = []
                for idx in range(dev.bands):
                    peq = dev.read_peq_index(idx)
                    if peq:
                        filters.append(peq)
                pre = dev.get_pregain()
                conf = build_pipewire_config(filters, pre, description=args.pw_name)
                with open(args.to_pipewire, 'w', encoding='utf-8') as fh:
                    fh.write(conf)
                active = [f for f in filters if f["type"] != "disabled"]
                print(f"Wrote {len(active)} band(s) + {pre:+.2f} dB pre-gain to {args.to_pipewire}")
                print(f"  cp {args.to_pipewire} ~/.config/pipewire/pipewire.conf.d/ "
                      f"&& systemctl --user restart pipewire pipewire-pulse")

            if args.export_json:
                import json
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
                import json
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
