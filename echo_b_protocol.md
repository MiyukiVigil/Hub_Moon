# Moondrop ECHO-B protocol notes (from MOONDROP app 2.26.2c, APKPure XAPK)

## Identification
- Product API (`cdn-service.moondroplab.tech/api/v1/products/all`): ECHO-B → `chipType: "synopsys"`, 9 EQ bands,
  no pre-gain, no total gain. (Echo-BP is a different chip: `jieliusb`, 32 bands.)
- Conexant/Synaptics stack ("Freeman3"/"CAPE" family). Device accepted only if:
  - USB string descriptor index 41 contains `FREEMAN_V03.00.00.01` or `CAPE`, and
  - VID:PID in `res/xml/devicelist.xml` "Freeman3": 0x35D8 : 0x1496–0x149C.
- Confirmed on real hardware (issue #1, macOS `ioreg`): VID 0x35D8, PID **0x149B** (5275), bcdDevice 0x0001,
  USB 2.0 high speed, bDeviceClass 0xEF/0x02/0x01 (IAD composite), strings "MOONDROP" / "ECHO-B", no serial.
  Plays as a normal UAC2 output on macOS without drivers.
- Dart → Java via method names `sySendEqParams`, `sySavePeqParamsToFlash`, `sySaveEqParamsToFlash`,
  `sySelectEqPreset`, `syGetEqParamsFromFlash` (handler in obfuscated `defpackage/ua5.java`).

## Transport (HID control transfers, interface 3)
- Write: bmRequestType 0x21, bRequest 0x09 (SET_REPORT), wValue 0x0201 (output, report ID 1), wIndex 3.
- Read:  bmRequestType 0xA1, bRequest 0x01 (GET_REPORT), wValue 0x0101 (input, report ID 1), wIndex 3.
  Poll until reply bit (byte5 & 0x80) is set, ≤1 s, 5 ms sleeps.
- Success: reply bit set and int16 at bytes 2..3 >= 0.

## CAF message (`CnxtUsbCommand.getUSBMessage(1)`), little-endian, length 10 + 4*n
| bytes | content |
|---|---|
| 0–1 | 0x01 0x00 (report ID 1) |
| 2–5 | u32: `n_words & 0xFF` \| `command_id << 16` \| `reply << 31` |
| 6–9 | u32 module ID = CAF id of "CTRL": ((c0-32)<<8)\|((c1-32)<<14)\|((c2-32)<<20)\|((c3-32)<<26) |
| 10.. | n × u32 data words |
Almost every command uses n = 13 (62-byte message); 187 is sent with n = 1. Data words are serialised as their low
32 bits, so negative coefficients go out as two's complement. Each read command is its write command + 256
(190/446 band, 90/346 EQ mode, 186/442 EQ config).

## Commands (module CTRL)
| cmd | data | meaning |
|---|---|---|
| 188 | [1, 0…] | enable EQ |
| 187 | [0] | EQ CFG bypass off (sent once together with 188) |
| 190 | [0, band+5, Gain, B0, B1, B2, A0, A1, 0…] | live-set one band's coefficients (live bands 0–4 → hw 5–9) |
| 220 | [0, band, freq_Hz, Q*256, filterType, gain_dB(int), 0…] | stage band params for flash |
| 220 | [srIdx(4..8), band, Gain, B0, B1, B2, A0, A1, 0…] | stage coeffs for each sample rate 44.1k/48k/96k/192k/384k |
| 220 | [255, 0…] | commit staged EQ to flash |
| 90  | [90, index, 0…] | switch EQ preset/mode |
| 346 | [62, 0…] | get current sample-rate index (result int at byte 14) |
| 259 | [1,0,0,0] (n=4) | get chip code (int at byte 10) |
| 346 | [90, 0…] | get current EQ mode (int at byte 14), read-only |
| 442 | [0…] | get sample-rate index the stored EQ was designed for (int at byte 10), read-only |
| 446 | [0, band(1..9), 0…] | read one stored band: Gain/shift byte at 18, B0 B1 B2 A0 A1 at 22/26/30/34/38 (24-bit signed), read-only |

Coefficients: biquad scaled by 2^(25 − Gain) (Gain 3 → 2^22), B0..B2 = b0..b2, A0 = −a1, A1 = −a2.
Live band 0–4 → hw band 5–9 (`shiftEQBandForFreeman3` = band + 5); flash save writes bands 1–9.
Bands 1–4 are not reachable from the app's live path, but they are live on the left channel (see Band map below).
FREQ_SAMPLE_RATE index table: [8000,16000,24000,32000,44100,48000,96000,192000,384000,882000,176000].
App always sends filterType 0 (peaking) from `sySendEqParams`, gain/Q as doubles.

## Confirmed on hardware (issue #1, `tools/echo_b_probe.py`, macOS, hidapi)
- hidapi enumerates interface 3 with `interface_number` 3; `open_path` + `write` + `get_input_report` work.
- USB string 41 is not readable on macOS (`get_indexed_string` error), so identify by VID:PID, not string 41.
- Report descriptor (usage page 0x0C, consumer):
  - report ID 1: 61-byte input and 61-byte output (vendor usage 0x00), i.e. 62 bytes with the ID = one CAF message.
  - report ID 2: 1-byte input, volume up/down (0xE9/0xEA), play/pause (0xCD), 0xCF. Media keys, ignore.
- Reply = request header with bit 31 set (byte 5 `0x01` → `0x81`), same module ID, words echoed or filled.
  Bytes 2..3 in the reply are just the word count (4, 13), so "int16 ≥ 0" really means "not an error code".
- cmd 259 reply: word 0 = 0x7CF4 (31988), ASCII `CX31988` at bytes 18..24, so the chip is a **Conexant CX31988**.
- cmd 346 reply: word 0 = 62 (echoed), word 1 = 5 (48000 Hz, matching the macOS output rate), word 3 = 0x20 (32,
  probably bit depth; not verified).
- cmd 346 [90] reply: EQ mode 0.
- cmd 442 reply: word 0 = 5 (stored EQ designed for 48 kHz). Further words 0xC0000003, 0, 1, 0, 0x1C0: meaning unknown.
- cmd 446, bands 1–9: **all** shift 3, [4194304, 0, 0, 0, 0] = exact pass-through (b0 = 1.0). This confirms the
  2^(25 − shift) scaling. Bands 1–4 are flat too, so on this unit they hold no factory tuning. The owner never managed
  to connect the Moondrop app, so this is probably the factory default.

## Coefficient design (Hub Moon, clean-room)
`echo_b_coefficients()` in `moondrop_control.py`: the existing RBJ `calculate_biquad` at the device's sample rate,
smallest shift that fits all five values in 24 bits, then a small search over the rounded ints for low corners.
Checked against Moondrop's library only as a black box (comparing the responses each produces):
- Shift choice agrees in ~96% of bands.
- From ~100 Hz to 1 kHz they agree within ~0.1 dB. At very low corners ours is as close to float RBJ as theirs or
  closer (20 Hz / 48 kHz: 0.25 vs 0.32 dB worst case; 20 Hz / 96 kHz: 0.92 vs 0.94).
- Higher up, Moondrop's design increasingly departs from RBJ (some bandwidth warping): worst case at 48 kHz ~0.3 dB at
  4 kHz, ~1.2 dB at 8 kHz, ~6 dB at 16 kHz. Ours stays exactly on RBJ, which is what AutoEQ / REW PEQ profiles assume.
- Moondrop's function returns nonzero codes (2048, 6144, 7680) for some bands; meaning unknown, ignored.

## App write sequence (`FreemanCnxtUsbDevice`)
- First EQ read after connect (`setDefaultAvailable`): writes flat [0|1, band, 3, 4194304, 0, 0, 0, 0] with cmd 190 to
  bands 0–9, once with word 0 = 0 and once with word 0 = 1 (word 0 may be a channel or bank; unverified).
- First live change per session: 188 [1, 0…], then 187 [0], then 190 [0, band+5, Gain, B0, B1, B2, A0, A1, 0…].

## Live write test (issue #1, `echo_b_probe.py --write-test`, macOS, 48 kHz)
- 188, 187 and 190 [0, 9, …] (−6 dB peaking at 1 kHz on band 9) all replied OK; cmd 190's reply echoes the words.
- cmd 446 then read exactly the written values back, so 446 reads the live copy.
- After a replug band 9 read flat again: **cmd 190 is volatile**, and the copy loaded at power-up is flat (the flash).
- 446 replies carry each coefficient in 3 bytes with the 4th zeroed (written `…8b ff`, read `…8b 00`), so readback
  must sign-extend from 24 bits. Writing the full two's-complement word is accepted.
- **Not heard**: the owner noticed no change in the music. Either the dip was too subtle, or band 9 / word 0 = 0 is not
  in the audio path in EQ mode 0. `--listen-test` narrows this down: a 400 Hz low-pass written to band 9 with word 0 = 0,
  then word 0 = 1, then bands 1–9, then 188/187 re-sent, asking after each step; everything goes back to flat at the end.

## Listen test (issue #1, `--listen-test`, macOS, 48 kHz)
- **Heard at step 1**: 188 [1], 187 [0], then 190 [0, 9, 3, 2771, 5543, 2771, 8078119, −3894901] (400 Hz low-pass)
  muffled the music at once, with no commit step. So the app's live sequence works as-is, and the −6 dB dip in
  `--write-test` was simply too subtle to notice.
- Writing flat to bands 1–9 (both values of word 0) brought the sound back, and every band read back flat.
- `--band-map` follows up: the same low-pass on one (band, word 0) pair at a time (9/0 as the control, then 9/1, 5/0,
  1/0, 4/0), asking whether it is heard and in which ears.

## Band map (issue #1, `--band-map`, macOS, 48 kHz)
| slot (band, word 0) | heard |
|---|---|
| 9, 0 | left only |
| 9, 1 | not heard |
| 5, 0 | left only |
| 1, 0 | left only |
| 4, 0 | left only |
- Bands 1–4 are live too, not just the app's 5–9, so a 9-band EQ can use all of 1–9.
- Every slot with word 0 = 0 acts on the **left channel only**. Word 0 = 1 is either not the right channel or not
  live in EQ mode 0. So the app's live path (always word 0 = 0) would only EQ the left ear, unless 188/187 or the
  EQ mode links the channels.
- Restoring flat on bands 1–9 (both values of word 0) brought the sound back.
- `--find-right` follows up. It tries 5/1 and 1/1 (slots the app writes), then 9/2, 9/3, 10/0, 14/0 and 18/0. Each
  slot is read with 446 first and written only if the reply echoes the slot and holds a filter (b0 ≠ 0). It gets
  the values it held before written back, and the run stops at the first slot heard on the right.

## Still unknown
- Where the right channel's bands are, and what word 0 of 190/446 selects.
- Whether a live EQ survives a sample-rate change, since the coefficients are designed for one rate.
- The live path rejects band > 4 for Freeman3 while the product says 9 bands; how the Dart UI maps 9 bands is unverified.
