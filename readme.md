# moondrop_control.py

A command-line tool for controlling Moondrop USB DACs over USB HID — read and write the parametric EQ, pre-gain, and DAC offset without the official web app. The protocol was reverse engineered from https://hub.moondroplab.tech/.

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
python3 moondrop_control.py --registry        # device registry as JSON; opens no device

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

# Community presets (Moondrop Hub library — reads need no account)
python3 moondrop_control.py --presets                      # for the connected device
python3 moondrop_control.py --presets --pid 011d           # ...or name the device
python3 moondrop_control.py --presets --search harman      # searches the whole library
python3 moondrop_control.py --presets --refresh            # bypass the local cache
python3 moondrop_control.py --preset <uuid>                # one curve, as bands JSON

# Diagnostics and scripting
python3 moondrop_control.py --stream-status   # ALSA sample rate/format (Linux only)
python3 moondrop_control.py --json            # full device state as JSON on stdout
```

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

## Disclaimer

Unofficial and not affiliated with Moondrop. The USB HID protocol here was reverse engineered from the official web app with the assistance of AI. The coefficient packing and PEQ byte layout have since been checked against that app's own JavaScript and match it exactly, but the command set is still inferred from observed behaviour rather than any documented spec — treat it as a best-effort reconstruction that works on the hardware it was tested against, not as authoritative.

This script is tested with the Moondrop Dawn Pro 2 only, which works as intended though further ironing is neccessary in my opinion. Other devices requires further testing by other people who owns the other devices mentioned above.

### What has actually been exercised on hardware

On a DAWN PRO2 (`0x011D`, firmware 1.5): `--list`, `--info`, `--get-peq`, `--json`, `--export-json`, `--import-json`, `--import-rew`, `--stream-status` both idle and while playing, `--save-flash`, and a full `--import-json` round-trip that wrote 8 bands plus both gains to flash and compared byte-identical to the backup afterwards.

Most importantly, PEQ writes were confirmed **audible**: a `low_pass` at 800 Hz written live in custom EQ mode audibly muffled playback, and restoring the original band returned it to normal. The write path is not just accepted by the device, it demonstrably changes the sound.

Flash persistence was confirmed across a physical unplug/replug: the flashed config survived the power cycle byte-identical, and writes made with `--no-flash` correctly did *not* survive.

`--presets` / `--preset` were verified against the live Moondrop Hub library (6,911 presets for the DAWN PRO2's device family), and both are strace-confirmed to open zero `/dev/hidraw` handles — same as `--registry`.

Not yet exercised on hardware: the interactive panel (`-i`), and **every device other than the DAWN PRO2**.

It writes to your DAC's flash. Export a backup with `--export-json` before experimenting.
