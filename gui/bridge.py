"""Bridge — the Python side of the GUI, with no Qt in it.

This replaces ``controller.py``. The shape is deliberately the same, because the
shape was right:

* ``DeviceWorker`` is the only thing that ever touches the hidraw. It runs on one
  thread and takes work from one queue, so every device call is serialised for free
  and a read can never interleave with a write — the one hard constraint
  ``moondrop_control`` documents. That used to come from ``QThread`` + queued
  signals; here it comes from ``queue.Queue``, which gives the same guarantee with
  none of the framework.

* ``HubWorker`` runs on a second thread for the community library, so a slow 4 MB
  fetch can never block a device read. ``IoWorker`` runs on a third for file
  dialogs and JSON, because zenity blocks for as long as the user stares at it.

* ``Bridge`` lives on the UI thread, holds the cached state, and is the only thing
  that writes to the Slint window.

The one piece worth understanding is how results get back. Qt's queued signals are
replaced by ``slint.slint.invoke_from_event_loop``, which runs a callable on the UI
thread at the next turn of the loop. Every worker → UI hop goes through ``_post``.
Touching window properties from a worker thread instead would be a data race that
happens to look fine until it doesn't.

The other is that **the view holds no maths and no policy**. It reports fractions
of the plot ("the pointer is 62% across and 31% down") and this file turns those
into frequencies and gains, clamps them against what the firmware can actually
represent, and hands back finished SVG. A control that cannot be dragged somewhere
is one this file refused, not one the UI forgot to allow.
"""
from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import timedelta

import slint
from slint.slint import invoke_from_event_loop

try:
    import moondrop_control as mc
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

from . import curve as curve_mod
from . import updater
from .diagnostics import log
from .icons import PATHS as ICON_PATHS

# A neutral 8-band example so the window is a usable playground with no DAC —
# the same curve the landing page draws.
DEMO_BANDS = [
    {"index": 0, "type": "low_shelf",  "frequency": 105,   "gain": 2.6,  "q": 0.700},
    {"index": 1, "type": "peaking",    "frequency": 172,   "gain": -3.6, "q": 0.441},
    {"index": 2, "type": "peaking",    "frequency": 770,   "gain": 1.9,  "q": 0.910},
    {"index": 3, "type": "peaking",    "frequency": 1404,  "gain": -1.0, "q": 2.281},
    {"index": 4, "type": "peaking",    "frequency": 3005,  "gain": 3.3,  "q": 1.461},
    {"index": 5, "type": "peaking",    "frequency": 4765,  "gain": -1.5, "q": 3.828},
    {"index": 6, "type": "peaking",    "frequency": 5884,  "gain": 4.8,  "q": 1.680},
    {"index": 7, "type": "high_shelf", "frequency": 10000, "gain": -2.3, "q": 0.699},
]

# The plot shows EQ directly — 0 dB is flat — rather than the old ±5 around a 60 dB
# reference. Same information, but "+6" means what a user already thinks it means.
#
# Symmetric, deliberately. The mockup's axis stopped at −6, which reads fine until a
# handle is draggable: the plot is the gain control now, so an axis that bottoms out
# at −6 dB is an axis that silently refuses every cut deeper than that. A −8 dB
# notch is an ordinary thing to want. So the window spans the full ±12 the firmware
# will take, and gains one more label for it.
TOP_DB = 12.0
BOT_DB = -12.0
DB_MAX = 12.0

# The readout view's axis, which is the vendor chart's rather than ours: a 60 dB
# reference line with the curve normalised around it. It draws the OUTPUT — pre-gain
# already paid — which is the one thing the editor's axis cannot show, since there
# 0 dB means "no EQ" rather than "no change in level".
#
# The room below the reference is deliberately double the room above, and that is not
# cosmetic: pre-gain only ever pushes the output DOWN, so the output curve lives below
# the line by however much headroom the user bought. A -4.8 dB pre-gain over a -7.7 dB
# cut puts 60 Hz at 47.5 dB, and on the vendor's own +5/-10 window that curve runs
# flat along the floor -- a chart that is lying about the shape it is drawing. Labels
# come out +65 / +60 / +55 / +50 / +45, evenly spaced, reference at the quarter.
READOUT_REF = 60.0
READOUT_TOP = 5.0
READOUT_BOT = -15.0
# Fractions of the plot, matching `db-marks`/`f-marks` in app.slint.
READOUT_ROWS = [0.0, 0.25, 0.5, 0.75, 1.0]
READOUT_COLS = [0.0, 0.13270, 0.23300, 0.33270, 0.46598,
                0.56600, 0.66667, 0.79930, 0.89900, 1.0]

# The preview chart is sampled into a fixed coordinate space and scaled to whatever
# size the dialog happens to be, rather than being resampled at its pixel size.
#
# The obvious version — the chart reports its size, Python redraws to match — does not
# work for an element inside an `if`: Slint's `changed` callbacks fire on a change, and
# a conditionally-created element evaluates its geometry once, at its final value, so
# there is no change to report and the curve never gets drawn at all. A fixed viewbox
# removes the round-trip, the timing bug and the resize cost together. The main graph
# keeps its pixel-space sampling because its dashed trace would shear under a
# non-uniform viewbox scale.
PREVIEW_W = 1000.0
PREVIEW_H = 600.0

# Platform-correct since 1.1.0 — %APPDATA% on Windows, ~/Library/Application Support
# on macOS, unchanged XDG on Linux. mc.config_dir() is the single answer, and
# mc.migrate_legacy_config() carries a 1.0.0 file across on the two that moved.
SETTINGS_PATH = os.path.join(mc.config_dir(), "settings.json")

# Q below ~0.1 is a filter so wide it is a tone control, above 10 so narrow it is a
# ring. The device would take more; nothing musical lives out there.
Q_MIN = 0.1
Q_MAX = 10.0

F_MIN = curve_mod.F_MIN
F_MAX = curve_mod.F_MAX

PREGAIN_MIN, PREGAIN_MAX = -12.0, 0.0
GLOBAL_MIN, GLOBAL_MAX = -12.0, 6.0

# Spectrum boundaries in Hz. `Regions` in theme.slint draws exactly these, converted
# to log-axis fractions; keep the two in step or a band's tag will disagree with the
# tint it is sitting on.
REGION_HZ = [
    (60,    "SUB"),
    (250,   "BASS"),
    (500,   "LOW-MID"),
    (2000,  "MID"),
    (4000,  "UPPER-MID"),
    (6000,  "PRESENCE"),
    (99999, "AIR"),
]

FILTER_ORDER = ["peaking", "low_shelf", "high_shelf", "low_pass", "high_pass", "disabled"]
TYPE_ABBR = {"peaking": "PK", "low_shelf": "LS", "high_shelf": "HS",
             "low_pass": "LP", "high_pass": "HP", "disabled": "—"}

# Starting-point curves — not the DAC's own preset slots (that is the hardware
# "slot" number in the control row), just a known-good shape to then tweak.
#
# Every band here was checked against the packer below: all fit inside the firmware's
# Q2.30 coefficient range with headroom, so none get clamped on apply. The tight ones
# are the high shelves — if you retune these, re-check them. `pre` is roughly minus
# the biggest boost, so applying one does not clip.
PRESETS = [
    ("Flat",     "remove",                (), 0.0),
    ("Bass",     "graphic_eq",            (("low_shelf", 100, 4.0, 0.7),), -4.0),
    ("V-shape",  "show_chart",            (("low_shelf", 90, 4.0, 0.7),
                                           ("peaking", 900, -2.0, 1.0),
                                           ("high_shelf", 8000, 3.0, 0.7)), -4.5),
    ("Vocals",   "record_voice_over",     (("peaking", 200, -3.0, 1.2),
                                           ("peaking", 450, -2.0, 1.0),
                                           ("peaking", 2600, 3.0, 1.2)), -3.5),
    ("Warm",     "local_fire_department", (("low_shelf", 220, 3.0, 0.7),
                                           ("high_shelf", 6000, -3.0, 0.7)), -3.5),
    ("Air",      "air",                   (("high_shelf", 9000, 4.0, 0.7),
                                           ("peaking", 5000, -1.5, 2.0)), -4.0),
    ("Podcast",  "podcasts",              (("high_pass", 85, 0.0, 0.7),
                                           ("peaking", 300, -2.5, 1.0),
                                           ("peaking", 3000, 3.0, 1.2)), -3.5),
    ("Loudness", "volume_up",             (("low_shelf", 80, 5.0, 0.7),
                                           ("high_shelf", 10000, 3.5, 0.7),
                                           ("peaking", 1500, -1.5, 1.0)), -5.5),
]


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _disabled_band(i):
    return {"index": i, "type": "disabled", "frequency": 1000, "gain": 0.0, "q": 1.0}


