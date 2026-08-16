"""Saved curves, kept on this machine.

The DAC has device slots, but those live in its flash, there are a fixed number of
them, and they travel with the hardware rather than with you. This is the other
thing people want: somewhere to keep *this curve, under this name*, without limit —
"HD 600", "late night", "the one that fixed the podcast".

**A profile is an exported file that happens to live in a known directory.** The
format written here is byte-identical to what Export produces, so a profile can be
mailed to somebody and imported, an exported file can be dropped into the profiles
directory and appear in the list, and there is exactly one schema to keep working.
The only additions are metadata (`name`, `saved`, `device`, `band_count`) that Import
already ignores.

Nothing here talks to the device. It reads and writes JSON, and the caller decides
what to do with it — which is what keeps a corrupt file on disk from being able to
write anything to a DAC.
"""
from __future__ import annotations

import json
import os
import re
import time

try:
    import moondrop_control as mc
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

MAX_NAME = 60
SCHEMA = 1


def store_dir():
    return os.path.join(mc.config_dir(), "profiles")


def slug(name):
    """A filename for a profile name.

    Deliberately lossy and deliberately not unique: two profiles whose names differ
    only in punctuation land on the same file and the second overwrites the first,
    which is the same thing "Save" does to a name that already exists. The `name`
    inside the file is what is displayed, so the slug only has to be a stable,
    portable filename — including on Windows, where a colon is not one.
    """
    out = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return (out or "profile")[:80]


def _path(name):
    return os.path.join(store_dir(), slug(name) + ".json")


def save(name, bands, pregain, global_gain=None, device=None, slot=None):
    """Write one profile. Returns its path.

    Atomic, like the settings file: a write interrupted halfway leaves the previous
    version of that profile intact rather than a truncated one.
    """
    name = str(name).strip()[:MAX_NAME] or "Untitled"
    payload = {
        # what Import reads — identical to an exported file
        "device_name": device or "",
        "pregain": float(pregain),
        "filters": [
            {"index": b["index"], "type": b["type"], "frequency": b["frequency"],
             "gain": b["gain"], "q": b["q"]}
            for b in bands
        ],
        # what this module adds; Import ignores all of it
        "hub_moon_profile": SCHEMA,
        "name": name,
        "saved": int(time.time()),
        "band_count": len(bands),
    }
    if global_gain is not None:
        payload["global_gain"] = float(global_gain)
    if slot is not None:
        payload["active_eq_profile"] = slot

    os.makedirs(store_dir(), exist_ok=True)
    path = _path(name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)
    return path


def load_all():
    """Every readable profile, newest first.

    A file that will not parse is skipped rather than raising: one bad JSON file in
    the directory must not take the whole list down with it, and the directory is
    somewhere a person can drop files by hand.
    """
    out = []
    try:
        names = sorted(os.listdir(store_dir()))
    except OSError:
        return out
    for fn in names:
        if not fn.endswith(".json") or fn.endswith(".tmp"):
            continue
        path = os.path.join(store_dir(), fn)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        filters = data.get("filters")
        if not isinstance(filters, list) or not filters:
            continue
        out.append({
            "name": str(data.get("name") or os.path.splitext(fn)[0])[:MAX_NAME],
            "path": path,
            "saved": int(data.get("saved") or 0),
            "device": str(data.get("device_name") or ""),
            "pregain": float(data.get("pregain") or 0.0),
            "global_gain": data.get("global_gain"),
            "bands": filters,
            "count": sum(1 for f in filters if f.get("type") != "disabled"),
        })
    out.sort(key=lambda p: (-p["saved"], p["name"].lower()))
    return out


def delete(name_or_path):
    """Remove a profile. Missing is success — the caller wanted it gone."""
    path = name_or_path if os.path.sep in str(name_or_path) else _path(name_or_path)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def summary(profile, band_count=None):
    """The line under a profile's name in the list."""
    bits = ["%d band%s" % (profile["count"], "" if profile["count"] == 1 else "s")]
    if profile["pregain"]:
        bits.append("pre-gain %+.1f dB" % profile["pregain"])
    if profile["device"]:
        bits.append(profile["device"])
    if band_count and len(profile["bands"]) > band_count:
        # Saved on a device with more bands than the one plugged in now. Applying it
        # will drop the tail, so say so before it happens rather than after.
        bits.append("saved with %d bands" % len(profile["bands"]))
    if profile["saved"]:
        bits.append(time.strftime("%Y-%m-%d", time.localtime(profile["saved"])))
    return " · ".join(bits)
