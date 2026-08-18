# Hub Moon

Parametric EQ for Moondrop USB DACs, written to the device's own DSP over USB HID — without the
official web app. The protocol was reverse engineered from https://hub.moondroplab.tech/.

There are two ways in, and **for almost everybody it is the desktop app**. Install a
package for your system and open **Hub Moon** from your launcher — that is all it takes,
and there is no terminal in it.

```bash
hub-moon              # a packaged install: no arguments opens the window
hub-moon --list       # any argument is the command line
hub-moon-gui          # from pip, where bare `hub-moon` prints help instead
```

The window shows the whole device on one screen: the response graph with a draggable handle per
band, the eight band cards, pre-gain with a one-tap **match** that works out the headroom your curve
needs, and a live A/B you hold to hear the headphone without any of it. Behind three buttons it also
has **8,827 AutoEQ corrections for 6,015 headphones**, the ~59,700-curve community library, and
saved profiles of your own. It edits live on the DSP so you hear every move, and only **save to
flash** keeps anything after you unplug. It updates itself, and it explains what it is doing while
it does it.

Everything below documents the **command line**, which is the same engine with a different front.
Reach for it to script something, to drive Hub Moon from another program (see *Building a front-end
on this*), or because you would rather not have a window open. It is not the reduced version — the
GUI imports it — but it is the one that assumes you already know what a shelf filter is.

**New here?** [Install it](https://hubmoon.miyukivigil.tech/install.html), open the app, and press
**Show me around**. The welcome screen also lists every supported DAC, which is the fastest way to
find out whether yours is one of them.

## Scope

This is a USB HID controller for a Moondrop DAC's own DSP, and nothing else. It reads and writes
that chip's parametric EQ, pre-gain, DAC offset and profile slot, and it can browse the Moondrop
Hub's community preset library. It does not touch your system audio, install anything, or run a
software EQ — if there is no supported DAC on the bus, there is nothing here for it to drive.

## Building a front-end on this

The CLI is designed to be driven. `--json` reports the full device state, and everything
device-shaped comes from there rather than being reimplemented in the caller: band count, which slot
custom PEQ lives on, whether the device supports pre-gain at all. This script stays the single
source of truth for the device registry.

`--registry` exists for one reason worth knowing about. Only one process can usefully hold the
hidraw at a time — two readers pick up each other's replies — so anything passive (a tray icon, a
status pill) must not be the second one. `--registry` prints this file's own device table and
touches no hardware, so a front-end can recognise a DAC from USB IDs the system already knows and
never open the device. `--presets` and `--preset` are hardware-free for the same reason. Nothing
downstream needs to hardcode a product ID.

If you are identifying the DAC from the system side, note that **PipeWire is not the source of truth
for playback**, so there are two paths:

- Normally the sink node carries `alsa.components = USB35d8:011d` — the USB pair, no name-matching
  needed — and it updates reactively on hotplug.
- A bit-perfect player (SONE, TIDAL) opens the card *directly* via exclusive ALSA. PipeWire never
  sees that stream, so `defaultAudioSink` will happily report "Speaker" while the music is
  physically going through the DAC — and PipeWire may hold no node for a card it cannot open. So
  the fallback asks the kernel instead: whoever holds `/proc/asound/card*/pcm*p/sub*` outside
  pipewire, identified by that card's `/proc/asound/cardN/usbid` — the same `35d8:011d` pair,
  available whether or not PipeWire has any idea the device exists.

## Requirements

- Python 3
- `pip install -r requirements.txt` (just `hidapi`)

Reading raw HID may work unprivileged depending on your distro's defaults — it does on CachyOS/Arch with a DAWN PRO2. If the tool reports that it failed to open the device, either run it with `sudo` or add a udev rule:

```
# /etc/udev/rules.d/70-moondrop.rules
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="35d8", TAG+="uaccess", MODE="0660"
SUBSYSTEM=="usb",    ATTRS{idVendor}=="35d8", TAG+="uaccess", MODE="0660"
```

Then reload with `sudo udevadm control --reload-rules && sudo udevadm trigger`, **and replug the DAC** — udev does not revisit nodes that already exist.

**Both lines are needed**, because hidapi has two Linux backends and which one you get depends on how you installed:

| Install | hidapi backend | Node it opens |
|---|---|---|
| Distro package, or `pip` against system libs | hidraw | `/dev/hidrawN` |
| Any binary release here — tarball, AppImage, `.deb`, `.rpm`, Arch | libusb | `/dev/bus/usb/BBB/DDD` |