def region_of(freq):
    for edge, name in REGION_HZ:
        if freq < edge:
            return name
    return "AIR"


def fmt_hz(f):
    if f >= 1000:
        return "%gk" % (round(f / 100.0) / 10.0)
    return "%d" % round(f)


def clamp_band(ftype, freq, gain, q):
    """Pull a band back to something the firmware can actually be given.

    Two separate ceilings, and both have to be applied in order:

    1. **Shelf slope.** RBJ's shelf formulas read Q as slope S, and their shared
       ``sqrt((A + 1/A)(1/S - 1) + 2)`` has no real solution once the slope is too
       steep for the gain. Past that there is no such filter — not a bad one, none.

    2. **Q2.30 coefficient range.** The firmware stores coefficients in [-2, 2), and
       plenty of reasonable-looking filters need more than that; ``write_peq_index``
       refuses those outright rather than let the DAC wrap and play a curve nobody
       drew. Better to stop the drag at the wall than let it sail past and bounce
       back as a refusal toast.

    Both ceilings come from ``moondrop_control`` itself rather than a second copy of
    the maths here — a limit the graph enforces has to be the same limit the writer
    enforces, or the graph is lying.
    """
    freq = int(round(_clamp(float(freq), F_MIN, F_MAX)))
    gain = _clamp(float(gain), -DB_MAX, DB_MAX)
    q = _clamp(float(q), Q_MIN, Q_MAX)

    if ftype == "disabled":
        return ftype, freq, 0.0, round(q, 3)

    if ftype in ("low_shelf", "high_shelf"):
        ceiling = mc.max_shelf_q(gain)
        # The 0.995 keeps float drift between here and the writer from landing us a
        # hair over its limit and turning a legal filter into a refusal.
        if math.isfinite(ceiling):
            q = _clamp(q, Q_MIN, max(Q_MIN, ceiling * 0.995))

    if not mc._packs_ok(freq, gain, q, ftype):
        sign = 1.0 if gain >= 0 else -1.0
        ceiling = mc.max_safe_gain(freq, q, ftype, sign, limit=DB_MAX)
        gain = 0.0 if ceiling is None else round(ceiling, 1)
        # Bisection can land a hair over; walk it back until it truly fits.
        for _ in range(8):
            if mc._packs_ok(freq, gain, q, ftype):
                break
            gain = round(gain - sign * 0.1, 1)

    return ftype, freq, round(gain, 1), round(q, 3)


def at_ceiling(band):
    """Is this band pinned against the coefficient wall — would a nudge further in
    the same direction overflow? Drives the amber "limit" tag on its card, so the
    wall is visible instead of just feeling like a stuck slider."""
    if band["type"] == "disabled":
        return False
    sign = 1.0 if band["gain"] >= 0 else -1.0
    return not mc._packs_ok(band["frequency"], band["gain"] + sign * 0.15,
                            band["q"], band["type"])


def load_settings():
    """Appearance choices, which are the user's and not the device's — so they live in
    the config dir rather than anywhere near an EQ profile. A missing or unreadable
    file is not an error; it just means the defaults."""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    accent = data.get("accent", 0)
    channel = data.get("channel")
    # Update checking defaults on where we ship the build ourselves and OFF where a
    # package manager owns the files. A distro package that phones home on launch is
    # a thing packagers rightly patch out, and there is nothing this could offer a
    # pacman or apt user that `-Syu` will not do better.
    try:
        auto_default = updater.can_self_update()
    except Exception:
        auto_default = False
    return {"dark": bool(data.get("dark", False)),
            "readout": bool(data.get("readout", False)),
            "accent": int(accent) if isinstance(accent, (int, float)) else 0,
            "seen_welcome": bool(data.get("seen_welcome", False)),
            "check_updates": bool(data.get("check_updates", auto_default)),
            "channel": channel if channel in updater.CHANNELS else "stable",
            # A version the user said no to. Offered once, then only again when
            # something newer than it turns up.
            "skipped_version": str(data.get("skipped_version", "") or "")}


def save_settings(settings):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        os.replace(tmp, SETTINGS_PATH)   # atomic: a killed write leaves the old file
    except OSError:
        pass                             # a preference that will not persist is not
                                         # worth interrupting the session over


def suggest_pregain(bands):
    """Headroom for the summed curve, which is what clips — not the biggest single
    band, since overlapping bands add."""
    return round(_clamp(-curve_mod.peak_db(bands), PREGAIN_MIN, PREGAIN_MAX), 1)


def _ps_quote(text):
    """A PowerShell single-quoted literal. Inside one, the only metacharacter is the
    quote itself, and it escapes by doubling — so a path can hold $, ` or & safely."""
    return "'%s'" % str(text).replace("'", "''")


def _as_quote(text):
    """An AppleScript string literal: backslash and double quote escape, nothing else
    is special."""
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"')


# ── workers ──────────────────────────────────────────────────────────────────

# The sentinel that ends a worker's queue. It lives at module scope, and that is not
# a style choice — it used to be `self._stop`, an instance attribute, which silently
# shadowed `threading.Thread._stop`, a real method CPython calls from inside join():
#
#     join() -> _wait_for_tstate_lock() -> self._stop()
#     TypeError: 'object' object is not callable
#
# So every quit raised, out of Bridge.stop(), through the `finally` in app.main(), and
# the process exited non-zero. On Windows PyInstaller's windowed bootloader turns that
# into an "Unhandled exception in script" dialog, which is where it was reported from;
# the Linux and macOS bundles did exactly the same thing with nobody watching stderr.
# It went unnoticed in development because CPython 3.13 deleted _wait_for_tstate_lock
# and a 3.13+ interpreter never calls _stop at all — the bug is invisible on a modern
# Python and fatal on the 3.12 the release builds are made with.
#
# Nothing on a Thread subclass may be named _stop, _start, _reset_internal_locks,
# _bootstrap, _delete or _set_ident.
_QUIT = object()


class _Worker(threading.Thread):
    """One thread, one job queue, jobs run in the order they arrive."""

    def __init__(self, name):
        super().__init__(name=name, daemon=True)
        self._jobs: queue.Queue = queue.Queue()

    def submit(self, fn, *args):
        self._jobs.put((fn, args))

    def shutdown(self):
        self._jobs.put((_QUIT, ()))

    def run(self):
        while True:
            fn, args = self._jobs.get()
            if fn is _QUIT:
                return
            try:
                fn(*args)
            except Exception as exc:  # a worker must never die on one bad job
                self.on_crash(exc)

    def on_crash(self, exc):
        pass


