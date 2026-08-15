# Changelog

All notable changes to **moondrop_control.py** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-08-15

First tagged release, so everything below is the starting feature set rather than a
diff against a previous version.

### Added

- **Packaging** — a root `pyproject.toml` makes Hub Moon a proper installable
  package (`hub-moon` CLI + `hub-moon-gui` windowed entry points, the `.slint`
  sources shipped as package data, slint an optional `[gui]` extra). On top of it,
  `packaging/` builds an installer for every platform from one PyInstaller spec:
  - **Windows** — a self-contained `.exe` plus an **Inno Setup** installer
    (`hub-moon.iss`, Start-menu/uninstaller) and a portable zip; `.ico` icon.
  - **macOS** — a `Hub Moon.app` (`BUNDLE` step, `.icns` icon) packaged as a
    drag-to-Applications **`.dmg`** for Apple Silicon. Ad-hoc signed, not notarized
    (Gatekeeper needs a right-click → Open on first launch).
  - **Linux** — a portable tarball, a single-file **AppImage** (`build-appimage.sh`),
    and **`.deb`** + **`.rpm`** via `nfpm`, plus an Arch `PKGBUILD` and a `flake.nix`
    for Nix. All install the `70-moondrop.rules` udev rule, a `.desktop` launcher
    and an icon.

  Three **GitHub Actions** workflows (`build-windows.yml`, `build-linux.yml`,
  `build-macos.yml`) build each on its own runner and attach the assets to a
  tagged Release. See `packaging/README.md`. (Verified on this Linux box: the
  wheel, Arch package, PyInstaller bundle, AppImage and `.deb`. Windows/macOS/Nix
  use the same spec/configs but need their own OS.)
- **Desktop GUI** (`--gui`) — a native window built with [Slint](https://slint.dev),
  in Hub Moon's own look: a warm rose on a light ground, or a dark palette re-picked
  rather than inverted, with six accents to choose from. One screen shows everything —
  device slot, pre-gain and global offset; eight preset pills; the response graph; all
  eight bands; and the actions. The graph carries named spectrum regions (SUB → AIR)
  behind a numbered, draggable handle per band, with three traces: a flat reference,
  the equalised curve, and a dashed `+ pre-gain (output)` curve showing what actually
  leaves the DAC. A second graph view drops the handles for the vendor chart's framing
  — one curve, normalised to a 60 dB reference. Drag a handle to move a band, scroll to
  change its Q, or use the band cards below: filter type, a gain fader, and frequency /
  Q steppers.

  **community** browses the Moondrop Hub library for the connected device; clicking a
  config opens a preview of its response curve, and nothing is written until you apply.
  There is a first-run welcome screen, a built-in tuning guide, JSON import/export via
  the desktop's own file chooser, and keyboard shortcuts (`esc` closes, `ctrl+S` saves
  to flash).

  It imports this file's engine rather than reimplementing the protocol — every write
  goes through the same `write_peq_index` and the same Q2.30 ceiling the CLI enforces,
  and a band the firmware cannot represent is clamped with its card tagged `limit`. All
  HID I/O runs on one worker thread so a read never interleaves with a write; the
  community library and the file dialogs get threads of their own. Edits are auditioned
  live on the DSP and only `save to flash` persists, while `revert` restores the last
  saved state — which a re-read cannot do, since the DSP reports whatever was written to
  it last. Icons are vector outlines generated from Material Symbols by
  `tools/build-icons.py`, so no font ships and none has to be found at runtime. With no
  DAC present it opens on a demo curve you can play with. slint is an optional extra
  (`gui/requirements.txt`), lazy-imported so the CLI keeps its hidapi-only footprint.
- **Read/write control of Moondrop USB DACs over USB HID** — parametric EQ bands,
  pre-gain, global offset, active EQ profile, and firmware version, without the
  official web app. `--list`, `--info`, `--get-peq`, `--set-peq`, `--set-pregain`,
  `--set-globalgain`, `--set-eq-index`.
- **Backup and restore** — `--export-json` / `--import-json` for a full device
  snapshot, and `--import-rew` for AutoEQ / REW `ParametricEQ.txt` files.
