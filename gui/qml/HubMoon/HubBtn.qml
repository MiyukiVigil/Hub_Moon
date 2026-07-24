import QtQuick

// Button, Moondrop-Hub style. kinds:
//   "primary"   white fill, dark text  (Write Cfg / save — the main CTA)
//   "accent"    blue fill               (Continue / apply)
//   "secondary" dark fill + hairline    (Reset / Import / Export / Read me)
//   "danger"    red text, subtle        (Disconnect)
Rectangle {
    id: btn
    property string label: ""
    property string icon: ""
    property string kind: "secondary"
    property bool enabled: true
    property int hpad: 14
    signal clicked()

    implicitHeight: 34
    implicitWidth: Math.max(64, row.implicitWidth + hpad * 2)
    radius: 8
    opacity: enabled ? 1 : 0.4

    readonly property bool _primary: kind === "primary"
    readonly property bool _accent: kind === "accent"
    readonly property bool _danger: kind === "danger"

    color: {
        if (_primary) return ma.containsMouse ? Qt.rgba(1,1,1,0.88) : Theme.primaryBtn;
        if (_accent)  return ma.containsMouse ? Qt.lighter(Theme.accent, 1.12) : Theme.accent;
        return ma.containsMouse ? Theme.card2 : Theme.a(Theme.card2, 0.6);
    }
    border.width: (_primary || _accent) ? 0 : 1
    border.color: ma.containsMouse ? Theme.line2 : Theme.line
    Behavior on color { ColorAnimation { duration: 90 } }

    readonly property color _fg: _primary ? Theme.primaryBtnText
                               : _accent ? "#ffffff"
                               : _danger ? Theme.bad : Theme.text

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 7
        Sym {
            visible: btn.icon !== ""
            anchors.verticalCenter: parent.verticalCenter
            text: btn.icon; sz: 16; color: btn._fg
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: btn.label
            font.family: Theme.font; font.pixelSize: 13
            font.weight: btn._primary ? Font.DemiBold : Font.Medium
            color: btn._fg
        }
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        enabled: btn.enabled
        cursorShape: Qt.PointingHandCursor
        onClicked: btn.clicked()
    }
}
