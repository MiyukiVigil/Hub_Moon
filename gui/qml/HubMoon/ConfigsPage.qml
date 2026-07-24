import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as C

// The "Config center" — MOONDROP's community PEQ library. Search + popularity
// sort over a virtualised card grid; "Apply" fetches the curve and auditions it
// live, then the app jumps to the tuner. `hub` is a root context property.
Item {
    id: page

    property var all: []          // full slim list from the controller
    property var shown: []        // filtered + sorted view
    property bool loaded: false
    property string query: ""
    property string sortBy: "likes"
    readonly property bool busy: hub.configsBusy

    // ── preview popup state ──
    property var previewCard: null      // the card being previewed
    property bool previewOpen: false
    property bool previewLoading: false
    property var previewBands: []
    property real previewPre: 0

    function openPreview(card) {
        previewCard = card;
        previewBands = [];
        previewPre = 0;
        previewLoading = true;
        previewOpen = true;
        hub.previewConfig(card.uuid);
    }
    function closePreview() { previewOpen = false; previewCard = null; previewBands = []; }

    readonly property var sortTabs: [
        { key: "likes",     label: "Popular" },
        { key: "rating",    label: "Top rated" },
        { key: "downloads", label: "Most downloaded" },
        { key: "comments",  label: "Most discussed" }
    ]

    function refilter() {
        var q = query.trim().toLowerCase();
        var out = [];
        for (var i = 0; i < all.length; i++) {
            var c = all[i];
            if (q === "" || c.title.toLowerCase().indexOf(q) >= 0
                         || c.author.toLowerCase().indexOf(q) >= 0
                         || c.desc.toLowerCase().indexOf(q) >= 0)
                out.push(c);
        }
        var k = sortBy;
        out.sort(function (a, b) {
            var d = (b[k] || 0) - (a[k] || 0);
            return d !== 0 ? d : (b.likes || 0) - (a.likes || 0);
        });
        shown = out;
    }
    onSortByChanged: refilter()

    function fmt(n) {
        if (n >= 10000) return Math.round(n / 1000) + "k";
        if (n >= 1000)  return (n / 1000).toFixed(1) + "k";
        return "" + n;
    }
    function avatarColor(name) {
        var h = 0;
        for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) & 0xffffff;
        return Qt.hsla((h % 360) / 360, 0.45, 0.5, 1);
    }

    Connections {
        target: hub
        function onConfigsLoaded(rows, cached) {
            page.all = rows; page.loaded = true; page.refilter();
        }
        function onConfigPreview(bands, pre, uuid) {
            if (!page.previewOpen || !page.previewCard) return;
            if (page.previewCard.uuid !== uuid) return;   // a stale/earlier request
            page.previewBands = bands;
            page.previewPre = pre;
            page.previewLoading = false;
        }
    }
    onVisibleChanged: if (visible && !loaded && !busy) hub.loadConfigs(false)
    Component.onCompleted: if (visible && !loaded && !busy) hub.loadConfigs(false)

    Timer { id: searchDebounce; interval: 180; onTriggered: page.refilter() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 14

        // ── heading ──
        ColumnLayout {
            Layout.fillWidth: true; spacing: 3
            Text {
                text: "Config center"
                color: Theme.text; font.family: Theme.font
                font.pixelSize: 20; font.weight: Font.DemiBold
            }
            Text {
                text: "Community EQ configs, selected by popularity — apply one to audition, then tune and save."
                color: Theme.faint; font.family: Theme.font; font.pixelSize: 13
            }
        }

        // ── controls: sort chips (left) · search + refresh (right) ──
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Repeater {
                model: page.sortTabs
                delegate: Rectangle {
                    required property var modelData
                    readonly property bool sel: page.sortBy === modelData.key
                    implicitHeight: 32
                    implicitWidth: chipTxt.implicitWidth + 26
                    radius: 16
                    color: sel ? Theme.a(Theme.accent, 0.16) : Theme.a(Theme.card2, 0.5)
                    border.width: 1
                    border.color: sel ? Theme.a(Theme.accent, 0.55) : Theme.line
                    Behavior on color { ColorAnimation { duration: 90 } }
                    Text {
                        id: chipTxt; anchors.centerIn: parent
                        text: modelData.label
                        color: sel ? Theme.accent : Theme.sub
                        font.family: Theme.font; font.pixelSize: 13
                        font.weight: sel ? Font.Medium : Font.Normal
                    }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: page.sortBy = modelData.key
                    }
                }
            }
            Item { Layout.fillWidth: true }
            // search
            Rectangle {
                Layout.preferredWidth: 260; implicitHeight: 34
                radius: 9
                color: Theme.a(Theme.card2, 0.55)
                border.width: 1
                border.color: searchInp.activeFocus ? Theme.accent : Theme.line
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 7
                    Sym { text: "search"; sz: 17; color: Theme.faint }
                    TextInput {
                        id: searchInp
                        Layout.fillWidth: true
                        color: Theme.text; font.family: Theme.font; font.pixelSize: 13
                        selectByMouse: true; clip: true
                        verticalAlignment: TextInput.AlignVCenter
                        onTextChanged: { page.query = text; searchDebounce.restart(); }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: parent.text === "" && !parent.activeFocus
                            text: "Search configs…"
                            color: Theme.faint; font.family: Theme.font; font.pixelSize: 13
                        }
                    }
                    Sym {
                        visible: searchInp.text !== ""
                        text: "close"; sz: 15; color: Theme.faint
                        MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                            onClicked: { searchInp.text = ""; } }
                    }
                }
            }
            HubBtn {
                label: "Refresh"; icon: "refresh"; kind: "secondary"
                enabled: !page.busy
                onClicked: hub.loadConfigs(true)
            }
        }

        // ── result count / state line ──
        Text {
            Layout.fillWidth: true
            text: page.busy ? "Loading the community library…"
                : !page.loaded ? ""
                : page.shown.length + (page.query ? " matching" : "") + " config"
                    + (page.shown.length === 1 ? "" : "s")
            color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
        }

        // ── card grid (+ loading / empty overlays) ──
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            color: "transparent"

            GridView {
                id: grid
                anchors.fill: parent
                clip: true
                model: page.shown
                cacheBuffer: 600
                readonly property int cols: Math.max(1, Math.floor(width / 400))
                cellWidth: width / cols
                cellHeight: 196
                C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded }

                delegate: Item {
                    width: grid.cellWidth
                    height: grid.cellHeight
                    required property var modelData

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 7
                        radius: 12
                        color: cardMa.containsMouse ? Theme.card : Theme.a(Theme.card, 0.7)
                        border.width: 1
                        border.color: cardMa.containsMouse ? Theme.line2 : Theme.line
                        Behavior on color { ColorAnimation { duration: 90 } }

                        MouseArea {
                            id: cardMa; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.openPreview(modelData)
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 8

                            // header: avatar + title/author
                            RowLayout {
                                Layout.fillWidth: true; spacing: 10
                                Rectangle {
                                    Layout.alignment: Qt.AlignTop
                                    width: 38; height: 38; radius: 19
                                    color: page.avatarColor(modelData.author)
                                    Text {
                                        anchors.centerIn: parent
                                        text: (modelData.author[0] || "?").toUpperCase()
                                        color: "#ffffff"; font.family: Theme.font
                                        font.pixelSize: 16; font.weight: Font.DemiBold
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.title
                                        color: Theme.text; font.family: Theme.font
                                        font.pixelSize: 14; font.weight: Font.DemiBold
                                        elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WordWrap
                                    }
                                    Text {
                                        text: "Created by " + modelData.author
                                        color: Theme.sub; font.family: Theme.font; font.pixelSize: 12
                                        elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                            }

                            // stats: stars + likes + downloads
                            RowLayout {
                                Layout.fillWidth: true; spacing: 12
                                // stars
                                RowLayout {
                                    spacing: 1
                                    visible: modelData.ratings > 0
                                    Repeater {
                                        model: 5
                                        delegate: Sym {
                                            required property int index
                                            text: "star"; sz: 14
                                            color: index < Math.round(modelData.rating) ? Theme.warn : Theme.line2
                                        }
                                    }
                                    Text {
                                        text: modelData.rating.toFixed(1)
                                        color: Theme.sub; font.family: Theme.font; font.pixelSize: 12
                                        leftPadding: 3
                                    }
                                }
                                Text {
                                    visible: modelData.ratings === 0
                                    text: "unrated"
                                    color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
                                }
                                Item { Layout.fillWidth: true }
                                Row {
                                    spacing: 4
                                    Sym { text: "thumb_up"; sz: 14; color: Theme.faint; anchors.verticalCenter: parent.verticalCenter }
                                    Text { text: page.fmt(modelData.likes); color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                                }
                                Row {
                                    spacing: 4
                                    Sym { text: "download"; sz: 14; color: Theme.faint; anchors.verticalCenter: parent.verticalCenter }
                                    Text { text: page.fmt(modelData.downloads); color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                                }
                            }

                            // description
                            Text {
                                Layout.fillWidth: true; Layout.fillHeight: true
                                text: modelData.desc || "No description."
                                color: modelData.desc ? Theme.sub : Theme.faint
                                font.family: Theme.font; font.pixelSize: 12
                                wrapMode: Text.WordWrap; elide: Text.ElideRight
                                lineHeight: 1.2
                            }

                            // footer: comments + Share + Apply
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Row {
                                    spacing: 5
                                    Sym { text: "chat_bubble_outline"; sz: 14; color: Theme.faint; anchors.verticalCenter: parent.verticalCenter }
                                    Text { text: "Comments (" + page.fmt(modelData.comments) + ")"; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
                                }
                                Item { Layout.fillWidth: true }
                                HubBtn {
                                    label: "Share"; icon: "content_copy"; kind: "secondary"; hpad: 11
                                    onClicked: hub.copyText(modelData.uuid)
                                }
                                HubBtn {
                                    label: "Apply"; icon: "check"; kind: "accent"; hpad: 13
                                    enabled: !page.busy
                                    onClicked: hub.applyConfig(modelData.uuid, modelData.title)
                                }
                            }
                        }
                    }
                }
            }

            // loading / empty overlay
            ColumnLayout {
                anchors.centerIn: parent
                spacing: 10
                visible: page.busy || (page.loaded && page.shown.length === 0)
                Sym {
                    Layout.alignment: Qt.AlignHCenter
                    text: page.busy ? "cloud_download" : (page.query ? "search_off" : "inbox")
                    sz: 40; color: Theme.faint
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: page.busy ? "Fetching community configs…"
                        : page.query ? "No configs match “" + page.query + "”."
                        : "No community configs found for this device."
                    color: Theme.sub; font.family: Theme.font; font.pixelSize: 13
                }
            }
        }
    }

    // ── preview popup (click a card → its response curve) ──
    Rectangle {
        id: previewScrim
        anchors.fill: parent
        visible: page.previewOpen
        color: Theme.a(Theme.bg, 0.72)
        z: 100
        // click outside the card closes
        MouseArea { anchors.fill: parent; onClicked: page.closePreview() }
        Shortcut { sequence: "Escape"; enabled: page.previewOpen; onActivated: page.closePreview() }

        Rectangle {
            id: pcard
            anchors.centerIn: parent
            width: Math.min(parent.width - 96, 780)
            height: Math.min(parent.height - 56, 600)
            radius: 16
            color: Theme.card
            border.width: 1; border.color: Theme.line2
            // swallow clicks so the scrim doesn't close when interacting with the card
            MouseArea { anchors.fill: parent }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 14

                // header: avatar + title/author + close
                RowLayout {
                    Layout.fillWidth: true; spacing: 12
                    Rectangle {
                        Layout.alignment: Qt.AlignTop
                        width: 42; height: 42; radius: 21
                        color: page.previewCard ? page.avatarColor(page.previewCard.author) : Theme.card2
                        Text {
                            anchors.centerIn: parent
                            text: page.previewCard ? (page.previewCard.author[0] || "?").toUpperCase() : ""
                            color: "#ffffff"; font.family: Theme.font; font.pixelSize: 18; font.weight: Font.DemiBold
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 2
                        Text {
                            Layout.fillWidth: true
                            text: page.previewCard ? page.previewCard.title : ""
                            color: Theme.text; font.family: Theme.font; font.pixelSize: 16; font.weight: Font.DemiBold
                            elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            text: page.previewCard ? "Created by " + page.previewCard.author : ""
                            color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; elide: Text.ElideRight
                        }
                    }
                    Rectangle {
                        Layout.alignment: Qt.AlignTop
                        width: 30; height: 30; radius: 8
                        color: closeMa.containsMouse ? Theme.card2 : "transparent"
                        border.width: 1; border.color: Theme.line
                        Sym { anchors.centerIn: parent; text: "close"; sz: 17; color: Theme.sub }
                        MouseArea { id: closeMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.closePreview() }
                    }
                }

                // stats row
                RowLayout {
                    Layout.fillWidth: true; spacing: 14
                    visible: !!page.previewCard
                    RowLayout {
                        spacing: 1
                        visible: page.previewCard && page.previewCard.ratings > 0
                        Repeater {
                            model: 5
                            delegate: Sym {
                                required property int index
                                text: "star"; sz: 15
                                color: (page.previewCard && index < Math.round(page.previewCard.rating)) ? Theme.warn : Theme.line2
                            }
                        }
                        Text { text: page.previewCard ? page.previewCard.rating.toFixed(1) : ""; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; leftPadding: 3 }
                    }
                    Text { visible: page.previewCard && page.previewCard.ratings === 0; text: "unrated"; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12 }
                    Item { Layout.fillWidth: true }
                    Row { spacing: 4
                        Sym { text: "thumb_up"; sz: 14; color: Theme.faint; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: page.previewCard ? page.fmt(page.previewCard.likes) : ""; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter } }
                    Row { spacing: 4
                        Sym { text: "download"; sz: 14; color: Theme.faint; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: page.previewCard ? page.fmt(page.previewCard.downloads) : ""; color: Theme.sub; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter } }
                    Row { spacing: 5
                        Sym { text: "chat_bubble_outline"; sz: 14; color: Theme.faint; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: page.previewCard ? page.fmt(page.previewCard.comments) : ""; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter } }
                }

                // graph preview
                Rectangle {
                    Layout.fillWidth: true; Layout.preferredHeight: 240
                    radius: 10; color: Theme.bg; border.width: 1; border.color: Theme.line
                    clip: true
                    ResponseGraph {
                        anchors.fill: parent; anchors.margins: 8
                        visible: page.previewBands.length > 0
                        bands: page.previewBands
                        pregain: page.previewPre
                        normalize: 60
                        showPre: false
                        selected: -1
                        interactive: false
                    }
                    ColumnLayout {
                        anchors.centerIn: parent; spacing: 8
                        visible: page.previewBands.length === 0
                        Sym { Layout.alignment: Qt.AlignHCenter; text: page.previewLoading ? "graphic_eq" : "error_outline"; sz: 30; color: Theme.faint }
                        Text { Layout.alignment: Qt.AlignHCenter; text: page.previewLoading ? "Loading preview…" : "Preview unavailable"; color: Theme.sub; font.family: Theme.font; font.pixelSize: 13 }
                    }
                }

                // description (scrolls if long)
                Flickable {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentHeight: descText.implicitHeight; clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    Text {
                        id: descText; width: parent.width
                        text: (page.previewCard && page.previewCard.desc) ? page.previewCard.desc : "No description provided."
                        color: (page.previewCard && page.previewCard.desc) ? Theme.sub : Theme.faint
                        font.family: Theme.font; font.pixelSize: 13; wrapMode: Text.WordWrap; lineHeight: 1.3
                    }
                }

                // footer
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Item { Layout.fillWidth: true }
                    HubBtn { label: "Share"; icon: "content_copy"; kind: "secondary"; onClicked: if (page.previewCard) hub.copyText(page.previewCard.uuid) }
                    HubBtn { label: "Close"; kind: "secondary"; onClicked: page.closePreview() }
                    HubBtn {
                        label: "Apply"; icon: "check"; kind: "accent"
                        enabled: !page.busy && page.previewBands.length > 0
                        onClicked: { if (page.previewCard) hub.applyConfig(page.previewCard.uuid, page.previewCard.title); page.closePreview(); }
                    }
                }
            }
        }
    }
}
