import QtQuick

// A − [value] + control (frequency / Q). Display-only value with step signals;
// the value box is click-to-edit when `editable`.
Rectangle {
    id: st
    property string text: ""
    property bool enabled: true
    property bool editable: false
    signal dec()
    signal inc()
    signal committed(string value)     // emitted on edit accept

    implicitHeight: 30
    implicitWidth: 108
    radius: 7
    color: Theme.a(Theme.card2, 0.55)
    border.width: 1; border.color: Theme.line
    opacity: enabled ? 1 : 0.4

    component StepBtn: Rectangle {
        property string glyph: ""
        signal act()
        width: 26; height: parent.height
        color: bma.containsMouse ? Theme.a(Theme.accent, 0.16) : "transparent"
        Sym { anchors.centerIn: parent; text: glyph; sz: 15; color: Theme.sub }
        MouseArea { id: bma; anchors.fill: parent; hoverEnabled: true
            enabled: st.enabled; cursorShape: Qt.PointingHandCursor; onClicked: parent.act() }
    }

    Row {
        anchors.fill: parent
        StepBtn { glyph: "remove"; onAct: st.dec() }
        Item {
            width: st.width - 52; height: parent.height
            Text {
                id: valText
                anchors.centerIn: parent
                visible: !editField.visible
                text: st.text
                font.family: Theme.font; font.pixelSize: 12; color: Theme.text
            }
            TextInput {
                id: editField
                anchors.fill: parent
                anchors.margins: 4
                visible: false
                horizontalAlignment: TextInput.AlignHCenter
                verticalAlignment: TextInput.AlignVCenter
                color: Theme.text; font.family: Theme.font; font.pixelSize: 12
                selectByMouse: true
                onAccepted: { st.committed(text); visible = false; }
                onActiveFocusChanged: if (!activeFocus) visible = false
            }
            MouseArea {
                anchors.fill: parent
                enabled: st.enabled && st.editable
                cursorShape: Qt.IBeamCursor
                onClicked: { editField.text = st.text; editField.visible = true; editField.forceActiveFocus(); editField.selectAll(); }
            }
        }
        StepBtn { glyph: "add"; onAct: st.inc() }
    }
}
