# moondrop_control.py

A command-line tool for controlling Moondrop USB DACs over USB HID — read and write the parametric EQ, pre-gain, and DAC offset without the official web app. The protocol was reverse engineered from https://hub.moondroplab.tech/.

## A UI, if you would rather not type

There is a full graphical control panel for this script: a live, region-labelled frequency-response
graph you can drag bands on (scroll to change Q), an all-bands column editor, eight one-tap presets,
AutoEQ / REW import through a file browser built into the panel, revert, and save-to-flash. Edits go
to the DSP in real time and persist only when you save.

It currently ships **only inside [sea-shell](https://seashell.miyukivigil.tech/)**
(`SUPER+SHIFT+E`) — a Hyprland rice, which vendors this script and drives it over its `--json` /
`--set-*` interface. Everything device-shaped comes from `--json` rather than being reimplemented
there: band count, which slot custom PEQ lives on, whether the device supports pre-gain at all. This
script stays the single source of truth for the device registry.

The panel is Quickshell QML, so it wants Hyprland 0.55 and Quickshell 0.3. There is no standalone
GTK/Qt build — on anything else, the CLI below is the interface.

## Requirements

- Python 3
- `pip install -r requirements.txt` (just `hidapi`)

Reading raw HID may work unprivileged depending on your distro's defaults — it does on CachyOS/Arch with a DAWN PRO2. If the tool reports that it failed to open the device, either run it with `sudo` or add a udev rule:

```
# /etc/udev/rules.d/70-moondrop.rules
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="35d8", MODE="0666"
```

Then reload with `sudo udevadm control --reload-rules && sudo udevadm trigger`.

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

## Usage

The first connected supported device is used automatically.

```bash
# Discovery
python3 moondrop_control.py --list            # list connected Moondrop devices
python3 moondrop_control.py --info            # firmware, active profile, gains
python3 moondrop_control.py --get-peq         # dump all PEQ slots

# Interactive tuning panel
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

# Diagnostics and scripting
python3 moondrop_control.py --stream-status   # ALSA sample rate/format (Linux only)
python3 moondrop_control.py --json            # full device state as JSON on stdout

# Universal (software) EQ — works on ANY output device, no Moondrop DAC needed
python3 moondrop_control.py --to-pipewire eq.conf --from-rew ParametricEQ.txt
python3 moondrop_control.py --to-pipewire eq.conf --from-json profile.json
python3 moondrop_control.py --to-pipewire eq.conf   # mirror the connected DAC's bands
```

## Universal EQ: the same curves on any hardware

> **Scope, honestly.** This one exists mainly for [sea-shell](https://seashell.miyukivigil.tech/)'s
> sake — it is the software-EQ escape hatch its DAC panel exports, so a curve you tuned on a
> Moondrop can follow you onto hardware that panel cannot drive. It is **not really core to
> `moondrop_control.py`**, which is a USB HID controller for a DAC's own DSP; everything else here
> is about talking to that chip. Treat `--to-pipewire` as a companion feature aimed at the panel
> rather than a pillar of this tool, and don't be surprised if it eventually lives closer to
> sea-shell than to this script. Nothing about it is managed for you either — the tool writes
> `eq.conf` and stops there; installing it into PipeWire is your job, in both the CLI and the panel.

The PEQ above runs on the DAC's own DSP chip, so it only exists on the devices listed above. `--to-pipewire` renders the same filters as a [PipeWire filter-chain](https://docs.pipewire.org/page_module_filter_chain.html) instead — software EQ that applies to *anything*: another brand's DAC, laptop speakers, Bluetooth.

With `--from-rew` or `--from-json` it needs **no Moondrop hardware connected at all**, so an AutoEQ preset for your headphones becomes a system-wide EQ:

```bash
python3 moondrop_control.py --to-pipewire eq.conf --from-rew ParametricEQ.txt
cp eq.conf ~/.config/pipewire/pipewire.conf.d/
systemctl --user restart pipewire pipewire-pulse
```

Then select the **Universal EQ** sink as your output; it feeds your real device. To remove it, delete the file and restart PipeWire again.

Two things differ from the hardware path, both in software's favour: floating-point biquads have no Q2.30 limit, so the shelf gains the DAC must refuse work fine; and it isn't pinned to 96 kHz, since PipeWire recomputes per graph rate.

### Filter types

`disabled`, `peaking`, `low_shelf`, `high_shelf`, `low_pass`, `high_pass`

### Flash behaviour

Writes are saved to device flash by default so they survive a reconnect. For live previewing (e.g. from a GUI), pass `--no-flash` to apply to the DSP only, then persist later with `--save-flash`:

```bash
python3 moondrop_control.py --set-peq 0 peaking 1000 -3.0 1.0 --no-flash
python3 moondrop_control.py --save-flash
```

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

## The site

`site/` holds the project page: three static files and hand-written CSS, no build step and no
dependencies. `site/dsp.js` is a port of this script's own biquad maths — same shelf-slope form,
same Q2.30 packing — so the draggable plot on the page refuses exactly where the firmware would.
It is checked against the Python rather than trusted: see the note at the top of that file.

The folder is deliberately kept out of git (`.gitignore`), so there is no repo for Cloudflare Pages
to build from. Deploys go straight from a working copy:

```bash
npx wrangler pages deploy      # wrangler.toml sets pages_build_output_dir = "site"
```

To preview it locally, **serve** it rather than opening the file — `app.js` is an ES module, so
`file://` will refuse to load it and the plot will not draw:

```bash
python3 -m http.server -d site 8000
```

## Disclaimer

Unofficial and not affiliated with Moondrop. The USB HID protocol here was reverse engineered from the official web app with the assistance of AI. The coefficient packing and PEQ byte layout have since been checked against that app's own JavaScript and match it exactly, but the command set is still inferred from observed behaviour rather than any documented spec — treat it as a best-effort reconstruction that works on the hardware it was tested against, not as authoritative.

This script is tested with the Moondrop Dawn Pro 2 only, which works as intended though further ironing is neccessary in my opinion. Other devices requires further testing by other people who owns the other devices mentioned above.

### What has actually been exercised on hardware

On a DAWN PRO2 (`0x011D`, firmware 1.5): `--list`, `--info`, `--get-peq`, `--json`, `--export-json`, `--import-json`, `--import-rew`, `--stream-status` both idle and while playing, `--save-flash`, and a full `--import-json` round-trip that wrote 8 bands plus both gains to flash and compared byte-identical to the backup afterwards.

Most importantly, PEQ writes were confirmed **audible**: a `low_pass` at 800 Hz written live in custom EQ mode audibly muffled playback, and restoring the original band returned it to normal. The write path is not just accepted by the device, it demonstrably changes the sound.

Flash persistence was confirmed across a physical unplug/replug: the flashed config survived the power cycle byte-identical, and writes made with `--no-flash` correctly did *not* survive.

`--to-pipewire` was verified by loading the generated config into a running PipeWire — the sink registers with correct FL/FR ports and no errors.

Not yet exercised on hardware: the interactive panel (`-i`), and **every device other than the DAWN PRO2**.

It writes to your DAC's flash. Export a backup with `--export-json` before experimenting.
