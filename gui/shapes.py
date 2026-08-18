"""What a curve *does*, named — so a library can be filtered by it.

The community index cannot be filtered on anything an author typed. Titles are things
like `"V2 final"` and `"7hz diablo "`, descriptions are free text, and of the 9,243
rows a DAWN PRO2 query returns, **25 carry any tags at all**. Whatever a shape filter
is built on, it cannot be metadata.

So it is built on the curve. Every preset resolves to eight bands, those bands sum to a
response, and the response answers the question directly: this one lifts the bass and
leaves everything else alone, that one scoops the mids. Nobody has to have labelled it.

The thresholds below are deliberately coarse. They are not trying to be a taxonomy of
tuning — they are trying to make a list of nine thousand curves browsable, and the only
failure that matters is a curve filed somewhere a person would not look for it. A curve
that matches nothing is `OTHER` rather than being forced into the nearest bucket:
"other" is honest and a wrong label is not.

`tools/classify-index.py` runs this over the real cached library and prints the
histogram, which is how the numbers here were chosen rather than guessed.
"""
from __future__ import annotations

from . import curve as curve_mod

# Where the ear splits the spectrum, near enough. These are the regions the editor
# already draws on the plot, collapsed to five — the distinction between UPPER-MID and
# PRESENCE does not survive being averaged into a single number.
REGIONS = (
    ("sub", 20.0, 120.0),
    ("low", 120.0, 500.0),
    ("mid", 500.0, 2000.0),
    ("presence", 2000.0, 6000.0),
    ("air", 6000.0, 16000.0),
)

# Samples per region, log-spaced. Twelve is enough to average out a narrow notch
# without letting one sit unnoticed between samples.
SAMPLES = 12

NEUTRAL = "Neutral"
BASS = "Bass boost"
VSHAPE = "V-shaped"
WARM = "Warm"
TAMED = "Treble-tamed"
BRIGHT = "Bright"
LEAN = "Bass-cut"
OTHER = "Other"

# The order the chips appear in, and the order `classify` tests in — most specific
# first, because a V-shape also satisfies "bass boost" and is the more useful answer.
LABELS = (NEUTRAL, BASS, VSHAPE, WARM, TAMED, BRIGHT, LEAN, OTHER)

# A curve flatter than this everywhere is not doing anything worth naming. Region
# averages of under 2 dB are a rounding error with ambitions — and at 1.5 the real
# library left a run of visibly-flat curves sitting in "Other".
FLAT_DB = 2.0

# …and no single point may stray further than this from flat, however calm the
# averages look. See `classify`.
PEAK_DB = 4.0

# Points across the audible range for the peak test, log-spaced. 48 puts a sample
# roughly every sixth of an octave, which no realisable band is narrow enough to hide
# between.
PEAK_SAMPLES = 48


def peak_deviation(bands):
    """The furthest this curve gets from flat in either direction."""
    worst = 0.0
    for i in range(PEAK_SAMPLES):
        f = curve_mod.F_MIN * (curve_mod.F_MAX / curve_mod.F_MIN) ** (
            i / (PEAK_SAMPLES - 1))
        worst = max(worst, abs(curve_mod.sum_response(bands, f)))
    return worst


def profile(bands):
    """Mean dB per region. The whole of what the rules below get to see."""
    out = {}
    for name, lo, hi in REGIONS:
        total = 0.0
        for i in range(SAMPLES):
            f = lo * (hi / lo) ** (i / (SAMPLES - 1))
            total += curve_mod.sum_response(bands, f)
        out[name] = total / SAMPLES
    return out


def classify(bands):
    """One of LABELS for a set of bands."""
    if not bands:
        return OTHER
    p = profile(bands)
    mid, presence, air = p["mid"], p["presence"], p["air"]
    top = max(presence, air)
    # The two low regions are read from whichever end is being asked about. A shelf at
    # 60 Hz and one at 300 Hz are both "bass boost" to somebody choosing a preset, so
    # a boost is the louder of the two — but a *cut* is the quieter one, and reusing
    # `max` for both meant a 6 dB shelf taken out at 120 Hz reported as −2 dB (the
    # half of the band the shelf had barely reached) and came out unclassified.
    bass_up = max(p["sub"], p["low"])
    bass_down = min(p["sub"], p["low"])

    # Everything below is measured *against the middle*, not against zero, and that
    # distinction is the whole classifier. Judged absolutely, 35% of the real library
    # came out "Bright" — because a great many curves lift the presence region a
    # little while doing something else entirely, and a label that catches a third of
    # everything has told you nothing. Tilt is what a person hears as a tuning: bright
    # means the top is up *relative to* the mids, and a curve that lifts the whole
    # spectrum evenly is not bright, it is louder.
    lift = bass_up - mid
    cut = bass_down - mid
    tilt = top - mid

    # Flat on both readings, or it is not flat. A region average is a mean over a
    # decade or so, and a single sharp 6 dB boost inside one of those regions barely
    # moves it — which is how a preset whose own title says 低频增强 ("bass boost")
    # came out of an average-only test labelled Neutral. The peak test is what stops
    # the smoothing from swallowing exactly the curves people reach for.
    if (max(abs(v) for v in p.values()) < FLAT_DB
            and peak_deviation(bands) < PEAK_DB):
        return NEUTRAL
    # Both ends above the middle — tested before BASS and BRIGHT, each of which it
    # partly satisfies, because "V-shaped" is what somebody reaching for it searches.
    if lift > 1.5 and tilt > 1.5:
        return VSHAPE
    if lift > 1.5 and air - mid < -1.5:
        return WARM
    if lift > 2.0 and tilt < 1.0:
        return BASS
    if tilt > 2.0 and lift < 1.5:
        return BRIGHT
    # The second half of this catches what the library is actually full of: curves
    # whose whole purpose is taking the air region down — the 改善齿音 ("fix the
    # sibilance") presets — which a rule reading only `max(presence, air)` misses
    # entirely whenever presence is left alone or lifted.
    if tilt < -2.0 or air - mid < -2.5:
        return TAMED
    # Cutting the bottom is a tuning somebody chose, not a leftover. Judged only by
    # what it adds, a curve that removes bloat looks like a curve that does nothing.
    if cut < -2.0:
        return LEAN
    return OTHER
