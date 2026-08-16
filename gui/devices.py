"""The supported-device table, read out of the registry rather than written twice.

The readme has carried this list since 1.0.0 and the app has never shown it, which is
the wrong way round: the person who needs it is the one holding a DAC that did not
come up, and they are looking at the app, not at GitHub.

Everything here is derived from `moondrop_control`'s own tables, so a device added to
the registry appears here without anyone remembering to update a second list. The one
fact that cannot be derived is which of them has been tried on real hardware — that
lives in TESTED below, and is deliberately short and honest.
"""
from __future__ import annotations

import moondrop_control as mc

# Eleven of the twelve are transcribed from the vendor's own web app and have never
# been near the hardware they describe. Saying so in the app is the point: a row that
# claims support it has not earned is how a bug report becomes "it just does nothing".
TESTED = {0x011D}          # DAWN PRO2

DEFAULT_BANDS = 8


def rows(connected_pid=None):
    """One row per supported device, connected one first, then by product id."""
    out = []
    for pid, name in mc.SUPPORTED_DEVICES.items():
        bands = mc.DEVICE_BANDS.get(pid, DEFAULT_BANDS)
        out.append({
            "pid": "0x%04X" % pid,
            "name": name,
            "bands": "%d bands" % bands,
            # The DAC's own EQ profile number that Hub Moon writes to. Not every
            # device puts the custom slot in the same place.
            "slot": "slot %d" % mc.DEVICE_PEQ_INDEX.get(pid, 7),
            "pregain": pid not in mc.NO_PREGAIN_DEVICES,
            "tested": pid in TESTED,
            "here": connected_pid is not None and pid == connected_pid,
        })
    out.sort(key=lambda r: (not r["here"], not r["tested"], r["pid"]))
    return out


def summary():
    n = len(mc.SUPPORTED_DEVICES)
    return ("%d Moondrop DACs, vendor 0x%04X. %d verified on hardware — the rest are "
            "transcribed from the vendor's own registry and unproven."
            % (n, mc.MOONDROP_VID, len(TESTED)))
