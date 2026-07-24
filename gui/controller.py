"""Controller — the Python side of the GUI.

Two objects:

* ``DeviceWorker`` lives on its own ``QThread`` and is the *only* thing that
  ever touches the hidraw. Because it's single-threaded, every device call is
  serialised for free — a read can never interleave with a write (the one hard
  constraint moondrop_control documents). It owns a ``MoondropDevice`` and does
  all I/O in slots, reporting back over signals.

* ``Controller`` lives on the GUI thread and is what QML talks to (context
  property ``hub``). It caches device state in Qt properties, hands edits to the
  worker, and relays the worker's snapshots back into those properties.

Writes are *live* (no flash) so a drag is instantly audible; ``saveToFlash()``
is the only call that persists. When no DAC is present the controller drops into
a demo mode — the graph is a working playground, just with nothing to write to.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import (
    QObject, QThread, Signal, Slot, Property, QUrl, Qt, QTimer,
)

# The engine. Imported, not reimplemented — this is the same code the CLI and
# the hardware tests exercise. Installed as a top-level module it imports
# directly; from a source checkout gui/ sits next to moondrop_control.py.
try:
    import moondrop_control as mc
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402


# A neutral 8-band example (the same curve the landing-page demo draws) so the
# window has something to show — and stays a usable playground — with no DAC.
DEMO_BANDS = [
    {"index": 0, "type": "low_shelf",  "frequency": 105,   "gain": 2.6,  "q": 4.273},
    {"index": 1, "type": "peaking",    "frequency": 172,   "gain": -3.6, "q": 0.441},
    {"index": 2, "type": "peaking",    "frequency": 770,   "gain": 1.9,  "q": 0.910},
    {"index": 3, "type": "peaking",    "frequency": 1404,  "gain": -1.0, "q": 2.281},
    {"index": 4, "type": "peaking",    "frequency": 3005,  "gain": 3.3,  "q": 1.461},
    {"index": 5, "type": "peaking",    "frequency": 4765,  "gain": -1.5, "q": 3.828},
    {"index": 6, "type": "peaking",    "frequency": 5884,  "gain": 4.8,  "q": 1.680},
    {"index": 7, "type": "high_shelf", "frequency": 10000, "gain": -2.3, "q": 0.699},
]


def _disabled_band(i):
    return {"index": i, "type": "disabled", "frequency": 1000, "gain": 0.0, "q": 1.0}


# ── community-library helpers (used by HubWorker) ─────────────────────────────
def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _flat(s):
    """Collapse authors' free-text (newlines, runs of spaces) to a single line."""
    return " ".join((s or "").split())


def _hub_slim_gui(row):
    """A raw peq-config row → just the fields a card needs. `score` is already the
    mean star rating (0–5, sometimes a half like "2.5"); `score_count` is how many
    people rated it. Most configs are unrated (count 0) and render without stars."""
    rc = _num(row.get("score_count"))
    try:
        sc = float(row.get("score") or 0)
    except (TypeError, ValueError):
        sc = 0.0
    return {
        "uuid": row.get("uuid", ""),
        "title": _flat(row.get("title")) or "Untitled",
        "author": _flat(row.get("username")) or "anonymous",
        "desc": _flat(row.get("desc"))[:280],
        "likes": _num(row.get("like")),
        "downloads": _num(row.get("downloadcount")),
        "comments": _num(row.get("comment_count")),
        "rating": round(sc, 1) if rc > 0 else 0.0,
        "ratings": rc,
    }


def _suggest_pregain(bands):
    """A little headroom so a boosty community curve doesn't clip on apply: pull
    the pre-gain down by the largest positive band gain (bounded, like AutoEQ's
    preamp). Shelves and peaks only; cuts need no headroom."""
    peak = 0.0
    for b in bands:
        if b.get("type") in ("peaking", "low_shelf", "high_shelf"):
            peak = max(peak, float(b.get("gain") or 0.0))
    return round(max(-12.0, min(0.0, -peak)), 1)


