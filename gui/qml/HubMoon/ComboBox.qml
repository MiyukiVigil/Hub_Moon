import QtQuick
import QtQuick.Controls.Basic as C

// A small dropdown (the per-band filter selector). options: [{value,label}].
Item {
    id: cb
    property var options: []
    property string currentValue: ""
    signal picked(string value)

    implicitHeight: 30
    implicitWidth: 112

    function labelFor(v) {
        for (var i = 0; i < options.length; i++) if (options[i].value === v) return options[i].label;
        return v;
    }

    Rectangle {
        id: field
        anchors.fill: parent
        radius: 7
        color: ma.containsMouse || popup.opened ? Theme.card2 : Theme.a(Theme.card2, 0.55)
        border.width: 1
        border.color: popup.opened ? Theme.accent : Theme.line
        Behavior on color { ColorAnimation { duration: 90 } }

        Text {
            anchors.left: parent.left; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: cb.labelFor(cb.currentValue)
            font.family: Theme.font; font.pixelSize: 12; color: Theme.text
            elide: Text.ElideRight
            width: parent.width - 32
        }
        Sym {
            anchors.right: parent.right; anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            text: "expand_more"; sz: 16; color: Theme.faint
        }
        MouseArea {
            id: ma
            anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: popup.opened ? popup.close() : popup.open()
        }
    }

    C.Popup {
        id: popup
        y: cb.height + 4
        width: cb.width
        padding: 4
        modal: false
        focus: true
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        background: Rectangle {
            color: Theme.card
            radius: 8
            border.width: 1; border.color: Theme.line2
        }
        contentItem: Column {
            spacing: 1
            Repeater {
                model: cb.options
                delegate: Rectangle {
                    required property var modelData
                    width: popup.availableWidth
                    height: 28
                    radius: 6
                    readonly property bool sel: modelData.value === cb.currentValue
                    color: rma.containsMouse ? Theme.a(Theme.accent, 0.18)
                         : (sel ? Theme.a(Theme.accent, 0.10) : "transparent")
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.label
                        font.family: Theme.font; font.pixelSize: 12
                        color: sel ? Theme.accent : Theme.text
                    }
                    MouseArea {
                        id: rma
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { cb.picked(modelData.value); popup.close(); }
                    }
                }
            }
        }
    }
}
