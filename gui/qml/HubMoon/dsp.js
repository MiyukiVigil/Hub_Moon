/*
 * dsp.js — QML JavaScript port of moondrop_control.py's biquad maths.
 *
 * A line-for-line copy of the site's dsp.js (itself a port of the Python),
 * adapted for QML: `.pragma library` (stateless, shared) and plain function
 * declarations instead of ES `export`. Same curve, same refusals, same Q2.30
 * ceiling as the tool that does the writing — so a band this refuses to move is
 * a band the DAC would refuse to accept.
 *
 * Used only for DRAWING and live clamping. The authoritative validation still
 * happens in Python on write (write_peq_index re-checks and raises).
 */
.pragma library

var FS = 96000;
var FILTER_TYPES = ['disabled', 'low_shelf', 'peaking', 'high_shelf', 'low_pass', 'high_pass'];

var Q30_SCALE = 1073741824;   // 2^30
var INT32_MIN = -2147483648;
var INT32_MAX = 2147483647;

function includes(arr, v) { for (var i = 0; i < arr.length; i++) if (arr[i] === v) return true; return false; }

/* RBJ shelf slope: sqrt((A+1/A)(1/S-1)+2) has no real solution once too steep. */
function maxShelfQ(gain) {
    var a = Math.pow(10, Math.abs(gain) / 40);
    var s = a + 1 / a;
    if (s <= 2) return Infinity;
    return 1 / (1 - 2 / s);
}

/* Returns { num:[3], den:[3] } with num[0] normalised to 1, or null when the
   shelf slope has no solution (caller treats null as "unrealisable"). */
function calculateBiquad(f, gain, Q, filterType) {
    if (!includes(FILTER_TYPES, filterType) || filterType === 'disabled')
        return { num: [0, 0, 0], den: [1, 0, 0] };

    if (filterType === 'low_shelf' || filterType === 'high_shelf') {
        if (Q > maxShelfQ(gain)) return null;
    }

    var w0 = f * Math.PI * 2 / FS;
    var cos_w0 = Math.cos(w0);
    var sin_w0 = Math.sin(w0);
    var num, den, a0, alpha, A, a, sa;

    if (filterType === 'peaking') {
        A = Math.sqrt(Math.pow(10, gain / 20));
        alpha = sin_w0 / (2 * Q);
        a0 = alpha / A + 1;
        num = [1.0, cos_w0 * -2.0 / a0, (1.0 - alpha / A) / a0];
        den = [(alpha * A + 1.0) / a0, cos_w0 * -2.0 / a0, (1.0 - alpha * A) / a0];
    } else if (filterType === 'low_shelf') {
        a = Math.pow(10, gain / 40); sa = Math.sqrt(a);
        alpha = sin_w0 / 2.0 * Math.sqrt((a + 1.0 / a) * (1.0 / Q - 1.0) + 2.0);
        a0 = a + 1.0 + (a - 1.0) * cos_w0 + 2.0 * sa * alpha;
        num = [1.0,
               -2.0 * (a - 1.0 + (a + 1.0) * cos_w0) / a0,
               (a + 1.0 + (a - 1.0) * cos_w0 - 2.0 * sa * alpha) / a0];
        den = [a * (a + 1.0 - (a - 1.0) * cos_w0 + 2.0 * sa * alpha) / a0,
               2.0 * a * (a - 1.0 - (a + 1.0) * cos_w0) / a0,
               a * (a + 1.0 - (a - 1.0) * cos_w0 - 2.0 * sa * alpha) / a0];
    } else if (filterType === 'high_shelf') {
        a = Math.pow(10, gain / 40); sa = Math.sqrt(a);
        alpha = sin_w0 / 2.0 * Math.sqrt((a + 1.0 / a) * (1.0 / Q - 1.0) + 2.0);
        a0 = a + 1.0 - (a - 1.0) * cos_w0 + 2.0 * sa * alpha;
        num = [1.0,
               2.0 * (a - 1.0 - (a + 1.0) * cos_w0) / a0,
               (a + 1.0 - (a - 1.0) * cos_w0 - 2.0 * sa * alpha) / a0];
        den = [a * (a + 1.0 + (a - 1.0) * cos_w0 + 2.0 * sa * alpha) / a0,
               -2.0 * a * (a - 1.0 + (a + 1.0) * cos_w0) / a0,
               a * (a + 1.0 + (a - 1.0) * cos_w0 - 2.0 * sa * alpha) / a0];
    } else if (filterType === 'low_pass') {
        alpha = sin_w0 / (2.0 * Q);
        a0 = alpha + 1.0;
        num = [1.0, cos_w0 * -2.0 / a0, (1.0 - alpha) / a0];
        den = [(1.0 - cos_w0) / 2.0 / a0, (1.0 - cos_w0) / a0, (1.0 - cos_w0) / 2.0 / a0];
    } else { // high_pass
        alpha = sin_w0 / (2.0 * Q);
        a0 = alpha + 1.0;
        num = [1.0, cos_w0 * -2.0 / a0, (1.0 - alpha) / a0];
        den = [(1.0 + cos_w0) / 2.0 / a0, (-1.0 - cos_w0) / a0, (1.0 + cos_w0) / 2.0 / a0];
    }
    return { num: num, den: den };
}

