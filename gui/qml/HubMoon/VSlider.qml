import QtQuick

// Vertical gain slider, filled from the 0 dB centre. Hub style: thin track,
// blue fill, white handle.
Item {
    id: sl
    property real from: -12
    property real to: 12
    property real value: 0
    property color fill: Theme.accent
    property bool enabled: true
    signal moved(real v)

    implicitWidth: 22

    function _clamp(v) { return Math.max(from, Math.min(to, v)) }
    readonly property real _t: (value - from) / (to - from)
    readonly property real _zero: (0 - from) / (to - from)

    Rectangle {
        id: trk
        anchors.horizontalCenter: parent.horizontalCenter
        width: 4; height: parent.height; radius: 2
        color: Theme.card2
        Rectangle {
            radius: 2; color: sl.enabled ? sl.fill : Theme.line2; width: parent.width
            y: trk.height * (1 - Math.max(sl._t, sl._zero))
            height: trk.height * Math.abs(sl._t - sl._zero)
        }
        Rectangle {
            width: parent.width + 5; height: 1; x: -2.5
            y: trk.height * (1 - sl._zero)
            color: Theme.a(Theme.faint, 0.7)
        }
    }
    Rectangle {
        width: 14; height: 14; radius: 4
        color: "#ffffff"
        border.width: 1; border.color: Theme.a("#000000", 0.25)
        anchors.horizontalCenter: parent.horizontalCenter
        y: (sl.height - height) * (1 - Math.max(0, Math.min(1, sl._t)))
        visible: sl.enabled
    }
    MouseArea {
        anchors.fill: parent
        enabled: sl.enabled
        cursorShape: Qt.PointingHandCursor
        function pick(my) {
            var t = 1 - Math.max(0, Math.min(1, my / sl.height));
            var v = sl.from + (sl.to - sl.from) * t;
            sl.value = Math.round(v * 10) / 10; sl.moved(sl.value);
        }
        onPressed: (e) => pick(e.y)
        onPositionChanged: (e) => { if (pressed) pick(e.y) }
        onWheel: (w) => { sl.value = sl._clamp(sl.value + (w.angleDelta.y > 0 ? 0.5 : -0.5)); sl.moved(sl.value) }
    }
}
