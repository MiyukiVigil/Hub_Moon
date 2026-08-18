"""The library's lists only build the rows you can see, and that rests on two numbers
agreeing with the interface around them.

`LazyRows` positions each slot at `i * pitch` and computes the scroll extent from
`count * pitch`, so the pitch has to be the card's real height plus the real gap. Get it
wrong and the list either overlaps its own rows or cannot be scrolled to the bottom —
neither of which any behavioural test would notice, because the rows are all still
there and all still correct.

So these read the interface file. It is the only place the numbers live.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = open(os.path.join(ROOT, "gui", "ui", "app.slint")).read()

# (the LazyRows id, the card it draws, the gap the old VerticalLayout used)
LISTS = [
    ("cprof", "ProfileCard", 7),
    ("chist", "HistoryCard", 6),
    ("chub", "HubCard", 7),
    ("caeq", "AeqCard", 7),
]


def card_height(name):
    """The `height:` on a card component, in px."""
    i = UI.index("component %s inherits" % name)
    m = re.search(r"^    height: (\d+)px;", UI[i:i + 3000], re.M)
    assert m, "%s has no fixed height" % name
    return int(m.group(1))


def block(list_id):
    """The LazyRows instantiation with this id."""
    i = UI.index("%s := LazyRows {" % list_id)
    return UI[i:UI.index("\n            }", i)]


@pytest.mark.parametrize("list_id,card,gap", LISTS)
def test_the_pitch_is_the_card_plus_the_gap(list_id, card, gap):
    b = block(list_id)
    pitch = int(re.search(r"pitch: (\d+)px;", b).group(1))
    row_h = int(re.search(r"row-height: (\d+)px;", b).group(1))
    h = card_height(card)
    assert row_h == h, "%s says row-height %d, %s is %d" % (list_id, row_h, card, h)
    assert pitch == h + gap, "%s pitch %d should be %d + %d" % (list_id, pitch, h, gap)


@pytest.mark.parametrize("list_id,card,gap", LISTS)
def test_every_slot_is_placed_at_its_own_pitch(list_id, card, gap):
    """The `y` on the slot and the `pitch` on the list are the same number written
    twice — Slint cannot bind `y` to a property of the enclosing element from inside a
    repeater, so this is the check that they stay equal."""
    b = block(list_id)
    pitch = int(re.search(r"pitch: (\d+)px;", b).group(1))
    ys = re.findall(r"y: i \* (\d+)px;", b)
    assert ys, "%s places no slot by index" % list_id
    assert all(int(y) == pitch for y in ys), "%s: y uses %s, pitch is %d" % (
        list_id, ys, pitch)


@pytest.mark.parametrize("list_id,card,gap", LISTS)
def test_the_row_count_comes_from_the_model(list_id, card, gap):
    """Not from a count pushed alongside it. The scroll extent is `count * pitch`, so
    the two disagreeing is a list you cannot reach the bottom of."""
    b = block(list_id)
    count = re.search(r"count: ([\w.\-]+);", b).group(1)
    assert count.endswith(".length"), "%s takes its count from %s" % (list_id, count)


@pytest.mark.parametrize("list_id,card,gap", LISTS)
def test_the_card_is_behind_the_visibility_gate(list_id, card, gap):
    """The whole point. A card built unconditionally costs its layout on every frame
    whether or not anyone can see it — 400 of them halved the frame rate."""
    b = block(list_id)
    assert "if %s.shown(i): %s {" % (list_id, card) in b, list_id


def test_no_list_in_the_library_was_left_unwindowed():
    """A fifth tab added later must not quietly go back to building everything."""
    i = UI.index("component LibrarySheet inherits")
    body = UI[i:UI.index("\n}\n", i)]
    # Every repeater in the sheet is either a LazyRows slot or one of the small fixed
    # strips (tabs, chips) that have a handful of entries by construction.
    for m in re.finditer(r"for (\w+)\[?\w*\]? in ([\w.\-]+):", body):
        model = m.group(2)
        assert model in ("root.profiles", "root.history", "root.hub-rows",
                         "root.aeq-rows", "root.sorts"), (
            "unrecognised list %s — window it or add it here" % model)