- **`--json`** — full device state on stdout for GUIs to consume, so a front-end
  never has to hardcode the device registry.
- **Community presets** — `--presets` browses the ~59,700-curve public library behind
  Moondrop Hub (with `--search` over the whole index, and a day-long cache under
  `~/.cache/hub_moon`), and `--preset <uuid>` pulls one down as bands. Reads need no
  account; publishing/liking are deliberately not implemented. Neither flag opens the
  DAC (strace-verified zero `/dev/hidraw` opens), so browsing can't collide with a GUI
  that is mid-write. `--registry` now also reports each device's `product_uuid`, which
  has to be hardcoded because the API's own `products/all` reports `pid: null` for all
  102 products.
- **`--no-flash` / `--save-flash`** — apply to the DSP live for auditioning, then
  persist deliberately. Writes go to flash by default.
- **`-i` interactive tuning panel** — a terminal dashboard for the same controls.
- **`--stream-status`** — hardware-level ALSA stream diagnostics (sample rate, bit
  format, supported rates). Linux-only; everything else is cross-platform.

### Protocol notes

Findings from reverse-engineering the official web app, all verified against its
JavaScript and — where marked — against real hardware. Documented in full in
[moondrop_hub_reverse_engineering.md](moondrop_hub_reverse_engineering.md).

- **Coefficient packing is Q2.30, layout `[b0, b1, b2, -a1, -a2]`**, scaled by
  2^30, computed against a fixed 96 kHz DSP rate. Confirmed byte-for-byte against
  the web app's own packing function, and corroborated by an independent
  reimplementation.
- **Filters the firmware cannot represent are refused, not wrapped.** Q2.30 spans
  only [-2, 2), which some reasonable filters exceed: a `high_shelf` above roughly
  +5 dB, a `high_shelf` with a corner below ~200 Hz at any gain, or any type at
  high gain + low Q + high frequency. The official app does not clamp, and its JS
  packs with bitwise operators that wrap modulo 2^32 — so past those limits it
  silently programs a filter unrelated to the curve it draws (a +6 dB shelf's `b1`
  wraps from -2.303 to +1.697, flipping sign). This tool refuses and reports the
  largest gain that fits. What the firmware does with a wrapped coefficient is
  untested.
- **The device registry is transcribed from the app**, correcting a scrambled
  name/ID mapping: `0x011D` is DAWN PRO2 (confirmed against real hardware),
  `0x43DA` is MOONRIVER 3, `0x011B` is Rays. "Rays Pro" does not exist. E.S. combo
  uses custom-PEQ profile slot 4; every other supported device uses 7.
- **Old Fashioned (`0x0122`) is detected but refused.** It does not use biquad
  coefficients at all — it writes PEQ through device registers as int8 gain ×10 /
  uint16 frequency / int16 Q ×1000, exposes 5 bands, and reports no pre-gain or
  global-gain support. None of this tool's commands would mean anything to it.
- **The active EQ profile is not a PEQ-mode indicator.** The official app gates
  edits on `readEQIndex() === peqIndex`, which does not hold: a DAWN PRO2 on
  firmware 1.5 reports profile 9 in *both* its EQ-off and custom-EQ modes, while
  band writes are plainly audible in custom-EQ mode. On that device the EQ toggle
  is hardware (both volume buttons) and is not reflected in any readable register —
  a sweep of every sub-command 0–254 returns identical data in both modes.
- **HID replies must be matched to their request.** A response echoes the command
  and sub-command it answers at bytes 1–2. Commands that never reply would
  otherwise leave the next read picking up the previous command's report, shifting
  every subsequent read by one and silently returning another register's data.

### Verified on hardware

DAWN PRO2 (`0x011D`, firmware 1.5): discovery, info, PEQ read, JSON export/import,
REW import, stream status idle and playing, live band writes confirmed **audible**
(an 800 Hz low-pass audibly muffled playback, restoring the band returned it to
normal), and a full flash round-trip that survived a physical unplug/replug
byte-identical — while `--no-flash` writes correctly did not survive.

Not exercised on hardware: the interactive panel (`-i`), and **every device other
than the DAWN PRO2** — those names and IDs come from the app's registry only.
