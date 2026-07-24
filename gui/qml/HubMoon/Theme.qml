pragma Singleton
import QtQuick

// Hub Moon's own look — modelled on MOONDROP's Hub / Sound-Tuning Tool:
// a near-black surface stack, a blue accent, white primary buttons, and the
// signature red "equalized" curve over a purple "flat" reference. Sans-serif
// throughout (this is NOT the sea-shell monospace theme).
QtObject {
    id: theme

    // ── surface stack (near-black → cards → inputs) ──
    readonly property color bg:      "#0a0a0c"
    readonly property color bgElev:  "#101013"   // top bar / raised strips
    readonly property color card:    "#161619"
    readonly property color card2:   "#1c1c20"   // inputs, hover
    readonly property color line:    "#26262b"
    readonly property color line2:   "#33333a"

    // ── text ──
    readonly property color text:    "#f4f4f6"
    readonly property color sub:      "#a9a9b3"
    readonly property color faint:    "#6d6d78"

    // ── accents ──
    readonly property color accent:  "#3b82f6"   // blue — toggles, active, links
    readonly property color accentDim: "#1e293b"
    readonly property color good:    "#22c55e"
    readonly property color warn:    "#f0a53b"
    readonly property color bad:     "#ef4444"

    // ── the two graph curves ──
    readonly property color flat:    "#b46fd6"   // purple reference line
    readonly property color curve:   "#f0554e"   // red equalized curve

    // primary CTA (Write Cfg / save) is a white button with dark text
    readonly property color primaryBtn:     "#f4f4f6"
    readonly property color primaryBtnText: "#0a0a0c"

    readonly property string font: "Roboto"
    // numbers use the same family; kept as a hook if a mono is ever wanted
    readonly property string mono: "Roboto"

    function a(c, al) { return Qt.rgba(c.r, c.g, c.b, al) }

    // ── spectrum regions (MOONDROP Hub names) ──
    readonly property var regions: [
        { n: "Sub Bass",        f1: 20,   f2: 60,    c: "#7c6bd6" },
        { n: "Mid Bass",        f1: 60,   f2: 250,   c: "#5a8bd8" },
        { n: "Lower Midrange",  f1: 250,  f2: 500,   c: "#4aa6cf" },
        { n: "Upper Midrange",  f1: 500,  f2: 2000,  c: "#46b7a6" },
        { n: "Presence Region", f1: 2000, f2: 4000,  c: "#6cc487" },
        { n: "Mid Treble",      f1: 4000, f2: 8000,  c: "#b9c46a" },
        { n: "Air",             f1: 8000, f2: 20000, c: "#d1a15e" }
    ]
    function regionOf(f) {
        for (var i = 0; i < regions.length; i++)
            if (f >= regions[i].f1 && f < regions[i].f2) return regions[i];
        return regions[regions.length - 1];
    }

    // ── filter types: wire name ↔ display label (matches FILTER_TYPES) ──
    readonly property var filterOrder: ["peaking", "low_shelf", "high_shelf", "low_pass", "high_pass", "disabled"]
    function filterLabel(t) {
        return ({ peaking: "Peaking", low_shelf: "Low Shelf", high_shelf: "High Shelf",
                  low_pass: "Low Pass", high_pass: "High Pass", disabled: "Off" })[t] || "Peaking";
    }
}
