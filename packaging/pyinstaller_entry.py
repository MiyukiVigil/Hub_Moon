"""Frozen entry point for PyInstaller builds (the .exe / bundled binary).

PyInstaller freezes a script, not a console-script entry point, so this just
calls the GUI's main(). The plain `hub-moon`/`hub-moon-gui` commands come from
pyproject's entry points and are used by the source/pip/distro installs instead.
"""
import sys

from gui.app import main

if __name__ == "__main__":
    sys.exit(main())
