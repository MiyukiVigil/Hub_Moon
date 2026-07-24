import QtQuick
import QtQuick.Layouts

// The landing screen, MOONDROP-Hub style: a step indicator, a "Prepare to
// connect" card with the Start-connecting CTA, the supported-products grid and
// a tips card. `hub` (the Python controller) is a root context property, so it
// is reachable here directly.
Item {
    id: page
    signal startConnecting()
    signal useDemo()

    // idle → step 1 active; probing → "Authorize access"; connected → done
    readonly property int activeStep: hub.connected ? 3 : (hub.busy ? 2 : 0)
    // set once the user has pressed Start; drives the "nothing found" fallback
    property bool attempted: false
    readonly property bool noDevice: attempted && !hub.busy && !hub.connected

    Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: col.implicitHeight + 80
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        ColumnLayout {
            id: col
            width: Math.min(parent.width - 48, 940)
            anchors.horizontalCenter: parent.horizontalCenter
            y: 40
            spacing: 22

            // ── heading ──
            ColumnLayout {
                Layout.fillWidth: true; spacing: 6
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "USB Device Connection"
                    color: Theme.text; font.family: Theme.font
                    font.pixelSize: 27; font.weight: Font.DemiBold
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: "Follow the steps below to connect your USB device"
                    color: Theme.sub; font.family: Theme.font; font.pixelSize: 14
                }
            }

            // ── step indicator ──
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4; Layout.bottomMargin: 4
                spacing: 0
                Repeater {
                    model: ["Prepare to connect", "Select device", "Authorize access", "Connection completed"]
                    delegate: RowLayout {
                        required property int index
                        required property string modelData
                        Layout.fillWidth: index > 0
                        spacing: 8
                        readonly property bool done: index < page.activeStep
                        readonly property bool active: index === page.activeStep
                        // connector line, pinned to the circle's vertical centre
                        Rectangle {
                            visible: index > 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: 2
                            Layout.topMargin: 15
                            Layout.alignment: Qt.AlignTop
                            radius: 1
                            color: index <= page.activeStep ? Theme.accent : Theme.line
                            Behavior on color { ColorAnimation { duration: 160 } }
                        }
                        ColumnLayout {
                            Layout.preferredWidth: 108
                            spacing: 7
                            Rectangle {
                                Layout.alignment: Qt.AlignHCenter
                                width: 32; height: 32; radius: 16
                                color: (done || active) ? Theme.accent : Theme.a(Theme.card2, 0.8)
                                border.width: 1
                                border.color: (done || active) ? Theme.accent : Theme.line
                                Behavior on color { ColorAnimation { duration: 160 } }
                                Sym {
                                    anchors.centerIn: parent; visible: done
                                    text: "check"; sz: 17; color: "#ffffff"
                                }
                                Text {
                                    anchors.centerIn: parent; visible: !done
                                    text: (index + 1).toString()
                                    color: active ? "#ffffff" : Theme.faint
                                    font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold
                                }
                            }
                            Text {
                                Layout.alignment: Qt.AlignHCenter
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData
                                color: index <= page.activeStep ? Theme.text : Theme.faint
                                font.family: Theme.font; font.pixelSize: 12
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            // ── "Prepare to connect" card ──
            Rectangle {
                Layout.fillWidth: true
                color: Theme.a(Theme.card, 0.6)
                border.width: 1; border.color: Theme.line
                radius: 14
                implicitHeight: prep.implicitHeight + 44
                ColumnLayout {
                    id: prep
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: parent.top; anchors.margins: 22
                    spacing: 12
                    Text {
                        text: "Prepare to connect"
                        color: Theme.text; font.family: Theme.font
                        font.pixelSize: 17; font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "Make sure your Moondrop USB DAC is plugged in, then press "
                            + "“Start connecting”. Hub Moon talks to the device directly over "
                            + "USB HID — no account and no browser needed."
                        color: Theme.sub; font.family: Theme.font; font.pixelSize: 13
                        wrapMode: Text.WordWrap; lineHeight: 1.25
                    }
                    HubBtn {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.topMargin: 4
                        hpad: 30
                        label: hub.busy ? "Connecting…" : "Start connecting"
                        icon: hub.busy ? "" : "usb"
                        kind: "accent"
                        enabled: !hub.busy
                        onClicked: { page.attempted = true; page.startConnecting(); }
                    }
                    // nothing-found fallback
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        visible: page.noDevice
                        radius: 10
                        color: Theme.a(Theme.warn, 0.10)
                        border.width: 1; border.color: Theme.a(Theme.warn, 0.4)
                        implicitHeight: fb.implicitHeight + 24
                        RowLayout {
                            id: fb
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 14; anchors.rightMargin: 14
                            spacing: 12
                            Sym { text: "usb_off"; sz: 20; color: Theme.warn }
                            Text {
                                Layout.fillWidth: true
                                text: "No supported device found. Check the cable and connection, or explore the tuner in demo mode."
                                color: Theme.text; font.family: Theme.font; font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                            HubBtn { label: "Try again"; kind: "secondary"; onClicked: page.startConnecting() }
                            HubBtn { label: "Demo mode"; kind: "primary"; onClicked: page.useDemo() }
                        }
                    }
                }
            }

            // ── supported products ──
            Rectangle {
                Layout.fillWidth: true
                color: Theme.a(Theme.card, 0.6)
                border.width: 1; border.color: Theme.line
                radius: 14
                implicitHeight: sp.implicitHeight + 44
                ColumnLayout {
                    id: sp
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: parent.top; anchors.margins: 22
                    spacing: 14
                    Text {
                        text: "Supported products"
                        color: Theme.text; font.family: Theme.font
                        font.pixelSize: 17; font.weight: Font.DemiBold
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 4
                        columnSpacing: 12; rowSpacing: 12
                        Repeater {
                            model: hub.supportedDevices
                            delegate: Rectangle {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.preferredHeight: 66
                                radius: 10
                                color: dma.containsMouse ? Theme.card2 : Theme.a(Theme.card2, 0.5)
                                border.width: 1
                                border.color: dma.containsMouse ? Theme.line2 : Theme.line
                                Behavior on color { ColorAnimation { duration: 90 } }
                                ColumnLayout {
                                    anchors.centerIn: parent; spacing: 1
                                    Text {
                                        visible: modelData.moondrop
                                        Layout.alignment: Qt.AlignHCenter
                                        text: "MOONDROP"
                                        color: Theme.faint; font.family: Theme.font
                                        font.pixelSize: 10; font.weight: Font.Medium
                                        font.letterSpacing: 1
                                    }
                                    Text {
                                        Layout.alignment: Qt.AlignHCenter
                                        Layout.maximumWidth: 150
                                        horizontalAlignment: Text.AlignHCenter
                                        text: modelData.name
                                        color: Theme.text; font.family: Theme.font
                                        font.pixelSize: 13; font.weight: Font.Medium
                                        wrapMode: Text.WordWrap
                                    }
                                }
                                MouseArea { id: dma; anchors.fill: parent; hoverEnabled: true }
                            }
                        }
                    }
                }
            }

            // ── connection tips ──
            Rectangle {
                Layout.fillWidth: true
                Layout.bottomMargin: 12
                color: Theme.a(Theme.card, 0.4)
                border.width: 1; border.color: Theme.line
                radius: 14
                implicitHeight: tips.implicitHeight + 40
                ColumnLayout {
                    id: tips
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.top: parent.top; anchors.margins: 20
                    spacing: 9
                    Text {
                        text: "Connection tips"
                        color: Theme.text; font.family: Theme.font
                        font.pixelSize: 15; font.weight: Font.DemiBold
                    }
                    Repeater {
                        model: [
                            "Ensure the device is correctly connected to the computer.",
                            "On Linux, a udev rule (shipped with the package) lets Hub Moon open the DAC without sudo.",
                            "Edits audition live over USB — use Write Cfg to save them to the device's flash."
                        ]
                        delegate: RowLayout {
                            required property string modelData
                            Layout.fillWidth: true; spacing: 9
                            Sym { text: "info"; sz: 16; color: Theme.accent; Layout.alignment: Qt.AlignTop }
                            Text {
                                Layout.fillWidth: true
                                text: modelData
                                color: Theme.sub; font.family: Theme.font; font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }
    }
}
