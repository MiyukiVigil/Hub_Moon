"""Frozen entry point for PyInstaller builds (the .exe and the bundled binary).

PyInstaller freezes a *script*, not a console-script entry point, so the two names
pyproject declares — `hub-moon` and `hub-moon-gui` — do not exist in a frozen build.
Until 1.2.0b4 this called `gui.app.main()` and nothing else, which meant the packages
built from it shipped a GUI and no command line at all: `hub-moon --list`, documented
in the readme and in the Linux guide, was unreachable on every `.deb`, `.rpm`, Arch
package, AppImage and tarball this project has published.

So it dispatches. With no arguments it opens the window, which is what a desktop
launcher, a double-click and a `.dmg` all do. With arguments it hands over to the
CLI's own `main()`, which already knows how to open the GUI itself for `--gui`.

One caveat worth stating: the Windows bundle is built windowed, so it has no console
attached and anything the CLI prints goes nowhere. That is not made worse by this —
there was no CLI to print at all before — but it does mean `hub-moon --list` is a
Linux and macOS affordance in a frozen build. On Windows the CLI comes from `pip`.
"""
import sys


def main():
    if len(sys.argv) > 1:
        import moondrop_control
        return moondrop_control.main()
    from gui.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