# ── hub worker (community library — network only, never the hidraw) ───────────
class HubWorker(QObject):
    """Fetches the Moondrop community PEQ library on its own thread. It touches
    the network and the on-disk cache, never the device — so browsing configs can
    never collide with a device read/write on the hidraw worker."""

    indexReady = Signal(object, bool)          # (slim rows, was_cached)
    indexError = Signal(str)
    bandsReady = Signal(object, float, int, str)  # (bands, suggested_pre, dropped, uuid)
    bandsError = Signal(str)
    previewReady = Signal(object, float, int, str)  # same shape, for a preview (no apply)
    previewError = Signal(str)
    hubBusy = Signal(bool)

    # The popular head of the library is what anyone actually wants; the long tail
    # is mostly empty test uploads (0 likes / 0 downloads). Cap the payload here so
    # a 7k-row index stays a snappy, searchable few-hundred-KB list in the GUI.
    CAP = 1000

    @Slot(str, int, bool)
    def loadIndex(self, product_uuid, band_count, refresh):
        self.hubBusy.emit(True)
        try:
            rows, cached = mc.hub_fetch_index(product_uuid, refresh=refresh)
            slim = [_hub_slim_gui(r) for r in rows]
            slim.sort(key=lambda d: (d["likes"], d["downloads"], d["rating"]),
                      reverse=True)
            self.indexReady.emit(slim[:self.CAP], cached)
        except Exception as e:  # noqa: BLE001 — any network/parse failure is a toast
            self.indexError.emit("Couldn't reach the Moondrop library (%s)." % e)
        finally:
            self.hubBusy.emit(False)

    def _fetch_curve(self, uuid, band_count):
        """uuid → (bands, suggested_pre, dropped). Raises LookupError with a
        user-facing message when the config can't be turned into a curve."""
        file_ref = mc.hub_resolve_file(uuid)
        if not file_ref:
            raise LookupError("This config is no longer on the server.")
        bands, dropped = mc.hub_preset_bands(file_ref, band_count)
        if not bands:
            raise LookupError("This config has no usable bands.")
        return bands, _suggest_pregain(bands), dropped

    @Slot(str, int)
    def resolveBands(self, uuid, band_count):
        self.hubBusy.emit(True)
        try:
            bands, pre, dropped = self._fetch_curve(uuid, band_count)
            self.bandsReady.emit(bands, pre, dropped, uuid)
        except LookupError as e:
            self.bandsError.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.bandsError.emit("Couldn't load this config (%s)." % e)
        finally:
            self.hubBusy.emit(False)

    @Slot(str, int)
    def resolvePreview(self, uuid, band_count):
        """Same fetch as resolveBands, but a preview never applies to the device —
        it just hands the curve back for the popup graph. No page-level busy flag."""
        try:
            bands, pre, dropped = self._fetch_curve(uuid, band_count)
            self.previewReady.emit(bands, pre, dropped, uuid)
        except LookupError as e:
            self.previewError.emit(str(e))
        except Exception as e:  # noqa: BLE001
            self.previewError.emit("Couldn't load this preview (%s)." % e)