The binary releases all bundle the manylinux `hidapi` wheel, which is libusb-backed. It never opens `/dev/hidrawN` at all, so a hidraw-only rule grants access to a node the program will not look at, and the DAC appears to be missing. `uaccess` hands the device to whoever owns the active login session, which is better than `MODE="0666"` — that made the node world-writable for every user on the machine.

## Supported devices

Vendor ID `0x35D8`. Names, IDs, and the per-device columns below are read out of the official web app's own device registry, and all expose 8 PEQ bands.

**Only the DAWN PRO2 has been tested against real hardware.** Every other row is transcribed from the app and should be treated as untested — plausible, but unproven. Reports welcome.

| Product ID | Device | Tested | Custom-PEQ profile | Pre-gain |
|---|---|---|---|---|
| `0x011B` | Rays | no | 7 | not supported |
| `0x011C` | Marigold | no | 7 | yes |
| `0x011D` | DAWN PRO2 | **yes** | 7 | yes |
| `0x011E` | AG Rays | no | 7 | yes |
| `0x0120` | DHA15 | no | 7 | yes |
| `0x012A` | INN Deco75-DH Audio | no | 7 | yes |
| `0x012B` | Deco Audio System | no | 7 | yes |
| `0x43DA` | MOONRIVER 3 | no | 7 | not supported |
| `0x98D3` | FreeDSP Pro | no | 7 | not supported |
| `0x98D4` | FreeDSP Mini | no | 7 | yes |
| `0x98D5` | E.S. combo | no | **4** | yes |

Rays, MOONRIVER 3, and FreeDSP Pro report no pre-gain support in the app's registry; `--set-pregain` warns and writes anyway, since whether the firmware ignores it is untested.

### Not supported: Old Fashioned (`0x0122`)

The web app lists it, but it does not drive PEQ with biquad coefficients the way every device above does. It writes through device registers (`EQ_REG_BASE 38`, `WRITE_REG 87`) using int8 gain ×10, uint16 frequency, and int16 Q ×1000, exposes 5 bands rather than 8, and reports no pre-gain or global-gain support. None of this tool's commands would mean anything to it, so it is detected and refused rather than driven. `--list` shows it with a note.

## "I changed the EQ and nothing happened"

On a DAWN PRO2 the EQ is toggled **on the hardware**: press both volume buttons to switch between the default (no EQ) mode and custom EQ. If your edits are inaudible, check that first — PEQ writes only affect the sound in custom EQ mode.

That toggle is not reflected in any register we could find: sweeping every readable sub-command (0–254) returns byte-identical data in both modes, so this tool cannot tell you which mode you are in. `--info` reports the active EQ profile, but on firmware 1.5 that reads `9` in *both* modes, and PEQ writes are audible in custom EQ mode regardless. Do not read anything into that number.

For the record, the official app assumes otherwise — it gates PEQ on `readEQIndex() === peqIndex` (7 for this device) — which does not describe firmware 1.5. That check would report "not in PEQ mode" even while custom EQ is plainly working.

## Desktop GUI

There's a desktop app too — a native window, not a web view: one screen with the
control row, the presets, the response graph and all eight bands visible at once.
Hub Moon's own look, a warm rose on a light ground, rather than a copy of the
vendor's near-black. It drives this same file — the GUI imports `moondrop_control`
and calls the hardware-tested engine rather than reimplementing the protocol, so
anything it writes went through the same validation the CLI uses.

```bash
pip install -r gui/requirements.txt     # adds slint; the CLI itself still needs only hidapi
python3 moondrop_control.py --gui       # from a checkout, the flag is how you get the window
```

- **Drag** a numbered handle on the graph to move that band, and **scroll** over the
  plot to widen or narrow the selected one. Each band also has a card of its own: a
  filter-type button (click cycles forward, right-click back), a vertical gain slider,
  and frequency / Q steppers.
- Named spectrum regions — SUB through AIR — are tinted behind the curve, and each band
  card carries the tag of the region its handle sits in.
- Three traces: the flat reference, the **equalized** curve, and a dashed
  **+ pre-gain (output)** curve — what actually leaves the DAC once headroom is paid.
  When the curve would clip, the pre-gain card turns amber and offers a one-tap
  **match**, which drops pre-gain to exactly the headroom the curve needs.
- Edits are **auditioned live** (written to the DSP, not flash) so you hear them as you
  tune; **save to flash** persists. **Revert** goes back to the last saved state — a
  re-read can't do that, since the DSP only ever reports what was written to it last.
