"""The tuning guide: seven pages, each with a figure drawn from the real filters.

Every diagram here is produced by `gui.curve` — the same sampler the editor plots
with, fed synthetic bands. Nothing is hand-drawn and nothing is an approximation of
the maths for illustration's sake. If a shelf below 200 Hz overflows the firmware's
coefficient format, the figure on that page shows the actual overflow.

That matters more than it sounds. The guide is almost entirely about *shape* — what Q
does, how a shelf differs from a peak, what pre-gain costs you — and the previous
version was seven paragraphs asking the reader to picture something the program was
already perfectly able to draw for them.
"""
from __future__ import annotations

from . import curve as curve_mod

# The figure box. Small, and the axis is deliberately wider than the editor's: these
# are shapes to compare, not values to read, so a ±12 dB window would flatten the
# differences the pages are pointing at.
FIG_W = 360.0
FIG_H = 118.0
FIG_TOP = 9.0
FIG_BOT = -9.0


def _band(kind, freq, gain, q, index=0):
    return {"index": index, "type": kind, "frequency": float(freq),
            "gain": float(gain), "q": float(q)}


def _fig(bands, offset_db=0.0, dash=None):
    if not bands:
        return ""
    return curve_mod.svg_curve(bands, width=FIG_W, height=FIG_H,
                               top_db=FIG_TOP, bot_db=FIG_BOT,
                               offset_db=offset_db, samples=200, dash=dash)


def _zero():
    return curve_mod.svg_flat(db=0.0, width=FIG_W, height=FIG_H,
                              top_db=FIG_TOP, bot_db=FIG_BOT)


# Two ways to end up with the same tone. The boost needs 6 dB of headroom; the cut
# version is the same curve translated down, and needs none.
_LOUD_MID = [_band("peaking", 1000, 6.0, 1.0)]
_CUT_AROUND = [_band("low_shelf", 300, -6.0, 0.7),
               _band("high_shelf", 4000, -6.0, 0.7, 1)]

