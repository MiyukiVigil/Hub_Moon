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
All observed commands use n = 13 (62-byte message).

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
FREQ_SAMPLE_RATE index table: [8000,16000,24000,32000,44100,48000,96000,192000,384000,882000,176000].
App always sends filterType 0 (peaking) from `sySendEqParams`, gain/Q as doubles.

## Still unknown
- Coefficient design: `Eq2Coeff.CxAudioConvertEqParams2Coeffs(fs, {flags, Freq_Hz, Gain_dB*256, Q*256}, 24)` is native
  (`libcxaudiodsplib_embca_jni.so`: `EqDesignFx`, `EqFxToFloat`, Q8.23 / Q31 helpers). Likely RBJ biquad quantised to
  24-bit with a per-band shift in `Gain`, but exact scaling/sign of A0/A1 needs disassembly or a USB capture.
- The live path rejects band > 4 for Freeman3 while the product says 9 bands; how the Dart UI maps 9 bands is unverified.
