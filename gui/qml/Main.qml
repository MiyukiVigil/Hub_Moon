import QtQuick
import QtQuick.Layouts
import QtQuick.Dialogs
import HubMoon

// Hub Moon — modelled on MOONDROP's Sound-Tuning Tool. Three screens behind one
// window: a connection wizard (the landing screen), the EQ tuner, and the
// community Config center. `hub` is the Python controller (a root context
// property), unchanged by the tuner logic below.
Window {
    id: win
    visible: true
    width: 1360
    height: 850
    minimumWidth: 1120
    minimumHeight: 720
    color: Theme.bg
    title: "Hub Moon"

    // "connect" (landing) · "tune" (EQ editor) · "configs" (community library)
    property string page: "connect"

    // ── working model (a local copy, pushed back debounced) ──
    property var workBands: []
    property int selected: -1
    property var pending: ({})
    property real normalizeDb: 60
    property bool showPre: true      // fold pre-gain into the curve (output level), Hub-style

    function syncFromHub() {
        var out = [], src = hub.bands;
        for (var i = 0; i < src.length; i++)
            out.push({ index: src[i].index, type: src[i].type, frequency: src[i].frequency, gain: src[i].gain, q: src[i].q });
        workBands = out;
    }
    function bandBySlot(slot) { for (var i = 0; i < workBands.length; i++) if (workBands[i].index === slot) return workBands[i]; return null; }
    function editField(slot, fields) {
        var b = bandBySlot(slot); if (!b) return;
        for (var k in fields) b[k] = fields[k];
        workBands = workBands.slice();
        pending[slot] = true; commitTimer.restart();
    }
    Connections {
        target: hub
        function onBandsReplaced() { win.syncFromHub(); }
        function onToast(msg, isError) { toast.show(msg, isError); }
        // a device appeared while on the landing screen → go straight to the tuner
        function onChanged() { if (win.page === "connect" && hub.connected) win.page = "tune"; }
        // a community config was auditioned → show it on the graph
        function onConfigApplied(title) { win.page = "tune"; }
    }
    Component.onCompleted: syncFromHub()
    Timer {
        id: commitTimer; interval: 90
        onTriggered: {
            for (var slot in win.pending) { var b = win.bandBySlot(parseInt(slot)); if (b) hub.commitBand(b.index, b.type, b.frequency, b.gain, b.q); }
            win.pending = {};
        }
    }

    // ── built-in presets (units the CLI takes) ──
    readonly property var presets: [
        { name: "Flat", pre: 0.0, bands: [] },
        { name: "Bass boost", pre: -3.0, bands: [ { type: "low_shelf", frequency: 90, gain: 5.0, q: 0.7 } ] },
        { name: "V-shaped", pre: -4.0, bands: [ { type: "low_shelf", frequency: 90, gain: 4.5, q: 0.7 }, { type: "high_shelf", frequency: 8000, gain: 4.0, q: 0.7 } ] },
        { name: "Vocal", pre: -2.0, bands: [ { type: "peaking", frequency: 300, gain: -2.0, q: 1.0 }, { type: "peaking", frequency: 2500, gain: 3.0, q: 1.2 } ] },
        { name: "Warm", pre: -2.0, bands: [ { type: "low_shelf", frequency: 200, gain: 2.0, q: 0.7 }, { type: "high_shelf", frequency: 6000, gain: -3.0, q: 0.7 } ] },
        { name: "Bright / Air", pre: -2.0, bands: [ { type: "high_shelf", frequency: 10000, gain: 4.0, q: 0.7 } ] },
        { name: "Podcast", pre: -3.0, bands: [ { type: "high_pass", frequency: 80, gain: 0.0, q: 0.7 }, { type: "peaking", frequency: 2000, gain: 3.0, q: 1.0 } ] },
        { name: "Loudness", pre: -5.0, bands: [ { type: "low_shelf", frequency: 90, gain: 5.0, q: 0.7 }, { type: "high_shelf", frequency: 9000, gain: 4.0, q: 0.7 }, { type: "peaking", frequency: 2500, gain: 2.0, q: 1.2 } ] }
    ]
    property string activePreset: ""
    function applyPreset(p) {
        var out = [];
        for (var i = 0; i < hub.bandCount; i++) {
            if (i < p.bands.length) { var s = p.bands[i]; out.push({ index: i, type: s.type, frequency: s.frequency, gain: s.gain, q: s.q }); }
            else out.push({ index: i, type: "disabled", frequency: 1000, gain: 0.0, q: 1.0 });
        }
        hub.applyBands(out, p.pre);
        win.selected = -1; win.activePreset = p.name;
    }
    function resetFlat() { applyPreset(presets[0]); win.activePreset = ""; }

    // log-freq fraction, for aligning the region strip under the graph
    function xFrac(f) { var l0 = Math.log(20)/Math.LN10, l1 = Math.log(20000)/Math.LN10; return (Math.log(f)/Math.LN10 - l0)/(l1 - l0); }

    // an inline editable numeric field (Normalize / Pre Gain boxes)
    component NumField: Rectangle {
        id: nf
        property real value: 0
        property string unit: ""
        property int decimals: 1
        property real min: -1e9
        property real max: 1e9
        signal edited(real v)
        implicitWidth: 76; implicitHeight: 30; radius: 7
        color: Theme.a(Theme.card2, 0.55); border.width: 1
        border.color: inp.activeFocus ? Theme.accent : Theme.line
        Text {
            anchors.centerIn: parent; visible: !inp.activeFocus
            text: nf.value.toFixed(nf.decimals) + (nf.unit ? " " + nf.unit : "")
            color: Theme.text; font.family: Theme.font; font.pixelSize: 12
        }
        TextInput {
            id: inp; anchors.fill: parent; anchors.margins: 4
            horizontalAlignment: TextInput.AlignHCenter; verticalAlignment: TextInput.AlignVCenter
            color: Theme.text; font.family: Theme.font; font.pixelSize: 12
            opacity: activeFocus ? 1 : 0; selectByMouse: true
            onAccepted: { var v = parseFloat(text); if (!isNaN(v)) nf.edited(Math.max(nf.min, Math.min(nf.max, v))); focus = false; }
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.IBeamCursor
            onClicked: { inp.text = nf.value.toFixed(nf.decimals); inp.forceActiveFocus(); inp.selectAll(); } }
    }

    // a top-bar navigation tab (Tune / Configs)
    component NavTab: Rectangle {
        id: nt
        property string label: ""
        property string pageKey: ""
        property string icon: ""
        readonly property bool sel: win.page === pageKey
        implicitHeight: 34
        implicitWidth: ntRow.implicitWidth + 26
        radius: 8
        color: sel ? Theme.a(Theme.accent, 0.16) : (ntMa.containsMouse ? Theme.a(Theme.card2, 0.7) : "transparent")
        border.width: 1; border.color: sel ? Theme.a(Theme.accent, 0.5) : "transparent"
        Behavior on color { ColorAnimation { duration: 90 } }
        Row {
            id: ntRow; anchors.centerIn: parent; spacing: 7
            Sym { anchors.verticalCenter: parent.verticalCenter; text: nt.icon; sz: 16; color: nt.sel ? Theme.accent : Theme.sub }
            Text { anchors.verticalCenter: parent.verticalCenter; text: nt.label; color: nt.sel ? Theme.accent : Theme.sub; font.family: Theme.font; font.pixelSize: 13; font.weight: nt.sel ? Font.DemiBold : Font.Medium }
        }
        MouseArea { id: ntMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: win.page = nt.pageKey }
    }

    // ═══════════════════════════════════════════════════════════════════
    // landing / connection screen
    ConnectPage {
        anchors.fill: parent
        visible: win.page === "connect"
        onStartConnecting: hub.refresh()
        onUseDemo: win.page = "tune"
    }

    // app shell (tuner + configs)
    ColumnLayout {
        anchors.fill: parent
        visible: win.page !== "connect"
        spacing: 0

        // ── top nav bar ──
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 52
            color: Theme.bgElev; border.width: 0
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                spacing: 12
                // device chip
                Rectangle {
                    Layout.preferredHeight: 32
                    implicitWidth: chipRow.implicitWidth + 22
                    radius: 8; color: Theme.a(Theme.card2, 0.7); border.width: 1; border.color: Theme.line
                    Row {
                        id: chipRow; anchors.centerIn: parent; spacing: 8
                        Sym { anchors.verticalCenter: parent.verticalCenter; text: "headphones"; sz: 16; color: hub.connected ? Theme.accent : Theme.faint }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: hub.deviceName; color: Theme.text; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Medium }
                        Rectangle { anchors.verticalCenter: parent.verticalCenter; width: 7; height: 7; radius: 4; color: hub.connected ? Theme.good : Theme.faint }
                    }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: hub.refresh() }
                }
                Text {
                    visible: hub.firmware !== ""
                    text: "Firmware " + hub.firmware
                    color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
                }

                Item { Layout.fillWidth: true }
                // centre nav
                NavTab { label: "Tune"; pageKey: "tune"; icon: "tune" }
                NavTab { label: "Configs"; pageKey: "configs"; icon: "grid_view" }
                Item { Layout.fillWidth: true }

                // back to the connection screen
                Rectangle {
                    Layout.preferredHeight: 32
                    implicitWidth: devRow.implicitWidth + 22
                    radius: 8; color: devMa.containsMouse ? Theme.card2 : "transparent"
                    border.width: 1; border.color: Theme.line
                    Behavior on color { ColorAnimation { duration: 90 } }
                    Row {
                        id: devRow; anchors.centerIn: parent; spacing: 7
                        Sym { anchors.verticalCenter: parent.verticalCenter; text: "usb"; sz: 15; color: Theme.sub }
                        Text { anchors.verticalCenter: parent.verticalCenter; text: "Devices"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 13 }
                    }
                    MouseArea { id: devMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: win.page = "connect" }
                }
                Text {
                    text: "Hub Moon"
                    color: Theme.faint; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold
                }
            }
        }

        // ── content ──
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true

            // ═══════════════ TUNE ═══════════════
            ColumnLayout {
                anchors.fill: parent
                visible: win.page === "tune"
                spacing: 0

                // ── controls row (Normalize / Pre Gain) ──
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 48
                    color: Theme.a(Theme.bgElev, 0.5)
                    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 16
                        spacing: 10
                        Text { text: "Normalize"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12 }
                        NumField { value: win.normalizeDb; unit: "dB"; decimals: 0; min: 20; max: 90; onEdited: (v) => win.normalizeDb = v }
                        NumField { value: 500; unit: "Hz"; decimals: 0; min: 20; max: 20000; onEdited: (v) => {} }
                        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 22; color: Theme.line }
                        Text { text: "Pre Gain"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; Layout.leftMargin: 6 }
                        NumField {
                            enabled: hub.supportsPregain
                            value: hub.pregain; unit: "dB"; decimals: 1; min: -24; max: 0
                            onEdited: (v) => hub.setPregain(v)
                        }
                        Rectangle {
                            Layout.preferredWidth: 30; Layout.preferredHeight: 30; radius: 7
                            color: win.showPre ? Theme.a(Theme.accent, 0.18) : "transparent"
                            border.width: 1; border.color: win.showPre ? Theme.accent : Theme.line
                            Sym { anchors.centerIn: parent; text: win.showPre ? "visibility" : "visibility_off"; sz: 16; color: win.showPre ? Theme.accent : Theme.faint }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: win.showPre = !win.showPre }
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            visible: hub.dirty
                            text: "● unsaved changes"; color: Theme.warn; font.family: Theme.font; font.pixelSize: 12
                        }
                    }
                }

                // ── main area ──
                RowLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    spacing: 0

                    // ── sidebar: presets ──
                    Rectangle {
                        Layout.preferredWidth: 234; Layout.fillHeight: true
                        color: Theme.a(Theme.card, 0.5)
                        Rectangle { anchors.right: parent.right; height: parent.height; width: 1; color: Theme.line }
                        ColumnLayout {
                            anchors.fill: parent; anchors.margins: 16; spacing: 10
                            Text { text: "Presets"; color: Theme.text; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.DemiBold }
                            Text { text: "Starting points — apply and tune"; color: Theme.faint; font.family: Theme.font; font.pixelSize: 11 }
                            ColumnLayout {
                                Layout.fillWidth: true; Layout.topMargin: 4; spacing: 5
                                Repeater {
                                    model: win.presets
                                    delegate: Rectangle {
                                        required property var modelData
                                        Layout.fillWidth: true; implicitHeight: 38; radius: 8
                                        readonly property bool sel: win.activePreset === modelData.name
                                        color: sel ? Theme.a(Theme.accent, 0.16) : (pma.containsMouse ? Theme.card2 : Theme.a(Theme.card2, 0.4))
                                        border.width: 1; border.color: sel ? Theme.a(Theme.accent, 0.5) : Theme.line
                                        RowLayout {
                                            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 10; spacing: 8
                                            Text { text: modelData.name; color: Theme.text; font.family: Theme.font; font.pixelSize: 13; Layout.fillWidth: true }
                                            Sym { visible: parent.parent.sel; text: "check"; sz: 15; color: Theme.accent }
                                        }
                                        MouseArea { id: pma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: win.applyPreset(modelData) }
                                    }
                                }
                            }
                            // browse the community library
                            HubBtn {
                                Layout.fillWidth: true; Layout.topMargin: 6
                                label: "Browse community"; icon: "grid_view"; kind: "secondary"
                                onClicked: win.page = "configs"
                            }
                            Item { Layout.fillHeight: true }
                            // FR lines legend (matches the graph)
                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                            Text { text: "Curves"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.Medium }
                            Row { spacing: 8
                                Rectangle { width: 11; height: 11; radius: 2; color: Theme.flat; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: "Flat"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter } }
                            Row { spacing: 8
                                Rectangle { width: 11; height: 11; radius: 2; color: Theme.curve; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: "Flat (Equalized)"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter } }
                        }
                    }

                    // ── graph + region strip + band grid + actions ──
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true
                        spacing: 0

                        // graph
                        Rectangle {
                            Layout.fillWidth: true; Layout.preferredHeight: 320
                            color: Theme.bg
                            ResponseGraph {
                                id: graph
                                anchors.fill: parent
                                anchors.leftMargin: 12; anchors.rightMargin: 16; anchors.topMargin: 12; anchors.bottomMargin: 6
                                bands: win.workBands
                                pregain: hub.pregain
                                normalize: win.normalizeDb
                                showPre: win.showPre
                                selected: win.selected
                                onEditBand: (index, freq, gain) => win.editField(index, { frequency: freq, gain: gain })
                                onSetQ: (index, q) => win.editField(index, { q: q })
                                onPick: (index) => win.selected = index
                            }
                        }

                        // region strip (names aligned to the log-freq axis)
                        Item {
                            Layout.fillWidth: true; Layout.preferredHeight: 26
                            readonly property real inset: 12 + 40      // graph left margin + dB gutter
                            readonly property real plotW: width - inset - 16
                            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.line }
                            Repeater {
                                model: Theme.regions
                                delegate: Text {
                                    required property var modelData
                                    readonly property real cx: parent.inset + win.xFrac(Math.sqrt(modelData.f1 * modelData.f2)) * parent.plotW
                                    x: cx - implicitWidth / 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.n
                                    color: Theme.faint; font.family: Theme.font; font.pixelSize: 11
                                }
                            }
                        }

                        // band grid + right actions
                        RowLayout {
                            Layout.fillWidth: true; Layout.fillHeight: true
                            spacing: 12

                            Rectangle {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                color: Theme.a(Theme.card, 0.35)
                                Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.line }
                                BandEditor {
                                    anchors.fill: parent
                                    anchors.leftMargin: 16; anchors.rightMargin: 12; anchors.topMargin: 14; anchors.bottomMargin: 14
                                    bands: win.workBands
                                    selected: win.selected
                                    onPick: (i) => win.selected = i
                                    onSetType: (i, t) => win.editField(i, { type: t })
                                    onSetGain: (i, g) => win.editField(i, { gain: g })
                                    onSetFreq: (i, f) => win.editField(i, { frequency: Math.round(f) })
                                    onSetQ: (i, q) => win.editField(i, { q: Math.round(q * 100) / 100 })
                                }
                            }

                            // ── Global Gain + actions ──
                            Rectangle {
                                Layout.preferredWidth: 178; Layout.fillHeight: true
                                color: Theme.a(Theme.card, 0.5)
                                Rectangle { anchors.left: parent.left; height: parent.height; width: 1; color: Theme.line }
                                RowLayout {
                                    anchors.fill: parent; anchors.margins: 14; spacing: 12
                                    // global gain
                                    ColumnLayout {
                                        Layout.fillHeight: true; spacing: 6
                                        Text { text: "Global"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; Layout.alignment: Qt.AlignHCenter }
                                        VSlider {
                                            Layout.alignment: Qt.AlignHCenter; Layout.fillHeight: true
                                            from: -12; to: 12; value: hub.globalGain
                                            onMoved: (v) => hub.setGlobalGain(v)
                                        }
                                        Text { text: hub.globalGain.toFixed(0) + " dB"; color: Theme.text; font.family: Theme.font; font.pixelSize: 12; Layout.alignment: Qt.AlignHCenter }
                                    }
                                    // actions
                                    ColumnLayout {
                                        Layout.fillHeight: true; Layout.preferredWidth: 96; spacing: 8
                                        HubBtn { Layout.fillWidth: true; label: "Reset"; kind: "secondary"; onClicked: win.resetFlat() }
                                        HubBtn { Layout.fillWidth: true; label: "Revert"; kind: "secondary"; enabled: hub.dirty; onClicked: hub.revert() }
                                        HubBtn { Layout.fillWidth: true; label: "Import"; kind: "secondary"; onClicked: importDlg.open() }
                                        HubBtn { Layout.fillWidth: true; label: "Export"; kind: "secondary"; enabled: hub.connected; onClicked: exportDlg.open() }
                                        Item { Layout.fillHeight: true }
                                        HubBtn {
                                            Layout.fillWidth: true
                                            label: hub.busy ? "…" : "Write Cfg"; kind: "primary"
                                            enabled: hub.dirty && !hub.busy
                                            onClicked: hub.saveToFlash()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ═══════════════ CONFIGS ═══════════════
            ConfigsPage {
                anchors.fill: parent
                visible: win.page === "configs"
            }
        }
    }

    // ── toast ──
    Rectangle {
        id: toast
        property bool err: false
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 24
        width: Math.min(toastText.implicitWidth + 40, win.width - 60); height: 40; radius: 9
        visible: opacity > 0; opacity: 0
        color: err ? Theme.bad : Theme.card
        border.width: 1; border.color: err ? Theme.bad : Theme.line2
        Behavior on opacity { NumberAnimation { duration: 160 } }
        Row {
            anchors.centerIn: parent; spacing: 9
            Sym { anchors.verticalCenter: parent.verticalCenter; text: toast.err ? "error" : "info"; sz: 16; color: toast.err ? "#ffffff" : Theme.accent }
            Text { id: toastText; anchors.verticalCenter: parent.verticalCenter; font.family: Theme.font; font.pixelSize: 12; color: toast.err ? "#ffffff" : Theme.text }
        }
        Timer { id: toastTimer; interval: 4200; onTriggered: toast.opacity = 0 }
        function show(msg, isError) { if (!msg) return; toastText.text = msg; err = isError; opacity = 1; toastTimer.restart(); }
    }

    FileDialog { id: importDlg; title: "Import EQ (JSON)"; nameFilters: ["EQ files (*.json)", "All files (*)"]; onAccepted: hub.importJson(selectedFile) }
    FileDialog { id: exportDlg; title: "Export device state (JSON)"; fileMode: FileDialog.SaveFile; nameFilters: ["EQ files (*.json)"]; onAccepted: hub.exportJson(selectedFile) }
}
