"""Every animation runs on the theme's timing, and can be turned off.

1.2.0 fixed seventeen animations that bypassed the theme with hand-written values, and
they came back the ordinary way: somebody adds an element, types a duration that looks
about right, and nothing anywhere complains. This is the thing that complains — it is a
grep, not a rendering test, because "the animations feel right" cannot be asserted and
"no animation carries its own number" can.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "gui", "ui", "app.slint")
THEME = os.path.join(ROOT, "gui", "ui", "theme.slint")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_no_animation_in_the_app_carries_its_own_duration():
    bad = re.findall(r"duration:\s*[0-9]", read(APP))
    assert not bad, "%d hand-written duration(s) in app.slint; use a Theme token" % len(bad)


def test_no_animation_in_the_app_carries_its_own_easing():
    """Two named curves, and which is which is a decision made once in the theme."""
    body = read(APP)
    assert "cubic-bezier" not in body, "raw cubic-bezier in app.slint; name it in theme.slint"
    # Read the value and check it, rather than asserting a negative lookahead: with
    # `\s*` in front, the lookahead simply backtracks to zero spaces and matches
    # everywhere, so the first version of this test passed on nothing at all.
    used = re.findall(r"easing:\s*([^;]+);", body)
    assert used, "no easings found at all — has the file moved?"
    stray = [e for e in used if not e.strip().startswith("Theme.")]
    assert not stray, "easings in app.slint that are not Theme tokens: %s" % stray


def test_every_duration_token_is_scaled_by_the_motion_setting():
    """A token that forgets the multiplier is an animation the Off setting cannot
    reach, which is worse than no setting at all — it would look like a bug."""
    theme = read(THEME)
    for name, value in re.findall(r"out property <duration> ([a-z-]+):\s*([^;]+);", theme):
        assert "Theme.motion" in value, "%s is not scaled by the motion setting" % name


def test_turning_motion_off_means_zero_and_not_merely_fast():
    from gui.bridge import MOTION_SCALES, MOTION_LABELS
    assert len(MOTION_SCALES) == len(MOTION_LABELS)
    assert MOTION_SCALES[0] == 1.0
    assert MOTION_SCALES[-1] == 0.0
