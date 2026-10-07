# DAWN PRO2 volume-control research

Checked on 2026-10-06 with a connected DAWN PRO2 (`35D8:011D`, firmware 1.5).

## Confirmed locally

- HID DAC offset (`0x03`) changes loudness and reads back in Q8.8 dB. A temporary
  -12 dB write was audible and restoration to 0 dB was verified. No flash write.
- Physical volume-down presses left this offset at 0 dB. It is a separate trim.
- HID read `[0x80, 0x24, 0]` received no matching reply on repeated attempts.
  This does not prove every possible hardware-volume route is unsupported.
- A raw button-report listener failed with a USB read error. It did not provide
  a conclusive capture of button events.
- Windows `IAudioEndpointVolume::QueryHardwareSupport()` returns `1` for
  `Speakers (DAWN PRO2)`: hardware volume support. Its reported range is
  -60 to 0 dB in 0.5 dB steps. Current level was 0 dB / scalar 1.0 after the earlier
  physical-button tests. This establishes another volume-control interface,
  not synchronization with the physical buttons. Only read operations were used.

## Source findings

- [Current official Moondrop Hub bundle, app 1.6.5](https://hub.moondroplab.tech/assets/index-DS5X-zjl.js)
  defines `DEVICE_VOLUME=36` and decodes unsolicited volume events. However, its
  DAWN PRO2 configuration does not enable the device-volume feature. Global gain
  uses DAC offset command 3. Generic support in the bundle is not evidence that
  DAWN PRO2 firmware implements it.
- [DawnPro-GUI Windows DAWN PRO2 backend](https://github.com/mohammed-just/DawnPro-GUI-windows/blob/main/device/dawnpro2_hid.py)
  implements pre-gain and DAC offset, with no separate physical-volume control.
  Its newer C# protocol likewise implements DAC offset. Legacy volume commands
  are routed to the original DAWN PRO backend.
- [macOS DeviceSession](https://github.com/jgoodliffe/moondrop_macOS/blob/main/Moondrop%20DSP%20Control/Core/DeviceSession.swift)
  includes an opcode-36 volume path, but states that it is gated off on shipping
  models. It is not a demonstrated working DAWN PRO2 solution.
- [DAWN PRO2 Studio protocol](https://github.com/Mexes-GM/dawnpro2-studio/blob/main/src/dawnpro2/device/protocol.py)
  lists command 36 among probe candidates, separate from its normal DAC-offset
  implementation. No verified button-synchronized implementation was found.
- [mdrop original DAWN PRO protocol](https://github.com/frahz/mdrop/blob/main/mdrop/src/lib.rs)
  uses USB vendor control transfers with VID/PID `2FC6:F06A`, volume query
  `C0 A5 A2` and volume write `C0 A5 04`. The connected DAWN PRO2 has different
  IDs and a HID protocol; this implementation does not establish compatibility.
- [Microsoft hardware-volume query](https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-queryhardwaresupport)
  and [endpoint-volume controls](https://learn.microsoft.com/en-us/windows/win32/coreaudio/endpoint-volume-controls)
  describe checking hardware support and hardware/software endpoint controls.
  They do not establish that a device's independent buttons update that endpoint.

## Assessment

Offline inspection of the official 1.5 firmware now identifies the HID
read/write dispatcher. Its subcommand comparisons contain no case for 36,
which explains the unanswered read probes. Subcommand 3 reads and writes a
separate DSP trim halfword. USB audio setup handlers expose other volume
halfwords, but their relationship to physical-button changes is still unverified.
Detailed image addresses and reproducible tooling are preserved locally in
`firmware-research/volume-code-notes.md` (ignored research files).

Physical-button volume readback is now confirmed on the connected firmware-1.5
unit through the firmware's subcommand-0 RAM read path. The signed halfword at
RAM `0x0011d22c` changed from -4284 to -4743 after three confirmed volume-down
presses, exactly three steps of -153. The user confirmed quieter audio.
Further live samples changed in the same step multiples. Windows volume stayed
at 0 dB, including unchanged values in its notifications.

Two-way slider writes are now verified: write the button-gain word while preserving
its neighboring halfword, then run the existing trim command with unchanged trim
to update the live gain. A temporary reduction produced quieter audio, confirmed
by the user, and the original state and loudness were restored. The implementation
is gated to DAWN PRO2 firmware 1.5 and uses serialized HID access, readback
verification, and error handling. No firmware modifications were performed.
Detailed packet format and evidence are in `firmware-research/volume-code-notes.md`.
Opcode 36 and legacy vendor commands are not used.