# ── worker ──────────────────────────────────────────────────────────────────
class DeviceWorker(QObject):
    """Owns the hidraw. Every method here runs on the worker thread."""

    snapshot = Signal(object)     # full device state dict
    noDevice = Signal(str)        # reason
    error = Signal(str)           # a write/import failed — message for the toast
    saved = Signal()              # flash write finished
    busy = Signal(bool)

    def __init__(self):
        super().__init__()
        self._dev = None

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
        name = info.get("product_string") or mc.SUPPORTED_DEVICES.get(dev.product_id, "Moondrop DAC")
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

    @Slot()
    def refresh(self):
        self.busy.emit(True)
        try:
            self._close()
            infos = mc.find_devices()
            if not infos:
                unsupported = mc.find_unsupported_devices()
                if unsupported:
                    self.noDevice.emit(unsupported[0][1] + " is not driveable by this tool.")
                else:
                    self.noDevice.emit("No Moondrop DAC connected.")
                return
            try:
                self._dev = mc.MoondropDevice(infos[0])
            except Exception as e:
                self.noDevice.emit("Could not open the DAC (%s). You may need a udev rule or sudo." % e)
                return
            self.snapshot.emit(self._read_all())
        finally:
            self.busy.emit(False)

    @Slot(int, str, int, float, float)
    def writeBand(self, index, ftype, freq, gain, q):
        if self._dev is None:
            return
        try:
            self._dev.write_peq_index(index, ftype, freq, gain, q)
        except ValueError as e:
            self.error.emit(str(e))

    @Slot(object, float, float)
    def applyBands(self, bands, pregain, global_gain):
        """Batch write (preset / import): all bands live, then gains."""
        if self._dev is None:
            return
        self.busy.emit(True)
        try:
            for b in bands:
                try:
                    self._dev.write_peq_index(
                        b["index"], b["type"], int(round(b["frequency"])),
                        float(b["gain"]), float(b["q"]))
                except ValueError as e:
                    self.error.emit(str(e))
            if self._dev.supports_pregain and pregain is not None:
                self._dev.set_pregain(pregain, save=False)
            if global_gain is not None:
                self._dev.set_global_gain(global_gain, save=False)
            self.snapshot.emit(self._read_all())
        finally:
            self.busy.emit(False)

    @Slot(float)
    def setPregain(self, db):
        if self._dev is None:
            return
        try:
            self._dev.set_pregain(db, save=False)
        except Exception as e:
            self.error.emit(str(e))

    @Slot(float)
    def setGlobalGain(self, db):
        if self._dev is None:
            return
        try:
            self._dev.set_global_gain(db, save=False)
        except Exception as e:
            self.error.emit(str(e))

    @Slot()
    def saveToFlash(self):
        if self._dev is None:
            return
        self.busy.emit(True)
        try:
            self._dev.save_eq_to_flash()
            self._dev.save_offset_to_flash()
            self.saved.emit()
        except Exception as e:
            self.error.emit("Save to flash failed: %s" % e)
        finally:
            self.busy.emit(False)

    @Slot(str)
    def exportJson(self, path):
        if self._dev is None:
            return
        import json
        try:
            state = self._read_all()
            payload = {
                "device_name": state["deviceName"],
                "pregain": state["pregain"],
                "global_gain": state["globalGain"],
                "active_eq_profile": state["activeProfile"],
                "filters": [
                    {"index": b["index"], "type": b["type"], "frequency": b["frequency"],
                     "gain": b["gain"], "q": b["q"]}
                    for b in state["bands"]
                ],
            }
            with open(path, "w") as fh:
                json.dump(payload, fh, indent=2)
            self.error.emit("")  # clear any prior toast
        except Exception as e:
            self.error.emit("Export failed: %s" % e)

    @Slot(str)
    def importJson(self, path):
        if self._dev is None:
            return
        import json
        self.busy.emit(True)
        try:
            with open(path) as fh:
                data = json.load(fh)
            filters = data.get("filters", [])
            for f in filters:
                if f.get("type") not in mc.FILTER_TYPES:
                    raise ValueError("unknown filter type %r in file" % f.get("type"))
            for f in filters:
                self._dev.write_peq_index(
                    f["index"], f["type"], int(round(f["frequency"])),
                    float(f["gain"]), float(f["q"]))
            if self._dev.supports_pregain and "pregain" in data:
                self._dev.set_pregain(float(data["pregain"]), save=False)
            if "global_gain" in data:
                self._dev.set_global_gain(float(data["global_gain"]), save=False)
            self.snapshot.emit(self._read_all())
        except Exception as e:
            self.error.emit("Import failed: %s" % e)
        finally:
            self.busy.emit(False)

    @Slot()
    def shutdown(self):
        self._close()


