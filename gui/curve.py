"""Frequency-response curves for the GUI.

This replaces ``qml/HubMoon/dsp.js``, which was a hand-kept JavaScript copy of
``moondrop_control``'s biquad maths. Two copies of the same formulas is one copy too
many: the point of the graph is that a curve it refuses to draw is a curve the DAC
would refuse to accept, and that only holds if both come from the same code. So the
coefficients come from :func:`moondrop_control.calculate_biquad` directly, and only
the magnitude response — which the engine never needed — lives here.

The output is SVG path commands, because that is what a Slint ``Path`` takes. Sampling
and string-building happen on the Python side, which means the UI holds no maths at
all: it is handed a finished curve and draws it.
"""
from __future__ import annotations

import math
import os
import sys

try:
    import moondrop_control as mc
except ImportError:  # running from a source checkout, gui/ next to the module
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

FS = 96000
F_MIN = 20.0
F_MAX = 20000.0

_L0 = math.log10(F_MIN)
_L1 = math.log10(F_MAX)


def x_of_freq(f, width):
    """Log frequency axis → pixels."""
    return (math.log10(f) - _L0) / (_L1 - _L0) * width


def freq_of_x(x, width):
    return 10.0 ** (_L0 + (x / width) * (_L1 - _L0))


def magnitude_db(num, den, f):
    """|H(f)| in dB on the unit circle. a0 is 1 by construction, and the engine hands
    back numerator/denominator already swapped the way Moondrop's implementation wants
    them — so `den` is the numerator of the response here. Kept in that order rather
    than "corrected", to stay readable next to calculate_biquad."""
    w = 2.0 * math.pi * f / FS
    c1, s1 = math.cos(w), math.sin(w)
    c2, s2 = math.cos(2.0 * w), math.sin(2.0 * w)
    n_re = den[0] + den[1] * c1 + den[2] * c2
    n_im = -(den[1] * s1 + den[2] * s2)
    d_re = 1.0 + num[1] * c1 + num[2] * c2
    d_im = -(num[1] * s1 + num[2] * s2)
    d2 = d_re * d_re + d_im * d_im
    if d2 == 0.0:
        return 0.0
    return 10.0 * math.log10((n_re * n_re + n_im * n_im) / d2)


def band_response(band, f):
    """One band's contribution at f. An unrealisable band contributes nothing rather
    than poisoning the sum — calculate_biquad raises on an impossible shelf slope."""
    if band.get("type") == "disabled":
        return 0.0
    try:
        num, den = mc.calculate_biquad(
            float(band["frequency"]), float(band["gain"]),
            float(band["q"]), band["type"])
    except (ValueError, ZeroDivisionError, KeyError):
        return 0.0
    db = magnitude_db(num, den, f)
    return db if math.isfinite(db) else 0.0


def sum_response(bands, f):
    return sum(band_response(b, f) for b in (bands or []))


def peak_db(bands, steps=64):
    """Largest boost anywhere in the sweep — what the pre-gain hint is derived from."""
    peak = -99.0
    for i in range(steps + 1):
        f = F_MIN * (F_MAX / F_MIN) ** (i / steps)
        peak = max(peak, sum_response(bands, f))
    return peak


def svg_curve(bands, *, width, height, top_db, bot_db, offset_db=0.0, samples=220,
              dash=None):
    """Sample the summed response across the log axis and return SVG path commands.

    `offset_db` is what the curve is worth before the EQ — the normalize reference,
    plus pre-gain when the user has asked to see output level rather than EQ alone.
    Points are emitted at ~1/3 px resolution, which is past what any display resolves
    and still only a couple of KB of string.
    """
    if width <= 0 or height <= 0 or top_db <= bot_db:
        return ""
    span = top_db - bot_db
    pts = []
    for i in range(samples + 1):
        x = width * i / samples
        f = freq_of_x(x, width)
        db = offset_db + sum_response(bands, f)
        y = height * (top_db - db) / span
        # Clamp to the box: a huge boost should run along the ceiling, not fly off
        # and make the renderer size a path thousands of pixels tall.
        pts.append((x, max(-4.0, min(height + 4.0, y))))

    if not dash:
        return " ".join("%s %.1f %.2f" % ("M" if i == 0 else "L", x, y)
                        for i, (x, y) in enumerate(pts))
    return dash_polyline(pts, *dash)


def dash_polyline(pts, on, off):
    """Cut a polyline into dashes and return SVG path commands.

    Done here rather than asked of the renderer: a stroke dash array is not something
    every Slint backend exposes, and the traces that have to look dashed — the output
    curve, the readout's grid — have to look dashed on all of them. `on`/`off` are
    lengths in pixels, walked along the line so the pattern is continuous across
    segment joins instead of restarting at every vertex.
    """
    period = on + off
    out, travelled, pen_down = [], 0.0, False
    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0:
            continue
        walked = 0.0
        while walked < seg:
            phase = (travelled + walked) % period
            drawing = phase < on
            step = min(seg - walked, (on - phase) if drawing else (period - phase))
            t0 = (walked) / seg
            t1 = (walked + step) / seg
            if drawing:
                ax, ay = x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0
                bx, by = x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1
                if not pen_down:
                    out.append("M %.1f %.2f" % (ax, ay))
                out.append("L %.1f %.2f" % (bx, by))
                pen_down = True
            else:
                pen_down = False
            walked += step
        travelled += seg
    return " ".join(out)


def svg_flat(*, width, height, top_db, bot_db, db):
    """The straight reference line the equalised curve is read against."""
    if width <= 0 or height <= 0 or top_db <= bot_db:
        return ""
    y = height * (top_db - db) / (top_db - bot_db)
    return "M 0 %.2f L %.1f %.2f" % (y, width, y)


def svg_grid(*, width, height, rows, cols, dash=(2.0, 4.0)):
    """The readout view's grid, as one dashed path.

    `rows`/`cols` are fractions of the plot, not values — the caller already knows
    where its gridlines go, and this only has to draw them. One path rather than a
    Rectangle per line keeps a 15-line grid to a single element.
    """
    if width <= 0 or height <= 0:
        return ""
    out = []
    for f in rows:
        y = height * f
        out.append(dash_polyline([(0.0, y), (width, y)], *dash))
    for f in cols:
        x = width * f
        out.append(dash_polyline([(x, 0.0), (x, height)], *dash))
    return " ".join(p for p in out if p)
