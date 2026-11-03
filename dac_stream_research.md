# DAWN PRO2 firmware stream-format investigation

Investigation on 2026-10-11, using the previously downloaded official firmware
1.5 CPU image and the connected DAWN PRO2. Stream-format and activity captures
are read-only; the later channel-trim tests make temporary reversible writes.
No firmware upgrade or flash writes were performed.

## Candidate fields

- `0x0011cb34`: little-endian uint32 sample rate. The USB setup handler at
  `0x48013ddc` reads this field and serializes its low three bytes for the
  audio endpoint sampling-frequency control. The rate table at `0x4800e9a4`
  contains 44100, 48000, 88200, 96000, 176400, 192000, 352800, and 384000.
- `0x0011cb38`: adjacent uint32, strongly identified as sample container width
  in bits. The USB audio processing path at `0x48017ae4` loads offset 4 of
  the same structure, divides it by eight, and uses it to convert buffer byte
  counts into sample counts. The same calculation occurs at `0x48017b72`.

## Live observations

The eight-byte reply at `0x0011cb34` was `80bb000020000000`, decoding to
48000 Hz and 32 bits. Windows shared output was separately configured as
32-bit integer PCM at 48000 Hz; its internal mixer was 32-bit float at 48000 Hz.
The user initially reported a Tidal source of 24-bit/48 kHz.

After the user switched to a source labelled 24-bit/96 kHz, the same RAM reply
remained 48000/32, as did Windows' shared-mode output configuration. Whether
Tidal Exclusive Mode was enabled initially needed confirmation. This first result
alone did not distinguish shared-mode resampling from a stale/default field.

The user then confirmed Exclusive Mode had been off and enabled it during the
96 kHz track. The DAC's reply changed to `0077010018000000`: 96000 Hz and
24 bits. This directly demonstrates the RAM fields follow the USB format and
matches the source format reported by the user. The earlier 48000/32 reading
was consistent with the Windows shared-mode path.

## Remaining verification

Extend the confirmed 48-to-96 kHz transition with 44.1 kHz playback and additional
format changes. Confirm how the
field behaves while paused before exposing it as a live stream indicator.
Container width cannot establish the source recording's effective bit depth
or prove bit-perfect playback. This investigation has not changed the app or
its prepared installer during the initial investigation.

## Application integration

The firmware-gated production reader now validates the eight-byte response and
reports USB rate/width plus left/right USB gain from RAM `0x0011cb64`.
DeviceWorker polls through its existing serial HID queue. Failed telemetry is
shown as unavailable rather than disconnecting an otherwise usable DAC.
The status panel exposes estimated EQ headroom, not a live clipping measurement.

An app-only physical-volume ceiling is remembered locally and applied to volume
requests; it does not claim to constrain physical buttons or other applications.
A read-only comparison can be armed before unplugging: it compares EQ bands,
pre-gain, and trim after an observed disconnect/reconnect in the same session.
Saving also captures a verification baseline. No power-cycle persistence result
has yet been established in the live hardware test.

Hardware-enforced ceiling behavior and live clipping telemetry remain unverified.
The physical volume-up handler around `0x480032d4` clamps at zero explicitly;
changing the advertised maximum alone would not establish a hardware ceiling.
Production stream reader live test passed with a guard rejecting every non-read
command. Balance verification is described below.

## Dedicated channel trim candidate

On 2026-10-11, static decoding identified HID subcommand 22 (`0x16`) as
an independent left/right gain trim. The write dispatcher at `0x4801992e`
jumps to `0x48019cac`. Report byte 4 chooses left (0) or right (nonzero).
Bytes 6 and 7 form a signed big-endian halfword, stored in RAM `0x11cbf6`
or `0x11cbf8`. The routine recalculates the selected gain path immediately.
Read handler `0x4801a0a0` returns the same halfword in bytes 6 and 7.
These are separate from host USB channel volume at `0x11cb66/68`.

Live readback confirmed both trims at zero. Initial left-channel listening
reported louder audio, so the sign was not assumed from that observation.
A one-step probe changed the stored channel-0 attenuation at `0x11c03c` from
68 to 69, with channel 1 unchanged at 68. Reapplying the unchanged global trim
kept those values; restoration returned both to 68.

Separate 6 dB tests then confirmed channel 1 makes the right ear quieter and,
on a repeat from a verified zero baseline, channel 0 makes the left ear quieter.
Each test reopened the HID handle before restoring and verified both trims back
at zero. An earlier stale-handle restoration failed; an explicit recovery read
detected the remaining -6 dB and restored it before subsequent tests.

The production balance control uses only subcommand 22, restricts the slider to
6 dB either side, preserves common attenuation, applies reductions first when
switching sides, verifies readback and attempts rollback on failure. It is gated
to DAWN PRO2 firmware 1.5 and does not save balance to flash.

## Playback activity captures

On the same 48 kHz/24-bit Tidal track, the first paused capture had engine word
`0x11c090 = 3`, then resume changed it to 1, `0x11c0b8` to 2, and `0x11c0c0`
to 1000. These values remained stable during playback. A second pause changed
the negotiated width to 32 bits (consistent with the shared Windows endpoint),
while those engine fields remained at their playing values. `0x11c0c8` became
1 with an adjacent word of 2000, remaining unchanged in a later capture.
Thus these fields cannot reliably identify audible playback across exclusive
and shared modes. The app deliberately leaves activity unavailable instead of
calling a retained format or an open silent USB stream "playing".