# ── controller ──────────────────────────────────────────────────────────────
class Controller(QObject):
    """GUI-thread facade. QML context property ``hub``."""

    # request signals → worker slots (queued across the thread boundary)
    _reqRefresh = Signal()
    _reqWriteBand = Signal(int, str, int, float, float)
    _reqApplyBands = Signal(object, float, float)
    _reqPregain = Signal(float)
    _reqGlobal = Signal(float)
    _reqSave = Signal()
    _reqExport = Signal(str)
    _reqImport = Signal(str)
    _reqShutdown = Signal()
    _reqLoadIndex = Signal(str, int, bool)   # → HubWorker.loadIndex
    _reqResolve = Signal(str, int)           # → HubWorker.resolveBands
    _reqPreview = Signal(str, int)           # → HubWorker.resolvePreview

    changed = Signal()          # any cached property changed
    bandsReplaced = Signal()    # the whole band set changed (refresh/preset/import/revert)
    toast = Signal(str, bool)   # (message, isError) — transient banner
    savedFlash = Signal()
    # QVariantList (not object) so QML receives a JS array it can filter/iterate
    configsLoaded = Signal("QVariantList", bool)   # (community rows, was_cached)
    configApplied = Signal(str)                    # a community config auditioned (title)
    configPreview = Signal("QVariantList", float, str)  # (bands, suggested_pre, uuid)

    def __init__(self):
        super().__init__()
        self._connected = False
        self._demo = True
        self._busy = False
        self._dirty = False
        self._deviceName = "No device"
        self._firmware = ""
        self._productId = 0
        self._activeProfile = -1
        self._peqIndex = 7
        self._supportsPregain = True
        self._pregain = 0.0
        self._globalGain = 0.0
        self._bandCount = 8
        self._bands = [dict(b) for b in DEMO_BANDS]

        # community-library state
        self._configsBusy = False
        self._pendingTitle = ""
        self._pendingPreview = ""
        self._supported = [
            {"name": v, "moondrop": not v.startswith(("INN", "Deco"))}
            for v in mc.SUPPORTED_DEVICES.values()
        ]

        self._thread = QThread()
        self._thread.setObjectName("hidworker")
        self._worker = DeviceWorker()
        self._worker.moveToThread(self._thread)

        # the community library lives on a second thread: network + disk cache
        # only, so a slow 4 MB fetch never blocks a device read/write.
        self._hubThread = QThread()
        self._hubThread.setObjectName("hubworker")
        self._hubWorker = HubWorker()
        self._hubWorker.moveToThread(self._hubThread)
        self._reqLoadIndex.connect(self._hubWorker.loadIndex)
        self._reqResolve.connect(self._hubWorker.resolveBands)
        self._reqPreview.connect(self._hubWorker.resolvePreview)
        self._hubWorker.indexReady.connect(self._onIndexReady)
        self._hubWorker.indexError.connect(self._onIndexError)
        self._hubWorker.bandsReady.connect(self._onBandsReady)
        self._hubWorker.bandsError.connect(self._onBandsError)
        self._hubWorker.previewReady.connect(self._onPreviewReady)
        self._hubWorker.previewError.connect(self._onPreviewError)
        self._hubWorker.hubBusy.connect(self._onHubBusy)
        self._hubThread.start()

        self._reqRefresh.connect(self._worker.refresh)
        self._reqWriteBand.connect(self._worker.writeBand)
        self._reqApplyBands.connect(self._worker.applyBands)
        self._reqPregain.connect(self._worker.setPregain)
        self._reqGlobal.connect(self._worker.setGlobalGain)
        self._reqSave.connect(self._worker.saveToFlash)
        self._reqExport.connect(self._worker.exportJson)
        self._reqImport.connect(self._worker.importJson)
        self._reqShutdown.connect(self._worker.shutdown)

        self._worker.snapshot.connect(self._onSnapshot)
        self._worker.noDevice.connect(self._onNoDevice)
        self._worker.error.connect(self._onError)
        self._worker.saved.connect(self._onSaved)
        self._worker.busy.connect(self._onBusy)

        self._thread.start()

    # ── lifecycle ──
    @Slot()
    def start(self):
        self._reqRefresh.emit()

    def stop(self):
        self._reqShutdown.emit()
        self._thread.quit()
        self._thread.wait(2000)
        self._hubThread.quit()
        self._hubThread.wait(2000)

    # ── worker callbacks ──
    @Slot(object)
    def _onSnapshot(self, s):
        self._connected = True
        self._demo = False
        self._deviceName = s["deviceName"]
        self._firmware = s["firmware"]
        self._productId = s["productId"]
        self._activeProfile = s["activeProfile"]
        self._peqIndex = s["peqIndex"]
        self._supportsPregain = s["supportsPregain"]
        self._pregain = s["pregain"]
        self._globalGain = s["globalGain"]
        self._bandCount = s["bandCount"]
        self._bands = [dict(b) for b in s["bands"]]
        self._dirty = False
        self.changed.emit()
        self.bandsReplaced.emit()

    @Slot(str)
    def _onNoDevice(self, reason):
        self._connected = False
        self._demo = True
        self._deviceName = "Demo — no DAC"
        self._firmware = ""
        self._dirty = False
        self.changed.emit()
        self.bandsReplaced.emit()
        self.toast.emit(reason + "  Showing a demo curve.", False)

    @Slot(str)
    def _onError(self, msg):
        if msg:
            self.toast.emit(msg, True)

    @Slot()
    def _onSaved(self):
        self._dirty = False
        self.changed.emit()
        self.savedFlash.emit()

    @Slot(bool)
    def _onBusy(self, b):
        self._busy = b
        self.changed.emit()

    # ── hub-worker callbacks ──
    @Slot(object, bool)
    def _onIndexReady(self, rows, cached):
        self.configsLoaded.emit(rows, cached)

    @Slot(str)
    def _onIndexError(self, msg):
        self.toast.emit(msg, True)
        self.configsLoaded.emit([], False)

    @Slot(object, float, int, str)
    def _onBandsReady(self, bands, pre, dropped, uuid):
        # Pad the fetched curve up to the device's band count so unused slots are
        # cleared, then audition it live (Write Cfg still required to persist).
        padded = []
        for i in range(self._bandCount):
            match = next((b for b in bands if int(b.get("index", -1)) == i), None)
            padded.append(match if match else _disabled_band(i))
        self.applyBands(padded, pre)
        title = self._pendingTitle or "config"
        note = "Applied “%s” — audition, then Write Cfg to keep." % title
        if dropped:
            note = "Applied “%s” (%d extra band%s dropped) — Write Cfg to keep." % (
                title, dropped, "" if dropped == 1 else "s")
        self.toast.emit(note, False)
        self.configApplied.emit(title)

    @Slot(str)
    def _onBandsError(self, msg):
        self.toast.emit(msg, True)

    @Slot(object, float, int, str)
    def _onPreviewReady(self, bands, pre, dropped, uuid):
        self.configPreview.emit(bands, pre, uuid)

    @Slot(str)
    def _onPreviewError(self, msg):
        self.toast.emit(msg, True)
        # clear the popup's loading state (empty bands = "preview unavailable")
        self.configPreview.emit([], 0.0, self._pendingPreview)

    @Slot(bool)
    def _onHubBusy(self, b):
        self._configsBusy = b
        self.changed.emit()

    # ── properties (read) ──
    def _g_connected(self): return self._connected
    def _g_demo(self): return self._demo
    def _g_busy(self): return self._busy
    def _g_dirty(self): return self._dirty
    def _g_name(self): return self._deviceName
    def _g_fw(self): return self._firmware
    def _g_pid(self): return self._productId
    def _g_active(self): return self._activeProfile
    def _g_peqIndex(self): return self._peqIndex
    def _g_supPre(self): return self._supportsPregain
    def _g_pregain(self): return self._pregain
    def _g_global(self): return self._globalGain
    def _g_bandCount(self): return self._bandCount
    def _g_bands(self): return self._bands
    def _g_configsBusy(self): return self._configsBusy
    def _g_supported(self): return self._supported
    def _g_productUuid(self):
        return mc.PRODUCT_UUIDS.get(self._productId) or mc.PRODUCT_UUIDS[0x011D]

    connected = Property(bool, _g_connected, notify=changed)
    demo = Property(bool, _g_demo, notify=changed)
    busy = Property(bool, _g_busy, notify=changed)
    dirty = Property(bool, _g_dirty, notify=changed)
    deviceName = Property(str, _g_name, notify=changed)
    firmware = Property(str, _g_fw, notify=changed)
    productId = Property(int, _g_pid, notify=changed)
    activeProfile = Property(int, _g_active, notify=changed)
    peqIndex = Property(int, _g_peqIndex, notify=changed)
    supportsPregain = Property(bool, _g_supPre, notify=changed)
    pregain = Property(float, _g_pregain, notify=changed)
    globalGain = Property(float, _g_global, notify=changed)
    bandCount = Property(int, _g_bandCount, notify=changed)
    bands = Property("QVariantList", _g_bands, notify=changed)
    configsBusy = Property(bool, _g_configsBusy, notify=changed)
    supportedDevices = Property("QVariantList", _g_supported, notify=changed)
    productUuid = Property(str, _g_productUuid, notify=changed)

    # ── QML-invokable actions ──
    @Slot(bool)
    def loadConfigs(self, refresh):
        """Browse the community library for the current device (falls back to the
        DAWN PRO2 family in demo mode)."""
        self._reqLoadIndex.emit(self._g_productUuid(), int(self._bandCount), bool(refresh))

    @Slot(str, str)
    def applyConfig(self, uuid, title):
        """Fetch a community config's curve and audition it live."""
        if not uuid:
            return
        self._pendingTitle = title
        self._reqResolve.emit(uuid, int(self._bandCount))

    @Slot(str)
    def previewConfig(self, uuid):
        """Fetch a community config's curve for the preview popup — no device write."""
        if not uuid:
            return
        self._pendingPreview = uuid
        self._reqPreview.emit(uuid, int(self._bandCount))

    @Slot(str)
    def copyText(self, text):
        """Copy a string (a config's share id) to the system clipboard."""
        from PySide6.QtGui import QGuiApplication
        cb = QGuiApplication.clipboard()
        if cb is not None:
            cb.setText(text or "")
            self.toast.emit("Config ID copied to clipboard.", False)

    @Slot()
    def refresh(self):
        self._reqRefresh.emit()

    @Slot(int, str, float, float, float)
    def commitBand(self, index, ftype, freq, gain, q):
        """A single band was edited in the UI. Cache it, mark dirty, write live."""
        ifreq = int(round(freq))
        for b in self._bands:
            if b["index"] == index:
                b["type"] = ftype
                b["frequency"] = ifreq
                b["gain"] = round(gain, 1)
                b["q"] = round(q, 3)
                break
        self._dirty = True
        self.changed.emit()
        if self._connected:
            self._reqWriteBand.emit(index, ftype, ifreq, float(gain), float(q))

    @Slot(float)
    def setPregain(self, db):
        self._pregain = round(db, 1)
        self._dirty = True
        self.changed.emit()
        if self._connected:
            self._reqPregain.emit(float(db))

    @Slot(float)
    def setGlobalGain(self, db):
        self._globalGain = round(db, 1)
        self._dirty = True
        self.changed.emit()
        if self._connected:
            self._reqGlobal.emit(float(db))

    @Slot("QVariantList", float)
    def applyBands(self, bands, pregain):
        """Preset / bulk apply. bands: list of {index,type,frequency,gain,q}."""
        norm = []
        for b in bands:
            norm.append({
                "index": int(b["index"]), "type": str(b["type"]),
                "frequency": int(round(b["frequency"])),
                "gain": round(float(b["gain"]), 1), "q": round(float(b["q"]), 3),
            })
        self._bands = norm
        if self._supportsPregain:
            self._pregain = round(float(pregain), 1)
        self._dirty = True
        self.changed.emit()
        self.bandsReplaced.emit()
        if self._connected:
            self._reqApplyBands.emit(norm, float(pregain), None if True else 0.0)

    @Slot()
    def saveToFlash(self):
        if self._connected:
            self._reqSave.emit()
        else:
            self._dirty = False
            self.changed.emit()
            self.toast.emit("Demo mode — nothing to save.", False)

    @Slot()
    def revert(self):
        if self._connected:
            self._reqRefresh.emit()
        else:
            self._bands = [dict(b) for b in DEMO_BANDS]
            self._pregain = 0.0
            self._dirty = False
            self.changed.emit()
            self.bandsReplaced.emit()

    @Slot(QUrl)
    def exportJson(self, url):
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        if not path:
            return
        if self._connected:
            self._reqExport.emit(path)
            self.toast.emit("Exported to %s" % os.path.basename(path), False)
        else:
            self.toast.emit("Demo mode — connect a DAC to export its state.", False)

    @Slot(QUrl)
    def importJson(self, url):
        path = url.toLocalFile() if isinstance(url, QUrl) else str(url)
        if not path:
            return
        if self._connected:
            self._reqImport.emit(path)
        else:
            import json
            try:
                with open(path) as fh:
                    data = json.load(fh)
                self._bands = [
                    {"index": f["index"], "type": f["type"], "frequency": f["frequency"],
                     "gain": f["gain"], "q": f["q"]}
                    for f in data.get("filters", [])
                ]
                if "pregain" in data:
                    self._pregain = float(data["pregain"])
                self._dirty = True
                self.changed.emit()
                self.bandsReplaced.emit()
            except Exception as e:
                self.toast.emit("Import failed: %s" % e, True)