class DeviceWorker(_Worker):
    """Owns the hidraw. Every method here runs on the worker thread."""

    def __init__(self, post):
        super().__init__("hidworker")
        self._post = post
        self._dev = None

    def on_crash(self, exc):
        self._post("error", "Device error: %s" % exc)

    def _close(self):
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None

    def _read_all(self):
        dev = self._dev
        bands = []
        for i in range(dev.bands):
            b = dev.read_peq_index(i)
            bands.append(b if b else _disabled_band(i))
        info = dev.hid_info
        name = info.get("product_string") or mc.SUPPORTED_DEVICES.get(
            dev.product_id, "Moondrop DAC")
        active = dev.get_active_eq_index()
        return {
            "connected": True,
            "deviceName": name,
            "productId": dev.product_id,
            "firmware": dev.get_firmware_version(),
            "activeProfile": -1 if active is None else int(active),
            "peqIndex": dev.peq_index,
            "supportsPregain": dev.supports_pregain,
            "pregain": dev.get_pregain(),
            "globalGain": dev.get_global_gain(),
            "bandCount": dev.bands,
            "bands": bands,
        }

    # ── jobs ──
    def refresh(self):
        self._post("busy", True)
        try:
            self._close()
            infos = mc.find_devices()
            if not infos:
                unsupported = mc.find_unsupported_devices()
                if unsupported:
                    name = mc.UNSUPPORTED_DEVICES[unsupported[0]["product_id"]][0]
                    self._post("no_device", name + " is not driveable by this tool.")
                else:
                    self._post("no_device", "No Moondrop DAC connected.")
                return
            try:
                self._dev = mc.MoondropDevice(infos[0])
            except Exception as exc:
                self._post("no_device",
                           "Could not open the DAC (%s). You may need a udev rule or sudo." % exc)
                return
            self._post("snapshot", (self._read_all(), True))
        finally:
            self._post("busy", False)

    def write_band(self, index, ftype, freq, gain, q):
        if self._dev is None:
            return
        try:
            self._dev.write_peq_index(index, ftype, freq, gain, q)
        except ValueError as exc:
            self._post("error", str(exc))

    def apply_bands(self, bands, pregain, global_gain):
        """Batch write — preset, import, community curve, revert. All bands live,
        then the gains, then one read-back so the view shows what the DAC took
        rather than what we asked for."""
        if self._dev is None:
            return
        self._post("busy", True)
        try:
            for b in bands:
                try:
                    self._dev.write_peq_index(b["index"], b["type"],
                                              int(round(b["frequency"])),
                                              float(b["gain"]), float(b["q"]))
                except ValueError as exc:
                    self._post("error", str(exc))
            if self._dev.supports_pregain and pregain is not None:
                self._dev.set_pregain(float(pregain), save=False)
            if global_gain is not None:
                self._dev.set_global_gain(float(global_gain), save=False)
            self._post("snapshot", (self._read_all(), False))
        finally:
            self._post("busy", False)

    def set_pregain(self, db):
        if self._dev is None:
            return
        try:
            self._dev.set_pregain(db, save=False)
        except Exception as exc:
            self._post("error", str(exc))

    def set_global_gain(self, db):
        if self._dev is None:
            return
        try:
            self._dev.set_global_gain(db, save=False)
        except Exception as exc:
            self._post("error", str(exc))

    def set_slot(self, index):
        if self._dev is None:
            return
        try:
            self._dev.set_active_eq_index(int(index), save=False)
        except Exception as exc:
            self._post("error", str(exc))

    def save_to_flash(self):
        if self._dev is None:
            return
        self._post("busy", True)
        try:
            self._dev.save_eq_to_flash()
            self._dev.save_offset_to_flash()
            self._post("saved", None)
        except Exception as exc:
            self._post("error", "Save to flash failed: %s" % exc)
        finally:
            self._post("busy", False)

    def shutdown_device(self):
        self._close()


class HubWorker(_Worker):
    """The Moondrop community library. Network and the on-disk cache only — it never
    opens the hidraw, which is exactly why browsing configs can never collide with a
    device read."""

    # The popular head of the library is what anyone actually wants; the long tail is
    # mostly empty test uploads. The index has no pagination at all (productUuid is
    # the only parameter the endpoint honours), so the cap is applied here.
    CAP = 400

    def __init__(self, post):
        super().__init__("hubworker")
        self._post = post

    def on_crash(self, exc):
        self._post("hub_error", "Community library: %s" % exc)

    def load_index(self, product_uuid, refresh):
        self._post("hub_busy", True)
        try:
            rows, cached = mc.hub_fetch_index(product_uuid, refresh=refresh)
            slim = [self._slim(r) for r in rows]
            slim.sort(key=lambda d: (d["likes"], d["downloads"], d["rating"]),
                      reverse=True)
            self._post("hub_index", (slim, cached))
        except Exception as exc:  # noqa: BLE001 — any network/parse failure is a toast
            self._post("hub_error", "Couldn't reach the Moondrop library (%s)." % exc)
        finally:
            self._post("hub_busy", False)

    def resolve(self, uuid, title, band_count):
        """Fetch one config's curve. Always a preview — nothing is written to the DAC
        until the user has seen the shape and said yes. Applying a stranger's curve
        sight-unseen over a profile you spent an evening on is not a thing a click
        should be able to do."""
        self._post("hub_busy", True)
        try:
            file_ref = mc.hub_resolve_file(uuid)
            if not file_ref:
                raise LookupError("This config is no longer on the server.")
            bands, dropped = mc.hub_preset_bands(file_ref, band_count)
            if not bands:
                raise LookupError("This config has no usable bands.")
            self._post("hub_bands", (bands, dropped, uuid))
        except LookupError as exc:
            self._post("hub_error", str(exc))
        except Exception as exc:  # noqa: BLE001
            self._post("hub_error", "Couldn't load this config (%s)." % exc)
        finally:
            self._post("hub_busy", False)

    @staticmethod
    def _slim(row):
        def num(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return 0

        def flat(s):
            # Authors format their descriptions: newlines, runs of spaces, hand-drawn
            # frequency tables. Collapse to one line so a card stays one card.
            return " ".join((s or "").split())

        rated = num(row.get("score_count"))
        try:
            score = float(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        return {
            "uuid": row.get("uuid", ""),
            "title": flat(row.get("title")) or "Untitled",
            "author": flat(row.get("username")) or "anonymous",
            "desc": flat(row.get("desc"))[:220],
            "likes": num(row.get("like")),
            "downloads": num(row.get("downloadcount")),
            "rating": round(score, 1) if rated > 0 else 0.0,
            "ratings": rated,
        }


class IoWorker(_Worker):
    """File dialogs and JSON. Its own thread because zenity blocks for as long as the
    user leaves the chooser open, and the UI thread cannot afford that."""

    def __init__(self, post):
        super().__init__("ioworker")
        self._post = post

    def on_crash(self, exc):
        self._post("error", "File error: %s" % exc)

    @staticmethod
    def _ask(argv, **kw):
        """Run a chooser and return what it printed, or None if it did not run or the
        user cancelled. Never lets a console window flash up behind a windowed build."""
        if sys.platform == "win32":
            kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            done = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=600, **kw)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None                    # cancelled, or the tool is not installed
        return done.stdout.strip() or None

    @staticmethod
    def _pick(save, suggestion):
        """A native file chooser without a toolkit.

        Slint has no dialog of its own, so this asks the desktop for one. Through
        1.0.0 that meant zenity and *only* zenity — a program that exists on neither
        Windows nor macOS, which quietly made Import and Export dead controls on both:
        the chooser never appeared, `_pick` returned None, and the app reported that
        the user had cancelled something they were never offered. Each platform now
        gets the chooser it actually has.

        Returns None when the user cancelled or nothing could be found to ask with;
        the caller falls back to a known path rather than failing.
        """
        title = "Export EQ profile" if save else "Import EQ profile"
        folder, name = os.path.split(suggestion)

        if sys.platform == "win32":
            # PowerShell driving WinForms' own dialog: present on every supported
            # Windows, no dependency to ship, and it is the dialog users know. Run
            # from a file rather than -Command so quoting cannot be got wrong, and
            # -STA because the common dialogs require a single-threaded apartment.
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms\n"
                "$d = New-Object System.Windows.Forms.%sFileDialog\n"
                "$d.Title = %s\n"
                "$d.Filter = 'EQ profile (*.json)|*.json|All files (*.*)|*.*'\n"
                "$d.FileName = %s\n"
                "$d.InitialDirectory = %s\n"
                "%s"
                "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
                " { [Console]::Out.Write($d.FileName) } else { exit 1 }\n"
            ) % ("Save" if save else "Open",
                 _ps_quote(title), _ps_quote(name), _ps_quote(folder),
                 "$d.OverwritePrompt = $true\n$d.DefaultExt = 'json'\n" if save
                 else "$d.CheckFileExists = $true\n")
            fd, script = tempfile.mkstemp(prefix="hubmoon-pick-", suffix=".ps1")
            try:
                with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
                    fh.write(ps)
                return IoWorker._ask(["powershell", "-NoProfile", "-STA",
                                      "-ExecutionPolicy", "Bypass", "-File", script])
            finally:
                try:
                    os.remove(script)
                except OSError:
                    pass

        if sys.platform == "darwin":
            # `choose file` is the Finder's own panel. It exits non-zero when the user
            # cancels, which _ask already reads as None.
            if save:
                body = ('choose file name with prompt %s default name %s'
                        ' default location POSIX file %s'
                        % (_as_quote(title), _as_quote(name), _as_quote(folder or "/")))
            else:
                body = ('choose file with prompt %s of type {"json", "public.json"}'
                        ' default location POSIX file %s'
                        % (_as_quote(title), _as_quote(folder or "/")))
            script = ('tell application "System Events" to activate\n'
                      'set theFile to %s\n'
                      'return POSIX path of theFile' % body)
            return IoWorker._ask(["osascript", "-e", script])

        # Linux and the rest: zenity where there is a portal, kdialog on the Plasma
        # desktops that ship that instead. Chosen by which one *exists*, not by trying
        # one and falling through — a cancel and a missing binary both look like "no
        # path", so falling through would answer a cancelled dialog with another one.
        if shutil.which("zenity"):
            return IoWorker._ask(
                ["zenity", "--file-selection", "--title", title,
                 "--file-filter=EQ profile (*.json) | *.json", "--filename", suggestion]
                + (["--save", "--confirm-overwrite"] if save else []))
        if shutil.which("kdialog"):
            return IoWorker._ask(
                ["kdialog", "--title", title,
                 "--getsavefilename" if save else "--getopenfilename",
                 suggestion, "application/json"])
        return None

    def export(self, payload, suggestion):
        path = self._pick(True, suggestion)
        if not path:
            self._post("io_note", ("Export cancelled.", False))
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except OSError as exc:
            self._post("error", "Export failed: %s" % exc)
            return
        self._post("io_note", ("Exported to %s" % os.path.basename(path), False))

    def load(self, suggestion):
        path = self._pick(False, suggestion)
        if not path:
            self._post("io_note", ("Import cancelled.", False))
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            self._post("error", "Import failed: %s" % exc)
            return
        filters = data.get("filters") or []
        if not filters:
            self._post("error", "That file has no filters in it.")
            return
        for f in filters:
            if f.get("type") not in mc.FILTER_TYPES:
                self._post("error", "Unknown filter type %r in that file." % f.get("type"))
                return
        self._post("imported", (data, os.path.basename(path)))


