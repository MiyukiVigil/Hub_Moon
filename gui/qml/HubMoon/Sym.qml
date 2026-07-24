import QtQuick

// A Material Symbols glyph.
Text {
    property int sz: 18
    font.family: "Material Symbols Outlined"
    font.pixelSize: sz
    color: Theme.text
    verticalAlignment: Text.AlignVCenter
    renderType: Text.NativeRendering
}
