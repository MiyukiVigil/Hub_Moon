import QtQuick
import "dsp.js" as Dsp

// The frequency-response plot, MOONDROP-Hub style: a normalized dB scale
// (+50…+65 around a 60 dB reference), faint grid, the purple "Flat" line and
// the red "Flat (Equalized)" curve, with a legend box. Region names live in a
// separate strip below (see BandEditor's caller), not inside the plot.
//
// Controlled: renders bands / pregain / selected and emits intent.
Item {
    id: root
    property var bands: []
    property real pregain: 0
    property real normalize: 60
    property bool showPre: false
    property int selected: -1
    property bool interactive: true

    readonly property real topDb: normalize + 5
    readonly property real botDb: normalize - 10
    readonly property real fMin: 20
    readonly property real fMax: 20000
    readonly property real _l0: Math.log(fMin) / Math.LN10
    readonly property real _l1: Math.log(fMax) / Math.LN10

    // gutters: a strip at the bottom for freq labels, a little air at the top
    readonly property int topGut: 8
    readonly property int botGut: 18

    function xOfFreq(f, w) { return (Math.log(f) / Math.LN10 - _l0) / (_l1 - _l0) * w }
    function freqOfX(x, w) { return Math.pow(10, _l0 + (x / w) * (_l1 - _l0)) }
    function yOfDb(db, h) { var ph = h - topGut - botGut; return topGut + ph * (topDb - db) / (topDb - botDb) }
    function eqOfY(y, h) { var ph = h - topGut - botGut; return topDb - ((y - topGut) / ph) * (topDb - botDb) - normalize }
    // what the displayed curve is worth at f: EQ, plus pre-gain when the eye is on
    function curveDb(f) { return normalize + Dsp.sumResponse(bands, f) + (showPre ? pregain : 0) }

    property real peak: 0
    function _recomputePeak() {
        var p = -99, f = 20;
        while (f <= 20000) { var v = Dsp.sumResponse(bands || [], f); if (v > p) p = v; f *= 1.15; }
        peak = p;
    }

    onBandsChanged: { _recomputePeak(); canvas.requestPaint() }
    onPregainChanged: canvas.requestPaint()
    onShowPreChanged: canvas.requestPaint()
    onNormalizeChanged: canvas.requestPaint()
    onSelectedChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()
    Component.onCompleted: _recomputePeak()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        function css(c, a) {
            return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + ","
                 + Math.round(c.b * 255) + "," + (a === undefined ? 1 : a) + ")";
        }

        onPaint: {
            var ctx = getContext("2d");
            var w = width, h = height;
            ctx.clearRect(0, 0, w, h);
            ctx.lineJoin = "round"; ctx.lineCap = "round";
            var bands = root.bands || [];

            var plotW = w - 40;
            var plotBot = h - root.botGut;
            function X(f) { return 40 + root.xOfFreq(f, plotW); }

            // ── horizontal dB grid + left labels (every 5 dB) ──
            ctx.textAlign = "left"; ctx.font = "11px " + Theme.font;
            for (var db = root.botDb; db <= root.topDb + 0.01; db += 5) {
                var gy = root.yOfDb(db, h);
                ctx.strokeStyle = css(Theme.line, 0.7); ctx.lineWidth = 1;
                ctx.beginPath(); ctx.moveTo(40, gy); ctx.lineTo(w, gy); ctx.stroke();
                ctx.fillStyle = css(Theme.faint, 1);
                ctx.fillText("+" + Math.round(db) + "dB", 2, gy + 4);
            }

            // ── vertical frequency grid + bottom labels ──
            var marks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
            var lbls = ["20Hz", "50Hz", "100Hz", "200Hz", "500Hz", "1KHz", "2KHz", "5KHz", "10KHz", "20KHz"];
            ctx.textAlign = "center"; ctx.font = "11px " + Theme.font;
            for (var i = 0; i < marks.length; i++) {
                var mx = X(marks[i]);
                ctx.strokeStyle = css(Theme.line, 0.55); ctx.lineWidth = 1;
                ctx.beginPath(); ctx.moveTo(mx, root.topGut); ctx.lineTo(mx, plotBot); ctx.stroke();
                ctx.fillStyle = css(Theme.faint, 1);
                ctx.fillText(lbls[i], Math.max(40 + 16, Math.min(w - 18, mx)), h - 4);
            }

            // ── flat reference (purple) ──
            var yFlat = root.yOfDb(root.normalize, h);
            ctx.strokeStyle = css(Theme.flat, 1); ctx.lineWidth = 1.6;
            ctx.beginPath(); ctx.moveTo(40, yFlat); ctx.lineTo(w, yFlat); ctx.stroke();

            // ── equalized curve (red) — EQ, plus pre-gain when the eye is on ──
            ctx.save();
            ctx.beginPath(); ctx.rect(40, 0, plotW, plotBot); ctx.clip();   // never spill past the plot
            ctx.strokeStyle = css(Theme.curve, 1); ctx.lineWidth = 2.4;
            ctx.beginPath();
            for (var cx = 0; cx <= plotW; cx += 2) {
                var cf = root.freqOfX(cx, plotW);
                var cy = root.yOfDb(root.curveDb(cf), h);
                if (cx === 0) ctx.moveTo(40 + cx, cy); else ctx.lineTo(40 + cx, cy);
            }
            ctx.stroke();
            ctx.restore();

            // ── selected band marker (subtle dot on the curve) ──
            if (root.selected >= 0) {
                for (var s = 0; s < bands.length; s++) {
                    if (bands[s].index === root.selected && bands[s].type !== "disabled") {
                        var shx = X(bands[s].frequency);
                        var shy = root.yOfDb(root.curveDb(bands[s].frequency), h);
                        ctx.beginPath(); ctx.arc(shx, shy, 5, 0, 2 * Math.PI);
                        ctx.fillStyle = css(Theme.curve, 1); ctx.fill();
                        ctx.lineWidth = 2; ctx.strokeStyle = css(Theme.bg, 1); ctx.stroke();
                        break;
                    }
                }
            }

            // ── legend box, bottom-left of the plot ──
            var lx = 52, ly = h - 52;
            var rows = [{ t: "Flat", c: Theme.flat }, { t: "Flat (Equalized)", c: Theme.curve }];
            ctx.font = "11px " + Theme.font; ctx.textAlign = "left";
            ctx.fillStyle = css(Theme.card, 0.85);
            ctx.strokeStyle = css(Theme.line, 1); ctx.lineWidth = 1;
            var boxW = 128, boxH = rows.length * 17 + 10;
            ctx.beginPath(); ctx.rect(lx - 8, ly - 12, boxW, boxH); ctx.fill(); ctx.stroke();
            for (var r = 0; r < rows.length; r++) {
                var ey = ly + r * 17;
                ctx.fillStyle = css(rows[r].c, 1);
                ctx.fillRect(lx, ey - 4, 9, 9);
                ctx.fillStyle = css(Theme.sub, 1);
                ctx.fillText(rows[r].t, lx + 15, ey + 4);
            }
        }
    }

    // ── interaction: click/drag a band, wheel = Q of selected ──
    function nearest(px, py) {
        var w = width - 40, h = height, best = -1, bestD = 1e9;
        for (var i = 0; i < root.bands.length; i++) {
            var b = root.bands[i];
            if (b.type === "disabled") continue;
            var hx = 40 + root.xOfFreq(b.frequency, w);
            var hy = root.yOfDb(root.curveDb(b.frequency), h);
            var dd = Math.hypot(px - hx, py - hy);
            if (dd < bestD) { bestD = dd; best = i; }
        }
        return { i: best, d: bestD };
    }
    function clampGain(band, wanted) {
        if (Dsp.packsOk(band.frequency, wanted, band.q, band.type)) return wanted;
        var ceil = Dsp.maxSafeGain(band.frequency, band.q, band.type, wanted >= 0 ? 1 : -1);
        if (ceil === null) return 0;
        return Math.trunc(ceil * 10) / 10;
    }

    signal editBand(int index, real freq, real gain)
    signal setQ(int index, real q)
    signal pick(int index)

    MouseArea {
        anchors.fill: parent
        anchors.leftMargin: 40
        enabled: root.interactive
        property int drag: -1
        cursorShape: drag >= 0 ? Qt.ClosedHandCursor : Qt.CrossCursor

        onPressed: (e) => {
            var n = root.nearest(e.x + 40, e.y);
            if (n.i >= 0 && n.d < 28) { drag = n.i; root.pick(root.bands[n.i].index); }
            else drag = -1;
        }
        onPositionChanged: (e) => {
            if (drag < 0) return;
            var b = root.bands[drag];
            var plotW = width - 40;
            var f = Math.round(Math.max(root.fMin, Math.min(root.fMax, root.freqOfX(Math.max(0, Math.min(plotW, e.x)), plotW))));
            // convert cursor Y → target EQ dB (back out pre-gain if the curve shows it)
            var wantEq = root.eqOfY(Math.max(0, Math.min(height, e.y)), height) - (root.showPre ? root.pregain : 0);
            var g = Math.round(Math.max(-12, Math.min(12, wantEq)) * 10) / 10;
            var tmp = { frequency: f, q: b.q, type: b.type };
            root.editBand(b.index, f, root.clampGain(tmp, g));
        }
        onReleased: drag = -1
        onWheel: (wh) => {
            if (root.selected < 0) return;
            for (var i = 0; i < root.bands.length; i++) {
                if (root.bands[i].index === root.selected) {
                    var nq = Math.max(0.1, Math.min(10, root.bands[i].q * (wh.angleDelta.y > 0 ? 1.12 : 0.89)));
                    root.setQ(root.selected, Math.round(nq * 1000) / 1000);
                    break;
                }
            }
        }
    }
}