def _net_message(exc):
    """Turn a urllib failure into a sentence about the world rather than about
    Python. "urlopen error [Errno -3]" tells a user nothing they can act on."""
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "There is no update manifest for that channel yet."
        return "The update server answered %s." % exc.code
    text = str(exc)
    if isinstance(exc, urllib.error.URLError) or "timed out" in text.lower():
        return "Couldn't reach the update server — check the network and try again."
    if "CERTIFICATE" in text.upper():
        return "The update server's certificate could not be verified."
    return "Update check failed: %s" % text


class UpdateWorker(_Worker):
    """The update check and the download, on a thread of their own.

    Not the hub thread: a community index is ~4 MB with no pagination, and an update
    check queued behind one would sit on "Checking…" for as long as the library takes.
    Not the io thread either — that one is blocked for exactly as long as a user
    leaves a file chooser open, which is unbounded.
    """

    def __init__(self, post):
        super().__init__("updateworker")
        self._post = post

    def on_crash(self, exc):
        self._post("update_error", _net_message(exc))

    def check(self, channel, current, force):
        try:
            found = updater.check(channel, current=current, force=force)
        except Exception as exc:
            log.info("update check on %s could not complete: %s", channel, exc)
            # A check the user asked for owes them an answer either way. One that
            # ran by itself on launch says nothing: the app is not broken because
            # a laptop opened on a train could not reach the internet.
            self._post("update_error" if force else "update_quiet", _net_message(exc))
            return
        log.info("update check on %s: %s", channel,
                 found["version"] if found else "already current")
        self._post("update_result", found)

    def install(self, update):
        self._post("update_progress", (0, 0))
        try:
            updater.install(update, on_progress=lambda d, t:
                            self._post("update_progress", (d, t)))
        except Exception as exc:
            log.error("staging %s failed", update.get("version"), exc_info=True)
            self._post("update_error", str(exc))
            return
        log.info("update to %s is staged", update.get("version"))
        self._post("update_staged", update.get("version", ""))


