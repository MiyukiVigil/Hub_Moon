# Moondrop Audio DAC USB HID Protocol Analysis

This document provides a reverse-engineered specification of the WebHID protocol used by the official [Moondrop Hub Sound-Tuning Tool](https://hub.moondroplab.tech/) to communicate with and control Moondrop USB DACs and DSP cables (e.g. FreeDSP Pro, DAWN PRO2, Rays).

It also details how to utilize this protocol natively on Linux to read/write EQ settings, change volume offset, pre-gain, and select EQ profiles.

> [!NOTE]
> **Provenance.** This was reverse engineered from the official web app with the assistance of AI. The device registry, byte layouts, and coefficient packing in this document were read directly out of the app's own JavaScript bundle (`/assets/index-CaVUzXsg.js`, app version 1.2.31) and are quoted below where it matters. That makes them a faithful record of *what the app does* — not a vendor spec, and not a substitute for testing against hardware. Only the DAWN PRO2 has been exercised against a real device. Sections marked **UNVERIFIED** rest on the JS alone.

---

## 1. Supported Devices & USB Identification

Moondrop USB DACs and audio cables expose a **USB HID (Human Interface Device)** interface alongside their standard UAC (USB Audio Class) interface. This allows software to change hardware registers in real time without interrupting audio playback.

* **USB Vendor ID (VID):** `0x35D8` (13784)
* **HID Report ID:** `75` (`0x4B`)
* **Internal DSP Sample Rate:** Hardcoded to **96 kHz (`96000` Hz)** across all devices for EQ calculations.

The app's registry is a single array; every entry defaults to the same vendor ID:

```js
gv=13784
vv=[{Class:Su,pid:283,brand:"MOONDROP"},{Class:mu,pid:286,brand:"MOONDROP"}, ...]
```

### Coefficient-protocol devices

These 11 devices all program PEQ by writing Q2.30 biquad coefficients (§4), and all expose 8 bands.

| Device Name | PID | Hex PID | Device Type | PEQ profile slot | Pre-gain | Global gain |
|:---|:---|:---|:---|:---|:---|:---|
| **Rays** | `283` | `0x011B` | Headphone | 7 | **no** | yes |
| **Marigold** | `284` | `0x011C` | Headphone | 7 | yes | yes |
| **DAWN PRO2** | `285` | `0x011D` | Audio Device | 7 | yes | yes |
| **AG Rays** | `286` | `0x011E` | Headphone | 7 | yes | yes |
| **DHA15** | `288` | `0x0120` | Audio Device | 7 | yes | yes |
| **INN Deco75-DH Audio** | `298` | `0x012A` | Audio Device | 7 | yes | yes |
| **Deco Audio System** | `299` | `0x012B` | Audio Device | 7 | yes | yes |
| **MOONRIVER 3** | `17370` | `0x43DA` | Audio Device | 7 | **no** | yes |
| **FreeDSP Pro** | `39123` | `0x98D3` | Audio Device | 7 | **no** | yes |
| **FreeDSP Mini** | `39124` | `0x98D4` | Audio Device | 7 | yes | yes |
| **E.S. combo** | `39125` | `0x98D5` | Audio Device | **4** | yes | yes |

Two details are easy to get wrong here:

* **E.S. combo uses PEQ profile slot 4**, not 7. This is the `peqIndex` field, and it is written into the last byte of every EQ update (§3.5). Hardcoding 7 targets the wrong slot on that device.
* **Rays, MOONRIVER 3, and FreeDSP Pro report `supportPreGain:false`.** The official app never writes pre-gain to them. Whether the firmware ignores such a write is **UNVERIFIED**.

`Rays`, `AG Rays`, `Marigold`, and `E.S. combo` additionally set `disabledFilterType:[HIGH_SHELF_2, LOW_SHELF_2]`, hiding shelf filters in the UI.

### Not a coefficient device: Old Fashioned (`290` / `0x0122`)

The Old Fashioned appears in the same registry but does **not** share this protocol. It uses register access with plain scaled values, no biquad math:

```js
Ji={EQ_REG_BASE:38,WRITE_REG:87,SAVE_REG:83,READ_REG:82}
sr={ADDR:0,CMD:4,DATA_SLOT_GAIN:6,DATA_SLOT_Q:6,DATA_SLOT_FREQUENCY:8}
or={SCALE_GAIN:10,SCALE_Q:1e3,GAIN_MIN:-12.8,GAIN_MAX:12.7,GAIN_INT8_MIN:-128,GAIN_INT8_MAX:127,PACKET_LEN:10,DELAY_MS:100}
```

* Gain: **int8**, scaled ×10, clamped to [-12.8, +12.7]
* Frequency: **uint16** little-endian
* Q: **int16**, scaled ×1000
* 5 PEQ bands, profile slot `0`, gain range `[-12, +3]`
* `supportPreGain:false`, `supportGlobalGain:false`, `supportFilter:false`

Its class defines `readSinglePEQ` / `writeSinglePEQ` / `readRegister` / `writeRegister` and never calls the coefficient builder. **Nothing in §3.5 or §4 applies to it.** A tool that speaks only the coefficient protocol cannot drive an Old Fashioned and should refuse rather than send it meaningless reports.

---

## 2. Protocol & Command Frame Structure

Communication is done through standard 64-byte HID output and input reports.

### Output Command Frame (Host to Device)
All commands are sent as a 64-byte payload. The first byte must be the report ID:

| Byte Offset | Field | Value / Description |
|:---|:---|:---|
| `0` | **Report ID** | `75` (`0x4B`) |
| `1` | **Command Category** | `1` (Write) or `128` / `0x80` (Read) |
| `2` | **Sub-Command ID** | Specific registers (e.g., EQ, Pre-Gain, Version) |
| `3` | **Length Indicator** | Usually `0` |
| `4+` | **Data Payload** | Varies by command (padded with `0`s to 64 bytes total) |

### Input Response Frame (Device to Host)
Responses sent back from the device are also received with Report ID `75` (`0x4B`):

| Byte Offset | Field | Value / Description |
|:---|:---|:---|
| `0` | **Report ID** | `75` (`0x4B`) |
| `1` | **Response Status** | Matches the command code |
| `2` | **Sub-Command Echo** | Matches the sent sub-command |
| `3` | **Status / Padding** | Usually `0` |
| `4+` | **Data Payload** | Requested values (e.g., version text, EQ indices, gains) |

> [!NOTE]
> Since standard Linux `hidapi` includes the Report ID as the first byte of both incoming and outgoing buffers, the indexes in Python are shifted by 1 relative to raw web browser buffers.

---

## 3. Command Registers Reference

### 3.1. Firmware Version (Read)
Queries the ASCII-encoded firmware version string.
* **Send:** `[128, 12, 0]` -> `[CMD_READ, SUB_FIRMWARE_VERSION, 0]`
* **Receive:** Starts at byte `4` (Python index). Read bytes until `0x00` (null-terminator) and decode as UTF-8.

### 3.2. Preset EQ Profile Selection (Read / Write)
Reads or selects the active EQ preset slot.
* **Read Send:** `[128, 15, 0]` -> `[CMD_READ, SUB_ACTIVE_EQ, 0]`
* **Read Receive:** Byte `4` contains the active EQ index integer.
* **Write Send:** `[1, 15, 0, index]` -> `[CMD_WRITE, SUB_ACTIVE_EQ, 0, index]`
  * To write custom PEQ parameters, select the device's custom-PEQ profile slot before writing coefficients. This is `7` on every supported device **except E.S. combo, which uses `4`** — see the table in §1.

### 3.3. Pre-Gain (Read / Write)
Pre-Gain attenuates the input signal to prevent digital clipping when adding positive gain values.
* **Read Send:** `[128, 35, 0]` -> `[CMD_READ, SUB_PRE_GAIN, 0]`
* **Read Receive:** Bytes `4` and `5` (16-bit signed little-endian integer). The gain value in dB is:
  $$\text{PreGain} = \frac{\text{Value}}{256.0}$$
* **Write Send:** `[1, 35, 0, low_byte, high_byte]` where bytes represent:
  $$\text{Value} = \text{round}(\text{Gain (dB)} \times 256)$$
* Not offered by the app on Rays, MOONRIVER 3, or FreeDSP Pro (§1).

### 3.4. Global DAC Volume Offset (Read / Write)
* **Read Send:** `[128, 3, 0]` -> `[CMD_READ, SUB_DAC_OFFSET, 0]`
* **Read Receive:** Bytes `4` and `5` (16-bit signed little-endian integer) divided by 256.0.
* **Write Send:** `[1, 3, 0, low_byte, high_byte]` where bytes represent the value scaled by 256.0.

### 3.5. Write PEQ Band Parameters (Write)
Updates parameters for a specific Parametric EQ band index (0 to 7).
* **Write Send:** A custom 63-byte payload:
  * Byte `1`: `CMD_WRITE` (`1`)
  * Byte `2`: `SUB_UPDATE_EQ` (`9`)
  * Byte `3-4`: `0`
  * Byte `5`: **Band Index** (`0` to `7`)
  * Byte `6-7`: `0`
  * Byte `8 to 27` (20 bytes): **Biquad Coefficients** (5 signed 32-bit little-endian integers, scaled by $2^{30}$)
  * Byte `28-29`: **Frequency (Hz)** (16-bit unsigned little-endian)
  * Byte `30-31`: **Q-Factor** (16-bit unsigned little-endian, scaled by 256)
  * Byte `32-33`: **Gain (dB)** (16-bit signed little-endian, scaled by 256)
  * Byte `34`: **Filter Type ID** (0 = Disabled, 1 = Low Shelf, 2 = Peaking, 3 = High Shelf, 4 = Low Pass, 5 = High Pass)
  * Byte `35`: `0`
  * Byte `36`: **PEQ profile slot** — the device's `peqIndex`: `7` on all supported devices except E.S. combo (`4`)
* **Enable Registers:** After writing the parameters, the host must immediately send an enabling packet:
  * `[CMD_WRITE, SUB_UPDATE_EQ_COEFF_TO_REG, band_index, 0, 255, 255, 255]` -> `[1, 10, index, 0, 255, 255, 255]`

The offsets above count the Report ID as byte `0`; the app builds the same buffer without it, so its indices are one lower:

```js
o[27..28]=frequency, o[29..30]=Q*256, o[31..32]=gain*256, o[33]=filterType, o[35]=peqIndex
```

---

## 4. Biquad Math & Coefficient Serialization

Moondrop devices expect raw biquad coefficients in addition to human-readable frequencies and gains. The coefficients are calculated using standard **Robert Bristow-Johnson (RBJ) Audio EQ Cookbook** equations, always against a 96 kHz sample rate regardless of what is playing.

### Q2.30 Fixed-Point Format
Each floating-point coefficient is converted to a 32-bit signed fixed-point integer (Q2.30 format):
$$\text{Coeff}_{\text{integer}} = \text{round}(\text{Coeff}_{\text{float}} \times 1073741824)$$

### Swapped Coefficient Mapping
The Moondrop firmware implementation maps coefficients in the following array order:
$$\text{Payload} = [ b_0, b_1, b_2, -a_1, -a_2 ]$$

*Where $b$ represents the numerator coefficients (calculated from standard DSP formulas) and $a$ represents the denominator coefficients (normalized so that $a_0 = 1.0$).*

Both facts come from one function, the only place `1073741824` appears in the entire app:

```js
function av(t,r){
  const n=t.map(i=>Math.round(i*1073741824)),
        a=r.map(i=>Math.round(i*1073741824));
  return[a[0],a[1],a[2],-n[1],-n[2]]
}
```

There is no alternative scale anywhere in the bundle — no `0x40000000`, no `Math.pow(2,N)`, no 2^28/2^29/2^31 — and no post-shift or headroom divisor applied to the numerator.

### 4.1. The Q2.30 range limit, and the app's silent overflow

Q2.30 spans only **[-2, 2)**. The feedback terms are always safe: $-a_1$ approaches 2.0 from below as the corner frequency drops (1.9991 at 10 Hz) but never crosses, and $|a_2| < 1$ for any stable biquad. Overflow is therefore always a **numerator** problem, and it arrives two ways:

| Condition | Trigger | Example |
|:---|:---|:---|
| $b_1 < -2$ | `high_shelf` above roughly +5 dB | 8 kHz, Q=0.7 → ceiling ≈ **+4.7 dB** |
| $b_1 < -2$ | `high_shelf` with a low corner, at **any** gain | 100 Hz, Q=0.7, +0.05 dB → $b_1$ = -2.0004 |
| $b_0 > 2$ | any type at high gain + low Q + high frequency | `peaking` 20 kHz +12 dB Q=0.3 → $b_0$ = 2.331 |

**The official app does not clamp, and its UI does not prevent this.** The default `gainRange` is `[-18, 12]` and 11 of the 12 registry devices inherit it, so a +6 dB shelf is reachable in the UI. `av()` has no `Math.min`/`Math.max` on the coefficient path, and the caller serializes with JS bitwise ops:

```js
i[l]=c&255, i[l+1]=c>>8&255, i[l+2]=c>>16&255, i[l+3]=c>>24&255
```

Bitwise operators apply **ToInt32, which wraps modulo 2^32** rather than saturating or throwing. Past the limits above, the app silently sends a wrong-signed, wrong-magnitude coefficient while its own response graph — drawn in float math — shows the curve the user asked for:

| Requested (high_shelf 8 kHz Q=0.7) | $b_1$ float | round($b_1 \cdot 2^{30}$) | Fits int32 | Value actually sent |
|:---|:---|:---|:---|:---|
| +3 dB | -1.680 | -1803668025 | yes | **-1.680** |
| +5 dB | -2.075 | -2227599001 | no | **+1.925** (sign flipped) |
| +6 dB | -2.303 | -2473018903 | no | **+1.697** (sign flipped) |
| +12 dB | -4.249 | -4562452245 | no | **-0.249** |

A strict implementation in a language that range-checks (e.g. Python's `struct.pack('<5i', ...)`) will raise here rather than wrap. That is the correct behaviour, not a porting defect. Three responses are possible: **reject** (safest — never programs a filter the user did not ask for), **wrap** (bit-identical to the official app, bug included), or **clamp** (saturate at ±(2^31-1)).

What the *firmware* does with a wrapped coefficient is **UNVERIFIED** — it lives past the Savitech→I2C bridge in the Cirrus programmable-filter block, and has not been measured on hardware. That uncertainty is the argument for rejecting rather than guessing.

> [!NOTE]
> `Rays`, `AG Rays`, `Marigold`, and `E.S. combo` disable shelf filters in the UI entirely, which sidesteps the $b_1$ case on those four. Whether that is why they are disabled is speculation.

---

## 5. Independent Corroboration

* [erikyo/JM98MAX-PEQ](https://github.com/erikyo/JM98MAX-PEQ) — independently reports Q2.30, Report ID 75, 63-byte reports, and 96 kHz.
* [mohammed-just/DawnPro-GUI-windows](https://github.com/mohammed-just/DawnPro-GUI-windows) — a separate implementation of the same device family.
