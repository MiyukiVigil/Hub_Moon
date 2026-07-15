# moondrop_control.py

A command-line tool for controlling Moondrop USB DACs over USB HID — read and write the parametric EQ, pre-gain, and DAC offset without the official web app. The protocol was reverse engineered from https://hub.moondroplab.tech/.

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

PEQ bands are only audible when the device's **active EQ profile** is its custom-PEQ profile (7 on every supported device except E.S. combo, which uses 4). On any other profile the bands are stored but a preset is what's playing — the official app gates on exactly this (`isInPEQMode: readEQIndex() === peqIndex`).

`--info` shows both, and `--set-peq` warns when they disagree:

```
Active EQ Profile: 9 (custom PEQ is 7)
```

Switch with `--set-eq-index 7`.

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
```

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

## Disclaimer

Unofficial and not affiliated with Moondrop. The USB HID protocol here was reverse engineered from the official web app with the assistance of AI. The coefficient packing and PEQ byte layout have since been checked against that app's own JavaScript and match it exactly, but the command set is still inferred from observed behaviour rather than any documented spec — treat it as a best-effort reconstruction that works on the hardware it was tested against, not as authoritative.

This script is tested with the Moondrop Dawn Pro 2 only, which works as intended though further ironing is neccessary in my opinion. Other devices requires further testing by other people who owns the other devices mentioned above.

### What has actually been exercised on hardware

On a DAWN PRO2 (`0x011D`, firmware 1.5): `--list`, `--info`, `--get-peq`, `--json`, `--stream-status`, `--export-json`, a live `--set-peq` with `--no-flash` read back and restored, `--save-flash`, and a full `--import-json` round-trip that wrote 8 bands plus both gains to flash and compared byte-identical to the backup afterwards.

Not yet exercised on hardware: `--import-rew`, the interactive panel (`-i`), the "playing" branch of `--stream-status`, persistence across a physical replug, and **every device other than the DAWN PRO2**.

It writes to your DAC's flash. Export a backup with `--export-json` before experimenting.
