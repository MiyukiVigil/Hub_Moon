# Hub Moon — UI

The reference mockup was the vision. What follows is that mockup written down so it
survives, plus the places the build deliberately departs from it and why.

**Its own identity, not MOONDROP's.** The layout, density and interactions below are
the target. The palette is Hub Moon's: a warm rose, light by default and dark on
request, which is what the mockup already reaches for and is nothing like the vendor
app's near-black and blue. The curve colours stay conventional because they are
learned, not decorative.

**No QML.** The earlier attempt ported sea-shell's QML across, which is why this ever
looked like a sibling of the shell. The view layer is Slint; `gui/qml/` and
`controller.py` are gone.

**The view holds no maths and no policy.** `ui/*.slint` reports fractions ("the pointer
is 62% across the plot") and renders finished geometry — SVG path commands, labels,
handle positions — all computed in `bridge.py`. That is what keeps a curve the graph
draws and a curve the DAC accepts the same curve: there is one copy of the maths, and
it is the one that writes to the hidraw.

## Layout, top to bottom

**Welcome.** Shown once on a first run, and again on request from appearance settings.
A crescent that scales in with an equaliser reading inside it, the wordmark, what device
was found, and four cards covering the interactions that are not self-evident: drag the
graph, use the faders, watch the dashed line, nothing is permanent. The bar animation's
timer lives inside the screen's own branch, so it stops existing when it is dismissed
rather than ticking behind the app all session.

**Header.** Mark, name, and the facts that never change (`8-band parametric EQ ·
DSP @ 96 kHz`). Device chip — click to re-scan — and a firmware chip. Right side:
an `unsaved` chip when there are live edits, then `community`, `how to tune`,
appearance settings, refresh. Appearance offers light/dark, one of six accents, the
graph view, and a way back to the welcome screen — all saved to
`~/.config/hub-moon/settings.json`.

**Control row.** `DEVICE SLOT` (the DAC's own EQ profile number, − / +, `as reported`),
`PRE-GAIN headroom` (slider + dB readout; turns amber with a one-tap `match` when the
curve would clip), `GLOBAL OFFSET volume` (slider + dB readout).

**Presets.** Eight pills: Flat, Bass, V-shape, Vocals, Warm, Air, Podcast, Loudness.

**Graph.** The centrepiece, in two views.
- *Editor* — named spectrum regions as tinted vertical bands (SUB · BASS · LOW-MID ·
  MID · UPPER-MID · PRESENCE · AIR), one numbered draggable handle per band, and three
  traces: the thin `Flat` reference, the solid `Equalized` curve, and a dashed
  `+ pre-gain (output)` curve. Legend bottom-left, log frequency labels 20 … 20k.
- *Readout* — the vendor chart's framing: no regions, no handles, a dashed grid, and
  one red output curve against a 60 dB reference. What actually leaves the DAC.
- The toggle sits at the graph's top-right, and also in appearance settings.

**Band strip.** Eight cards. Region name + slot number, a filter-type button (`PK`;
click cycles forward, right-click back), a vertical gain fader with tick marks, the
gain readout, a frequency stepper with a `FREQ` caption, and a Q stepper.

**Footer.** Hints on the left, then import, export, revert, reload, and `save to
flash` as the one primary action.

## Two departures from the mockup

**The dB axis is symmetric.** The mockup labelled `+12 / +6 / 0 / −6`, which reads fine
until a handle is draggable — the plot *is* the gain control now, so an axis that
bottoms out at −6 dB is an axis that silently refuses every cut deeper than that. A
−8 dB notch is an ordinary thing to want. The editor spans the full ±12 the firmware
will take.

**No DAC / software toggle.** The mockup carries one because sea-shell's panel drives
two backends: the DAC's DSP and a PipeWire filter-chain. Hub Moon has no software EQ,
so the toggle would be a dead control. If `sea-eq.py` ever moves here, this is where
it goes.

## Interactions that define the feel

- Drag a handle to move that band; the curve, the band card and the readout follow.
- Scroll over the plot changes the selected band's Q, written on a trailing edge.
- Every readout is live while dragging, but the hidraw is only touched on release —
  a HID write per mouse-move would queue hundreds behind the one that matters.
- Writes are live to the DSP; only `save to flash` persists. `revert` restores the
  last saved state, which a re-read cannot do: the DSP only ever reports what was
  written to it last.
- A band the firmware's Q2.30 coefficients cannot represent is clamped, and its card
  tags itself `limit` — the wall is visible instead of feeling like a stuck slider.

## Icons are vectors, not a font

They were a bundled Material Symbols subset drawn as ligature text. That only ever
worked here because this machine has the font installed system-wide: **Slint's Python
API has no font-registration call**, and `SLINT_DEFAULT_FONT` sets the default family
rather than making one resolvable by name. Run under a fontconfig sandbox with Material
Symbols removed, every icon rendered as its own name — "gr", "he", "rem" — which is what
a clean install would have looked like.

`tools/build-icons.py` now extracts the glyph outlines and generates `ui/icons.slint`
(referenced as `Icons.usb-off`, so a typo is a build error) plus `gui/icons.py` (so the
preset table can hand the view a finished path). No font ships, nothing to register,
and no silent fallback. The header mark is a `Mark` component rather than an icon —
it is the identity, not one symbol among thirty.

## Status

Everything above is built and running against a real DAWN PRO2 — reads, writes,
readback, drag, presets, import/export, the community library, both graph views, and
both themes.

Known gaps:

- **File dialogs** shell out to `zenity`. There is no fallback chooser if it is absent —
  import/export just report a cancel.
- **Slint models are updated in place, never replaced.** Assigning a fresh `ListModel`
  makes the repeater tear down and rebuild every card — including the one whose
  `TouchArea` currently has the pointer grabbed, which turns every fader into a control
  you can click but not drag. See `push()`.
- **The cyclic GC is pinned to the UI thread** (`gui/app.py`). This is load-bearing, not
  tuning: every struct handed to Slint is an unsendable `PyStruct`, and a collection
  triggered on a worker thread walks one and aborts the process. Read the comment
  there before changing anything about the workers.
