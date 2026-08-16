"""The tuning guide's pages.

The figures are the point: they are drawn by the same response sampler the editor
plots with, so a page claiming "these are both +6 dB at 100 Hz" is checkable rather
than a caption somebody has to keep true by hand.
"""
import pytest

from gui import guide


@pytest.fixture(scope="module")
def pages():
    return guide.pages()


def test_every_page_has_a_heading_a_lede_and_a_body(pages):
    assert len(pages) >= 6
    for p in pages:
        assert p["head"] and p["head"].isupper(), p["key"]
        assert p["lede"].endswith((".", "?")), p["key"]
        assert len(p["body"]) > 120, p["key"]


def test_a_page_with_a_figure_has_a_zero_line_and_a_legend(pages):
    """A trace with nothing to measure it against says nothing — the reader cannot
    tell a boost from a cut without knowing where 0 dB is."""
    for p in pages:
        if not p["fig-a"]:
            continue
        assert p["fig-zero"], p["key"]
        for slot, key in (("fig-a", "key-a"), ("fig-b", "key-b"), ("fig-c", "key-c")):
            assert bool(p[slot]) == bool(p[key]), "%s: %s" % (p["key"], slot)


def test_figures_are_path_commands_within_the_figure_box(pages):
    """Drawn into a fixed viewbox, so a point outside it is a trace that leaves the
    frame — which is what a wrong `width=` would look like and nothing else would."""
    import re
    for p in pages:
        for slot in ("fig-a", "fig-b", "fig-c"):
            d = p[slot]
            if not d:
                continue
            assert d[0] == "M", p["key"]
            nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", d)]
            xs, ys = nums[0::2], nums[1::2]
            assert min(xs) >= -0.5 and max(xs) <= guide.FIG_W + 0.5, p["key"]
            assert min(ys) >= -5.0 and max(ys) <= guide.FIG_H + 5.0, p["key"]


def test_the_cut_page_really_does_show_the_cheaper_curve(pages):
    """The whole claim of that page is that one shape costs headroom and the other
    does not. If the two band sets ever drift, the figure argues the opposite."""
    from gui import curve
    boost = curve.peak_db(guide._LOUD_MID)
    cuts = curve.peak_db(guide._CUT_AROUND)
    assert boost > 5.0, "the boost example no longer needs headroom"
    assert cuts <= 0.05, "the cut example now needs headroom too"


def test_the_q_page_compares_the_same_gain_at_the_same_frequency(pages):
    """Three different Q values, one variable. Change the gain or the centre and the
    figure stops being about Q at all."""
    page = next(p for p in guide._PAGES if p["key"] == "q")
    bands = [b[0][0] for b in page["figs"]]
    assert len({b["frequency"] for b in bands}) == 1
    assert len({b["gain"] for b in bands}) == 1
    assert len({b["q"] for b in bands}) == len(bands)


# ── palettes ─────────────────────────────────────────────────────────────────

def test_every_palette_defines_both_halves():
    """A skin missing a dark value renders as transparent black on that surface —
    invisible in exactly the mode most people run this in."""
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "gui", "ui", "theme.slint"), encoding="utf-8") as fh:
        body = fh.read()
    fields = re.search(r"export struct Skin \{(.*?)\}", body, re.S).group(1)
    want = {f.strip() for f in re.findall(r"([a-z0-9-]+)\s*:", fields)} - {"name", "note"}

    table = body.split("out property <[Skin]> all:", 1)[1].split("];", 1)[0]
    # No nested braces inside an entry, and comments sit between them — so match the
    # brace pairs directly rather than trying to describe what separates them.
    entries = [e for e in re.findall(r"\{[^{}]*\}", table, re.S) if "name:" in e]
    assert len(entries) >= 4, "expected at least four palettes"
    for e in entries:
        name = re.search(r'name:\s*"([^"]+)"', e).group(1)
        have = {f for f in re.findall(r"([a-z0-9-]+)\s*:", e)}
        missing = want - have
        assert not missing, "%s is missing %s" % (name, sorted(missing))
        assert re.search(r'note:\s*"[^"]+"', e), "%s has no note" % name



# ── the Wayland app id ───────────────────────────────────────────────────────

def test_the_app_id_matches_the_desktop_entry():
    """On Wayland the app id is the key to everything outside the window: window
    rules, the taskbar icon, session grouping. It has to equal the basename of the
    installed .desktop file or the icon lookup misses — and a Hyprland rule matching
    a class the window does not have silently does nothing, which looks exactly like
    a rule that was typed wrong.
    """
    import os
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    with open(os.path.join(root, "gui", "app.py"), encoding="utf-8") as fh:
        app_id = re.search(r'^APP_ID = "([^"]+)"', fh.read(), re.M).group(1)

    desktop = os.path.join(root, "packaging", "hub-moon.desktop")
    assert app_id == os.path.basename(desktop)[: -len(".desktop")]

    with open(desktop, encoding="utf-8") as fh:
        entry = dict(
            ln.split("=", 1) for ln in fh.read().splitlines() if "=" in ln and not ln.startswith("["))
    # The icon name is looked up in hicolor by this exact string.
    assert entry["Icon"] == app_id
    assert os.path.exists(os.path.join(root, "packaging", "hub-moon.svg"))


def test_the_app_actually_sets_it():
    """A constant nothing calls is worth nothing."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "gui", "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "set_xdg_app_id(APP_ID)" in src
