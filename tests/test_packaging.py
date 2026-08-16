"""What the shipped packages actually put on disk.

The frozen bundle and the wheel are two different products with two different entry
points, and until 1.2.0b4 they disagreed: the wheel declared `hub-moon` and
`hub-moon-gui`, and the packages built from the bundle shipped a GUI and no command
line at all. `hub-moon --list` is documented in the readme and in the Linux guide and
could not be run on any `.deb`, `.rpm`, Arch package, AppImage or tarball.
"""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def entry():
    path = os.path.join(ROOT, "packaging", "pyinstaller_entry.py")
    spec = importlib.util.spec_from_file_location("frozen_entry", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_arguments_opens_the_window(entry, monkeypatch):
    """What a desktop launcher, a double-click and a .dmg all do."""
    called = []
    monkeypatch.setattr(entry.sys, "argv", ["hub-moon"])
    import gui.app
    monkeypatch.setattr(gui.app, "main", lambda: called.append("gui") or 0)
    entry.main()
    assert called == ["gui"]


def test_arguments_hand_over_to_the_command_line(entry, monkeypatch):
    called = []
    monkeypatch.setattr(entry.sys, "argv", ["hub-moon", "--list"])
    import moondrop_control
    monkeypatch.setattr(moondrop_control, "main", lambda: called.append("cli") or 0)
    entry.main()
    assert called == ["cli"]


def test_the_packages_put_both_names_on_the_path():
    """Only `hub-moon-gui` was shipped, so a packaged install had no `hub-moon`."""
    with open(os.path.join(ROOT, "packaging", "nfpm.yaml"), encoding="utf-8") as fh:
        nfpm = fh.read()
    assert "dst: /usr/bin/hub-moon\n" in nfpm
    assert "dst: /usr/bin/hub-moon-gui\n" in nfpm


def test_the_launchers_are_scripts_rather_than_symlinks():
    """A symlink from /usr/bin into /opt breaks `python -m installer --destdir=…` on
    the same machine: it resolves the target against the *live* filesystem before
    writing, sees the link escape /usr/bin, and refuses — so the source package cannot
    be built on any machine with the binary package installed."""
    with open(os.path.join(ROOT, "packaging", "nfpm.yaml"), encoding="utf-8") as fh:
        nfpm = fh.read()
    assert "type: symlink" not in nfpm

    launcher = os.path.join(ROOT, "packaging", "hub-moon-launcher.sh")
    assert os.path.exists(launcher)
    with open(launcher, encoding="utf-8") as fh:
        body = fh.read()
    assert body.startswith("#!/bin/sh")
    # `exec` and `"$@"`, or the CLI arguments never reach the bundle and every
    # `hub-moon --list` silently opens a window instead.
    assert 'exec /opt/hub-moon/hub-moon "$@"' in body


def test_the_desktop_entry_launches_something_that_exists():
    with open(os.path.join(ROOT, "packaging", "hub-moon.desktop"), encoding="utf-8") as fh:
        entry = dict(ln.split("=", 1) for ln in fh.read().splitlines()
                     if "=" in ln and not ln.startswith("["))
    with open(os.path.join(ROOT, "packaging", "nfpm.yaml"), encoding="utf-8") as fh:
        nfpm = fh.read()
    assert "dst: /usr/bin/%s\n" % entry["Exec"] in nfpm
