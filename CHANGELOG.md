# Changelog

All notable changes to **moondrop_control.py** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Initial release — nothing is tagged yet, so everything below is the starting
feature set rather than a diff against a previous version.

### Added

- **Read/write control of Moondrop USB DACs over USB HID** — parametric EQ bands,
  pre-gain, global offset, active EQ profile, and firmware version, without the
  official web app. `--list`, `--info`, `--get-peq`, `--set-peq`, `--set-pregain`,
  `--set-globalgain`, `--set-eq-index`.
- **Backup and restore** — `--export-json` / `--import-json` for a full device
  snapshot, and `--import-rew` for AutoEQ / REW `ParametricEQ.txt` files.
- **`--json`** — full device state on stdout for GUIs to consume, so a front-end
  never has to hardcode the device registry.
- **`--no-flash` / `--save-flash`** — apply to the DSP live for auditioning, then
  persist deliberately. Writes go to flash by default.
- **`-i` interactive tuning panel** — a terminal dashboard for the same controls.
- **`--stream-status`** — hardware-level ALSA stream diagnostics (sample rate, bit
  format, supported rates). Linux-only; everything else is cross-platform.
- **Universal (software) EQ via `--to-pipewire`** — render the same filters as a
  PipeWire filter-chain, which applies to *any* output device rather than only a
  Moondrop DAC. With `--from-json` / `--from-rew` it needs no Moondrop hardware
  connected at all, so an AutoEQ file can be turned into a software EQ directly.
  Software biquads are floating point and so are not subject to the firmware's
  coefficient limits below.

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

The PipeWire config was verified by loading it into a running PipeWire: the sink
registers with correct FL/FR ports and no errors.

Not exercised on hardware: the interactive panel (`-i`), and **every device other
than the DAWN PRO2** — those names and IDs come from the app's registry only.