/* The Q2.30 gate — same five values, same scale, same int32 bounds as
   pack_coefficients(). Returns true when all five fit. */
function packsOk(freq, gain, Q, filterType) {
    var c = calculateBiquad(freq, gain, Q, filterType);
    if (c === null) return false;
    var floats = [c.den[0], c.den[1], c.den[2], -c.num[1], -c.num[2]];
    for (var i = 0; i < 5; i++) {
        var v = Math.round(floats[i] * Q30_SCALE);
        if (!(v >= INT32_MIN && v <= INT32_MAX)) return false;
    }
    return true;
}

/* Largest gain in the given sign direction whose coefficients still fit, by
   bisection. null when even a sliver overflows. */
function maxSafeGain(freq, Q, filterType, sign, limit) {
    if (limit === undefined) limit = 18.0;
    if (packsOk(freq, sign * limit, Q, filterType)) return sign * limit;
    var lo = 0.0, hi = limit;
    for (var i = 0; i < 40; i++) {
        var mid = (lo + hi) / 2.0;
        if (packsOk(freq, sign * mid, Q, filterType)) lo = mid; else hi = mid;
    }
    return lo > 0.05 ? sign * lo : null;
}

/* |H(f)| in dB on the unit circle. a0 == 1 by construction. */
function magnitudeDb(c, f) {
    if (c === null) return 0;
    var num = c.num, den = c.den;
    var w = 2 * Math.PI * f / FS;
    var c1 = Math.cos(w), s1 = Math.sin(w);
    var c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
    var nRe = den[0] + den[1] * c1 + den[2] * c2;
    var nIm = -(den[1] * s1 + den[2] * s2);
    var dRe = 1 + num[1] * c1 + num[2] * c2;
    var dIm = -(num[1] * s1 + num[2] * s2);
    var d2 = dRe * dRe + dIm * dIm;
    if (d2 === 0) return 0;
    return 10 * Math.log10 ? 10 * Math.log10((nRe * nRe + nIm * nIm) / d2)
                           : 10 * Math.log((nRe * nRe + nIm * nIm) / d2) / Math.LN10;
}

/* Response of one band across the whole sweep — for the ghost curves. */
function bandResponse(band, f) {
    if (band.type === 'disabled') return 0;
    var c = calculateBiquad(band.frequency, band.gain, band.q, band.type);
    if (c === null) return 0;
    var db = magnitudeDb(c, f);
    return isFinite(db) ? db : 0;
}

/* Total response of a band list. Unrealisable bands contribute nothing rather
   than poisoning the sum with NaN. */
function sumResponse(bands, f) {
    var total = 0;
    for (var i = 0; i < bands.length; i++) {
        total += bandResponse(bands[i], f);
    }
    return total;
}
