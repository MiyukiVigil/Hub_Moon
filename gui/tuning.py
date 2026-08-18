"""Where a curve came from, and what has been applied lately.

Through 1.2.0 bands were anonymous. Once written, a community config, an AutoEQ fit for
a named pair of headphones and a curve somebody drew by hand were byte-identical, and
the app could not tell you which one you were listening to. Three separate asks — show
me what is applied, let me see what I applied before, let me flip between two of them —
are all the same missing fact.

**The DAC holds the bands; only the host can hold the label.** There is nowhere on the
device to put a name, so the label lives here and is re-attached by comparing what the
device reports against a fingerprint taken when it was applied:

- fingerprints match  → "AutoEQ · HD 600"
- they differ         → "AutoEQ · HD 600 (edited)"
- nothing stored      → nothing claimed. Not "manual": we do not know, and a label
                        that guesses is worse than no label at all.

Two files, and the difference between them is the point. `state.json` is what is on the
device right now, per device. `history.json` is a capped ring of what has been applied,
automatic and disposable — as opposed to `profiles/`, which is explicit and permanent
and which the user is the only one who ever writes to. History is what you *did*;
profiles are what you *chose to keep*.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

try:
    import moondrop_control as mc
except ImportError:                                  # pragma: no cover - see profiles
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

SCHEMA = 2

# Twenty is plenty: this is "what have I tried this evening", not an archive. Anything
# worth keeping past that gets promoted to a profile, which is the whole distinction.
HISTORY_MAX = 20

# What a source can be. `manual` is never stored — see the module docstring.
KINDS = ("hub", "autoeq", "profile", "import", "preset")

# What each kind is called in front of a person.
KIND_NAMES = {
    "hub": "Community",
    "autoeq": "AutoEQ",
    "profile": "Profile",
    "import": "Imported",
    "preset": "Preset",
}


def state_path():
    return os.path.join(mc.config_dir(), "state.json")


def history_path():
    return os.path.join(mc.config_dir(), "history.json")


def fingerprint(bands):
    """A stable short hash of what the curve *is*.

    Rounded exactly as far as the device round-trips — gain to 0.1 dB, Q to three
    decimals, frequency to whole hertz — because anything finer means every reconnect
    reports "(edited)" against a curve nobody touched.

    A disabled band contributes only the word `disabled`. Its frequency and gain are
    whatever they happened to be when it was switched off, they are not written to the
    device, and letting them into the hash would make two identical curves hash apart.
    """
    parts = []
    for b in sorted(bands or [], key=lambda x: int(x.get("index", 0))):
        ftype = str(b.get("type", "disabled"))
        if ftype == "disabled":
            parts.append("disabled")
            continue
        parts.append("%s|%d|%.1f|%.3f" % (ftype, int(round(float(b.get("frequency", 0)))),
                                          round(float(b.get("gain", 0.0)), 1),
                                          round(float(b.get("q", 0.7)), 3)))
    body = "\n".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()[:16]


def source(kind, name, bands, *, ident="", author="", pregain=0.0):
    """Build the record that travels with a set of bands."""
    return {
        "kind": kind if kind in KINDS else "import",
        "id": str(ident or ""),
        "name": str(name or "")[:80],
        "author": str(author or "")[:60],
        "applied": int(time.time()),
        "fingerprint": fingerprint(bands),
        "pregain": float(pregain or 0.0),
        "bands": [{"index": b["index"], "type": b["type"], "frequency": b["frequency"],
                   "gain": b["gain"], "q": b["q"]} for b in bands],
    }


def describe(src, bands):
    """The chip under the curve: "AutoEQ · HD 600", or "" when nothing is known."""
    if not src or not src.get("name"):
        return ""
    label = "%s · %s" % (KIND_NAMES.get(src.get("kind"), "Applied"), src["name"])
    if fingerprint(bands) != src.get("fingerprint"):
        label += " (edited)"
    return label


def edited(src, bands):
    return bool(src) and fingerprint(bands) != src.get("fingerprint")


# ── the two files ──

def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}                                     # absent or corrupt: start clean


def _write(path, payload):
    """Atomic, like every other file this app owns: a write interrupted halfway leaves
    the previous version rather than a truncated one. A failure is not worth
    interrupting anybody over — this is a label, not a curve."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _key(product_id):
    return "0x%04X" % int(product_id or 0)


def load_state(product_id):
    """What was last applied to this device, if this machine applied it."""
    data = _read(state_path())
    if data.get("schema") != SCHEMA:
        return None                                   # a shape we do not know
    src = (data.get("devices") or {}).get(_key(product_id))
    return src if isinstance(src, dict) else None


def save_state(product_id, src):
    data = _read(state_path())
    devices = data.get("devices") if isinstance(data.get("devices"), dict) else {}
    if src is None:
        devices.pop(_key(product_id), None)
    else:
        devices[_key(product_id)] = src
    return _write(state_path(), {"schema": SCHEMA, "devices": devices})


def load_history():
    data = _read(history_path())
    if data.get("schema") != SCHEMA:
        return []
    entries = data.get("entries")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def remember(src):
    """Add to the ring, newest first, and return the new list.

    Deduplicated by fingerprint rather than by name, so applying the same curve twice
    moves one entry to the top instead of making two — and so does re-applying it from
    history, which is the same curve arriving by a different route.
    """
    if not src:
        return load_history()
    entries = [e for e in load_history()
               if e.get("fingerprint") != src.get("fingerprint")]
    entries.insert(0, src)
    entries = entries[:HISTORY_MAX]
    _write(history_path(), {"schema": SCHEMA, "entries": entries})
    return entries


def forget_all():
    return _write(history_path(), {"schema": SCHEMA, "entries": []})


def summary(entry):
    """The line under a history entry's name."""
    bits = [KIND_NAMES.get(entry.get("kind"), "Applied")]
    if entry.get("author"):
        bits.append("by %s" % entry["author"])
    used = sum(1 for b in entry.get("bands") or [] if b.get("type") != "disabled")
    if used:
        bits.append("%d band%s" % (used, "" if used == 1 else "s"))
    when = int(entry.get("applied") or 0)
    if when:
        bits.append(time.strftime("%H:%M", time.localtime(when))
                    if time.time() - when < 86400
                    else time.strftime("%Y-%m-%d", time.localtime(when)))
    return " · ".join(bits)
