# PyInstaller spec — builds a self-contained Hub Moon bundle (PySide6 + QML in).
#
#   pip install pyinstaller
#   pyinstaller packaging/hub-moon.spec            # → dist/hub-moon/  (onedir)
#
# Run this ON THE TARGET OS: PyInstaller is not a cross-compiler. Build the
# Windows .exe on Windows, the macOS .app on macOS, the Linux binary on Linux.
# On Windows the result is dist/hub-moon/hub-moon.exe (windowed, no console).
#
# collect_all('PySide6') bundles the whole Qt runtime — big (~200 MB) but
# reliable; it guarantees the QtQuick/QtQml plugins the UI needs are present.
import os
from PyInstaller.utils.hooks import collect_all

# paths are resolved relative to this spec file, so `pyinstaller` can run from anywhere
here = os.path.abspath(SPECPATH)
repo = os.path.dirname(here)

datas = [(os.path.join(repo, "gui", "qml"), "gui/qml")]   # QML tree → <bundle>/gui/qml
binaries = []
hiddenimports = ["hid"]                                     # hidapi's compiled module

_d, _b, _h = collect_all("PySide6")
datas += _d
binaries += _b
hiddenimports += _h

# a Windows .ico gives the exe/taskbar a real icon; optional elsewhere
_ico = os.path.join(here, "hub-moon.ico")
icon = _ico if os.path.exists(_ico) else None

a = Analysis(
    [os.path.join(here, "pyinstaller_entry.py")],
    pathex=[repo],                            # finds moondrop_control + gui
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="hub-moon",
    console=False,                            # windowed app (no terminal)
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="hub-moon")
