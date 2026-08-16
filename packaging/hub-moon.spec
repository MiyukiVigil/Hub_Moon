# PyInstaller spec — builds a self-contained Hub Moon bundle (slint in).
#
#   pip install pyinstaller
#   pyinstaller packaging/hub-moon.spec            # → dist/hub-moon/  (onedir)
#
# Run this ON THE TARGET OS: PyInstaller is not a cross-compiler. Build the
# Windows .exe on Windows, the macOS .app on macOS, the Linux binary on Linux.
# On Windows the result is dist/hub-moon/hub-moon.exe (windowed, no console).
#
# collect_all('slint') bundles the toolkit's compiled extension and its data. Far
# smaller than the Qt runtime this replaced, and there are no plugins to chase — the
# .slint sources above are compiled at startup and the icons are vectors in them.
import os
import re
import sys
from PyInstaller.utils.hooks import collect_all

# paths are resolved relative to this spec file, so `pyinstaller` can run from anywhere
here = os.path.abspath(SPECPATH)
repo = os.path.dirname(here)


def _version():
    """moondrop_control.__version__, read rather than imported.

    Importing it here would need hidapi installed on the build machine, and the
    alternative — a copy of the number in this file — is what let the macOS bundle
    keep announcing 0.2.0 for the whole of 1.0.0.
    """
    src = open(os.path.join(repo, "moondrop_control.py"), encoding="utf-8").read()
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', src, re.M)
    if not m:
        raise SystemExit("hub-moon.spec: no __version__ in moondrop_control.py")
    return m.group(1)


version = _version()

# The whole gui/ui directory: the .slint sources, which are compiled at startup, and
# hub-moon.png, which app.slint loads by @image-url as the window icon.
datas = [
    (os.path.join(repo, "gui", "ui"), "gui/ui"),
]
binaries = []
# hidapi ships `hid` as a compiled extension, so PyInstaller's binary analysis follows
# it to the native library on its own (libhidapi-hidraw.so.0 / hidapi.dll). It is
# named here anyway because nothing *imports* it by name until runtime.
#
# collect_dynamic_libs("hid") is deliberately NOT used: `hid` is a bare extension
# module rather than a package, so that call collects nothing and only adds a warning
# to the build log. What actually protects this path is moondrop_control refusing to
# die silently when the import fails — see the ImportError handler there.
hiddenimports = ["hid"]

_d, _b, _h = collect_all("slint")
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
            # Read from moondrop_control.__version__ above. Hardcoded through 1.0.0,
            # where it had drifted to 0.2.0 — Get Info and the About panel both
            # reported a version two releases old.
            "CFBundleShortVersionString": version,
            "CFBundleVersion": version,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # a GUI-only agent still shows in the Dock; keep it a normal app
            "LSApplicationCategoryType": "public.app-category.music",
        },
    )
