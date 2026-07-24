import QtQuick
import QtQuick.Layouts
import "dsp.js" as Dsp

// The MOONDROP-Hub band grid: row labels (Filter / Gain / Frequency / Q) down
// the left, one column per band. The Gain row is tall (a vertical slider with a
// −/value/+ fine adjust); the others are steppers. Controlled — emits intent.
Item {
    id: root
    property var bands: []
    property int selected: -1

    signal pick(int index)
    signal setType(int index, string t)
    signal setGain(int index, real g)
    signal setFreq(int index, real f)
    signal setQ(int index, real q)

    readonly property var filterOptions: {
        var o = [];
        for (var i = 0; i < Theme.filterOrder.length; i++)
            o.push({ value: Theme.filterOrder[i], label: Theme.filterLabel(Theme.filterOrder[i]) });
        return o;
    }
    function fmtQ(q) { var s = q.toFixed(2); return s.replace(/0$/, "").replace(/\.$/, ""); }

    readonly property int labelW: 74

    GridLayout {
        anchors.fill: parent
        columns: 1 + root.bands.length
        rowSpacing: 6
        columnSpacing: 8

        // ── labels column helper text style is inline below ──

        // Row 0 — Filter
        Text {
            Layout.preferredWidth: root.labelW
            text: "Filter"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12
        }
        Repeater {
            model: root.bands
            delegate: ComboBox {
                required property var modelData
                Layout.fillWidth: true; Layout.preferredWidth: 1
                Layout.preferredHeight: 30
                options: root.filterOptions
                currentValue: modelData.type
                onPicked: (v) => { root.pick(modelData.index); root.setType(modelData.index, v); }
            }
        }

        // Row 1 — Gain (tall)
        Text {
            Layout.preferredWidth: root.labelW
            Layout.fillHeight: true
            verticalAlignment: Text.AlignTop
            text: "Gain"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12
        }
        Repeater {
            model: root.bands
            delegate: ColumnLayout {
                required property var modelData
                readonly property bool off: modelData.type === "disabled"
                readonly property bool atCeiling: !off && Math.abs(modelData.gain) > 0.05
                    && !Dsp.packsOk(modelData.frequency, modelData.gain + (modelData.gain >= 0 ? 0.1 : -0.1), modelData.q, modelData.type)
                Layout.fillWidth: true; Layout.preferredWidth: 1
                Layout.fillHeight: true
                spacing: 5

                VSlider {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.fillHeight: true
                    Layout.preferredHeight: 120
                    from: -12; to: 12; value: modelData.gain
                    enabled: !parent.off
                    fill: parent.atCeiling ? Theme.warn : (root.selected === modelData.index ? Theme.accent : Theme.a(Theme.accent, 0.85))
                    onMoved: (v) => { root.pick(modelData.index); root.setGain(modelData.index, v); }
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 0
                    Rectangle { width: 20; height: 20; radius: 5; color: dma.containsMouse ? Theme.a(Theme.accent, 0.16) : "transparent"
                        Sym { anchors.centerIn: parent; text: "remove"; sz: 13; color: Theme.sub }
                        MouseArea { id: dma; anchors.fill: parent; hoverEnabled: true; enabled: !parent.parent.off
                            cursorShape: Qt.PointingHandCursor; onClicked: root.setGain(modelData.index, Math.max(-12, modelData.gain - 0.1)) } }
                    Text {
                        Layout.minimumWidth: 46; horizontalAlignment: Text.AlignHCenter
                        text: parent.parent.off ? "—" : modelData.gain.toFixed(1) + " dB"
                        color: parent.parent.atCeiling ? Theme.warn : Theme.text
                        font.family: Theme.font; font.pixelSize: 12
                    }
                    Rectangle { width: 20; height: 20; radius: 5; color: ima.containsMouse ? Theme.a(Theme.accent, 0.16) : "transparent"
                        Sym { anchors.centerIn: parent; text: "add"; sz: 13; color: Theme.sub }
                        MouseArea { id: ima; anchors.fill: parent; hoverEnabled: true; enabled: !parent.parent.off
                            cursorShape: Qt.PointingHandCursor; onClicked: root.setGain(modelData.index, Math.min(12, modelData.gain + 0.1)) } }
                }
            }
        }

        // Row 2 — Frequency
        Text {
            Layout.preferredWidth: root.labelW
            text: "Frequency"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12
        }
        Repeater {
            model: root.bands
            delegate: Stepper {
                required property var modelData
                Layout.fillWidth: true; Layout.preferredWidth: 1
                enabled: modelData.type !== "disabled"
                editable: true
                text: "" + Math.round(modelData.frequency)
                onDec: root.setFreq(modelData.index, Math.max(20, modelData.frequency / 1.05))
                onInc: root.setFreq(modelData.index, Math.min(20000, modelData.frequency * 1.05))
                onCommitted: (v) => { var f = parseFloat(v); if (!isNaN(f)) root.setFreq(modelData.index, Math.max(20, Math.min(20000, f))); }
            }
        }

        // Row 3 — Q
        Text {
            Layout.preferredWidth: root.labelW
            text: "Q"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12
        }
        Repeater {
            model: root.bands
            delegate: Stepper {
                required property var modelData
                Layout.fillWidth: true; Layout.preferredWidth: 1
                enabled: modelData.type !== "disabled"
                editable: true
                text: root.fmtQ(modelData.q)
                onDec: root.setQ(modelData.index, Math.max(0.1, modelData.q - 0.1))
                onInc: root.setQ(modelData.index, Math.min(10, modelData.q + 0.1))
                onCommitted: (v) => { var q = parseFloat(v); if (!isNaN(q)) root.setQ(modelData.index, Math.max(0.1, Math.min(10, q))); }
            }
        }
    }
}
