# DAWN PRO2 EQ mode and persistence investigation

Investigated 2026-10-06 on connected VID 35D8 / PID 011D, firmware 1.5.
No EQ, mode, or flash writes were performed during this investigation.

## Physical mode versus stored profile

The user switched the LED from red to yellow while the read-only capture was
running. At 34.094 seconds, RAM byte `0x10648c` changed from 8 to 9.
The saved/custom profile field at `0x11d236` and the ordinary active-profile
HID query both remained 9. These fields cannot be used interchangeably.
The capture is in ignored `.dac-test/eq-mode-capture.jsonl`, produced by
`.dac-test/watch_eq_mode.py`.

Firmware coordinates below refer to the extracted CPU image's disassembly,
with its file offsets labelled relative to `0x48000000`.

The long-button handler at `0x48012f1c` checks the other button's GPIO bit,
reads `0x10648c`, and chooses either mode 8 or the custom profile stored at
`0x11d236`. It writes the current-mode byte and calls `0x48012c58` to reload
the corresponding DSP configuration. This supports the observed 8/9 switch.
Only one red-to-yellow transition has been captured; reconnect persistence
and additional firmware versions have not been tested.

## Why live tuning can affect red mode

The write-EQ handler at `0x48019b60` copies coefficients and band metadata
into the custom configuration. At `0x48019be6` it unconditionally sets the
stored profile to 9, then at `0x48019c04` passes profile 9 to `0x48012a18`,
which rebuilds the working EQ filter data. It also schedules deferred work
through `0x106550` and a 500-unit timer call.

That path does not update the current hardware-mode byte at `0x10648c`.
Consequently, the firmware has a path that applies custom filter data while
the hardware mode/LED still describes mode 8. This is a strong explanation
for the user's reported live-tuning behavior, rather than proof that saving
to flash is broken. An audible controlled test in each mode is still needed
to validate the complete effect of the scheduled processing.

Hub Moon currently sends the live band-update command followed by the
coefficient-enable command. Its active-profile getter reads the stored
profile, not the separate hardware mode. A fix needs to distinguish these
states and restore/reload the selected mode after live updates if normal
mode must stay flat. Writing the mode byte alone would not reload the DSP
and is not a sufficient fix.

## Flash save path

The save handler at `0x48019a8c` marks the configuration format as 2 and calls
`0x48015670` with RAM configuration base `0x11d21c`.
That routine invokes ROM routines with flash offset `0x38000`, length
`0xbc0` (3008 bytes), and the supplied configuration buffer. The sequence
appears to erase and program that flash region. ROM symbols are unavailable,
so those names are inferred from arguments and call order.

This is evidence of a real persistence mechanism, but it does not establish
that the current app saves the intended data or that the DAC reloads it
correctly after losing power. Verify with a full configuration backup,
controlled save, physical power cycle, and readback before declaring flash
save fixed or broken.

## Separate flag: not the LED EQ mode

HID subcommand 30 reads/writes runtime `0x106490` and configuration byte
`0x11cb19`. Both stayed 1 throughout the red/yellow transition. Its purpose
remains unconfirmed; it must not be presented as the physical EQ-mode switch.

## Remaining work

### Software switching verified

The temporary switch test started in mode 8. Writing the selected mode and
requesting firmware job 3 reloaded the DSP through `0x48011d04` -> `0x48012c58`.
The applied-profile byte at `0x1064ed` changed from 8 to 9 and back to 8.
The user confirmed the LED changed yellow then red and audio changed as well.
The original profile/format word `0x11d234` was restored to `02000900`.
No flash commands were sent.

Implemented `set_custom_eq_enabled`: firmware-gated, verifies format 2/custom
profile 9, waits for the firmware job slot, temporarily substitutes the reload
profile, updates current mode, requests job 3, waits for completion, restores
the custom profile header, and verifies both current and applied modes. Failure
attempts to restore the original mode. Aligned writes preserve neighboring
bytes of the firmware job word. All GUI calls run on its existing HID queue.

The GUI now replaces Device Slot with a mode button on supported firmware.
Switching custom EQ on also applies staged editor changes. Physical-button mode
changes continue to update its status through polling. Other devices retain
their existing slot control.

Implemented in the GUI: firmware-gated mode reader and status indicator; staged
band/pre-gain edits in normal mode; automatic application when EQ is enabled;
a normal-mode check before saving. Save sends the complete editor curve.
This avoids speculative DSP reload writes. A/B audition is inactive in normal
mode. Existing live editing of built-in EQ modes is retained. The button hold
remains the way to change physical mode.

The read-only follow-up found mode 8 again when the user confirmed the LED was
already red. The return transition itself was not captured.

Validation: 508 regression tests passed, followed by 127 focused EQ/GUI tests
after the final enabled-mode handling change. A guarded live test on the
connected DAC in mode 8 staged a band and pre-gain change, and intercepted all
commands to prohibit writes: only two READ commands were sent. The updated
Hub Moon GUI opened and responded. Audible mode behavior and persistence after
power loss still require user hardware confirmation.

1. Capture yellow-to-red and confirm the mode byte returns to 8.
2. The software reload path is now verified; continue to avoid using the stored
   active-profile query or flag 30 as the physical mode status.
3. Back up the configuration and separately test flash persistence across a
   physical disconnect, when a controlled saved-EQ change is authorized.
4. The firmware-gated mode indicator, button, and explicit preview/save behavior
   are implemented. Flash persistence still needs a controlled power-cycle test.

Primary firmware source: Moondrop's OTA service, firmware 1.5 image:
https://cdn.moondroplab.tech/ota-config-file/65Sh-9xMUd3Qnj5ZO-p65.bin

Container SHA-256:
`59f4afcb60fcf2cbb42cecb5b64dbc3696ebde95690819a943f8c9972c1ba109`.
