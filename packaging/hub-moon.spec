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
import sys
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

# per-OS icon: Windows wants .ico, macOS wants .icns (both shipped, generated
# from hub-moon.svg); Linux ignores this and uses the .desktop's Icon= instead.
if sys.platform == "darwin":
    _icon_path = os.path.join(here, "hub-moon.icns")
else:
    _icon_path = os.path.join(here, "hub-moon.ico")
icon = _icon_path if os.path.exists(_icon_path) else None

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

# macOS: wrap the onedir into a real .app so it can be .dmg'd, dropped into
# /Applications and shown in Launchpad. (No-op on Windows/Linux.)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Hub Moon.app",
        icon=icon,
        bundle_identifier="tech.miyukivigil.hubmoon",
        info_plist={
            "CFBundleName": "Hub Moon",
            "CFBundleDisplayName": "Hub Moon",
            "CFBundleShortVersionString": "0.2.0",
            "CFBundleVersion": "0.2.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # a GUI-only agent still shows in the Dock; keep it a normal app
            "LSApplicationCategoryType": "public.app-category.music",
        },
    )