# ── the bridge ───────────────────────────────────────────────────────────────
class Bridge:
    """UI-thread facade. Owns the cached state and is the only writer of window
    properties."""

    def __init__(self, window):
        self.win = window
        self.connected = False
        self.busy = False
        self.dirty = False
        self.device_name = "No device"
        self.firmware = ""
        self.product_id = 0x011D
        self.supports_pregain = True
        self.pregain = 0.0
        self.global_gain = 0.0
        self.slot = 7
        self.band_count = 8
        self.bands = [dict(b) for b in DEMO_BANDS]
        self.selected = -1
        self.active_preset = ""

        # What the DAC held when we last knew flash and the DSP agreed: the first read
        # of a session, and again after each save. Edits go live with save=False and a
        # re-read reports what we just wrote — the DSP cannot tell us what flash still
        # holds — so re-reading can never undo an edit. Only this can.
        self.pristine = None
        self._reverting = False

        self.settings = load_settings()
        # First run gets the welcome screen. After that it is on request only — an
        # opening screen you have already read is an obstacle, not a welcome.
        self.welcome_open = not self.settings["seen_welcome"]
        self.help_open = False
        self.settings_open = False
        self.hub_open = False
        self.hub_busy = False
        self.hub_note = ""
        self.hub_query = ""
        self.hub_rows = []          # the full slim index
        self.hub_visible = []       # what the list is showing
        self.preview = None         # the config being looked at, before it is applied

        # plot geometry, reported by the view on resize; until the first report we
        # have no pixels to sample against and draw nothing.
        self._plot_w = 0.0
        self._plot_h = 0.0
        self._drag = -1
        self._q_gen = 0
        self._grid_for = None
        self._grid_path = ""
        # Models are built once and updated in place — see push() for why that matters.
        self._band_model = None
        self._hub_model = None
        self._hub_sig = None

        # ── updates ──
        # "idle" | "checking" | "current" | "available" | "downloading" | "staged"
        self.update_state = "idle"
        self.update_found = None      # the dict from updater.check(), when there is one
        self.update_note = ""
        self.update_progress = 0.0
        self.install_kind = updater.install_kind()
        self.install_note = updater.describe_install(self.install_kind)
        # Set once an updater helper is armed and waiting for this process to exit.
        # app.main() reads it after the loop returns; nothing else may write it.
        self.handing_over = False

        self.dev = DeviceWorker(self._post)
        self.hub = HubWorker(self._post)
        self.io = IoWorker(self._post)
        self.upd = UpdateWorker(self._post)
        for w in (self.dev, self.hub, self.io, self.upd):
            w.start()

        window.refresh = self.refresh
        window.save_flash = self.save_to_flash
        window.revert = self.revert
        window.plot_resized = self.on_plot_resized
        window.select_band = self.select_band
        window.set_pregain = self.set_pregain
        window.set_global_gain = self.set_global_gain
        window.match_headroom = self.match_headroom
        window.step_slot = self.step_slot
        window.plot_press = self.plot_press
        window.plot_move = self.plot_move
        window.plot_release = self.plot_release
        window.plot_scroll = self.plot_scroll
        window.set_band_gain = self.set_band_gain
        window.commit_band_edit = self.commit_band_edit
        window.step_freq = self.step_freq
        window.step_q = self.step_q
        window.cycle_type = self.cycle_type
        window.apply_preset = self.apply_preset
        window.import_file = self.import_file
        window.export_file = self.export_file
        window.toggle_help = self.toggle_help
        window.toggle_settings = self.toggle_settings
        window.set_dark = self.set_dark
        window.set_readout = self.set_readout
        window.set_accent = self.set_accent
        window.dismiss_welcome = self.dismiss_welcome
        window.show_welcome = self.show_welcome
        window.toggle_community = self.toggle_community
        window.community_search = self.community_search
        window.community_apply = self.community_apply
        window.community_reload = self.community_reload
        window.preview_apply = self.preview_apply
        window.preview_close = self.preview_close
        window.dismiss_toast = lambda: (self.toast(""), self.push())
        window.check_update = lambda: self.check_update(True)
        window.install_update = self.install_update
        window.skip_update = self.skip_update
        window.set_channel = self.set_channel
        window.set_auto_update = self.set_auto_update
        window.open_logs = self.open_logs
        window.open_notes = self.open_notes
        window.app_version = mc.__version__
        # The sheet used to print "~/.config/hub-moon/settings.json" as a literal,
        # which stopped being true on two of the three platforms.
        window.settings_path = "saved to " + SETTINGS_PATH

        # The view is handed the finished outline rather than a name: it has no way to
        # look a name up, and giving it one would mean a second copy of the icon table.
        window.presets = slint.ListModel([
            {"name": name, "icon": ICON_PATHS.get(icon, "")}
            for name, icon, _b, _p in PRESETS
        ])

    # ── worker → UI ──
    def _post(self, kind, payload):
        """Called ON A WORKER THREAD. Hands the result to the UI thread; never
        touches window properties directly."""
        invoke_from_event_loop(lambda: self._handle(kind, payload))

    def _handle(self, kind, payload):
        if kind == "snapshot":
            state, clean = payload
            self.connected = True
            self.device_name = state["deviceName"]
            self.firmware = state["firmware"]
            self.product_id = state["productId"]
            self.supports_pregain = state["supportsPregain"]
            self.pregain = round(state["pregain"], 1)
            self.global_gain = round(state["globalGain"], 1)
            self.slot = state["activeProfile"]
            self.band_count = state["bandCount"]
            self.bands = [dict(b) for b in state["bands"]]
            if self._reverting:
                clean, self._reverting = True, False
            if clean:
                self.dirty = False
                self.toast("")
                if self.pristine is None:
                    self._snapshot()
        elif kind == "no_device":
            self.connected = False
            self.device_name = "Demo — no DAC"
            self.firmware = ""
            self.bands = [dict(b) for b in DEMO_BANDS]
            self.pregain = 0.0
            self.global_gain = 0.0
            self.dirty = False
            # The demo curve is NOT a device state, and must never become one that
            # `revert` can write back. It used to: start the app while something else
            # still holds the hidraw, get demo bands, snapshot them as pristine, then
            # reconnect — pristine stays demo because it is no longer None, and the
            # next `revert` writes a playground curve over the user's real profile.
            # Clearing it means `revert` in demo mode falls through to `refresh`,
            # which restores the demo bands anyway, and a device that turns up later
            # gets to take its own pristine.
            self.pristine = None
            self.toast(str(payload) + "  Showing a demo curve.", False)
        elif kind == "busy":
            self.busy = bool(payload)
        elif kind == "saved":
            self.dirty = False
            self._snapshot()
            self.toast("Written to flash.", False)
        elif kind == "error":
            if payload:
                self.toast(str(payload), True)
        elif kind == "io_note":
            self.toast(payload[0], payload[1])
        elif kind == "imported":
            self._adopt_import(*payload)
        elif kind == "hub_busy":
            self.hub_busy = bool(payload)
        elif kind == "hub_error":
            self.hub_note = str(payload)
        elif kind == "hub_index":
            rows, cached = payload
            self.hub_rows = rows
            self.hub_note = "%d configs%s" % (len(rows), " (cached)" if cached else "")
            self._filter_hub()
        elif kind == "hub_bands":
            self._adopt_hub_bands(*payload)
        elif kind == "update_result":
            self.update_found = payload
            if payload is None:
                self.update_state = "current"
                self.update_note = "Hub Moon %s is the latest on %s." % (
                    mc.__version__, self.settings["channel"])
            elif payload["version"] == self.settings["skipped_version"]:
                # Offered once and declined. Say nothing until something newer lands.
                self.update_state = "idle"
                self.update_note = ""
                self.update_found = None
            else:
                self.update_state = "available"
                self.update_note = payload.get("summary") or ""
        elif kind == "update_error":
            self.update_state = "idle"
            self.update_note = str(payload)
            self.update_progress = 0.0
        elif kind == "update_quiet":
            # A background check that got nowhere. It leaves no trace in the UI, so
            # the panel still reads "nothing has been checked yet" — which is true.
            self.update_state = "idle"
            self.update_progress = 0.0
        elif kind == "update_progress":
            done, total = payload
            self.update_state = "downloading"
            self.update_progress = (done / total) if total else 0.0
            self.update_note = ("Downloading… %.1f MB" % (done / 1048576.0)
                                if not total else
                                "Downloading… %.1f of %.1f MB"
                                % (done / 1048576.0, total / 1048576.0))
        elif kind == "update_staged":
            # The helper is armed and waiting on this process. Everything from here is
            # about leaving quickly and cleanly — the device handle still has to be
            # closed, so this quits the loop rather than calling os._exit().
            self.update_state = "staged"
            self.update_progress = 1.0
            self.update_note = ("Hub Moon %s is ready. Closing to finish the update…"
                                % payload)
            self.handing_over = True
            self.push()
            slint.Timer.single_shot(timedelta(milliseconds=900),
                                    slint.quit_event_loop)
            return
        self.push()

    def _snapshot(self):
        self.pristine = {
            "bands": [dict(b) for b in self.bands],
            "pregain": self.pregain,
            "global_gain": self.global_gain,
        }

    # ── UI → workers ──
    def start(self):
        self.dev.submit(self.dev.refresh)
        # The opening update check is quiet and cached: it only reaches the network
        # once a day, it never raises, and it says nothing at all unless there is
        # something newer than what is running. A check that announces "you are up to
        # date" on every launch is a notification nobody asked for.
        if self.settings["check_updates"]:
            self.upd.submit(self.upd.check, self.settings["channel"],
                            mc.__version__, False)

    # ── updates ──
    def check_update(self, force=False):
        """Ask now. `force` is what the button does: bypass the cache, and report a
        failure instead of swallowing it."""
        self.update_state = "checking"
        self.update_note = "Checking for updates…"
        self.update_progress = 0.0
        self.upd.submit(self.upd.check, self.settings["channel"],
                        mc.__version__, bool(force))
        self.push()

    def install_update(self):
        """Download the staged build and hand over to the helper that installs it."""
        found = self.update_found
        if not found:
            return
        if not found.get("can_install"):
            # Nothing to do here but tell the truth: this install belongs to a package
            # manager, and the command that updates it is already on screen.
            self.toast("This build updates with:  %s" % found.get("hint", ""), False)
            self.push()
            return
        self.update_state = "downloading"
        self.update_progress = 0.0
        self.update_note = "Starting download…"
        self.upd.submit(self.upd.install, found)
        self.push()

    def skip_update(self):
        """Not this one. Asked again only when something newer than it appears."""
        if self.update_found:
            self.settings["skipped_version"] = self.update_found["version"]
            save_settings(self.settings)
        self.update_found = None
        self.update_state = "idle"
        self.update_note = ""
        self.push()

    def set_channel(self, index):
        """0 stable, 1 beta. Changing channel clears the skip — a version declined on
        one channel says nothing about what the other is offering."""
        channel = updater.CHANNELS[int(_clamp(int(index), 0, len(updater.CHANNELS) - 1))]
        if channel == self.settings["channel"]:
            return
        self.settings["channel"] = channel
        self.settings["skipped_version"] = ""
        save_settings(self.settings)
        self.update_found = None
        self.check_update(True)

    def set_auto_update(self, on):
        self.settings["check_updates"] = bool(on)
        save_settings(self.settings)
        if not on:
            self.update_found = None
            self.update_state = "idle"
            self.update_note = ""
        self.push()

    def open_logs(self):
        from . import diagnostics
        if not diagnostics.reveal_logs():
            self.toast("The log is at %s" % diagnostics.log_path(), False)
        self.push()

    def open_notes(self):
        url = ((self.update_found or {}).get("notes_url")
               or "https://hubmoon.miyukivigil.tech/changelog.html")
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            self.toast("Release notes: %s" % url, False)
        self.push()

    def refresh(self):
        self.dev.submit(self.dev.refresh)

    def revert(self):
        """Back to the last state we know flash agrees with — which a re-read cannot
        give us, because the DSP answers with whatever was written to it last."""
        if self.pristine is None or len(self.pristine["bands"]) != self.band_count:
            # No saved state to go back to, or one that belongs to a different device.
            # Re-reading is the honest answer rather than writing something that only
            # looks like the right shape.
            self.refresh()
            return
        self.bands = [dict(b) for b in self.pristine["bands"]]
        self.pregain = self.pristine["pregain"]
        self.global_gain = self.pristine["global_gain"]
        self.active_preset = ""
        self.dirty = False
        if self.connected:
            # apply_bands reports back as a dirty snapshot, because normally it is one.
            # This is the exception: the state it restores is by definition the one
            # flash already holds, so the flag tells the handler to land it clean.
            self._reverting = True
            self.dev.submit(self.dev.apply_bands, [dict(b) for b in self.bands],
                            self.pregain if self.supports_pregain else None,
                            self.global_gain)
        self.toast("Reverted to the saved profile.", False)
        self.push()

    def save_to_flash(self):
        if self.connected:
            self.dev.submit(self.dev.save_to_flash)
        else:
            self.dirty = False
            self._snapshot()
            self.toast("Demo mode — nothing to save.", False)
            self.push()

    # ── band edits ──
    def _band(self, index):
        for b in self.bands:
            if b["index"] == index:
                return b
        return None

    def _set_band(self, index, *, ftype=None, freq=None, gain=None, q=None, write=False):
        b = self._band(index)
        if b is None:
            return
        ftype, freq, gain, q = clamp_band(
            b["type"] if ftype is None else ftype,
            b["frequency"] if freq is None else freq,
            b["gain"] if gain is None else gain,
            b["q"] if q is None else q,
        )
        b.update(type=ftype, frequency=freq, gain=gain, q=q)
        self.dirty = True
        self.active_preset = ""
        if write and self.connected:
            self.dev.submit(self.dev.write_band, index, ftype, freq, gain, q)
        self.push()

    def commit_band(self, index, ftype, freq, gain, q):
        """Kept for callers outside the view (tests, scripts): set and write in one."""
        self._set_band(index, ftype=ftype, freq=freq, gain=gain, q=q, write=True)

    def commit_band_edit(self, index):
        """Write whatever the band currently holds. This is the release half of every
        live control — the drag and the sliders repaint on every frame but only reach
        the hidraw once the pointer comes up, because a HID write per mouse-move
        would queue up hundreds of them behind the one that matters."""
        b = self._band(int(index))
        if b is None or not self.connected:
            return
        self.dev.submit(self.dev.write_band, b["index"], b["type"],
                        b["frequency"], b["gain"], b["q"])

    def select_band(self, index):
        self.selected = int(index)
        self.push()

    def set_band_gain(self, index, gain):
        self.selected = int(index)
        self._set_band(int(index), gain=gain)

    def step_freq(self, index, direction):
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        # A sixth of an octave per press: fine enough to place a notch, coarse enough
        # that walking from 20 Hz to 20 kHz does not take all afternoon.
        factor = 1.06 if direction > 0 else 1 / 1.06
        self._set_band(int(index), freq=b["frequency"] * factor, write=True)

    def step_q(self, index, direction):
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        self._set_band(int(index), q=b["q"] + (0.1 if direction > 0 else -0.1), write=True)

    def cycle_type(self, index, direction):
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        n = FILTER_ORDER.index(b["type"]) if b["type"] in FILTER_ORDER else 0
        n = (n + int(direction)) % len(FILTER_ORDER)
        self._set_band(int(index), ftype=FILTER_ORDER[n], write=True)

    # ── the graph ──
    def on_plot_resized(self, w, h):
        self._plot_w, self._plot_h = float(w), float(h)
        self.push()

    def _handle_px(self, band):
        """Where a band's handle sits, in plot pixels."""
        x = curve_mod.x_of_freq(band["frequency"], self._plot_w)
        y = self._plot_h * (TOP_DB - _clamp(band["gain"], BOT_DB, TOP_DB)) / (TOP_DB - BOT_DB)
        return x, y

    def plot_press(self, xf, yf):
        """The view reports a fraction of the plot, not a frequency — it has no idea
        the axis is logarithmic and does not need one. Nearest-handle hit testing
        happens here because this is where the band positions live."""
        if self._plot_w < 1:
            return
        px, py = float(xf) * self._plot_w, float(yf) * self._plot_h
        best, best_d = -1, 1e9
        for b in self.bands:
            if b["type"] == "disabled":
                continue
            hx, hy = self._handle_px(b)
            d = math.hypot(px - hx, py - hy)
            if d < best_d:
                best, best_d = b["index"], d
        if best >= 0 and best_d < 26.0:
            self.selected = best
            self._drag = best
            self.push()

    def plot_move(self, xf, yf):
        if self._drag < 0 or self._plot_w < 1:
            return
        freq = curve_mod.freq_of_x(_clamp(float(xf), 0.0, 1.0) * self._plot_w, self._plot_w)
        gain = TOP_DB - _clamp(float(yf), 0.0, 1.0) * (TOP_DB - BOT_DB)
        self._set_band(self._drag, freq=freq, gain=gain)

    def plot_release(self):
        if self._drag < 0:
            return
        idx, self._drag = self._drag, -1
        self.commit_band_edit(idx)

    def plot_scroll(self, delta):
        """Scroll over the plot narrows or widens the selected band. Written on a
        250 ms trailing edge — a wheel notch is a dozen events and only the last one
        is worth a HID write."""
        if self.selected < 0:
            return
        b = self._band(self.selected)
        if b is None or b["type"] == "disabled":
            return
        self._set_band(self.selected, q=b["q"] * (1.12 if delta > 0 else 0.89))
        self._q_gen += 1
        gen = self._q_gen
        idx = self.selected

        def fire():
            if gen == self._q_gen:
                invoke_from_event_loop(lambda: self.commit_band_edit(idx))
        threading.Timer(0.25, fire).start()

    # ── gains and slot ──
    def set_pregain(self, db):
        """Pre-gain is headroom, not tone: it shifts the whole output down so a
        boosted curve does not clip. Live like every other edit — only save_to_flash
        persists."""
        db = round(_clamp(float(db), PREGAIN_MIN, PREGAIN_MAX), 1)
        if abs(db - self.pregain) < 0.05:
            return
        self.pregain = db
        self.dirty = True
        if self.connected and self.supports_pregain:
            self.dev.submit(self.dev.set_pregain, float(db))
        self.push()

    def set_global_gain(self, db):
        """The DAC's own output offset, downstream of the EQ. Distinct from pre-gain:
        this is volume, that is headroom."""
        db = round(_clamp(float(db), GLOBAL_MIN, GLOBAL_MAX), 1)
        if abs(db - self.global_gain) < 0.05:
            return
        self.global_gain = db
        self.dirty = True
        if self.connected:
            self.dev.submit(self.dev.set_global_gain, float(db))
        self.push()

    def match_headroom(self):
        self.set_pregain(suggest_pregain(self.bands))

    def step_slot(self, direction):
        """The DAC's active EQ profile. Worth knowing before you touch it: on a
        DAWN PRO2 running firmware 1.5 this reads 9 whether the EQ is on or off, so
        it is *not* a proxy for "is the EQ engaged" — and moving it off the custom
        slot means the bands below stop being what you hear."""
        if not self.connected or self.slot < 0:
            return
        nxt = int(_clamp(self.slot + int(direction), 0, 9))
        if nxt == self.slot:
            return
        self.slot = nxt
        self.dirty = True
        self.dev.submit(self.dev.set_slot, nxt)
        self.push()

    # ── presets ──
    def apply_preset(self, i):
        i = int(i)
        if not 0 <= i < len(PRESETS):
            return
        name, _icon, shapes, pre = PRESETS[i]
        bands = []
        for n in range(self.band_count):
            if n < len(shapes):
                ftype, freq, gain, q = clamp_band(*shapes[n])
            else:
                # Fill every slot: bands the preset does not define are explicitly
                # disabled, so applying one never leaves a stray filter behind.
                ftype, freq, gain, q = "disabled", 1000, 0.0, 1.0
            bands.append({"index": n, "type": ftype, "frequency": freq,
                          "gain": gain, "q": q})
        self._replace_bands(bands, pre)
        self.active_preset = name
        self.toast("Applied %s." % name, False)
        self.push()

    def _replace_bands(self, bands, pregain):
        self.bands = bands
        self.selected = -1
        self.dirty = True
        if self.supports_pregain and pregain is not None:
            self.pregain = round(_clamp(float(pregain), PREGAIN_MIN, PREGAIN_MAX), 1)
        if self.connected:
            self.dev.submit(self.dev.apply_bands, [dict(b) for b in bands],
                            self.pregain if self.supports_pregain else None, None)

    # ── import / export ──
    def _default_path(self, name):
        base = os.path.expanduser("~/Documents")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        return os.path.join(base, name)

    def export_file(self):
        payload = {
            "device_name": self.device_name,
            "pregain": self.pregain,
            "global_gain": self.global_gain,
            "active_eq_profile": self.slot,
            "filters": [
                {"index": b["index"], "type": b["type"], "frequency": b["frequency"],
                 "gain": b["gain"], "q": b["q"]}
                for b in self.bands
            ],
        }
        self.io.submit(self.io.export, payload, self._default_path("hub-moon-eq.json"))

    def import_file(self):
        self.io.submit(self.io.load, self._default_path("hub-moon-eq.json"))

    def _adopt_import(self, data, filename):
        bands = []
        for n, f in enumerate(data.get("filters") or []):
            if n >= self.band_count:
                break
            ftype, freq, gain, q = clamp_band(f["type"], f["frequency"], f["gain"], f["q"])
            bands.append({"index": n, "type": ftype, "frequency": freq,
                          "gain": gain, "q": q})
        while len(bands) < self.band_count:
            bands.append(_disabled_band(len(bands)))
        pre = data.get("pregain")
        self._replace_bands(bands, None if pre is None else float(pre))
        if "global_gain" in data:
            self.set_global_gain(float(data["global_gain"]))
        self.active_preset = ""
        self.toast("Imported %s." % filename, False)

    # ── community library ──
    def toggle_community(self):
        self.hub_open = not self.hub_open
        if self.hub_open:
            self.help_open = False
            if not self.hub_rows:
                self.community_reload(False)
        self.push()

    def community_reload(self, refresh=True):
        uuid = mc.PRODUCT_UUIDS.get(self.product_id) or mc.PRODUCT_UUIDS[0x011D]
        self.hub_note = "Fetching the library…"
        self.hub.submit(self.hub.load_index, uuid, bool(refresh))
        self.push()

    def community_search(self, text):
        self.hub_query = str(text)
        self._filter_hub()
        self.push()

    def _filter_hub(self):
        q = self.hub_query.strip().lower()
        rows = self.hub_rows
        if q:
            rows = [r for r in rows
                    if q in r["title"].lower() or q in r["author"].lower()
                    or q in r["desc"].lower()]
        self.hub_visible = rows[:HubWorker.CAP]

    def community_apply(self, uuid):
        row = next((r for r in self.hub_rows if r["uuid"] == uuid), None)
        self.hub_note = "Loading “%s”…" % (row["title"] if row else "config")
        self.preview = None
        self.hub.submit(self.hub.resolve, str(uuid), row["title"] if row else "config",
                        self.band_count)
        self.push()

    def _adopt_hub_bands(self, raw, dropped, uuid):
        """A fetched config, mapped onto this device's slots and held for review."""
        bands = []
        for n in range(self.band_count):
            if n < len(raw):
                ftype, freq, gain, q = clamp_band(raw[n]["type"], raw[n]["frequency"],
                                                  raw[n]["gain"], raw[n]["q"])
            else:
                ftype, freq, gain, q = "disabled", 1000, 0.0, 1.0
            bands.append({"index": n, "type": ftype, "frequency": freq,
                          "gain": gain, "q": q})
        row = next((r for r in self.hub_rows if r["uuid"] == uuid), None)
        # Published configs carry no pre-gain of their own, and most are boost-heavy.
        # Derive headroom from the curve rather than leaving it to clip.
        pre = suggest_pregain(bands)
        used = sum(1 for b in bands if b["type"] != "disabled")
        note = "%d of %d bands · pre-gain %+.1f dB" % (used, self.band_count, pre)
        if dropped:
            note += " · %d band%s past this device's %d dropped" % (
                dropped, "" if dropped == 1 else "s", self.band_count)
        self.preview = {
            "uuid": uuid,
            "title": row["title"] if row else "Config",
            "author": row["author"] if row else "",
            "desc": row["desc"] if row else "",
            "bands": bands,
            "pregain": pre,
            "note": note,
        }
        self.hub_note = ""

    def preview_apply(self):
        if not self.preview:
            return
        p, self.preview = self.preview, None
        self._replace_bands([dict(b) for b in p["bands"]], p["pregain"])
        self.active_preset = ""
        self.hub_open = False
        self.toast("Applied “%s”." % p["title"], False)
        self.push()

    def preview_close(self):
        self.preview = None
        self.push()

    # ── help and appearance ──
    def toggle_help(self):
        self.help_open = not self.help_open
        if self.help_open:
            self.hub_open = False
            self.settings_open = False
        self.push()

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.hub_open = False
            self.help_open = False
        self.push()

    def set_dark(self, on):
        self.settings["dark"] = bool(on)
        save_settings(self.settings)
        self.push()

    def set_accent(self, index):
        """The one accent, out of the table in theme.slint. It drives the primary
        action, the active state and the equalised curve — the reference and output
        traces are deliberately left alone."""
        self.settings["accent"] = int(_clamp(int(index), 0, 5))
        save_settings(self.settings)
        self.push()

    def dismiss_welcome(self):
        self.welcome_open = False
        self.settings["seen_welcome"] = True
        save_settings(self.settings)
        self.push()

    def show_welcome(self):
        self.welcome_open = True
        self.settings_open = False
        self.help_open = False
        self.hub_open = False
        self.push()

    def set_readout(self, on):
        """Editor ↔ readout. Two views of the same maths: one shows what each band does
        and lets you drag it, the other shows the level that leaves the DAC. Neither
        substitutes for the other, which is why it is a toggle and not a replacement."""
        self.settings["readout"] = bool(on)
        save_settings(self.settings)
        self.push()

    def stop(self):
        """Close the DAC and let the workers finish — but never at the cost of the
        exit itself.

        Each join is guarded separately, and that is the shape 1.0.0 got wrong twice
        over: one join raised (see _QUIT), and because the joins shared a loop the
        other two workers were then never waited on at all. A worker wedged in a
        blocking read must not be able to hold up the two that are not.
        """
        workers = (self.dev, self.hub, self.io, self.upd)
        self.dev.submit(self.dev.shutdown_device)
        for w in workers:
            w.shutdown()
        for w in workers:
            try:
                w.join(timeout=2.0)
            except Exception:
                log.error("joining %s failed", w.name, exc_info=True)
            else:
                if w.is_alive():
                    log.warning("%s did not finish within 2s", w.name)

    # ── push state into the view ──
    def toast(self, text, is_error=False):
        self.win.toast_text = text
        self.win.toast_error = bool(is_error)

    def push(self):
        w = self.win
        w.connected = self.connected
        w.busy = self.busy
        w.dirty = self.dirty
        w.device_name = self.device_name
        w.firmware = self.firmware or ""
        w.band_count = self.band_count
        w.pregain = float(self.pregain)
        w.supports_pregain = self.supports_pregain
        w.global_gain = float(self.global_gain)
        w.slot = self.slot
        w.selected = self.selected
        w.active_preset = self.active_preset
        w.dark = self.settings["dark"]
        w.readout = self.settings["readout"]
        w.accent_index = self.settings["accent"]
        w.welcome_open = self.welcome_open
        w.help_open = self.help_open
        w.settings_open = self.settings_open
        w.hub_open = self.hub_open
        w.hub_busy = self.hub_busy
        w.hub_note = self.hub_note
        w.hub_count = len(self.hub_visible)

        w.update_state = self.update_state
        w.update_note = self.update_note
        w.update_progress = float(self.update_progress)
        w.update_version = (self.update_found or {}).get("version", "")
        w.update_can_install = bool((self.update_found or {}).get("can_install"))
        w.update_hint = (self.update_found or {}).get("hint", "")
        w.update_reason = (self.update_found or {}).get("reason", "")
        w.update_channel = updater.CHANNELS.index(self.settings["channel"])
        w.update_auto = self.settings["check_updates"]
        w.install_note = self.install_note

        pv = self.preview
        w.preview_open = pv is not None
        w.preview_title = pv["title"] if pv else ""
        w.preview_author = pv["author"] if pv else ""
        w.preview_desc = pv["desc"] if pv else ""
        w.preview_note = pv["note"] if pv else ""
        if pv:
            box = dict(width=PREVIEW_W, height=PREVIEW_H,
                       top_db=TOP_DB, bot_db=BOT_DB)
            w.preview_curve_path = curve_mod.svg_curve(pv["bands"], **box)
            w.preview_flat_path = curve_mod.svg_flat(db=0.0, **box)

        # Headroom: the summed curve is what clips, and a +6 dB peak with no pre-gain
        # means peaks leave the DAC 6 dB hotter than they arrived.
        peak = curve_mod.peak_db(self.bands)
        overshoot = peak + self.pregain
        w.clipping = bool(self.supports_pregain and overshoot > 0.05)
        w.headroom_hint = ("%+.1f dB over" % overshoot) if overshoot > 0.05 else "ok"

        # A `[BandRow]` property takes a Model, not a plain list — assigning a list
        # raises "Object is not a dict or NamedTuple" as it tries to read the list
        # itself as one row.
        #
        # And the model is updated IN PLACE, never replaced. Assigning a fresh
        # ListModel makes the repeater tear down every BandCard and build new ones —
        # including the one whose TouchArea currently has the pointer grabbed. The
        # first mouse-move of a drag would repaint at the new value, destroy the
        # element that was tracking the drag, and every move after it would land on
        # nothing: sliders you could click but not drag. set_row_data keeps the
        # elements alive and only pushes the values through.
        rows = [self._row(b) for b in self.bands]
        if self._band_model is None or self._band_model.row_count() != len(rows):
            self._band_model = slint.ListModel(rows)
            w.bands = self._band_model
        else:
            for i, row in enumerate(rows):
                self._band_model.set_row_data(i, row)
        # Same rule as the bands: only hand over a new model when the list actually
        # changed, or every push tears down and rebuilds up to 400 cards.
        sig = tuple(r["uuid"] for r in self.hub_visible)
        if sig != self._hub_sig:
            self._hub_sig = sig
            self._hub_model = slint.ListModel([
                {"uuid": r["uuid"], "title": r["title"], "author": r["author"],
                 "desc": r["desc"], "likes": r["likes"], "downloads": r["downloads"],
                 "rating": float(r["rating"]), "ratings": r["ratings"]}
                for r in self.hub_visible
            ])
            w.hub_rows = self._hub_model

        if self._plot_w > 1 and self._plot_h > 1:
            # Fewer samples while a handle is under the pointer: the curve is resampled
            # on every mouse-move, and 220 points across 8 biquads twice over (the
            # output trace is a second sweep) is more than a frame's budget.
            geom = dict(width=self._plot_w, height=self._plot_h,
                        top_db=TOP_DB, bot_db=BOT_DB,
                        samples=110 if self._drag >= 0 else 220)
            w.curve_path = curve_mod.svg_curve(self.bands, **geom)
            w.flat_path = curve_mod.svg_flat(db=0.0, width=self._plot_w,
                                             height=self._plot_h,
                                             top_db=TOP_DB, bot_db=BOT_DB)
            # The output trace: the same curve shifted by pre-gain, which is what the
            # DAC actually emits. Drawn dashed so it reads as a consequence of the
            # solid one rather than a second thing to tune.
            w.pregain_path = curve_mod.svg_curve(
                self.bands, offset_db=float(self.pregain), dash=(7.0, 5.0), **geom)

            # The readout, on its own axis. Only the output curve exists here — the
            # solid one would be the same line drawn 4.8 dB higher, which is precisely
            # the confusion this view is meant to remove.
            box = dict(width=self._plot_w, height=self._plot_h,
                       top_db=READOUT_TOP, bot_db=READOUT_BOT)
            w.readout_path = curve_mod.svg_curve(
                self.bands, offset_db=float(self.pregain),
                samples=110 if self._drag >= 0 else 220, **box)
            w.readout_flat_path = curve_mod.svg_flat(db=0.0, **box)
            # Cached on the plot size: fourteen dashed lines come to ~40 KB of path
            # commands, and they only change when the window is resized. Rebuilding
            # that string on every mouse-move of a drag is pure waste.
            size = (self._plot_w, self._plot_h)
            if size != self._grid_for:
                self._grid_for = size
                self._grid_path = curve_mod.svg_grid(
                    width=self._plot_w, height=self._plot_h,
                    rows=READOUT_ROWS, cols=READOUT_COLS)
                w.readout_grid_path = self._grid_path

    def _row(self, b):
        on = b["type"] != "disabled"
        gain = float(b["gain"])
        hx, hy = (0.0, 0.5)
        if self._plot_w > 1:
            px, py = self._handle_px(b)
            hx, hy = px / self._plot_w, py / self._plot_h
        return {
            "index": int(b["index"]),
            "type": str(b["type"]),
            "abbr": TYPE_ABBR.get(b["type"], "PK"),
            "frequency": int(b["frequency"]),
            "freq_label": fmt_hz(b["frequency"]),
            "gain": gain,
            "gain_label": "%+.1f" % gain if on else "—",
            "q": float(b["q"]),
            "q_label": "Q%.2f" % b["q"],
            "enabled": on,
            "region": region_of(b["frequency"]) if on else "—",
            "hx": float(_clamp(hx, 0.0, 1.0)),
            "hy": float(_clamp(hy, 0.0, 1.0)),
            "gain_frac": float((gain + DB_MAX) / (2 * DB_MAX)),
            "at_limit": bool(at_ceiling(b)),
        }
