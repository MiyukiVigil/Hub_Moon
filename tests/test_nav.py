"""The screen stack.

These are cheap because the thing under test has no dependencies at all — which is the
point of pulling it out of `bridge.py`, where the same rules used to be spread over 44
assignments that no test could see.
"""
import pytest

from gui import nav


def test_nothing_is_open_to_begin_with():
    n = nav.Nav()
    assert n.top == nav.EDITOR
    assert n.depth == 0
    assert n.under == nav.EDITOR


def test_opening_a_screen_puts_it_on_top():
    n = nav.Nav()
    n.open(nav.SETTINGS)
    assert n.top == nav.SETTINGS
    assert n.at(nav.SETTINGS)


def test_only_the_top_counts_as_open():
    """A screen with another one over it is not the one you are looking at. Work that
    polls for the visible screen — thumbnails, curve classification — must not run for
    a list nobody can see."""
    n = nav.Nav()
    n.open(nav.LIBRARY)
    n.open(nav.SETTINGS)
    assert not n.at(nav.LIBRARY)
    assert nav.LIBRARY in n.trail


def test_back_returns_to_what_was_underneath():
    n = nav.Nav()
    n.open(nav.SETTINGS)
    n.open(nav.WHATSNEW)
    assert n.under == nav.SETTINGS
    assert n.back() == nav.WHATSNEW
    assert n.top == nav.SETTINGS


def test_back_off_the_bottom_lands_on_the_editor_and_stays_there():
    n = nav.Nav()
    n.open(nav.HELP)
    n.back()
    n.back()
    n.back()
    assert n.top == nav.EDITOR
    assert n.depth == 0


def test_revisiting_a_screen_goes_back_to_it_rather_than_stacking_another():
    """Settings → notes → Settings is one screen deep. Otherwise a user bouncing
    between two panels builds a stack they then have to press Escape out of once per
    bounce."""
    n = nav.Nav()
    n.open(nav.SETTINGS)
    n.open(nav.WHATSNEW)
    n.open(nav.SETTINGS)
    assert n.trail == (nav.SETTINGS,)
    assert n.top == nav.SETTINGS


def test_no_screen_can_appear_twice_however_hard_you_try():
    n = nav.Nav()
    for _ in range(20):
        for name in nav.SCREENS:
            n.open(name)
    assert len(set(n.trail)) == len(n.trail)
    assert n.depth <= len(nav.SCREENS)


def test_toggling_the_screen_you_are_on_closes_it():
    n = nav.Nav()
    assert n.toggle(nav.HELP) is True
    assert n.toggle(nav.HELP) is False
    assert n.top == nav.EDITOR


def test_toggling_a_screen_you_are_not_on_opens_it_over_the_one_you_are():
    """The rail is covered while a sheet is up, so this is reached from inside one:
    the guide's device-list button, say. It should not throw away where you were."""
    n = nav.Nav()
    n.toggle(nav.HELP)
    assert n.toggle(nav.DEVICES) is True
    assert n.top == nav.DEVICES
    assert n.under == nav.HELP


def test_reset_empties_the_stack():
    n = nav.Nav()
    n.open(nav.LIBRARY)
    n.open(nav.DEVICES)
    n.reset()
    assert n.top == nav.EDITOR
    assert n.trail == ()


def test_reset_to_a_screen_leaves_nothing_underneath_it():
    """An interrupt — the post-update panel — arrives over the whole window, not on
    top of a journey. One step back from it is the editor."""
    n = nav.Nav()
    n.open(nav.LIBRARY)
    n.reset(nav.WHATSNEW)
    assert n.trail == (nav.WHATSNEW,)
    assert n.under == nav.EDITOR


def test_a_screen_that_does_not_exist_is_a_mistake_not_a_no_op():
    """A typo used to mean a boolean that was never read and a panel that never
    opened. Here it stops the run."""
    n = nav.Nav()
    with pytest.raises(ValueError):
        n.open("libary")
    with pytest.raises(ValueError):
        n.toggle("")


def test_every_screen_name_is_unique():
    assert len(set(nav.SCREENS)) == len(nav.SCREENS)
    assert nav.EDITOR not in nav.SCREENS


def test_the_view_and_the_stack_agree_on_every_name():
    """`nav` crosses into Slint as a string, so a typo on either side is a screen that
    silently never opens — no exception, no failed binding, just a sheet that does not
    come up. Nothing else would catch it."""
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui = open(os.path.join(root, "gui", "ui", "app.slint")).read()
    drawn = set(re.findall(r'root\.nav == "([^"]*)"', ui))
    assert drawn == set(nav.SCREENS), sorted(drawn ^ set(nav.SCREENS))