_PAGES = [
    {
        "key": "cut",
        "head": "CUT FIRST",
        "lede": "Boosting spends headroom. Cutting is free.",
        "body": "Both curves give the midrange the same emphasis relative to "
                "everything else — the shape your ears judge is the same. The boost "
                "does it by adding 6 dB the DAC has to find somewhere, which comes "
                "straight out of headroom and has to be paid back as pre-gain. The "
                "cuts do it by lowering what surrounds the midrange instead, and need "
                "no pre-gain at all. When something is too loud, reach for the ranges "
                "around it before you reach for it.",
        "figs": [(_LOUD_MID, 0.0, None), (_CUT_AROUND, 0.0, None)],
        "keys": ["+6 dB boost — costs headroom", "−6 dB either side — costs nothing"],
    },
    {
        "key": "q",
        "head": "WIDE, THEN NARROW",
        "lede": "Q is how much of the spectrum a band touches.",
        "body": "All three of these are +6 dB at 1 kHz. Low Q shapes a whole region "
                "and reads as tone — a warmer, brighter, fuller sound. High Q is a "
                "scalpel for one resonance: a ring on a cymbal, a room mode, a "
                "sibilant peak. It is nearly inaudible as tone and very audible on the "
                "one thing it is sitting on. Start wide. Go narrow only when you are "
                "chasing something specific and can hear it move.",
        "figs": [([_band("peaking", 1000, 6.0, 0.7)], 0.0, None),
                 ([_band("peaking", 1000, 6.0, 2.0)], 0.0, None),
                 ([_band("peaking", 1000, 6.0, 6.0)], 0.0, None)],
        "keys": ["Q 0.7 — tone", "Q 2 — a range", "Q 6 — one resonance"],
    },
    {
        "key": "shelves",
        "head": "SHELVES AT THE EDGES",
        "lede": "A peak at 20 Hz only moves 20 Hz.",
        "body": "A shelf lifts or drops everything past its corner frequency and "
                "stays there, which is what you want at the ends of the spectrum — "
                "there is no far side for it to come back down on. Both of these are "
                "+6 dB at 100 Hz. The peak gives you a bump around 100 Hz and nothing "
                "below 40. The low shelf gives you the whole bottom of the record.",
        "figs": [([_band("low_shelf", 100, 6.0, 0.7)], 0.0, None),
                 ([_band("peaking", 100, 6.0, 0.7)], 0.0, None)],
        "keys": ["low shelf at 100 Hz", "peak at 100 Hz"],
    },
    {
        "key": "dashed",
        "head": "WATCH THE DASHED LINE",
        "lede": "The dashed trace is what actually leaves the DAC.",
        "body": "The solid curve is what the EQ does. The dashed one is the same "
                "curve after pre-gain is paid, and it is the one that matters for "
                "clipping: while any part of it sits above 0 dB, loud passages have "
                "nowhere to go and the DAC squares them off. Tap match on the "
                "pre-gain card and it drops by exactly the headroom your curve needs "
                "— no more, because every dB of pre-gain is a dB of noise floor you "
                "are giving away.",
        "figs": [(_LOUD_MID, 0.0, None), (_LOUD_MID, -6.0, (7.0, 5.0))],
        "keys": ["what the EQ does", "what leaves the DAC"],
    },
    {
        "key": "small",
        "head": "SMALL MOVES",
        "lede": "±3 dB is a lot. ±1 dB is audible.",
        "body": "These are the same band at 1, 3 and 6 dB. Six is a change you could "
                "not miss on any material; three is clearly a different tuning; one is "
                "the size of most of the moves that actually improve a headphone. If a "
                "change is not obvious at 3 dB, it is not the band you wanted — move "
                "it back, and go looking somewhere else rather than pushing harder. "
                "One band at a time, against something you know well.",
        "figs": [([_band("peaking", 1000, 1.0, 1.0)], 0.0, None),
                 ([_band("peaking", 1000, 3.0, 1.0)], 0.0, None),
                 ([_band("peaking", 1000, 6.0, 1.0)], 0.0, None)],
        "keys": ["+1 dB", "+3 dB", "+6 dB"],
    },
    {
        "key": "limit",
        "head": "WHY A SLIDER STOPS",
        "lede": "Some filters do not fit in the firmware's number format.",
        "body": "The DAC stores its coefficients in a fixed-point format that spans "
                "only a certain range, and some perfectly ordinary-looking filters "
                "need more than it has. A high shelf below about 200 Hz overflows at "
                "any gain at all. When a band is pinned against that wall it tags "
                "itself limit, and the shape you get is the one drawn here rather than "
                "the one you asked for. Move the frequency, or lower Q, and it will go "
                "further. This is the hardware's limit, not the app's — the same "
                "ceiling the CLI enforces.",
        "figs": [([_band("high_shelf", 150, 6.0, 0.7)], 0.0, None)],
        "keys": ["a high shelf at 150 Hz"],
    },
    {
        "key": "keys",
        "head": "WITHOUT THE MOUSE",
        "lede": "Every edit has a key, because tuning is done listening.",
        "body": "1–8 picks a band — the same number on its card and on its graph "
                "handle. Up and down move its gain half a decibel, or two with shift "
                "held. Left and right walk the frequency a sixth of an octave; [ and "
                "] widen and narrow Q. 0 flattens the band without losing its shape. "
                "m mutes it, s solos it, and holding space bypasses the whole curve "
                "for as long as you hold it. Ctrl-Z steps back, Ctrl-S writes to "
                "flash, Ctrl-R re-reads the DAC, and Esc closes whatever is open. "
                "None of them fire while a panel is open, so typing a headphone name "
                "into a search box stays typing.",
        "figs": [],
        "keys": [],
    },
    {
        "key": "slot",
        "head": "THE DEVICE SLOT",
        "lede": "That number is the DAC's own profile, not a preset.",
        "body": "The bands you edit here are written to the custom slot. Moving the "
                "device off it means what you are editing is no longer what you hear "
                "— the DAC is playing one of its built-in profiles instead. On a DAWN "
                "PRO2 the number reads the same whether the EQ is switched on or off, "
                "so it cannot tell you which mode the hardware is in. The button on "
                "the device does that, and nothing here can see it.",
        "figs": [],
        "keys": [],
    },
]


def pages():
    """The guide, as rows the UI can render. Built once at startup — the figures are
    fixed strings, so there is nothing here to recompute on a resize or a repaint."""
    out = []
    for page in _PAGES:
        figs = [_fig(b, off, dash) for b, off, dash in page["figs"]]
        figs += [""] * (3 - len(figs))
        keys = list(page["keys"]) + [""] * (3 - len(page["keys"]))
        out.append({
            "key": page["key"],
            "head": page["head"],
            "lede": page["lede"],
            "body": page["body"],
            "fig-a": figs[0], "fig-b": figs[1], "fig-c": figs[2],
            "key-a": keys[0], "key-b": keys[1], "key-c": keys[2],
            "fig-zero": _zero() if figs[0] else "",
        })
    return out
