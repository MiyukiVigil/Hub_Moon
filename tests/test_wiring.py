"""Every callback the window declares has something on the Python side answering it.

A `callback` with no assignment is not an error in Slint and not an error in Python: the
button is built, it is clickable, it highlights on hover, and it does nothing. Nothing
else in this suite would notice — `test_every_property_push_writes_exists_on_the_window`
covers the traffic in the other direction only.
"""
import os
import re

import pytest

slint = pytest.importorskip("slint")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = open(os.path.join(ROOT, "gui", "ui", "app.slint")).read()
BRIDGE = open(os.path.join(ROOT, "gui", "bridge.py")).read()


def declared():
    """The callbacks on MainWindow itself, not on the components inside it."""
    i = UI.index("export component MainWindow inherits Window {")
    body = UI[i:]
    out = []
    for m in re.finditer(r"^    callback ([a-z][\w-]*)\s*\(", body, re.M):
        out.append(m.group(1))
    return sorted(set(out))


def test_the_window_declares_callbacks_at_all():
    """If this ever returns nothing the parametrised test below passes vacuously."""
    assert len(declared()) > 20


@pytest.mark.parametrize("name", declared())
def test_every_declared_callback_is_assigned(name):
    py = name.replace("-", "_")
    assert re.search(r"^\s*window\.%s\s*=" % re.escape(py), BRIDGE, re.M), (
        "the view declares %s(); nothing in bridge.py assigns window.%s" % (name, py))
