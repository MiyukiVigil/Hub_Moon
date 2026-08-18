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
import re
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import slint
from slint.slint import invoke_from_event_loop

try:
    import moondrop_control as mc
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

from . import autoeq
from . import curve as curve_mod
from . import devices as devices_mod
from . import guide as guide_mod
from . import nav as nav_mod
from . import profiles as prof
from . import shapes as shape_mod
from . import tuning
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
# The catalogue is 8,827 rows. The list is capped rather than paginated: nobody
# scrolls past 200 headphones, and building more cards than that costs a frame.
AEQ_LIMIT = 200

PREVIEW_W = 1000.0
PREVIEW_H = 600.0

# How the community library can be ordered, and by what. The labels are pushed to the
# view rather than written into it, so this table is the only place the choice exists.
#
# There is no "newest": the index carries no timestamp on any row — uuid, title, file,
# desc, like, dislike, favorite, downloadcount, comment_count, score, score_count and
# the two product uuids is the whole of it — and inventing an order out of the array's
# own sequence would be a label that lies. Every key here is descending, with the
# tie-breaks chosen so that a preset with no ratings at all cannot outrank one with a
# hundred.
# The curve drawn on each community card. Small, and drawn from the same maths as the
# big plot — a thumbnail that disagreed with the preview would be worse than none.
THUMB_W = 108.0
THUMB_H = 30.0

# The thumbnail's own dB range, and deliberately not the editor's ±12. Thirty pixels
# of height across 24 dB turns nearly every real preset into a flat line — the shapes
# are there, drawn two pixels tall. ±8 dB is where the library's curves actually live,
# and `svg_curve` runs anything bigger along the ceiling rather than off the card.
THUMB_TOP = 8.0
THUMB_BOT = -8.0

# How many curves one prefetch job fetches before reporting back. Eight is about a
# second of work at these sizes: short enough that a click never waits long behind it,
# long enough that the queue is not mostly overhead.
PREFETCH_CHUNK = 8

# Concurrent fetches inside one chunk. See `HubWorker.prefetch`.
PREFETCH_FETCHERS = 4

def aeq_key(row):
    """What identifies one correction. AutoEQ publishes no ids: a headphone is a
    model, measured by somebody, on a rig — and the whole point of the list is that
    the same model appears a dozen times with different measurers."""
    return "%s|%s|%s" % (row.get("source", ""), row.get("form", ""),
                         row.get("model", ""))


HUB_SORTS = (
    ("Liked", lambda r: (r["likes"], r["downloads"], r["rating"])),
    ("Downloaded", lambda r: (r["downloads"], r["likes"], r["rating"])),
    ("Rated", lambda r: (r["rating"], r["ratings"], r["likes"])),
)

# The library's four sources, in the order the tab strip draws them: what is yours
# first, then what is everybody else's. The index is what crosses into the view.
LIB_SAVED, LIB_RECENT, LIB_COMMUNITY, LIB_HEADPHONES = range(4)
LIB_TABS = 4

# What the motion setting multiplies every animation by. Zero is not "very fast": a
# duration of zero animates nothing at all, which is exactly what somebody asking for
# no motion is asking for.
MOTION_SCALES = (1.0, 0.5, 0.0)
MOTION_LABELS = ("Full", "Reduced", "Off")

# How often to ask whether a DAC has been plugged in or pulled out. Through 1.2.0
# nothing asked at all: the app looked once at launch, and a device connected a second
# later stayed invisible until somebody thought to click the device chip. Two seconds
# is under the time it takes to plug a cable in and look at the screen, and the poll
# is one `hid.enumerate()` — measured at 0.75 ms — on a worker thread that skips the
# check whenever it has real work queued.
DEVICE_POLL = timedelta(seconds=2)

# How long a notice stays up. Through 1.2.0 it stayed forever: `toast()` set the text
# and nothing ever cleared it, so "Written to flash." sat over the interface until it
# was clicked or something else replaced it. Errors get longer because they are the
# ones worth reading twice — and both are in the log either way.
TOAST_SECONDS = timedelta(seconds=5)
TOAST_ERROR_SECONDS = timedelta(seconds=10)

# Platform-correct since 1.1.0 — %APPDATA% on Windows, ~/Library/Application Support
# on macOS, unchanged XDG on Linux. mc.config_dir() is the single answer, and
# mc.migrate_legacy_config() carries a 1.0.0 file across on the two that moved.
def settings_path():
    """Resolved on every call, not captured at import.

    It used to be a module constant, which meant the answer was fixed by whatever
    `mc.config_dir()` said the moment this file was first imported — before any test
    could redirect it. The tests that redirect the config directory therefore did not
    redirect this, and every `save_settings` in the suite wrote to the real settings
    file of whoever ran it: this is how a test run flipped a developer's update
    channel and community filters under them.
    """
    return os.path.join(mc.config_dir(), "settings.json")

# The settings file has never carried a version. Adding one now is what makes any
# *future* change to its shape survivable: `load_settings` whitelists keys and silently
# drops the rest, so today an unrecognised file and an old one are indistinguishable.
# 2.0 is the release that can afford the break, so it is the release that takes it.
SETTINGS_SCHEMA = 2


def _thumbnail(bands):
    """A row's curve as an SVG path.

    Drawn on the fetching thread, deliberately. It was drawn where the results
    landed — on the UI thread — and at 3.1 ms a curve that is 25 ms of dead frames
    every time a chunk of eight arrived, fifty times over while a page filled in.
    It is arithmetic over plain floats with nothing device- or view-shaped in it, so
    there is no reason for the main loop to be the one doing it.
    """
    return curve_mod.svg_curve(bands, width=THUMB_W, height=THUMB_H,
                               top_db=THUMB_TOP, bot_db=THUMB_BOT)


def _clamp_int(value, low, high):
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return low

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