- A band the firmware's Q2.30 coefficients can't represent is **clamped**, and its card
  tags itself `limit` — the same ceiling the CLI enforces, computed by the same code.
- Eight starting-point **presets**, JSON **import / export**, and a built-in
  **how to tune** guide.
- **community** browses [Hub](https://hub.moondroplab.tech)'s PEQ library for your
  device — search it locally, and applying one auditions it live, auto-headroomed so a
  loud curve won't clip.
- **No DAC connected?** It opens in a demo mode — a working playground curve — so you can
  see the interface without hardware. Writes light up once a device is found.

Cross-platform via [Slint](https://slint.dev) (Linux/macOS/Windows), compiled to native
widgets — no browser, no web view. It's an optional extra: the plain CLI keeps its
two-dependencies-one-file footprint and only pulls in the toolkit when you actually open
the window.

## Usage

The first connected supported device is used automatically.

```bash
# Discovery
python3 moondrop_control.py --list            # list connected Moondrop devices
python3 moondrop_control.py --info            # firmware, active profile, gains
python3 moondrop_control.py --get-peq         # dump all PEQ slots
python3 moondrop_control.py --registry        # device registry as JSON; opens no device

# Desktop EQ GUI (needs slint). A packaged install opens this from your launcher instead.
python3 moondrop_control.py --gui

# Interactive terminal tuning panel
python3 moondrop_control.py -i

# Gains (dB)
python3 moondrop_control.py --set-pregain -3.5
python3 moondrop_control.py --set-globalgain 0.0
python3 moondrop_control.py --set-eq-index 7  # select EQ profile (custom PEQ is usually 7)

# One PEQ band: INDEX TYPE FREQ GAIN Q
python3 moondrop_control.py --set-peq 0 peaking 1000 -3.0 1.0

# Backup and restore
python3 moondrop_control.py --export-json profile.json
python3 moondrop_control.py --import-json profile.json
python3 moondrop_control.py --import-rew filters.txt   # REW-exported EQ

# Community presets (Moondrop Hub library — reads need no account)
python3 moondrop_control.py --presets                      # for the connected device
python3 moondrop_control.py --presets --pid 011d           # ...or name the device
python3 moondrop_control.py --presets --search harman      # searches the whole library
python3 moondrop_control.py --presets --refresh            # bypass the local cache
python3 moondrop_control.py --preset <uuid>                # one curve, as bands JSON

# Diagnostics and scripting
python3 moondrop_control.py --stream-status   # ALSA sample rate/format (Linux only)
python3 moondrop_control.py --json            # full device state as JSON on stdout

# Version and updates (open no device, install nothing)
python3 moondrop_control.py --version
python3 moondrop_control.py --check-update                 # the stable channel
python3 moondrop_control.py --check-update --channel beta
```

## Updates

The desktop app can check whether a newer Hub Moon has been released: one small
manifest over HTTPS, cached for a day, compared against the running version. There are
two channels — **stable**, published from the `main` branch, and **beta**, published
from `test` — and Settings has a toggle for each.

**It only installs itself where doing so is safe.** Hub Moon ships ten ways, and half
of them are owned by a package manager; an app that overwrites files `dpkg` believes it
owns has broken the system it was trying to update. So it works out how this particular
copy got here and acts accordingly:

| How you installed it | What the update button does |
| --- | --- |
| Windows installer | downloads and runs the new one — it upgrades in place |
| Windows portable zip | swaps the extracted folder and restarts |
| macOS `.app` | mounts the `.dmg`, de-quarantines and re-signs the bundle, swaps it |
| AppImage | replaces the single file and restarts |
| Linux tarball | swaps the unpacked directory and restarts |
| `.deb` / `.rpm` / AUR / Nix | **nothing** — it shows you the right command |
| `pip` / `pipx` | **nothing** — it shows you `pipx upgrade hub-moon` |

Every download is checked against a SHA-256 taken from the manifest, and an asset with
no checksum is refused rather than trusted. That is the whole security model: the
manifest is served over TLS from a domain this project controls, so if the manifest is
authentic the download is. **There is no code signature on any platform** — the Windows
installer is unsigned and the macOS bundle is only ad-hoc signed — so this protects you
against a corrupted download, not against somebody who can serve you a manifest.

Checking defaults to **on** where Hub Moon ships the build itself and **off** where a
package manager owns it, because there is nothing an update check can tell a `pacman`
user that `-Syu` will not. `HUB_MOON_NO_UPDATE_CHECK=1` disables it everywhere without
opening the app.

### Where Hub Moon keeps its files

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| settings | `~/.config/hub-moon` | `~/Library/Application Support/HubMoon` | `%APPDATA%\HubMoon` |
| preset cache | `~/.cache/hub_moon` | `~/Library/Caches/HubMoon` | `%LOCALAPPDATA%\HubMoon\Cache` |
| log | `~/.local/state/hub-moon` | `~/Library/Logs/HubMoon` | `%LOCALAPPDATA%\HubMoon\Logs` |

Every session writes to that log, and an unhandled error — on the UI thread or on any
worker — is recorded there with its traceback. **If you are reporting a bug, that file
is what to attach**; Settings has an *Open log folder* button. Before 1.1.0 every
platform used the Linux paths; an existing config is copied to the new location on
first run, and the Linux paths have not changed.

## Community presets

Moondrop Hub carries a public library of user-made curves — about **59,700** of them
from **19,900** authors. Reading it needs no account, no key and no token, so
`--presets` browses it and `--preset` pulls one down as bands you can apply. This tool
only ever reads: it never publishes, likes or comments (those need a login).

```bash
python3 moondrop_control.py --presets --search "harman" | jq '.presets[0]'
```
```json
{
  "uuid": "4ba6fbe4-6a97-48f1-b487-9d2a640ee30c",
  "title": "水月雨aria2 模拟入耳式耳机使用哈曼2019在HEAD acoustics第三代人工头曲线",
  "author": "rockyuan",
  "downloads": 31072,
  "likes": 604,
  "file": "peq-config-file/fQ0QdflTTrx27gduE14KQpeq.txt"
}
```

Worth knowing:

* **You get your whole device family's presets, not just your model.** The server pools
  by the app's `sharedConfigGroupId`, so a DAWN PRO2 sees ~6,900 curves (its own 1,270
  plus every other FreeDSP-family device) rather than only its own.
* **The index is cached for a day** under `~/.cache/hub_moon/`. It has to be: the API has
  no pagination at all — `productUuid` is the only parameter it honours, and `page` /
  `limit` / `sortBy` return *zero rows* rather than being ignored — so the smallest
  possible request is the entire ~3.6 MB index for your device. `--search` then runs
  locally over all of it, and `--refresh` refetches.
* **Neither `--presets` nor `--preset` opens the DAC** (strace-verified: zero
  `/dev/hidraw` opens, same as `--registry`), so browsing can't collide with a GUI
  that's mid-write.
* **Published presets carry no pre-gain**, unlike AutoEQ. A loud community curve will
  clip unless you set your own headroom — see [Filters this hardware cannot represent](#filters-this-hardware-cannot-represent).
* **Bands with no `filterType` become peaking**, which is what the official app does
  (and it's the common case — most published bands omit the field). See §5.7 of the
  [protocol notes](moondrop_hub_reverse_engineering.md).

The full API — hosts, endpoints, the product-UUID table, and why that table has to be
hardcoded — is documented in [§5 of the protocol notes](moondrop_hub_reverse_engineering.md).

## Notes

- The DSP runs at a fixed 96 kHz internal sample rate; biquad coefficients are computed against that regardless of the playback rate.
- Coefficients use the standard Bristow-Johnson formulas, packed as Q2.30 signed 32-bit integers in the layout `[b0, b1, b2, -a1, -a2]`. This matches the official web app's packing function exactly.
- `--stream-status` reads `/proc/asound` and is Linux-only. Everything else is cross-platform via hidapi.
- `--import-rew` reads REW's exported filter text: it maps `PK`/`LS`/`HS`/`LP`/`HP`, honours the `Preamp` line, and disables any bands the file doesn't define. Filters outside the device's band count are skipped with a note.
- Read/write flags compose and are applied in a fixed order, with `--save-flash` last — so `--set-peq 0 peaking 1000 -3 1 --no-flash --save-flash` previews then persists. `--json` and `-i` are exclusive modes and ignore the rest.

### Filters this hardware cannot represent

Q2.30 spans only [-2, 2), and some otherwise reasonable filters need coefficients outside it. This tool refuses those with an error naming the safe ceiling. Two cases:

- **`b1 < -2`** — a `high_shelf` above roughly +5 dB (at 8 kHz / Q=0.7 the ceiling is about +4.7 dB), or a `high_shelf` with a corner below roughly 200 Hz at *any* gain.
- **`b0 > 2`** — any type at high gain, low Q, and high frequency, e.g. `peaking 20000 12 0.3`.

The official web app allows up to +12 dB and does **not** clamp. Its JS packs coefficients with bitwise ops, which wrap modulo 2³² instead of failing, so past these limits it silently programs a filter unrelated to the curve it draws — a +6 dB shelf's `b1` wraps from -2.303 to +1.697, flipping sign. This tool rejects rather than reproduce that. What the firmware would actually do with a wrapped coefficient is untested.

## Thanks

Hub Moon was written on one Linux machine against one DAWN PRO2. Everything it can claim
beyond that setup, it can claim because someone else ran it and said what happened.

- **[AndrewYii](https://github.com/AndrewYii)** — ran the packaged build on
  **Windows 11**: it installs, launches, the interface renders, and the community library
  loads and searches. No DAC was attached.
- **[NyChieng](https://github.com/NyChieng)** — ran the same build on a second **Windows**
  machine, again through the interface and the community browser. One report is an
  anecdote; two on different machines is the difference between "it worked once" and
  something a stranger can be told to download.

A second pair of hands on a second platform is worth more than it sounds — if you run it
on a device or an OS listed as untested below, please open an issue and say so.

## Disclaimer

Unofficial and not affiliated with Moondrop. The USB HID protocol here was reverse engineered from the official web app with the assistance of AI. The coefficient packing and PEQ byte layout have since been checked against that app's own JavaScript and match it exactly, but the command set is still inferred from observed behaviour rather than any documented spec — treat it as a best-effort reconstruction that works on the hardware it was tested against, not as authoritative.

This script is tested with the Moondrop Dawn Pro 2 only, which works as intended though further ironing is neccessary in my opinion. Other devices requires further testing by other people who owns the other devices mentioned above.

### What has actually been exercised on hardware

On a DAWN PRO2 (`0x011D`, firmware 1.5): `--list`, `--info`, `--get-peq`, `--json`, `--export-json`, `--import-json`, `--import-rew`, `--stream-status` both idle and while playing, `--save-flash`, and a full `--import-json` round-trip that wrote 8 bands plus both gains to flash and compared byte-identical to the backup afterwards.

Most importantly, PEQ writes were confirmed **audible**: a `low_pass` at 800 Hz written live in custom EQ mode audibly muffled playback, and restoring the original band returned it to normal. The write path is not just accepted by the device, it demonstrably changes the sound.

Flash persistence was confirmed across a physical unplug/replug: the flashed config survived the power cycle byte-identical, and writes made with `--no-flash` correctly did *not* survive.

`--presets` / `--preset` were verified against the live Moondrop Hub library (6,911 presets for the DAWN PRO2's device family), and both are strace-confirmed to open zero `/dev/hidraw` handles — same as `--registry`.

The desktop GUI was exercised on the same DAWN PRO2: device reads, live band writes through the GUI's own code path with a verified read-back, drag-to-edit on the graph, presets, JSON import/export, the community library, and both graph views. It was also run under a fontconfig sandbox with Material Symbols removed, to confirm the icons render without any font installed.

On **Windows**, the packaged build was exercised by [AndrewYii](https://github.com/AndrewYii)
(Windows 11) and [NyChieng](https://github.com/NyChieng) on a second machine: installation,
launch, the interface, and the community library. **Neither had a DAC attached**, so USB HID
reads and writes on Windows are still untested — see [Thanks](#thanks).

On **macOS**, the packaged `.dmg` was installed and run on an **M4 MacBook Air** (Apple
Silicon). It is ad-hoc signed rather than notarized, so the first launch needs a
right-click → Open.

The 1.1.0 work was verified on Linux: the quit crash was reproduced against CPython
3.12 — the version the release builds are made with — and the fix confirmed on 3.11,
3.12 and 3.14; the update check, channel switching, download, checksum verification and
refusal-on-mismatch were all exercised against a local manifest; and the frozen
PyInstaller bundle was built and run, quitting cleanly with exit code 0.

**The Windows and macOS fixes in 1.1.0 have not been run on Windows or macOS.** The quit
crash is platform-independent and its fix is proven; the file-chooser, config-path and
window-icon changes are not — they were written against each platform's documented
behaviour and compile, but nobody has yet double-clicked the 1.1.0 `.exe`. The
self-update paths for the Windows installer, the portable zip and the `.app` are
likewise unrun outside review; the AppImage and tarball paths were exercised on Linux.

Not yet exercised on hardware: the interactive panel (`-i`), **USB HID on Windows**, and
**every device other than the DAWN PRO2**.

It writes to your DAC's flash. Export a backup with `--export-json` before experimenting.
