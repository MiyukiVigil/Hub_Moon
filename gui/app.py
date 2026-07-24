"""Entry point for the Hub Moon desktop GUI.

Run via ``python3 moondrop_control.py --gui`` (preferred) or ``python3 -m gui``.
Kept import-light at module load so ``--gui`` can lazy-import it without dragging
PySide6 into the plain CLI.
"""
from __future__ import annotations

import os
import signal
import sys


def _res_dir(sub):
    """Locate a bundled resource dir (``qml`` / ``assets``), whether the app runs
    from source, an installed wheel, or a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        # PyInstaller unpacks datas under _MEIPASS (onefile) or next to the exe
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
        return os.path.join(base, "gui", sub)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), sub)


def _qml_dir():
    return _res_dir("qml")


def _load_fonts():
    """Register the bundled UI + icon fonts so the app looks identical on every
    OS. Without this, machines that lack the Material Symbols font render icon
    ligatures as raw text (``usb_off``, ``check`` …) — the fonts are installed on
    the dev box but not on a fresh Windows/macOS/other-distro install."""
    from PySide6.QtGui import QFontDatabase

    fonts_dir = os.path.join(_res_dir("assets"), "fonts")
    if not os.path.isdir(fonts_dir):
        return
    for name in sorted(os.listdir(fonts_dir)):
        if name.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
            QFontDatabase.addApplicationFont(os.path.join(fonts_dir, name))


def main(argv=None):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication, QIcon
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow

    from .controller import Controller

    argv = list(sys.argv if argv is None else argv)
    # Ctrl-C in a terminal should kill the app, not be swallowed by the Qt loop.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QGuiApplication(argv)
    app.setApplicationName("Hub Moon")
    app.setOrganizationName("hub_moon")
    app.setApplicationDisplayName("Hub Moon")

    # register the bundled fonts before any QML loads, so the icon/UI families
    # resolve regardless of what's installed on the host
    _load_fonts()

    controller = Controller()

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("hub", controller)

    qml_dir = _qml_dir()
    engine.addImportPath(qml_dir)
    engine.load(QUrl.fromLocalFile(os.path.join(qml_dir, "Main.qml")))

    if not engine.rootObjects():
        print("Hub Moon: failed to load the QML UI.", file=sys.stderr)
        return 1

    # No auto-probe: the app opens on the connection screen (like the Hub web app)
    # and the "Start connecting" button drives the first device scan.

    def _cleanup():
        controller.stop()
    app.aboutToQuit.connect(_cleanup)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