def _table_count(prop):
    """How many entries theme.slint's `prop` table has.

    Counted from the source rather than written down here, because the clamp on the
    saved setting has to move when the table does — and a hardcoded bound that is
    wrong is a settings file that silently loses the choice somebody made.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "theme.slint")
    try:
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        table = body.split(prop, 1)[1].split("];", 1)[0]
        return max(1, len(re.findall(r"\{\s*name:", table)))
    except Exception:
        return 1


# Counts, not lists. `len()` on one of these raised TypeError inside `set_skin`, so
# every click on a palette threw and the picker silently did nothing — which is what
# happens when a test asserts the constant and never calls the callback that uses it.
SKINS = _table_count("out property <[Skin]> all:")
ACCENTS = _table_count("out property <[Accent]> all:")


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
        with open(settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    accent = data.get("accent", 0)
    skin = data.get("skin", 0)
    channel = data.get("channel")
    # Update checking defaults on where we ship the build ourselves and OFF where a
    # package manager owns the files. A distro package that phones home on launch is
    # a thing packagers rightly patch out, and there is nothing this could offer a
    # pacman or apt user that `-Syu` will not do better.
    try:
        auto_default = updater.can_self_update()
    except Exception:
        auto_default = False
    def flag(key, default):
        return bool(data.get(key, default))

    return {"schema": SETTINGS_SCHEMA,
            "dark": bool(data.get("dark", False)),
            "readout": bool(data.get("readout", False)),
            "accent": int(accent) if isinstance(accent, (int, float)) else 0,
            # The surfaces and the ink, independent of the accent hue. Index into
            # Skins.all in theme.slint; out-of-range falls back to the original.
            "skin": int(skin) if isinstance(skin, (int, float)) else 0,
            "seen_welcome": bool(data.get("seen_welcome", False)),
            "check_updates": bool(data.get("check_updates", auto_default)),
            "channel": channel if channel in updater.CHANNELS else "stable",
            # A version the user said no to. Offered once, then only again when
            # something newer than it turns up.
            "skipped_version": str(data.get("skipped_version", "") or ""),
            # what ran last, so a new version can say what changed
            "last_run_version": str(data.get("last_run_version", "") or ""),

            # ── 2.0 ──
            # Off everywhere by default. On a tiling compositor the window already
            # fills its tile, and true fullscreen additionally covers the bar — which
            # is rarely what anyone wants from an EQ they use *alongside* a player.
            "start_fullscreen": flag("start_fullscreen", False),
            # The two-second device watch. An off switch rather than a knob: the only
            # reasons to want it off are a machine where enumeration is expensive and
            # somebody who simply does not want the app looking.
            "watch_devices": flag("watch_devices", True),
            # Community defaults, so the tab opens the way it was left.
            # product id (as a string, because JSON keys are) -> the index the DAC's
            # custom EQ profile answers to. Learned by observation the first time
            # anything is applied; see Bridge._note_slot.
            "custom_slots": {str(k): int(v) for k, v in
                             (data.get("custom_slots") or {}).items()
                             if str(v).lstrip("-").isdigit()},
            "hub_own": flag("hub_own", True),
            "hub_sort": _clamp_int(data.get("hub_sort", 0), 0, len(HUB_SORTS) - 1),
            # Fetching curves to classify and draw them. Off means no chips and no
            # thumbnails — the tab still works, from the cached index alone.
            "hub_prefetch": flag("hub_prefetch", True),
            # The recent list. Off stops recording; it does not erase what is there.
            "keep_history": flag("keep_history", True),
            # 0 full · 1 reduced · 2 off. Index into MOTION_SCALES.
            "motion": _clamp_int(data.get("motion", 0), 0, len(MOTION_SCALES) - 1)}


def save_settings(settings):
    try:
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        os.replace(tmp, path)            # atomic: a killed write leaves the old file
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

    def idle(self):
        """Nothing queued. Only a hint — a job may start between the check and the
        next submit — which is all the device watch needs it to be: it decides
        whether to add a poll to a queue that already has real work in it."""
        return self._jobs.empty()

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
    def probe(self):
        """Is the world still the way the UI thinks it is?

        Enumeration only — this never opens the hidraw and never reads from a device
        it already holds, so it can run on the same queue as real work without being
        able to disturb it. `hid.enumerate()` costs well under a millisecond here, and
        the watch skips itself entirely whenever the queue is busy.

        Silence is the normal case: nothing is posted unless the answer changed.
        """
        present = bool(mc.find_devices())
        if present and self._dev is None:
            self._post("appeared", None)
        elif not present and self._dev is not None:
            # Drop the handle before telling anyone. The device is already gone; what
            # is being closed is our own stale file descriptor, and leaving it open
            # means the next plug-in of the same DAC opens a second one.
            self._close()
            self._post("vanished", None)

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

    def audition(self, bands, pregain):
        """Write a set of bands live and say nothing about it.

        The difference from `apply_bands` is the read-back it does *not* do. This is
        what A/B uses, and A/B writes a curve the app must never adopt as its state —
        posting a snapshot would let the flat set it just wrote overwrite the one the
        user is editing, and letting go of the key would restore flat onto flat.

        Nothing here touches flash, and nothing sets `dirty`: the DAC is being asked a
        question, not told a fact.
        """
        if self._dev is None:
            return
        for b in bands:
            try:
                self._dev.write_peq_index(b["index"], b["type"],
                                          int(round(b["frequency"])),
                                          float(b["gain"]), float(b["q"]))
            except ValueError as exc:
                self._post("error", str(exc))
        if self._dev.supports_pregain and pregain is not None:
            self._dev.set_pregain(float(pregain), save=False)

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
        """Ask for a profile, then report the one the device is actually on.

        The asking and the getting are different things. A DAWN PRO2 on firmware 1.5
        accepts 0–4 and **silently ignores everything above**, so the old optimistic
        `self.slot = requested` had the app displaying profiles the DAC had refused —
        and once it had walked down to 4 there was no way back up, because the custom
        profile has no settable index at all. One extra read on an explicit click, and
        the number on screen can only ever be one the device agreed to.
        """
        if self._dev is None:
            return
        try:
            self._dev.set_active_eq_index(int(index), save=False)
        except Exception as exc:
            self._post("error", str(exc))
            return
        self._post("slot", self._slot_now())

    def _slot_now(self):
        got = self._dev.get_active_eq_index()
        return -1 if got is None else int(got)

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
            # Unsorted on purpose since 2.0: the order is the user's choice now, and
            # re-sorting a list of 9,000 dictionaries is far cheaper than re-fetching
            # 5 MB to change it.
            slim = [self._slim(r, product_uuid) for r in rows]
            self._post("hub_index", (slim, cached))
        except Exception as exc:  # noqa: BLE001 — any network/parse failure is a toast
            self._post("hub_error", "Couldn't reach the Moondrop library (%s)." % exc)
        finally:
            self._post("hub_busy", False)

    def prefetch(self, chunk, band_count):
        """Fetch and classify a handful of curves, and say what they turned out to be.

        Deliberately a *chunk* rather than "the whole list": one thread serves this
        queue, and a job that walked 400 presets would sit in front of the click that
        actually matters — somebody opening a preset they can already see. Small jobs,
        re-submitted by the bridge as each lands, let a `resolve` slip in between two
        of them.

        Nothing here is fatal. A curve that will not fetch or will not parse is left
        unclassified and the card simply carries no shape; the alternative is a
        network hiccup emptying a filter.
        """
        def fetch(item):
            uuid, file_ref = item
            try:
                bands, _dropped = mc.hub_preset_bands(file_ref, band_count)
            except Exception:                          # noqa: BLE001 — see docstring
                return None
            if not bands:
                return None
            return (uuid, shape_mod.classify(bands), _thumbnail(bands))

        # Concurrent, because every one of these is a few hundred bytes and several
        # hundred milliseconds of waiting for somebody else's CDN; done one after
        # another a chunk took long enough to watch. Four at a time is a deliberate
        # ceiling rather than a tuning knob: this is a third-party client, and getting
        # its user agent rate-limited would break the feature for everyone at once.
        with ThreadPoolExecutor(max_workers=PREFETCH_FETCHERS) as pool:
            out = [r for r in pool.map(fetch, chunk) if r]
        if out:
            self._post("hub_shapes", out)

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
    def _slim(row, product_uuid=""):
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
            # Carried from the index rather than looked up later. `hub_resolve_file`
            # answers the same question by scanning the whole 5 MB cache file, which
            # is fine for the one preset somebody clicked and is not fine at all for
            # the four hundred the prefetch is about to classify.
            "file": row.get("file") or "",
            # Uploaded *for* this device, as opposed to merely pooled with it.
            #
            # The server pools by sharedConfigGroupId, so a DAWN PRO2 query answers
            # with the whole family: 9,243 rows, of which 1,485 are DAWN PRO2 uploads
            # and 5,518 belong to a FreeDSP Pro. Each row carries both keys — the
            # lower-case `productuuid` is who it was uploaded for, the camelCase
            # `productUuid` is what we asked about — so telling the two apart costs one
            # comparison and no extra request. Neither key is a typo for the other.
            "own": str(row.get("productuuid", "")) == str(product_uuid),
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
            done = subprocess.run(argv, capture_output=True, text=True, env=mc.system_env(),
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


class AutoEqWorker(_Worker):
    """The AutoEQ catalogue, on a thread of its own.

    Separate from HubWorker even though both fetch curves over HTTPS: the community
    index is ~4 MB with no pagination, and a search that queued behind one would look
    frozen. They are also never both busy — the two sheets are mutually exclusive — so
    this thread is idle almost all of the time and costs nothing to keep.
    """

    def __init__(self, post):
        super().__init__("autoeqworker")
        self._post = post
        self.index = None

    def on_crash(self, exc):
        self._post("aeq_error", _net_message(exc))

    def load(self, query, refresh=False):
        self._post("aeq_busy", True)
        try:
            if self.index is None or refresh:
                self.index = autoeq.load_index(refresh=refresh)
            rows = autoeq.search(self.index, query, limit=AEQ_LIMIT)
        except Exception as exc:
            log.warning("AutoEQ catalogue: %s", exc)
            self._post("aeq_error", _net_message(exc))
            return
        finally:
            self._post("aeq_busy", False)
        self._post("aeq_rows", (rows, self.index.get("count", 0)))

    def prefetch(self, chunk, band_count):
        """Fit and classify a handful of corrections so the list can draw them.

        Same shape as the community prefetch and for the same reasons: small chunks so
        a click never queues behind the whole page, concurrent fetches because the wait
        is somebody else's server rather than the arithmetic, and silence for anything
        that will not load — a headphone with no drawable curve simply has no curve on
        its row.

        AutoEQ profiles are already cached to disk by `fetch_profile`, so this is a
        one-time cost per headphone and free on every visit after it.
        """
        if self.index is None:
            return

        def one(row):
            try:
                text = autoeq.fetch_profile(self.index, row)
                bands, _pre, _dropped, _warn = autoeq.fit(text, band_count)
            except Exception:                          # noqa: BLE001 — see docstring
                return None
            if not bands:
                return None
            return (aeq_key(row), shape_mod.classify(bands), _thumbnail(bands))

        with ThreadPoolExecutor(max_workers=PREFETCH_FETCHERS) as pool:
            out = [r for r in pool.map(one, chunk) if r]
        if out:
            self._post("aeq_shapes", out)

    def pick(self, row, band_count):
        self._post("aeq_busy", True)
        try:
            text = autoeq.fetch_profile(self.index, row)
            bands, preamp, dropped, warnings = autoeq.fit(text, band_count)
        except Exception as exc:
            log.warning("AutoEQ profile %s: %s", row.get("model"), exc)
            self._post("aeq_error", _net_message(exc))
            return
        finally:
            self._post("aeq_busy", False)
        self._post("aeq_profile", (row, bands, preamp, dropped, warnings))


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

    def fetch(self, update):
        """Download a package we must not install. Verifies, then hands back the
        path and the one command that installs it."""
        self._post("update_progress", (0, 0))
        try:
            path, command = updater.fetch(
                update, on_progress=lambda d, t: self._post("update_progress", (d, t)))
        except Exception as exc:
            log.error("fetching %s failed", update.get("version"), exc_info=True)
            self._post("update_error", str(exc))
            return
        log.info("downloaded %s to %s", update.get("version"), path)
        self._post("update_fetched", (path, command, ""))

    def fetch_and_install(self, update):
        """Download, verify, then ask the desktop for permission to install.

        Declining is an ordinary outcome and lands back on the "here is the command"
        state, with the file already downloaded — so saying no costs nothing but the
        keystroke.
        """
        self._post("update_progress", (0, 0))
        try:
            path, command = updater.fetch(
                update, on_progress=lambda d, t: self._post("update_progress", (d, t)))
        except Exception as exc:
            log.error("fetching %s failed", update.get("version"), exc_info=True)
            self._post("update_error", str(exc))
            return
        kind = update.get("install_kind") or updater.install_kind()
        self._post("update_authorising", (path, command))
        ok, message = updater.install_elevated(kind, path)
        log.info("elevated install of %s: %s (%s)", update.get("version"), ok, message)
        if ok:
            self._post("update_installed", update.get("version", ""))
        else:
            self._post("update_fetched", (path, command, message))

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
        # Which profile index the app's own curve lives at, learned the only way it can
        # be: a write to the custom PEQ store selects that store, so whatever the device
        # reports right after we have written *is* the custom one. Not hardcoded to 9 —
        # that is what one DAWN PRO2 on one firmware happened to say, and a constant
        # here would be a guess about every other device in the table.
        self.custom_slot = None
        # True while the DAC is playing one of its own built-in profiles. The bands on
        # screen are then that profile's, read back from the device, and they are not
        # ours to edit: the next write would select the custom store and the other seven
        # bands would snap to whatever it holds.
        self.on_builtin = False
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
        # Whether the opening screen has been shown for this run, by any route — a
        # first launch or a DAC that did not turn up. Either counts.
        self._greeted = self.welcome_open
        # Which screen is up, and what is under it. One object instead of the seven
        # booleans this used to be — see gui/nav.py for why.
        self.nav = nav_mod.Nav()
        # …and which of the library's four sources it is showing, when that is the
        # screen. Not persisted: it is set by whichever button opened the library, so
        # remembering it across runs would send you somewhere you did not click.
        self.lib_tab = LIB_COMMUNITY
        self.hub_busy = False
        self.hub_note = ""
        self.hub_query = ""
        self.hub_rows = []          # the full slim index
        self.hub_visible = []       # what the list is showing
        self.hub_sort = self.settings["hub_sort"]
        # Default to this DAC's own uploads. The pooled family is five sixths other
        # people's hardware, and a first-time visitor scrolling FreeDSP Pro presets
        # while looking for DAWN PRO2 ones has no way to know that is what happened.
        self.hub_own = self.settings["hub_own"]
        self.hub_matched = 0        # how many passed the filters, before the cap
        # Where the current curve came from, and the ring of what came before it.
        # `source` is None whenever nothing is known — which is not the same as
        # "hand-drawn", and must never be labelled as though it were.
        self.source = None
        self.history = tuning.load_history()
        self.hub_shape = ""         # the shape chip in force; "" is all of them
        self.hub_counts = {}        # label -> how many match, before the chip
        self._shape_of = {}         # uuid -> one of shapes.LABELS, once classified
        self._thumb_of = {}         # uuid -> its curve as an svg path
        self._to_classify = []      # what the prefetch has left to do
        self._chip_sig = None       # so the chip row is only rebuilt when it changes
        self._hist_sig = None       # likewise for the recent list
        self.preview = None         # the config being looked at, before it is applied

        # plot geometry, reported by the view on resize; until the first report we
        # have no pixels to sample against and draw nothing.
        self._plot_w = 0.0
        self._plot_h = 0.0
        self._drag = -1
        self._q_gen = 0
        self._grid_for = None
        self._dev_sig = object()   # never equal to a real pid, so the first push builds
        # Set by _push_curves; None means "nothing drawn yet, compute it".
        self._curve_sig = None
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
        # The one command that installs a package we fetched but must not install.
        self.update_command = ""

        # ── A/B, bypass, undo ──
        # True only while the compare control is held. Never persisted, never saved to
        # flash, and never allowed to become `self.bands` — see `compare_hold`.
        self.comparing = False
        # index -> the filter type a band had before it was bypassed. Kept beside the
        # band rather than in it, because `self.bands` is what gets exported and saved
        # and a band that is off should export as off rather than as a secret.
        self._muted = {}
        # Bounded, and holding whole states rather than deltas: a state is four floats
        # per band and there are eight bands, so forty of them is nothing, and an
        # inverse operation per edit type is a great deal to keep correct for no gain.
        self._history = []
        # What the last edit was, as (band, which knob). A fader dragged across thirty
        # frames is one gesture and must be one undo step, so a snapshot is taken when
        # this *changes* rather than on every call. Cleared by `_remember`, so anything
        # that is not a fine edit ends the run and the next nudge starts a new step.
        self._edit_mark = None
        # Which band is soloed, or -1. Its own store rather than sharing `_muted`,
        # so ending a solo puts back exactly what solo turned off and leaves a band
        # you had muted by hand still muted.
        self._solo = -1
        self._solo_muted = {}

        # ── the supported-device list and the walkthrough ──
        # -1 when the tour is not running. It points at real elements rather than
        # describing them, so the steps are numbered by what they highlight.
        self.tour_step = -1

        # ── what's new ──
        self.whatsnew_from = ""
        self.whatsnew_notes = []
        # Which version the panel is describing, and whether it is installed. The same
        # panel does both jobs: what you just got, and what a check is offering you.
        self.whatsnew_version = mc.__version__
        self.whatsnew_preview = False
        # True while a check the user clicked is in flight. A background check that
        # finds something must not throw a full-screen panel over whatever they were
        # doing; one they asked for is a question they are waiting on an answer to.
        self.update_asked = False

        # ── saved profiles ──
        self.prof_rows = []
        self.prof_name = ""
        self._prof_model = None
        self._prof_sig = None

        # ── AutoEQ ──
        self.aeq_busy = False
        self.aeq_note = ""
        self.aeq_query = ""
        self.aeq_found = []         # everything the search matched
        self.aeq_rows = []          # …after the measurer chip
        self.aeq_source = ""        # the measurer chip in force; "" is all of them
        self.aeq_counts = {}        # measurer -> how many of the matches are theirs
        self._aeq_shape_of = {}     # aeq_key -> shape label
        self._aeq_thumb_of = {}     # aeq_key -> svg path
        self._aeq_to_classify = []
        self._aeq_chip_sig = None
        self.aeq_total = 0
        self._aeq_model = None
        self._aeq_sig = None

        # Bumped by every toast; an expiry timer only fires if its stamp is still
        # current. See `toast`.
        self._toast_gen = 0
        # The device watch, started in `start()` and held so it keeps ticking.
        self._watch = None

        self.install_kind = updater.install_kind()
        self.install_note = updater.describe_install(self.install_kind)
        # Set once an updater helper is armed and waiting for this process to exit.
        # app.main() reads it after the loop returns; nothing else may write it.
        self.handing_over = False

        self.dev = DeviceWorker(self._post)
        self.hub = HubWorker(self._post)
        self.io = IoWorker(self._post)
        self.upd = UpdateWorker(self._post)
        self.aeq = AutoEqWorker(self._post)
        for w in (self.dev, self.hub, self.io, self.upd, self.aeq):
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
        window.reselect_custom = self.reselect_custom
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
        window.set_skin = self.set_skin
        window.dismiss_welcome = self.dismiss_welcome
        window.show_welcome = self.show_welcome
        window.toggle_community = self.toggle_community
        window.library_tab = self.library_tab
        window.community_search = self.community_search
        window.community_apply = self.community_apply
        window.community_reload = self.community_reload
        window.community_sort = self.community_sort
        window.community_own = self.community_own
        window.community_shape = self.community_shape
        window.set_motion = self.set_motion
        window.motion_labels = slint.ListModel(list(MOTION_LABELS))
        window.autoeq_source = self.autoeq_source
        window.set_switch = self.set_switch
        window.history_apply = self.history_apply
        window.history_keep = self.history_keep
        window.history_clear = self.history_clear
        window.preview_apply = self.preview_apply
        window.preview_close = self.preview_close
        window.dismiss_toast = lambda: (self.toast(""), self.push())
        window.toggle_band = self.toggle_band
        window.compare_hold = self.compare_hold
        window.toggle_devices = self.toggle_devices
        window.start_tour = self.start_tour
        window.tour_next = self.tour_next
        window.tour_end = self.tour_end
        window.step_gain = self.step_gain
        window.reset_band = self.reset_band
        window.solo_band = self.solo_band
        window.undo = self.undo
        window.nav_pop = self.nav_pop
        window.show_whatsnew = self.show_whatsnew
        window.preview_whatsnew = self.preview_whatsnew
        window.copy_diagnostics = self.copy_diagnostics
        window.copy_update_command = self.copy_update_command
        window.toggle_profiles = self.toggle_profiles
        window.profile_name_edited = self.profile_name_edited
        window.profile_save = self.profile_save
        window.profile_apply = self.profile_apply
        window.profile_delete = self.profile_delete
        window.toggle_autoeq = self.toggle_autoeq
        window.autoeq_search = self.autoeq_search
        window.autoeq_pick = self.autoeq_pick
        window.autoeq_reload = self.autoeq_reload
        window.check_update = lambda: self.check_update(True)
        window.install_update = self.install_update
        window.skip_update = self.skip_update
        window.restart_app = self.restart_app
        window.set_channel = self.set_channel
        window.set_auto_update = self.set_auto_update
        window.open_logs = self.open_logs
        window.open_notes = self.open_notes
        window.app_version = mc.__version__
        # The sheet used to print "~/.config/hub-moon/settings.json" as a literal,
        # which stopped being true on two of the three platforms.
        window.settings_path = "saved to " + settings_path()

        # The view is handed the finished outline rather than a name: it has no way to
        # look a name up, and giving it one would mean a second copy of the icon table.
        # Fixed strings built from fixed filters, so this is a one-off: nothing in
        # the guide depends on the window size or on what is connected.
        window.guide_pages = slint.ListModel(guide_mod.pages())
        # Built once: the labels never change, and handing the view a fresh model on
        # every push would rebuild the control under the pointer.
        window.hub_sorts = slint.ListModel([label for label, _key in HUB_SORTS])
        window.presets = slint.ListModel([
            {"name": name, "icon": ICON_PATHS.get(icon, "")}
            for name, icon, _b, _p in PRESETS
        ])

    # ── the device watch ──
    def _poll_device(self):
        """Ask the worker to look, unless looking would get in the way.

        Runs on the UI thread and does nothing there but decide. Skipped while a job
        is running or queued, so a poll can never land between a drag's writes, and
        skipped once the updater has taken over, when the process is on its way out.
        """
        if self.busy or self.handing_over or not self.dev.idle():
            return
        self.dev.submit(self.dev.probe)

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
            # `clean` is False exactly when this snapshot followed a batch write, and a
            # write to the custom PEQ store selects that store — so this is the one
            # moment the custom profile's own index can be observed, and it costs
            # nothing: `apply_bands` already re-reads to show what the DAC took.
            self._note_slot(learn=not clean)
            self.band_count = state["bandCount"]
            self.bands = [dict(b) for b in state["bands"]]
            # The device can hold bands but not their name, so the name is read back
            # from this machine and re-attached by fingerprint. `describe` decides
            # whether it still fits; nothing here assumes it does.
            self.source = tuning.load_state(self.product_id)
            if self._reverting:
                clean, self._reverting = True, False
            if clean:
                self.dirty = False
                self.toast("")
                if self.pristine is None:
                    self._snapshot()
        elif kind == "slot":
            # The device's answer, not the request. See DeviceWorker.set_slot.
            was = self.slot
            self.slot = int(payload)
            # A profile chosen live is unsaved state like any other, but only when the
            # device actually moved — a refused step must not mark anything dirty.
            if self.slot != was:
                self.dirty = True
            self._note_slot()
            self.push()
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
            # Land on the home screen rather than dropping straight into an editor
            # that is not editing anything. It names what was not found, offers the
            # supported-device list, and still has "Try the demo" on it — which is a
            # better first thirty seconds than a curve wired to nothing and a toast
            # that scrolls away.
            #
            # Only on the first miss of the session: a refresh that fails while
            # somebody is deliberately playing with the demo should stay where it is.
            if not self._greeted:
                self._greeted = True
                self.welcome_open = True
        elif kind == "appeared":
            # A DAC turned up between polls. This is the device chip's own job, done
            # without anybody having to know the chip is a button.
            log.info("a device appeared; reading it")
            self.dev.submit(self.dev.refresh)
        elif kind == "vanished":
            # An unplug is not a failed search. `no_device` opens the welcome screen
            # on the session's first miss, which is right when the app started with
            # nothing plugged in and wrong here — it would throw a full-screen
            # greeting over the work of somebody who just pulled a cable out and
            # knows perfectly well what they did.
            log.info("the device went away")
            self._greeted = True
            self._handle("no_device", "The DAC was unplugged.")
            return                      # the inner call already pushed
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
            self._filter_hub()
            self._hub_summary()
            if cached:
                self.hub_note += " · cached"
        elif kind == "hub_shapes":
            # Kept, because `push` runs on every state change and re-sampling 220
            # points per card each time would cost more than the fetch did. Drawn on
            # the worker — see `_thumbnail`.
            for uuid, label, thumb in payload:
                self._shape_of[uuid] = label
                self._thumb_of[uuid] = thumb
            self._filter_hub()
        elif kind == "hub_bands":
            self._adopt_hub_bands(*payload)
        elif kind == "aeq_busy":
            self.aeq_busy = bool(payload)
        elif kind == "aeq_error":
            self.aeq_busy = False
            self.aeq_note = str(payload)
        elif kind == "aeq_rows":
            rows, total = payload
            self.aeq_found = rows
            self.aeq_total = total
            self._filter_aeq()
            self.aeq_note = ("%d of %d headphones" % (len(self.aeq_rows), total)
                             if self.aeq_rows else "No headphone matches that.")
        elif kind == "aeq_shapes":
            for key, label, thumb in payload:
                self._aeq_shape_of[key] = label
                self._aeq_thumb_of[key] = thumb
            self._filter_aeq()
        elif kind == "aeq_profile":
            self._adopt_autoeq(*payload)
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
                if payload.get("rollback"):
                    self.update_note = ("Going back to the stable release. This is "
                                        "older than what you are running.")
                # Asked, and there is an answer: show what is in it. Not on a rollback
                # — "what's new in 1.1.0" is the wrong question when 1.1.0 is older
                # than what is running, and the notes would describe changes already
                # installed.
                if self.update_asked and not payload.get("rollback"):
                    self.preview_whatsnew()
            self.update_asked = False
        elif kind == "update_error":
            self.update_asked = False
            self.update_state = "idle"
            self.update_note = str(payload)
            self.update_progress = 0.0
        elif kind == "update_quiet":
            self.update_asked = False
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
        elif kind == "update_authorising":
            path, command = payload
            self.update_state = "authorising"
            self.update_progress = 1.0
            self.update_command = command
            self.update_note = "Waiting for permission to install…"
        elif kind == "update_fetched":
            path, command, why = payload
            self.update_state = "fetched"
            self.update_progress = 1.0
            self.update_command = command
            self.update_note = ("%s Saved to %s" % (why, path)) if why \
                else "Saved to %s" % path
        elif kind == "update_installed":
            self.update_state = "installed"
            self.update_progress = 1.0
            self.update_command = ""
            self.update_note = ("Hub Moon %s is installed. Restart to start using it."
                                % payload)
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
        self._apply_motion()
        self._check_whats_new()
        self.dev.submit(self.dev.refresh)
        # Held on the Bridge: dropping a Timer stops it, so a local would watch for
        # exactly as long as this method runs.
        if self.settings["watch_devices"]:
            self._watch = slint.Timer()
            self._watch.start(slint.TimerMode.Repeated, DEVICE_POLL, self._poll_device)
        if self.settings["start_fullscreen"]:
            # Not `full-screen: true` in the .slint — that is read before the window
            # exists and winit ignores it, verified on a bare Slint window as well as
            # on ours. The property only takes effect when it *changes* after the
            # window is up, so the change is made from a timer once the loop is
            # running rather than during construction.
            slint.Timer.single_shot(timedelta(milliseconds=200),
                                    lambda: setattr(self.win, "want_fullscreen", True))
        # The opening update check is quiet and cached: it only reaches the network
        # once a day, it never raises, and it says nothing at all unless there is
        # something newer than what is running. A check that announces "you are up to
        # date" on every launch is a notification nobody asked for.
        if self.settings["check_updates"]:
            self.upd.submit(self.upd.check, self.settings["channel"],
                            mc.__version__, False)

    # ── about, and what changed ──
    def diagnostics_text(self):
        """The facts a bug report needs, in the order someone would ask for them.

        This exists because of what the coverage table says: eleven of the twelve
        supported DACs and the whole of USB HID on Windows have never been exercised.
        Closing that gap depends on strangers reporting what happened on hardware
        nobody here owns, and the cheapest way to make a good report is a button that
        writes one.
        """
        import platform

        from . import diagnostics

        lines = [
            "Hub Moon %s" % mc.__version__,
            "install    %s" % self.install_kind,
            "channel    %s" % self.settings["channel"],
            "os         %s %s (%s)" % (platform.system(), platform.release(),
                                       platform.machine()),
            "python     %s" % platform.python_version(),
            "frozen     %s" % bool(getattr(sys, "frozen", False)),
            "device     %s" % (self.device_name if self.connected
                               else "none connected"),
        ]
        if self.connected:
            lines.append("firmware   %s" % (self.firmware or "unknown"))
            lines.append("product id 0x%04X · %d bands" % (self.product_id,
                                                           self.band_count))
        lines += [
            "config     %s" % mc.config_dir(),
            "cache      %s" % mc.cache_dir(),
            "log        %s" % diagnostics.log_path(),
        ]
        return "\n".join(lines)

    def copy_diagnostics(self):
        """Put the About text on the clipboard, using whatever the desktop has.

        Slint has no clipboard API, so this shells out — and if none of the usual
        programs is installed it says so rather than pretending it worked, because a
        silent no-op here means somebody pastes nothing into an issue.
        """
        self._to_clipboard(self.diagnostics_text(), "System info copied.")

    def copy_update_command(self):
        if self.update_command:
            self._to_clipboard(self.update_command, "Command copied.")

    def _to_clipboard(self, text, ok_message):
        cmds = {
            "win32": [["clip"]],
            "darwin": [["pbcopy"]],
        }.get(sys.platform, [["wl-copy"], ["xclip", "-selection", "clipboard"],
                             ["xsel", "--clipboard", "--input"]])
        for argv in cmds:
            if not shutil.which(argv[0]):
                continue
            try:
                kw = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} \
                    if sys.platform == "win32" else {}
                subprocess.run(argv, input=text, text=True, timeout=10, env=mc.system_env(),
                               check=True, **kw)
                self.toast(ok_message, False)
                self.push()
                return
            except (OSError, subprocess.SubprocessError):
                continue
        log.warning("no clipboard tool found; nothing copied")
        self.toast("No clipboard tool found — the text is in the log.", True)
        log.info("clipboard text was:\n%s", text)
        self.push()

    def _check_whats_new(self):
        """Show what changed, once, the first time a new version runs.

        Not shown on a first install — there is nothing to have changed *from*, and
        the welcome screen is already doing that job. The notes come from the cached
        manifest, which is the one the updater downloaded before installing this
        version, so this works with no network.
        """
        was = self.settings.get("last_run_version") or ""
        if was == mc.__version__:
            return
        self.settings["last_run_version"] = mc.__version__
        save_settings(self.settings)
        if not was:
            return                              # fresh install; welcome covers it
        if not updater.is_newer(mc.__version__, was):
            return                              # a downgrade says nothing useful
        self.whatsnew_from = was
        self.whatsnew_version = mc.__version__
        self.whatsnew_preview = False
        self.whatsnew_notes = updater.release_notes(self.settings["channel"])
        # One opening screen, not two — and nothing under it, because an update
        # notice arrives before you have been anywhere.
        self.nav.reset(nav_mod.WHATSNEW)
        self.welcome_open = False
        log.info("updated from %s to %s; showing what changed", was, mc.__version__)

    def show_whatsnew(self):
        """Open it on request, for the version that is running.

        The notes come from the cached manifest, and failing that from the ones
        compiled into this build — so this answers even on a copy that never came from
        a release. When there are genuinely none it still opens and says so, because a
        button that sometimes does nothing is worse than one that always answers.
        """
        # `last_run_version` is set to the running version the moment the panel is
        # shown after an update, so by the time anybody opens it from Settings it says
        # what they are already running: "Hub Moon 1.2.0b2 — from 1.2.0b2". An empty
        # string means the chip is not drawn at all, which is the truth here.
        was = self.settings.get("last_run_version") or ""
        self.whatsnew_from = "" if was == mc.__version__ else was
        self.whatsnew_version = mc.__version__
        self.whatsnew_preview = False
        self.whatsnew_notes = updater.release_notes(self.settings["channel"])
        self.nav.open(nav_mod.WHATSNEW)
        self.push()

    def preview_whatsnew(self):
        """What is in the version being offered — before installing it.

        The notes travel in the manifest the check just read, so this costs no extra
        request. A build whose release carried none falls back to what that version
        compiled in, which is only reachable for a version already installed — so an
        empty list here is shown as an empty list, not papered over with this build's
        own notes.
        """
        found = self.update_found
        if not found:
            return
        self.whatsnew_from = mc.__version__
        self.whatsnew_version = found.get("version", "")
        self.whatsnew_preview = True
        self.whatsnew_notes = list(found.get("notes") or [])
        self.nav.open(nav_mod.WHATSNEW)
        self.push()

    # ── saved profiles ──
    def toggle_profiles(self):
        self.open_library(LIB_SAVED)

    def _reload_profiles(self):
        self.prof_rows = prof.load_all()

    def profile_name_edited(self, text):
        self.prof_name = str(text or "")

    def profile_save(self):
        """Keep the curve that is on screen, under a name.

        What is saved is the *edited* state, not what the DAC last confirmed — the
        whole point is to keep something you are in the middle of making. It does not
        touch flash and it does not touch the device.
        """
        name = self.prof_name.strip()
        if not name:
            self.toast("Give the profile a name first.", True)
            self.push()
            return
        try:
            prof.save(name, self.bands, self.pregain,
                      global_gain=self.global_gain,
                      device=self.device_name if self.connected else "",
                      slot=self.slot)
        except OSError as exc:
            log.error("saving profile %r failed", name, exc_info=True)
            self.toast("Could not save: %s" % exc, True)
            self.push()
            return
        log.info("saved profile %r", name)
        self.prof_name = ""
        self._reload_profiles()
        self.toast("Saved “%s”." % name, False)
        self.push()

    def profile_apply(self, name):
        """Load a saved curve onto the current device.

        A profile saved on a device with more bands than this one has is not refused —
        it is fitted, and the toast says how many were dropped. Refusing would make a
        library useless the moment somebody owns two DACs.
        """
        row = next((r for r in self.prof_rows if r["name"] == str(name)), None)
        if not row:
            return
        bands, dropped = [], 0
        for n, f in enumerate(row["bands"]):
            if n >= self.band_count:
                dropped += 1
                continue
            ftype, freq, gain, q = clamp_band(f["type"], f["frequency"],
                                              f["gain"], f["q"])
            bands.append({"index": n, "type": ftype, "frequency": freq,
                          "gain": gain, "q": q})
        while len(bands) < self.band_count:
            bands.append(_disabled_band(len(bands)))

        self._replace_bands(bands, float(row["pregain"]),
                            tuning.source("profile", row["name"], bands,
                                          ident=row.get("path", ""),
                                          pregain=row["pregain"]))
        if row.get("global_gain") is not None:
            self.set_global_gain(float(row["global_gain"]))
        self.active_preset = ""
        self.nav.reset()
        note = "Loaded “%s”." % row["name"]
        if dropped:
            note += " %d band%s past this device's %d were dropped." % (
                dropped, "" if dropped == 1 else "s", self.band_count)
        self.toast(note, False)
        self.push()

    def _apply_motion(self):
        """Push the motion scale into the theme.

        Slint globals are writable from Python, so one assignment reaches every
        animation in the app — none of which has to know the setting exists. Guarded
        because a window built from a .slint without the global (an older interface
        file, a test double) must not take the app down over an animation preference.
        """
        try:
            self.win.Theme.motion = MOTION_SCALES[self.settings["motion"]]
        except Exception:                              # noqa: BLE001 — see docstring
            log.debug("could not set the motion scale", exc_info=True)

    def set_motion(self, index):
        self.settings["motion"] = max(0, min(int(index), len(MOTION_SCALES) - 1))
        save_settings(self.settings)
        self._apply_motion()
        self.push()

    # ── the 2.0 settings ──
    SWITCHES = ("start_fullscreen", "watch_devices", "hub_prefetch", "keep_history")

    def set_switch(self, key, on):
        """Flip one of the plain on/off settings and act on it immediately.

        A preference that only takes effect after a restart is a preference people
        report as broken, so each of these is applied here as well as saved — except
        fullscreen, which is a start-up choice by definition and says so in the UI.
        """
        key = str(key)
        if key not in self.SWITCHES:
            return
        self.settings[key] = bool(on)
        save_settings(self.settings)
        if key == "watch_devices":
            if on and self._watch is None:
                self._watch = slint.Timer()
                self._watch.start(slint.TimerMode.Repeated, DEVICE_POLL,
                                  self._poll_device)
            elif on:
                self._watch.start(slint.TimerMode.Repeated, DEVICE_POLL,
                                  self._poll_device)
            elif self._watch is not None:
                self._watch.stop()
        elif key == "hub_prefetch" and on:
            self._pump_classification()          # pick up where it left off
        self.push()

    # ── history: what has been applied lately ──
    def history_apply(self, fingerprint):
        """Put a recent curve back on the device.

        The bands travel in the entry itself rather than being re-fetched, so this
        works with no network and on a config that has since been deleted from the
        library — and it is what makes flipping between two tunings cheap. The entry
        is re-used as the source, so coming back to a curve does not invent a new
        identity for it; `remember` simply moves the one entry to the top.
        """
        entry = next((e for e in self.history
                      if e.get("fingerprint") == str(fingerprint)), None)
        if not entry:
            return
        bands, dropped = [], 0
        for n, f in enumerate(entry.get("bands") or []):
            if n >= self.band_count:
                dropped += 1
                continue
            ftype, freq, gain, q = clamp_band(f["type"], f["frequency"],
                                              f["gain"], f["q"])
            bands.append({"index": n, "type": ftype, "frequency": freq,
                          "gain": gain, "q": q})
        while len(bands) < self.band_count:
            bands.append(_disabled_band(len(bands)))
        self._replace_bands(bands, entry.get("pregain"), dict(entry))
        self.active_preset = ""
        self.nav.reset()
        note = "Back to “%s”." % (entry.get("name") or "that curve")
        if dropped:
            note += " %d band%s past this device's %d were dropped." % (
                dropped, "" if dropped == 1 else "s", self.band_count)
        self.toast(note, False)
        self.push()

    def history_keep(self, fingerprint):
        """Promote a history entry to a profile — the one bridge between the two.

        History is capped and rolls over on its own; a profile is permanent and only
        ever written by the person using the app. This is how something crosses from
        the first to the second, and it copies rather than moves: the entry stays in
        history until it ages out normally.
        """
        entry = next((e for e in self.history
                      if e.get("fingerprint") == str(fingerprint)), None)
        if not entry:
            return
        name = entry.get("name") or "Kept curve"
        try:
            prof.save(name, entry.get("bands") or [], entry.get("pregain") or 0.0,
                      device=self.device_name if self.connected else "",
                      slot=self.slot if self.slot >= 0 else None)
        except OSError as exc:
            self.toast("Could not save that profile (%s)." % exc, True)
            self.push()
            return
        self._reload_profiles()
        self.toast("Kept “%s” as a profile." % name, False)
        self.push()

    def history_clear(self):
        tuning.forget_all()
        self.history = []
        self.toast("Cleared the recent list.", False)
        self.push()

    def profile_delete(self, name):
        if prof.delete(str(name)):
            log.info("deleted profile %r", name)
            self._reload_profiles()
            self.toast("Deleted “%s”." % name, False)
        else:
            self.toast("Could not delete “%s”." % name, True)
        self.push()

    # ── AutoEQ ──
    def toggle_autoeq(self):
        self.open_library(LIB_HEADPHONES)

    def autoeq_search(self, text):
        self.aeq_query = str(text or "")
        self.aeq.submit(self.aeq.load, self.aeq_query, False)
        self.push()

    def autoeq_reload(self):
        self.aeq_note = "Refetching the catalogue…"
        self.aeq.submit(self.aeq.load, self.aeq_query, True)
        self.push()

    def autoeq_pick(self, model, source, form):
        """Fetch one headphone's correction and hold it for review — the same preview
        the community library uses, because nothing should reach the DAC unseen."""
        self.aeq.submit(self.aeq.pick,
                        {"model": str(model), "source": str(source), "form": str(form)},
                        self.band_count)
        self.push()

    def _adopt_autoeq(self, row, raw, preamp, dropped, warnings):
        """An AutoEQ correction, clamped onto this device and held for review.

        Unlike a community config, this one arrives with a pre-gain of its own —
        AutoEQ computes the headroom its curve needs, and that figure is better than
        anything derived here, so it is kept. What is *not* kept quiet is the fit:
        every AutoEQ profile has ten filters and this DAC has eight, so two are always
        left behind and the note says which.
        """
        bands = []
        for n in range(self.band_count):
            if n < len(raw):
                ftype, freq, gain, q = clamp_band(raw[n]["type"], raw[n]["frequency"],
                                                  raw[n]["gain"], raw[n]["q"])
            else:
                ftype, freq, gain, q = "disabled", 1000, 0.0, 1.0
            bands.append({"index": n, "type": ftype, "frequency": freq,
                          "gain": gain, "q": q})

        pre = round(_clamp(float(preamp), PREGAIN_MIN, PREGAIN_MAX), 1)
        used = sum(1 for b in bands if b["type"] != "disabled")
        note = "%d of %d bands · pre-gain %+.1f dB (AutoEQ's own)" % (
            used, self.band_count, pre)
        if dropped:
            note += " · left out: " + ", ".join(
                "%.0f Hz %+.1f dB" % (d["frequency"], d["gain"]) for d in dropped)
        for w in warnings:
            note += " · " + w

        self.preview = {
            # Which library this came from, so the applied curve can be labelled with
            # it later. The preview dialog serves both and looks identical for each.
            "kind": "autoeq",
            "uuid": "",
            "title": row["model"],
            # The dialog renders this as "by <author>", so it must not say "measured
            # by" itself — that read "by measured by oratory1990".
            "author": "%s · %s" % (row["source"], row["form"]),
            "desc": "AutoEQ correction, fitted to this device's %d bands. %s"
                    % (self.band_count, autoeq.CREDIT),
            "bands": bands,
            "pregain": pre,
            "note": note,
        }
        self.aeq_note = ""

    # ── updates ──
    def check_update(self, force=False):
        """Ask now. `force` is what the button does: bypass the cache, report a failure
        instead of swallowing it, and — because it is a question somebody is waiting on
        — show what the answer contains rather than only that there is one."""
        self.update_asked = bool(force)
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
        # Reached from the preview panel as well as from Settings. Leaving that open
        # over a progress bar it cannot show would hide the thing it just started —
        # and `open` on a screen already in the stack goes back to it, so this lands
        # on the Settings that the preview was opened from.
        self.nav.open(nav_mod.SETTINGS)
        self.update_state = "downloading"
        self.update_progress = 0.0
        self.update_command = ""
        if found.get("can_install"):
            self.update_note = "Starting download…"
            self.upd.submit(self.upd.install, found)
        elif found.get("can_elevate"):
            # Fetch it, then let the desktop's own polkit agent ask for the password.
            # Nothing about it passes through this process.
            self.update_note = "Downloading the package…"
            self.upd.submit(self.upd.fetch_and_install, found)
        elif found.get("can_fetch"):
            # No agent to ask with, so the download is all we can usefully do.
            self.update_note = "Downloading the package…"
            self.upd.submit(self.upd.fetch, found)
        else:
            self.update_state = "available"
            self.toast("This build updates with:  %s" % found.get("hint", ""), False)
        self.push()

    def restart_app(self):
        """Relaunch, once this process has actually exited.

        Needed after an install replaced the files under us: what is running is the
        old code, still perfectly alive in memory.
        """
        try:
            updater.relaunch()
        except Exception:
            log.error("could not arm the relaunch", exc_info=True)
            self.toast("Close and reopen Hub Moon to finish.", False)
            self.push()
            return
        self.handing_over = True
        log.info("restarting after an update")
        slint.Timer.single_shot(timedelta(milliseconds=250), slint.quit_event_loop)

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

    # ── the walkthrough ──
    #
    # Five stops, in the order somebody meets them going down the window. Each one
    # names a thing that is genuinely non-obvious rather than describing what is
    # already written on the control: a tour that says "this is the preset row" for
    # a row labelled PRESETS has spent somebody's attention and given nothing back.
    TOUR = [
        ("Headroom lives here",
         "The slot is the DAC's own EQ profile — the bands you edit are written to "
         "it, so moving the device off it means you stop hearing your own work. "
         "Pre-gain is what stops a boosted curve clipping, and match sets it to "
         "exactly what your curve needs and no more."),
        ("Somewhere to start",
         "These are starting points, not the DAC's own presets — they load a curve "
         "you then edit. Nothing is written to flash until you say so, so trying one "
         "costs nothing."),
        ("Two curves, and the dashed one is the truth",
         "Drag a numbered handle to move that band, scroll anywhere to widen or "
         "narrow it. The solid line is what the EQ does; the dashed one is what "
         "actually leaves the DAC once pre-gain is paid. While the dashed line is "
         "above 0 dB, loud passages clip."),
        ("The same eight bands, as numbers",
         "Filter type, a gain fader, and steppers for frequency and Q. Hover the "
         "slot number to mute a band and hear what it was doing — and every one of "
         "these has a key, listed under How to tune."),
        ("Nothing here is permanent",
         "Edits go live to the DSP so you hear them as you move, and vanish when you "
         "unplug. Compare is hold-to-bypass. Undo steps back one move; revert goes "
         "all the way to what flash holds; save to flash is the only thing that "
         "keeps a curve."),
    ]

    def start_tour(self):
        """Walk the window, not a slideshow of it."""
        self.tour_step = 0
        self.welcome_open = False
        # The tour points at the window itself, so the window has to be what you are
        # looking at.
        self.nav.reset()
        self.push()

    def tour_next(self):
        # A stray advance after the tour has ended must not start it again. The
        # overlay is gone by then, so this would run the whole thing with nothing on
        # screen to click and no way to stop it.
        if self.tour_step < 0:
            return
        self.tour_step += 1
        if self.tour_step >= len(self.TOUR):
            self.tour_end()
            return
        self.push()

    def tour_end(self):
        self.tour_step = -1
        # Seeing the tour counts as having been welcomed. Somebody who has just been
        # walked through the window does not need the opening screen next launch.
        if not self.settings.get("seen_welcome"):
            self.settings["seen_welcome"] = True
            save_settings(self.settings)
        self.push()

    # ── the supported-device list ──
    def toggle_devices(self):
        self.nav.toggle(nav_mod.DEVICES)
        self.push()

    # ── A/B, bypass, undo ──
    HISTORY_MAX = 40

    def _state(self):
        return {"bands": [dict(b) for b in self.bands],
                "pregain": self.pregain,
                "global_gain": self.global_gain,
                "active_preset": self.active_preset,
                "muted": dict(self._muted)}

    def _remember(self):
        """Snapshot before a change. Called by the things that replace the curve
        wholesale — a preset, an import, an AutoEQ fit, a profile, a bypass — and by
        the *start* of a drag rather than every frame of one, so one gesture is one
        undo step instead of two hundred."""
        self._edit_mark = None
        state = self._state()
        if self._history and self._history[-1]["bands"] == state["bands"] \
                and self._history[-1]["pregain"] == state["pregain"]:
            return
        self._history.append(state)
        del self._history[:-self.HISTORY_MAX]

    def can_undo(self):
        return bool(self._history)

    def undo(self):
        """Back one step. Distinct from `revert`, which goes all the way back to what
        flash holds — this is for the move you just wish you had not made."""
        # Skip steps that turn out to have changed nothing. Whether a snapshot was
        # worth taking is only knowable *after* the action it preceded — applying the
        # preset that is already loaded records a step and then does not move — and a
        # press of undo that visibly does nothing is worse than no undo at all.
        here = [dict(b) for b in self.bands]
        state = None
        while self._history:
            candidate = self._history.pop()
            if candidate["bands"] != here or candidate["pregain"] != self.pregain:
                state = candidate
                break
        if state is None:
            self.toast("Nothing to undo.", False)
            self.push()
            return
        self.bands = [dict(b) for b in state["bands"]]
        self.pregain = state["pregain"]
        self.global_gain = state["global_gain"]
        self.active_preset = state["active_preset"]
        self._muted = dict(state["muted"])
        self.dirty = True
        if self.connected:
            self.dev.submit(self.dev.apply_bands, [dict(b) for b in self.bands],
                            self.pregain, self.global_gain)
        self.push()

    def toggle_band(self, index):
        """Mute one band and hear what it was contributing.

        Written to the DAC as the `disabled` filter type, which the firmware treats as
        a pass-through — so this is genuinely the curve without that band, not the
        curve with one band flattened by a gain of zero. Its type comes back from
        `_muted` when you switch it on again.
        """
        b = self._band(int(index))
        if b is None:
            return
        self._remember()
        idx = int(index)
        if b["type"] == "disabled":
            b["type"] = self._muted.pop(idx, "peaking")
        else:
            self._muted[idx] = b["type"]
            b["type"] = "disabled"
        self.selected = idx
        self.dirty = True
        self.active_preset = ""
        if self.connected:
            self.dev.submit(self.dev.write_band, idx, b["type"],
                            b["frequency"], b["gain"], b["q"])
        self.push()

    def compare_hold(self, on):
        """Hold to hear the headphone without any of it. Release to hear it back.

        Pre-gain goes to 0 for the duration, and that is the whole reason this is
        trustworthy. A curve with a +6 dB peak carries -6 dB of pre-gain, so leaving
        that in place would compare your tuning against a flat signal six decibels
        quieter — and louder always wins a blind comparison. Both sides now peak at
        the same place, so what you are judging is tone.

        `self.bands` is not touched. The DAC is written to directly and told to put
        it back on release; nothing here sets `dirty` and nothing reaches flash.
        """
        on = bool(on)
        if on == self.comparing:
            return
        self.comparing = on
        if not self.connected:
            # Nothing to hear, so say so rather than lighting the control up as if
            # something happened.
            self.comparing = False
            self.toast("Connect a DAC to compare.", False)
            self.push()
            return
        if on:
            flat = [dict(b, type="disabled") for b in self.bands]
            self.dev.submit(self.dev.audition, flat, 0.0)
        else:
            self.dev.submit(self.dev.audition, [dict(b) for b in self.bands],
                            self.pregain)
        self.push()

    # ── band edits ──
    def _band(self, index):
        for b in self.bands:
            if b["index"] == index:
                return b
        return None

    def _set_band(self, index, *, ftype=None, freq=None, gain=None, q=None,
                  write=False, knob=None):
        """`knob` names which control is being moved, and is what makes a fine edit
        undoable without making it thirty undos.

        A snapshot is taken when (band, knob) changes — so a fader dragged across many
        frames, or a stepper pressed five times, collapses to the one step somebody
        would expect. `None` means the caller has already taken its own snapshot: a
        graph drag does that when the pointer goes down, which is the same idea with a
        real gesture boundary to hang it on.
        """
        b = self._band(index)
        if b is None:
            return
        if knob is not None and self._edit_mark != (index, knob):
            self._remember()                      # clears the mark
            self._edit_mark = (index, knob)
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
        self._set_band(index, ftype=ftype, freq=freq, gain=gain, q=q, write=True, knob="set")

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

    def step_gain(self, index, direction, coarse=False):
        """Nudge one band's gain. The keyboard's half of the fader.

        0.5 dB a press, 2 dB with shift. Half a decibel because that is about the
        smallest move worth making — the guide's own advice is that ±1 dB is audible
        and ±3 dB is a lot — and a step small enough to be inaudible would just mean
        holding the key down.
        """
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        step = (2.0 if coarse else 0.5) * (1 if direction > 0 else -1)
        self._set_band(int(index), gain=round(b["gain"] + step, 1), write=True, knob="gain")

    def reset_band(self, index):
        """Back to flat, keeping the type, frequency and Q. What you want after a
        move that did not work — not the whole band thrown away."""
        b = self._band(int(index))
        if b is None or b["gain"] == 0.0:
            return
        self._remember()
        self.selected = int(index)
        self._set_band(int(index), gain=0.0, write=True)

    def solo_band(self, index):
        """Hear one band and nothing else.

        The counterpart to mute, and the faster question most of the time: "what is
        this one doing" beats "what is everything except this one doing" when you are
        hunting for the band that owns a problem.
        """
        idx = int(index)
        b = self._band(idx)
        if b is None:
            return
        self._remember()
        if self._solo == idx:
            for other in self.bands:
                if other["index"] in self._solo_muted:
                    other["type"] = self._solo_muted.pop(other["index"])
            self._solo = -1
        else:
            # Ending one solo and starting another in a single step, rather than
            # making the user turn the first one off first.
            for other in self.bands:
                if other["index"] in self._solo_muted:
                    other["type"] = self._solo_muted.pop(other["index"])
            if b["type"] == "disabled":
                b["type"] = self._muted.pop(idx, "peaking")
            for other in self.bands:
                if other["index"] == idx or other["type"] == "disabled":
                    continue
                self._solo_muted[other["index"]] = other["type"]
                other["type"] = "disabled"
            self._solo = idx
        self.selected = idx
        self.dirty = True
        self.active_preset = ""
        if self.connected:
            self.dev.submit(self.dev.apply_bands, [dict(x) for x in self.bands],
                            self.pregain, self.global_gain)
        self.push()

    def set_band_gain(self, index, gain):
        self.selected = int(index)
        self._set_band(int(index), gain=gain, knob="gain")

    def step_freq(self, index, direction):
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        # A sixth of an octave per press: fine enough to place a notch, coarse enough
        # that walking from 20 Hz to 20 kHz does not take all afternoon.
        factor = 1.06 if direction > 0 else 1 / 1.06
        self._set_band(int(index), freq=b["frequency"] * factor, write=True, knob="freq")

    def step_q(self, index, direction):
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        self._set_band(int(index), q=b["q"] + (0.1 if direction > 0 else -0.1), write=True, knob="q")

    def cycle_type(self, index, direction):
        b = self._band(int(index))
        if b is None:
            return
        self.selected = int(index)
        n = FILTER_ORDER.index(b["type"]) if b["type"] in FILTER_ORDER else 0
        n = (n + int(direction)) % len(FILTER_ORDER)
        self._set_band(int(index), ftype=FILTER_ORDER[n], write=True, knob="type")

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
            # Snapshot on the way in, not on every move: a drag is one thing the user
            # did, and two hundred undo steps for one gesture is not an undo history.
            self._remember()
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
        self._set_band(self.selected, q=b["q"] * (1.12 if delta > 0 else 0.89), knob="q")
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

    def _note_slot(self, learn=False):
        """Are we on our curve, or on one of the DAC's own presets?

        Learned rather than declared. `learn` is set only for a reading taken straight
        after this app wrote bands, and since writing the custom PEQ store is what
        selects it, that reading *is* the custom index by definition. Never guessed from
        a plain refresh: a user who cycled to a built-in with the volume buttons and then
        launched the app would have that built-in adopted as "mine", which is the exact
        confusion this is here to clear up.

        Remembered per product, because it is a fact about the hardware and not about
        this session — so the answer survives a relaunch, and the app can say "that is
        the DAC's preset, not your curve" the first time you look at it.
        """
        if learn and self.slot >= 0:
            self.custom_slot = self.slot
            known = dict(self.settings.get("custom_slots") or {})
            if known.get(str(self.product_id)) != self.slot:
                known[str(self.product_id)] = self.slot
                self.settings["custom_slots"] = known
                save_settings(self.settings)
                log.info("custom EQ profile on 0x%04X is index %d",
                         self.product_id, self.slot)
        if self.custom_slot is None:
            self.custom_slot = (self.settings.get("custom_slots") or {}).get(
                str(self.product_id))
        self.on_builtin = (self.custom_slot is not None and self.slot >= 0
                           and self.slot != self.custom_slot)

    def slot_note(self):
        """What the profile row says under its number."""
        if not self.connected:
            return "no device"
        if self.slot < 0:
            return "not reported"
        if self.on_builtin:
            return "the DAC's own preset"
        if self.custom_slot is None:
            # Honest about not knowing. It is learned the first time anything is
            # applied, and claiming ownership before that would be a guess.
            return "as reported"
        return "your curve"

    def step_slot(self, direction):
        """The DAC's active EQ profile.

        Two things worth knowing, both measured. On a DAWN PRO2 (firmware 1.5) this
        reads 9 whether the EQ is on or off, so it is *not* a proxy for "is the EQ
        engaged". And the device accepts 0–4 only, silently ignoring anything above —
        which is why nothing is assumed here: the request goes out, and the worker
        reports back whichever profile the DAC is actually on.

        Stepping off the custom profile means the bands below stop being what you hear.
        Stepping back on cannot be done by index at all — see `reselect_custom`.
        """
        if not self.connected or self.slot < 0:
            return
        want = self.slot + int(direction)
        if want < 0:
            return
        self.dev.submit(self.dev.set_slot, want)

    def reselect_custom(self):
        """Back to your own curve from one of the DAC's presets.

        Not a profile change — a rewrite. The custom profile has no settable index, and
        writing the custom PEQ store is what selects it.

        **Not the bands on screen.** While the DAC is on one of its own presets, those
        bands *are* that preset's, read back from the device — so writing them would put
        the DAC's preset into your custom profile and call it yours, which is losing the
        curve rather than restoring it. The first version of this did exactly that, and
        the DAWN PRO2 came back holding −6 dB at 200 Hz.

        What gets written is what this machine last recorded applying to this device. If
        there is no such record there is nothing to restore: the custom profile cannot be
        read without first selecting it, and selecting it means writing — so the honest
        answer is to say so and let the user apply something.
        """
        if not self.connected or not self.on_builtin:
            return
        src = tuning.load_state(self.product_id) or {}
        bands = [dict(b) for b in (src.get("bands") or [])]
        if len(bands) != self.band_count:
            self.toast("No curve saved for this DAC on this computer — apply a preset "
                       "or a saved profile and it becomes the custom profile again.",
                       True)
            return
        self._replace_bands(bands, src.get("pregain"), src)
        self.toast("Back to “%s”." % (src.get("name") or "your curve"), False)
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
        self._replace_bands(bands, pre,
                            tuning.source("preset", name, bands, ident=str(i),
                                          pregain=pre))
        self.active_preset = name
        self.toast("Applied %s." % name, False)
        self.push()

    def _replace_bands(self, bands, pregain, source=None):
        # Every wholesale change lands here — preset, import, community config,
        # AutoEQ fit, saved profile — so this is the one place undo has to be told,
        # and the one place that knows what the curve about to be written *is*.
        self._remember()
        # Recorded before the write rather than after: what is on the device from here
        # on is this curve, whether or not the write itself reports back.
        self.source = source
        tuning.save_state(self.product_id, source)
        if source and self.settings["keep_history"]:
            self.history = tuning.remember(source)
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
        self._replace_bands(bands, None if pre is None else float(pre),
                            tuning.source("import", filename, bands,
                                          pregain=pre or 0.0))
        if "global_gain" in data:
            self.set_global_gain(float(data["global_gain"]))
        self.active_preset = ""
        self.toast("Imported %s." % filename, False)

    # ── community library ──
    def toggle_community(self):
        self.open_library(LIB_COMMUNITY)

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
        """Device filter, then search, then shape, then order. All local — the index
        is already on disk, and nothing here goes near the network."""
        rows = self.hub_rows
        if self.hub_own:
            rows = [r for r in rows if r["own"]]
        q = self.hub_query.strip().lower()
        if q:
            # The shape counts as text. Typing "bass" should find the bass-boosted
            # curves whose authors never used the word — most of this library is
            # titled in Chinese, and a substring search over `title` alone is no help
            # at all to somebody who cannot type it. Only for rows whose curve has
            # come down: an unclassified row is not silently excluded, it simply has
            # one fewer field to match on, the same as one with an empty description.
            rows = [r for r in rows
                    if q in r["title"].lower() or q in r["author"].lower()
                    or q in r["desc"].lower()
                    or q in self._shape_of.get(r["uuid"], "").lower()]
        # Counted before the shape filter is applied, or picking a chip would empty
        # every other chip's count and there would be no way back.
        self.hub_counts = {}
        for r in rows:
            label = self._shape_of.get(r["uuid"])
            if label:
                self.hub_counts[label] = self.hub_counts.get(label, 0) + 1
        if self.hub_shape:
            rows = [r for r in rows
                    if self._shape_of.get(r["uuid"]) == self.hub_shape]
        rows = sorted(rows, key=HUB_SORTS[self.hub_sort][1], reverse=True)
        self.hub_matched = len(rows)
        self.hub_visible = rows[:HubWorker.CAP]
        self._queue_classification()
        # Pumped from here rather than from the one place a chunk lands, or changing
        # the filter would queue work that nothing ever picked up: the prefetch would
        # stop for good the first time it ran dry.
        self._pump_classification()

    def _queue_classification(self):
        """Line up the curves for what is on screen, nearest the top first.

        Only what the user can actually reach: the whole family is 9,243 presets and
        fetching all of them to answer a question nobody asked would be both slow and
        rude to somebody else's CDN. What is visible is at most `CAP`, they are cached
        permanently once fetched, and the second visit classifies nothing at all.
        """
        self._to_classify = [(r["uuid"], r["file"]) for r in self.hub_visible
                             if r["uuid"] not in self._shape_of and r.get("file")]

    def _pump_classification(self):
        """Hand the next chunk to the worker, if there is one and anyone is looking."""
        if (not self.showing(LIB_COMMUNITY) or not self._to_classify
                or not self.settings["hub_prefetch"]):
            return
        chunk, self._to_classify = (self._to_classify[:PREFETCH_CHUNK],
                                    self._to_classify[PREFETCH_CHUNK:])
        self.hub.submit(self.hub.prefetch, chunk, self.band_count)

    def _filter_aeq(self):
        """Apply the measurer chip and count what each measurer has to offer.

        Counted before the chip is applied, for the same reason the community counts
        are: picking one must not zero every other chip and leave no way back.
        """
        self.aeq_counts = {}
        for r in self.aeq_found:
            src = r.get("source", "")
            self.aeq_counts[src] = self.aeq_counts.get(src, 0) + 1
        rows = self.aeq_found
        if self.aeq_source:
            rows = [r for r in rows if r.get("source") == self.aeq_source]
        self.aeq_rows = rows
        self._aeq_to_classify = [r for r in rows
                                 if aeq_key(r) not in self._aeq_shape_of]
        self._pump_aeq()

    def _pump_aeq(self):
        if (not self.showing(LIB_HEADPHONES) or not self._aeq_to_classify
                or not self.settings["hub_prefetch"]):
            return
        chunk, self._aeq_to_classify = (self._aeq_to_classify[:PREFETCH_CHUNK],
                                        self._aeq_to_classify[PREFETCH_CHUNK:])
        self.aeq.submit(self.aeq.prefetch, chunk, self.band_count)

    def autoeq_source(self, source):
        """Filter by who measured it. Clicking the chip in force clears it."""
        want = str(source)
        self.aeq_source = "" if want == self.aeq_source else want
        self._filter_aeq()
        self.push()

    def community_shape(self, label):
        """Filter by what the curve does. Clicking the chip already in force clears
        it, so the filter can always be undone with the control that set it."""
        want = str(label)
        self.hub_shape = "" if want == self.hub_shape else want
        self._filter_hub()
        self.push()

    def community_sort(self, index):
        self.hub_sort = max(0, min(int(index), len(HUB_SORTS) - 1))
        self.settings["hub_sort"] = self.hub_sort
        save_settings(self.settings)
        self._filter_hub()
        self.push()

    def community_own(self, own):
        """Show only what was uploaded for this DAC, or the whole pooled family."""
        self.hub_own = bool(own)
        self.settings["hub_own"] = self.hub_own
        save_settings(self.settings)
        self._filter_hub()
        self._hub_summary()
        self.push()

    def _hub_summary(self):
        """The line under the title. It has to say what is being hidden, because a
        filter that silently drops five sixths of a library is indistinguishable from
        a library that is nearly empty."""
        if not self.hub_rows:
            return
        own = sum(1 for r in self.hub_rows if r["own"])
        if self.hub_own:
            self.hub_note = "%d for your DAC · %d more in the family" % (
                own, len(self.hub_rows) - own)
        else:
            self.hub_note = "%d in the family · %d for your DAC" % (
                len(self.hub_rows), own)
        if self.hub_matched > HubWorker.CAP:
            self.hub_note += " · showing the top %d" % HubWorker.CAP

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
            "kind": "hub",
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
        self._replace_bands(
            [dict(b) for b in p["bands"]], p["pregain"],
            tuning.source(p.get("kind", "hub"), p["title"], p["bands"],
                          ident=p.get("uuid", ""), author=p.get("author", ""),
                          pregain=p["pregain"]))
        self.active_preset = ""
        # Whatever was being browsed, the answer to "apply this" is the curve itself.
        self.nav.reset()
        self.toast("Applied “%s”." % p["title"], False)
        self.push()

    def preview_close(self):
        self.preview = None
        self.push()

    # ── help and appearance ──
    # ── the library ──
    def open_library(self, tab):
        """The Saved / Recent / Community / Headphones buttons all land here.

        They stay four separate buttons on purpose: three of them used to open three
        separate panels, and landing somewhere you did not ask for — one click from
        where you did — would not be an improvement. What changed is that the other
        three are now a tab away rather than a close and a reopen.
        """
        if self.showing(tab):
            self.nav.back()                 # the button you came in by closes it
        else:
            self.lib_tab = tab
            self.nav.open(nav_mod.LIBRARY)
            self._enter_tab(tab)
        self.push()

    def library_tab(self, index):
        """Switching source from inside the sheet."""
        tab = _clamp_int(index, 0, LIB_TABS - 1)
        if tab != self.lib_tab:
            self.lib_tab = tab
            self._enter_tab(tab)
        self.push()

    def showing(self, tab):
        """Is that source the one on screen? Both halves matter: a tab that is not
        selected and a library that is not the top of the stack are equally invisible,
        and background work — thumbnails, curve classification — must stop for either."""
        return self.nav.at(nav_mod.LIBRARY) and self.lib_tab == tab

    def _enter_tab(self, tab):
        """Whatever arriving at a source costs, paid once, on arrival.

        Nothing is fetched for a tab nobody has opened: the community index is 5.4 MB
        and the AutoEQ catalogue is 8,827 models, and loading both to show one would
        be the unification making the app slower than the three panels it replaced.
        """
        if tab == LIB_SAVED:
            self._reload_profiles()
        elif tab == LIB_COMMUNITY:
            if not self.hub_rows:
                self.community_reload(False)
        elif tab == LIB_HEADPHONES:
            if not self.aeq_rows:
                self.aeq_note = "Loading the catalogue…"
                self.aeq.submit(self.aeq.load, self.aeq_query, False)

    def _lib_note(self):
        """The line beside "Library" — whatever the tab you are on has to say.

        Deliberately empty when a list is empty: explaining that is the list's own
        job, in the middle of the space where the rows would have been, and saying it
        here as well left the sheet stating "Nothing saved yet." twice, once in each
        half of itself.
        """
        if self.lib_tab == LIB_SAVED:
            return "%d saved" % len(self.prof_rows) if self.prof_rows else ""
        if self.lib_tab == LIB_RECENT:
            return "%d recent" % len(self.history) if self.history else ""
        if self.lib_tab == LIB_COMMUNITY:
            return self.hub_note
        return self.aeq_note

    def nav_pop(self):
        """One step back: Escape, the back arrow, and every sheet's close button.

        There is one of these and not one per screen. A screen opened from another —
        the release notes from Settings, the device list from the guide — comes back
        to the one it was opened from, because that is what the stack says, not
        because this function knows about that pair.
        """
        left = self.nav.back()
        if left == nav_mod.WHATSNEW:
            # Only meaningful while the panel is up: it decides whether the panel is
            # describing this build or the one on offer.
            self.whatsnew_preview = False
        self.push()

    def toggle_help(self):
        self.nav.toggle(nav_mod.HELP)
        self.push()

    def toggle_settings(self):
        self.nav.toggle(nav_mod.SETTINGS)
        self.push()

    def set_dark(self, on):
        self.settings["dark"] = bool(on)
        save_settings(self.settings)
        self.push()

    def set_skin(self, index):
        """The palette the whole screen is drawn on, out of the table in theme.slint.

        Separate from the accent on purpose: the accent is a few hundred pixels of
        button and the palette is everything else, so somebody who finds the warm
        mauve too present had nothing to reach for while there was only one ground.
        """
        # SKINS is a count, not a list — `len()` on it raised TypeError, so every
        # click on a palette threw and the picker did nothing at all.
        self.settings["skin"] = int(_clamp(int(index), 0, SKINS - 1))
        save_settings(self.settings)
        self.push()

    def set_accent(self, index):
        """The one accent, out of the table in theme.slint. It drives the primary
        action, the active state and the equalised curve — the reference and output
        traces are deliberately left alone."""
        self.settings["accent"] = int(_clamp(int(index), 0, ACCENTS - 1))
        save_settings(self.settings)
        self.push()

    def dismiss_welcome(self):
        self.welcome_open = False
        self.settings["seen_welcome"] = True
        save_settings(self.settings)
        self.push()

    def show_welcome(self):
        self.welcome_open = True
        self.nav.reset()
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
        # Before anything else: stop asking. A poll queued after the shutdown job
        # would be a device read on a worker that has already closed its handle.
        if self._watch is not None:
            self._watch.stop()
        workers = (self.dev, self.hub, self.io, self.upd, self.aeq)
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
        """Say something, and take it back down again.

        Every notice is stamped with a generation. A timer only clears the text if the
        stamp it captured is still the current one, so a message that has already been
        replaced or dismissed cannot have its successor cleared out from under it by
        an old timer arriving late. `slint.Timer.single_shot` cannot be cancelled,
        which is why the check is on the value rather than on the timer.
        """
        self.win.toast_text = text
        self.win.toast_error = bool(is_error)
        self._toast_gen += 1
        if not text:
            return
        gen = self._toast_gen

        def expire():
            if self._toast_gen == gen:
                self.win.toast_text = ""

        slint.Timer.single_shot(
            TOAST_ERROR_SECONDS if is_error else TOAST_SECONDS, expire)

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
        w.slot_note = self.slot_note()
        w.slot_on_builtin = self.on_builtin
        w.selected = self.selected
        w.active_preset = self.active_preset
        w.dark = self.settings["dark"]
        w.readout = self.settings["readout"]
        w.accent_index = self.settings["accent"]
        w.skin_index = self.settings["skin"]
        w.welcome_open = self.welcome_open
        # One property for seven screens. The view draws whichever this names and
        # nothing else, so it cannot show two at once however this got here.
        w.nav = self.nav.top
        w.nav_under = self.nav.under
        w.lib_tab = self.lib_tab
        w.lib_note = self._lib_note()
        w.hub_busy = self.hub_busy
        w.hub_count = len(self.hub_visible)
        w.hub_sort = self.hub_sort
        w.hub_own = self.hub_own
        w.hub_shape = self.hub_shape
        w.opt_fullscreen = self.settings["start_fullscreen"]
        w.opt_watch = self.settings["watch_devices"]
        w.opt_prefetch = self.settings["hub_prefetch"]
        w.opt_history = self.settings["keep_history"]
        w.opt_motion = self.settings["motion"]
        # Recomputed on every push rather than stored: it is a statement about the
        # bands as they are *now*, and the whole point is that it stops being true
        # the moment somebody drags a handle.
        # Silent while the DAC is playing one of its own presets: the bands on screen
        # are that preset's, and naming them after the last thing this app applied read
        # as "your community curve, edited" over eight bands the app never wrote. The
        # source is kept rather than cleared, so cycling back to the custom profile gets
        # its name back.
        w.source_label = ("" if self.on_builtin
                          else tuning.describe(self.source, self.bands))
        hist = [{"fingerprint": e.get("fingerprint", ""),
                 "name": e.get("name", ""), "note": tuning.summary(e)}
                for e in self.history]
        hsig = tuple(h["fingerprint"] for h in hist)
        if hsig != self._hist_sig:
            self._hist_sig = hsig
            w.history_rows = slint.ListModel(hist)
        w.history_count = len(hist)
        # Only the shapes something actually matched, so the row never offers a chip
        # that leads to an empty list.
        chips = [{"name": s_, "count": self.hub_counts.get(s_, 0)}
                 for s_ in shape_mod.LABELS if self.hub_counts.get(s_)]
        sig = tuple((c["name"], c["count"]) for c in chips)
        if sig != self._chip_sig:
            self._chip_sig = sig
            w.hub_shapes = slint.ListModel(chips)

        w.update_state = self.update_state
        w.update_note = self.update_note
        w.update_progress = float(self.update_progress)
        w.update_version = (self.update_found or {}).get("version", "")
        w.update_can_install = bool((self.update_found or {}).get("can_install"))
        w.update_hint = (self.update_found or {}).get("hint", "")
        w.update_command = self.update_command
        w.update_can_fetch = bool((self.update_found or {}).get("can_fetch"))
        w.update_rollback = bool((self.update_found or {}).get("rollback"))
        w.update_can_elevate = bool((self.update_found or {}).get("can_elevate"))
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
        hub_rows = [
            {"uuid": r["uuid"], "title": r["title"], "author": r["author"],
             "desc": r["desc"], "likes": r["likes"], "downloads": r["downloads"],
             "rating": float(r["rating"]), "ratings": r["ratings"],
             "shape": self._shape_of.get(r["uuid"], ""),
             "curve": self._thumb_of.get(r["uuid"], "")}
            for r in self.hub_visible
        ]
        sig = tuple(r["uuid"] for r in self.hub_visible)
        if sig != self._hub_sig:
            self._hub_sig = sig
            self._hub_model = slint.ListModel(hub_rows)
            w.hub_rows = self._hub_model
        elif self._hub_model is not None:
            # Same presets, new information about them: the curves arrive in chunks
            # from the prefetch, so the list is filled in under the reader rather than
            # rebuilt around them. Same rule as the band cards — a fresh ListModel
            # tears down every card, and here that would jump the scroll position
            # every time eight more thumbnails landed.
            for i, row in enumerate(hub_rows):
                self._hub_model.set_row_data(i, row)

        w.comparing = self.comparing
        w.tour_step = self.tour_step
        if 0 <= self.tour_step < len(self.TOUR):
            head, body = self.TOUR[self.tour_step]
            w.tour_head = head
            w.tour_body = body
        # The device table only changes when something is plugged or unplugged, so it
        # is rebuilt on that rather than on every push.
        dsig = self.product_id if self.connected else None
        if dsig != self._dev_sig:
            self._dev_sig = dsig
            w.device_rows = slint.ListModel(devices_mod.rows(dsig))
            w.device_note = devices_mod.summary()
        w.soloed = self._solo
        w.can_undo = bool(self._history)
        w.whatsnew_from = self.whatsnew_from
        w.whatsnew_version = self.whatsnew_version
        w.whatsnew_preview = self.whatsnew_preview
        w.whatsnew_notes = slint.ListModel(list(self.whatsnew_notes)) \
            if self.whatsnew_notes else slint.ListModel([])
        w.about_text = self.diagnostics_text()

        w.prof_count = len(self.prof_rows)
        psig = tuple((r["name"], r["saved"]) for r in self.prof_rows)
        if psig != self._prof_sig:
            self._prof_sig = psig
            self._prof_model = slint.ListModel([
                {"name": r["name"], "note": prof.summary(r, self.band_count)}
                for r in self.prof_rows])
            w.prof_rows = self._prof_model

        w.aeq_busy = self.aeq_busy
        w.aeq_count = len(self.aeq_rows)
        aeq_rows = [
            {"model": r["model"], "source": r["source"], "form": r["form"],
             "shape": self._aeq_shape_of.get(aeq_key(r), ""),
             "curve": self._aeq_thumb_of.get(aeq_key(r), "")}
            for r in self.aeq_rows]
        asig = tuple((r["model"], r["source"]) for r in self.aeq_rows)
        if asig != self._aeq_sig:
            self._aeq_sig = asig
            self._aeq_model = slint.ListModel(aeq_rows)
            w.aeq_rows = self._aeq_model
        elif self._aeq_model is not None:
            # Same headphones, new curves: filled in under the reader as each chunk
            # lands, never rebuilt around them.
            for i, row in enumerate(aeq_rows):
                self._aeq_model.set_row_data(i, row)
        w.aeq_source = self.aeq_source
        # Measurers, most-published first — which is also roughly most-trusted, and is
        # the order `autoeq.SOURCE_ORDER` already ranks search results by.
        chips = sorted(self.aeq_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        chips = [{"name": n, "count": c} for n, c in chips[:12]]
        csig = tuple((c["name"], c["count"]) for c in chips)
        if csig != self._aeq_chip_sig:
            self._aeq_chip_sig = csig
            w.aeq_sources = slint.ListModel(chips)

        if self._plot_w > 1 and self._plot_h > 1:
            self._push_curves(w)

    # Resampling the response is the most expensive thing this class does — about
    # 2.7 ms per trace across eight biquads at a 980 px plot, on the UI thread. Two
    # things used to make that much worse than it needed to be, and both are fixed
    # here rather than by drawing less:
    #
    #   * It ran on every push(). A toast, an update check finishing, a sheet opening
    #     and a hover all call push(), and none of them can change the curve.
    #   * It computed both views every time. The editor draws curve+flat+pregain and
    #     the readout draws readout+readout-flat; whichever view is not on screen was
    #     being sampled anyway and thrown away.
    #
    # Together that is roughly a third of the work on an idle frame and all of it on
    # a frame that changed nothing.
    def _push_curves(self, w):
        # Everything the traces are a function of. `_drag` is in here because the
        # sample count changes with it, so letting go of a handle has to redraw at
        # full resolution.
        sig = (tuple((b["type"], b["frequency"], b["gain"], b["q"]) for b in self.bands),
               float(self.pregain), self._plot_w, self._plot_h,
               bool(self.settings["readout"]), self._drag >= 0)
        if sig == self._curve_sig:
            return
        self._curve_sig = sig

        # Fewer samples while a handle is under the pointer: the curve is resampled on
        # every mouse-move, and that is the one moment the budget is real.
        samples = 110 if self._drag >= 0 else 220

        if self.settings["readout"]:
            # The readout, on its own axis. Only the output curve exists here — the
            # solid one would be the same line drawn 4.8 dB higher, which is precisely
            # the confusion this view is meant to remove.
            box = dict(width=self._plot_w, height=self._plot_h,
                       top_db=READOUT_TOP, bot_db=READOUT_BOT)
            w.readout_path = curve_mod.svg_curve(
                self.bands, offset_db=float(self.pregain), samples=samples, **box)
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
            return

        geom = dict(width=self._plot_w, height=self._plot_h,
                    top_db=TOP_DB, bot_db=BOT_DB, samples=samples)
        w.curve_path = curve_mod.svg_curve(self.bands, **geom)
        w.flat_path = curve_mod.svg_flat(db=0.0, width=self._plot_w,
                                         height=self._plot_h,
                                         top_db=TOP_DB, bot_db=BOT_DB)
        # The output trace: the same curve shifted by pre-gain, which is what the DAC
        # actually emits. Drawn dashed so it reads as a consequence of the solid one
        # rather than a second thing to tune.
        w.pregain_path = curve_mod.svg_curve(
            self.bands, offset_db=float(self.pregain), dash=(7.0, 5.0), **geom)

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
