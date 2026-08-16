"""The bridge against the real window.

Skipped without slint, and run in CI by the job that installs it. Everything in here
is a bug class the unit tests structurally cannot catch: they never construct a
Bridge, so a property the .slint does not declare, a callback bound to a name that
does not exist, or a field `push()` reads but `__init__` never set is invisible to
them and fatal on launch. Each has happened.
"""
import gc
import os

import pytest

# A plain try/except rather than `pytest.importorskip`: that helper treats a module
# which imports *and then* raises ImportError as a hard error from pytest 9.1, and
# silencing it needs an `exc_type=` argument older pytests in the version matrix do
# not accept. This means the same thing on every version.
try:
    import slint
except ImportError:                     # the GUI extra is not installed
    pytest.skip("slint is not installed", allow_module_level=True)

from gui.bridge import Bridge          # noqa: E402 — must not import before the skip
import moondrop_control as mc          # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


OFFER = {
    "version": "9.9.9", "rollback": False, "notes": ["first note", "second note"],
    "channel": "beta", "date": "2026-01-01", "summary": "A summary line.",
    "notes_url": "https://example.invalid/notes", "install_kind": "appimage",
    "can_install": True, "can_fetch": False, "can_elevate": False,
    "asset": {"name": "x.AppImage"}, "hint": "", "reason": "",
}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A Bridge on a real window, writing to a throwaway config directory.

    The `gc.disable()` is not tidiness — without it this file aborts the interpreter
    partway through, and the message names a worker thread rather than anything in the
    test. A Bridge starts five real worker threads, and every row handed to Slint is a
    `PyStruct` that pyo3 marks unsendable: it records the thread that made it and
    panics on access from any other. The cyclic collector runs on whichever thread
    trips the allocation threshold, so a worker allocating anything can start a
    collection that walks a UI-thread struct, and the process dies.

    `gui/app.py` solves this for the real app by taking the collector off automatic and
    running it from a timer on the event loop. There is no event loop here, so the
    tests take the first half of that and leave collection to interpreter shutdown.
    """
    monkeypatch.setattr(mc, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(mc, "cache_dir", lambda: str(tmp_path / "cache"))
    was_enabled = gc.isenabled()
    gc.disable()
    window = slint.load_file("gui/ui/app.slint").MainWindow()
    b = Bridge(window)
    try:
        yield b
    finally:
        b.stop()
        if was_enabled:
            gc.enable()


def test_every_property_push_writes_exists_on_the_window(bridge):
    """`push()` setting a name the .slint never declared raises. The app then dies on
    its first update rather than at startup, which is how `self.update_command` got as
    far as a running build."""
    bridge.push()


def test_a_check_the_user_clicked_shows_what_is_in_the_release(bridge):
    bridge.check_update(True)
    bridge._handle("update_result", OFFER)
    assert bridge.whatsnew_open
    assert bridge.whatsnew_preview
    assert bridge.whatsnew_version == "9.9.9"
    assert bridge.whatsnew_from == mc.__version__
    assert bridge.whatsnew_notes == ["first note", "second note"]


def test_a_background_check_does_not_throw_a_panel_over_the_app(bridge):
    """The startup check finding something must not interrupt what somebody is doing.
    It is reported in Settings, where they can go and look."""
    bridge.check_update(False)
    bridge._handle("update_result", OFFER)
    assert bridge.update_state == "available"
    assert not bridge.whatsnew_open


def test_a_rollback_offer_does_not_call_itself_new(bridge):
    """Going back to stable offers an *older* release. "What's new in 1.1.0" over a
    1.2.0b1 would list changes that are already installed."""
    bridge.check_update(True)
    bridge._handle("update_result", dict(OFFER, version="0.9.0", rollback=True))
    assert bridge.update_state == "available"
    assert not bridge.whatsnew_open


def test_asking_twice_does_not_leave_the_flag_armed(bridge):
    """A click that finds nothing must not make the *next* background check pop the
    panel open."""
    bridge.check_update(True)
    bridge._handle("update_result", None)
    assert not bridge.update_asked
    bridge._handle("update_result", OFFER)
    assert not bridge.whatsnew_open


def test_dismissing_a_preview_goes_back_to_settings(bridge):
    bridge.update_found = OFFER
    bridge.preview_whatsnew()
    assert not bridge.settings_open          # the panel replaced it
    bridge.dismiss_whatsnew()
    assert bridge.settings_open
    assert not bridge.whatsnew_preview


def test_dismissing_the_post_update_panel_does_not_open_settings(bridge):
    """The one shown once after an update is not something anybody navigated to."""
    bridge.show_whatsnew()
    assert not bridge.whatsnew_preview
    bridge.dismiss_whatsnew()
    assert not bridge.settings_open


def test_the_running_version_has_notes_with_no_manifest_at_all(bridge):
    """The beta case, end to end: nothing has ever been downloaded, and the panel
    still says what is in the build."""
    bridge.show_whatsnew()
    assert bridge.whatsnew_version == mc.__version__
    assert bridge.whatsnew_notes, "What's New would open empty on a repo build"


# ── A/B compare ──────────────────────────────────────────────────────────────

def _catch_writes(bridge, monkeypatch):
    sent = []
    monkeypatch.setattr(bridge.dev, "submit",
                        lambda fn, *a: sent.append((fn.__name__, a)))
    return sent


def test_compare_writes_flat_and_puts_it_back(bridge, monkeypatch):
    bridge.connected = True
    bridge.pregain = -4.5
    sent = _catch_writes(bridge, monkeypatch)

    bridge.compare_hold(True)
    bridge.compare_hold(False)

    assert [n for n, _ in sent] == ["audition", "audition"]
    off, on = sent[0][1], sent[1][1]
    assert {b["type"] for b in off[0]} == {"disabled"}
    assert "disabled" not in {b["type"] for b in on[0]}
    # Level-matched: comparing a curve against a signal 4.5 dB quieter is not a
    # comparison, it is a demonstration that louder sounds better.
    assert off[1] == 0.0
    assert on[1] == -4.5


def test_compare_never_becomes_the_state(bridge, monkeypatch):
    """The flat set is written to the DAC and must never be adopted back. If it were,
    letting go would restore flat onto flat and the curve would be gone."""
    bridge.connected = True
    before = [dict(b) for b in bridge.bands]
    _catch_writes(bridge, monkeypatch)
    bridge.compare_hold(True)
    assert [dict(b) for b in bridge.bands] == before
    assert bridge.dirty is False
    assert bridge._history == []


def test_compare_with_no_dac_says_so_rather_than_lighting_up(bridge):
    bridge.connected = False
    bridge.compare_hold(True)
    assert bridge.comparing is False


def test_holding_twice_does_not_write_twice(bridge, monkeypatch):
    bridge.connected = True
    sent = _catch_writes(bridge, monkeypatch)
    bridge.compare_hold(True)
    bridge.compare_hold(True)
    assert len(sent) == 1


# ── per-band bypass ──────────────────────────────────────────────────────────

def test_a_bypassed_band_round_trips_through_its_old_type(bridge):
    b = bridge._band(0)
    was = b["type"]
    assert was != "disabled"
    bridge.toggle_band(0)
    assert b["type"] == "disabled"
    bridge.toggle_band(0)
    assert b["type"] == was


def test_bypass_is_written_as_the_firmwares_own_passthrough(bridge, monkeypatch):
    """`disabled` is a real filter type the DAC accepts and treats as unity — so this
    is genuinely the curve without that band, not the band flattened to 0 dB."""
    import moondrop_control as mc
    assert "disabled" in mc.FILTER_TYPES
    num, den = mc.calculate_biquad(1000.0, 6.0, 1.0, "disabled")
    assert num == [0.0, 0.0, 0.0] and den == [1.0, 0.0, 0.0]


def test_bypass_is_undoable(bridge):
    was = bridge._band(1)["type"]
    bridge.toggle_band(1)
    assert bridge._band(1)["type"] == "disabled"
    bridge.undo()
    assert bridge._band(1)["type"] == was


# ── undo ─────────────────────────────────────────────────────────────────────

def test_undo_restores_the_curve_a_preset_replaced(bridge):
    before = [dict(b) for b in bridge.bands]
    bridge.apply_preset(1)
    assert [dict(b) for b in bridge.bands] != before
    bridge.undo()
    assert [dict(b) for b in bridge.bands] == before


def test_undo_walks_back_more_than_one_step(bridge):
    first = [dict(b) for b in bridge.bands]
    bridge.apply_preset(1)
    second = [dict(b) for b in bridge.bands]
    bridge.apply_preset(2)
    bridge.undo()
    assert [dict(b) for b in bridge.bands] == second
    bridge.undo()
    assert [dict(b) for b in bridge.bands] == first


def test_undo_with_nothing_to_undo_does_not_raise(bridge):
    assert bridge._history == []
    bridge.undo()


def test_the_history_is_bounded(bridge):
    for _ in range(bridge.HISTORY_MAX * 2):
        bridge.apply_preset(1)
        bridge.apply_preset(2)
    assert len(bridge._history) <= bridge.HISTORY_MAX


def test_undo_never_appears_to_do_nothing(bridge):
    """Applying the same preset twice records a step that does not move the curve.
    One press of undo has to get past it — a button that visibly does nothing is
    worse than no button."""
    before = [dict(b) for b in bridge.bands]
    bridge.apply_preset(1)
    bridge.apply_preset(1)
    bridge.undo()
    assert [dict(b) for b in bridge.bands] == before


# ── keyboard editing ─────────────────────────────────────────────────────────

def test_gain_steps_are_fine_by_default_and_coarse_with_shift(bridge):
    was = bridge._band(2)["gain"]
    bridge.step_gain(2, 1)
    assert bridge._band(2)["gain"] == pytest.approx(was + 0.5)
    bridge.step_gain(2, -1, coarse=True)
    assert bridge._band(2)["gain"] == pytest.approx(was - 1.5)


def test_stepping_gain_selects_the_band_it_moved(bridge):
    bridge.selected = -1
    bridge.step_gain(3, 1)
    assert bridge.selected == 3


def test_reset_flattens_the_band_and_keeps_its_shape(bridge):
    b = bridge._band(0)
    ftype, freq, q = b["type"], b["frequency"], b["q"]
    bridge.reset_band(0)
    assert b["gain"] == 0.0
    assert (b["type"], b["frequency"], b["q"]) == (ftype, freq, q)


def test_reset_is_undoable(bridge):
    was = bridge._band(0)["gain"]
    assert was != 0.0
    bridge.reset_band(0)
    bridge.undo()
    assert bridge._band(0)["gain"] == was


def test_a_step_on_no_band_is_not_an_error(bridge):
    bridge.step_gain(-1, 1)
    bridge.reset_band(99)


# ── solo ─────────────────────────────────────────────────────────────────────

def _off(bridge):
    return sorted(b["index"] for b in bridge.bands if b["type"] == "disabled")


def test_solo_leaves_one_band_playing(bridge):
    bridge.solo_band(3)
    assert 3 not in _off(bridge)
    assert len(_off(bridge)) == len(bridge.bands) - 1


def test_soloing_another_band_moves_the_solo(bridge):
    """Rather than making you turn the first one off first."""
    bridge.solo_band(3)
    bridge.solo_band(5)
    assert 5 not in _off(bridge)
    assert 3 in _off(bridge)


def test_ending_a_solo_restores_everything_it_turned_off(bridge):
    before = [b["type"] for b in bridge.bands]
    bridge.solo_band(3)
    bridge.solo_band(3)
    assert [b["type"] for b in bridge.bands] == before


def test_a_solo_does_not_unmute_what_you_muted_by_hand(bridge):
    """Solo and mute keep separate memories, so ending a solo puts back exactly what
    solo turned off and nothing else."""
    bridge.toggle_band(0)
    bridge.solo_band(3)
    bridge.solo_band(3)
    assert _off(bridge) == [0]


def test_soloing_a_muted_band_turns_it_on(bridge):
    """Asking to hear only this one, when this one is off, means turning it on."""
    bridge.toggle_band(4)
    bridge.solo_band(4)
    assert 4 not in _off(bridge)


# ── the supported-device list ────────────────────────────────────────────────

def test_the_device_list_comes_from_the_registry_not_a_second_copy(bridge):
    """A device added to moondrop_control must appear here without anyone
    remembering to update a list. The readme's table has drifted before."""
    from gui import devices
    assert len(devices.rows()) == len(mc.SUPPORTED_DEVICES)
    names = {r["name"] for r in devices.rows()}
    assert names == set(mc.SUPPORTED_DEVICES.values())


def test_the_connected_device_is_marked_and_sorted_first(bridge):
    from gui import devices
    pid = next(iter(mc.SUPPORTED_DEVICES))
    rows = devices.rows(pid)
    assert rows[0]["here"] is True
    assert rows[0]["pid"] == "0x%04X" % pid
    assert sum(1 for r in rows if r["here"]) == 1


def test_pregain_support_is_read_from_the_registry(bridge):
    from gui import devices
    for r in devices.rows():
        pid = int(r["pid"], 16)
        assert r["pregain"] == (pid not in mc.NO_PREGAIN_DEVICES), r["name"]


def test_only_hardware_that_was_actually_tried_is_marked_verified(bridge):
    """Claiming support that has not been earned is how a bug report becomes
    'it just does nothing'."""
    from gui import devices
    verified = {r["name"] for r in devices.rows() if r["tested"]}
    assert verified == {"DAWN PRO2"}


# ── the walkthrough ──────────────────────────────────────────────────────────

def test_the_tour_runs_through_every_step_and_stops(bridge):
    bridge.start_tour()
    seen = []
    while bridge.tour_step >= 0:
        seen.append(bridge.tour_step)
        bridge.tour_next()
    assert seen == list(range(len(bridge.TOUR)))


def test_advancing_a_finished_tour_does_not_restart_it(bridge):
    """The overlay is gone by then, so this would run the whole thing with nothing
    on screen to click and no way to stop it."""
    bridge.start_tour()
    for _ in range(len(bridge.TOUR) + 4):
        bridge.tour_next()
    assert bridge.tour_step == -1


def test_the_tour_closes_whatever_was_over_the_window(bridge):
    """It highlights the controls underneath; a sheet on top of them would be
    pointing at something nobody can see."""
    bridge.settings_open = True
    bridge.dev_open = True
    bridge.start_tour()
    assert not bridge.settings_open and not bridge.dev_open
    assert not bridge.welcome_open


def test_finishing_the_tour_counts_as_being_welcomed(bridge):
    bridge.settings["seen_welcome"] = False
    bridge.start_tour()
    bridge.tour_end()
    assert bridge.settings["seen_welcome"] is True


def test_every_tour_step_says_something(bridge):
    for head, body in bridge.TOUR:
        assert head and not head.isupper()
        assert len(body) > 80


# ── landing without a DAC ────────────────────────────────────────────────────

def test_no_dac_lands_on_the_home_screen(bridge):
    """Rather than an editor that is not editing anything, with the only explanation
    in a toast that scrolls away."""
    bridge.welcome_open = False
    bridge._greeted = False
    bridge._handle("no_device", "Could not open the DAC.")
    assert bridge.welcome_open is True
    assert bridge.connected is False


def test_a_later_failed_rescan_leaves_you_where_you_are(bridge):
    """Somebody deliberately playing with the demo should not be thrown back to the
    home screen every time they press reload."""
    bridge.welcome_open = False
    bridge._greeted = False
    bridge._handle("no_device", "first")
    bridge.welcome_open = False
    bridge._handle("no_device", "second")
    assert bridge.welcome_open is False


def test_the_demo_curve_never_becomes_a_state_revert_can_write(bridge):
    bridge._handle("no_device", "x")
    assert bridge.pristine is None


# ── the graph actually redraws ───────────────────────────────────────────────
#
# The traces are cached on a signature of what they are a function of, which is what
# took an idle push from 9.7 ms to 1.6 ms. The failure mode of that cache is a graph
# that silently stops matching the numbers under it — invisible to every other test
# here, because they check state rather than what was drawn.

@pytest.fixture
def plotted(bridge):
    bridge.on_plot_resized(900, 520)
    return bridge


def _paths(bridge):
    w = bridge.win
    return w.curve_path, w.flat_path, w.pregain_path


def test_a_band_edit_redraws_both_editor_traces(plotted):
    before = _paths(plotted)
    plotted.set_band_gain(3, -8.0)
    after = _paths(plotted)
    assert after[0] != before[0], "the equalised curve went stale"
    assert after[2] != before[2], "the output trace went stale"


def test_pregain_moves_only_the_output_trace(plotted):
    """Pre-gain is what the DAC emits, not what the EQ does — the solid curve must
    not move when it changes, or the graph is lying about one of the two."""
    before = _paths(plotted)
    plotted.set_pregain(-6.0)
    after = _paths(plotted)
    assert after[2] != before[2]
    assert after[0] == before[0]


def test_switching_views_draws_the_one_being_shown(plotted):
    w = plotted.win
    plotted.set_readout(True)
    assert w.readout_path and w.readout_flat_path and w.readout_grid_path
    plotted.set_readout(False)
    assert w.curve_path and w.pregain_path


def test_a_resize_redraws_at_the_new_size(plotted):
    before = _paths(plotted)
    plotted.on_plot_resized(640, 380)
    assert _paths(plotted)[0] != before[0]


@pytest.mark.parametrize("act", ["preset", "mute", "solo", "undo"])
def test_every_way_of_changing_the_curve_redraws_it(plotted, act):
    """Built rather than borrowed from a preset. Two bands are doing something, so
    muting one and soloing the other both genuinely move the curve — leaning on a
    preset's contents meant testing whether it happened to have one loud band or
    two, which is not what this is about."""
    plotted.set_band_gain(0, 6.0)
    plotted.set_band_gain(4, -6.0)
    before = _paths(plotted)
    {"preset": lambda: plotted.apply_preset(2),
     "mute": lambda: plotted.toggle_band(0),
     "solo": lambda: plotted.solo_band(0),
     "undo": lambda: plotted.undo()}[act]()
    assert _paths(plotted)[0] != before[0], "%s left a stale curve" % act


def test_a_push_that_changes_nothing_does_not_resample(plotted, monkeypatch):
    """The point of the cache. A toast, an update check landing or a sheet opening
    must not cost three sweeps across eight biquads."""
    from gui import curve as curve_mod
    calls = []
    real = curve_mod.svg_curve
    monkeypatch.setattr(curve_mod, "svg_curve",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    plotted.push()
    plotted.toast("something happened")
    plotted.push()
    assert calls == []


def test_a_fader_drag_is_one_undo_step_not_thirty(plotted):
    """`set_band_gain` fires on every frame of a drag. Snapshotting each one would
    make undo useless in the other direction."""
    n = len(plotted._history)
    for g in range(-60, 60, 5):
        plotted.set_band_gain(2, g / 10.0)
    assert len(plotted._history) == n + 1


def test_moving_to_a_different_knob_starts_a_new_step(plotted):
    plotted.set_band_gain(2, 4.0)
    n = len(plotted._history)
    plotted.step_freq(2, 1)
    assert len(plotted._history) == n + 1


def test_moving_to_a_different_band_starts_a_new_step(plotted):
    plotted.set_band_gain(2, 4.0)
    n = len(plotted._history)
    plotted.set_band_gain(5, 4.0)
    assert len(plotted._history) == n + 1


def test_a_nudged_fader_can_be_undone(plotted):
    """The commonest thing anybody wants back, and it was the one edit undo could
    not reach: only drags and wholesale replacements ever took a snapshot."""
    was = plotted._band(2)["gain"]
    plotted.set_band_gain(2, was - 7.0)
    assert plotted._band(2)["gain"] != was
    plotted.undo()
    assert plotted._band(2)["gain"] == was


def test_a_stepper_can_be_undone(plotted):
    was = plotted._band(1)["frequency"]
    plotted.step_freq(1, 1)
    plotted.step_freq(1, 1)
    plotted.undo()
    assert plotted._band(1)["frequency"] == was


# ── the theme pickers ────────────────────────────────────────────────────────
#
# These drive the callbacks the UI is wired to, not the constants behind them.
# Asserting `SKINS == 4` passed happily while `set_skin` raised TypeError on every
# click, because it clamped with `len(SKINS)` and SKINS is a count.

@pytest.mark.parametrize("setter,key,count", [
    ("set_skin", "skin", "SKINS"),
    ("set_accent", "accent", "ACCENTS"),
])
def test_every_choice_can_actually_be_picked(bridge, setter, key, count):
    from gui import bridge as bridge_mod
    n = getattr(bridge_mod, count)
    assert n > 1
    for i in range(n):
        getattr(bridge, setter)(i)
        assert bridge.settings[key] == i
        assert getattr(bridge.win, key + "_index") == i


@pytest.mark.parametrize("setter,key,count", [
    ("set_skin", "skin", "SKINS"),
    ("set_accent", "accent", "ACCENTS"),
])
def test_an_out_of_range_choice_is_clamped_not_raised(bridge, setter, key, count):
    from gui import bridge as bridge_mod
    n = getattr(bridge_mod, count)
    getattr(bridge, setter)(999)
    assert bridge.settings[key] == n - 1
    getattr(bridge, setter)(-7)
    assert bridge.settings[key] == 0


def test_a_picked_palette_survives_a_restart(bridge):
    """It is written to settings.json, so the app has to come back on it."""
    from gui.bridge import load_settings
    bridge.set_skin(2)
    assert load_settings()["skin"] == 2


def test_the_bridge_agrees_with_the_palette_table(bridge):
    """The clamp on the saved setting is counted from theme.slint, so adding a palette
    cannot silently leave it unreachable.

    It lives here rather than beside the other palette test because reaching
    `gui.bridge` means importing slint, and the version matrix deliberately does not
    install it — the CLI standing alone is part of what that matrix proves.
    """
    import re

    from gui import bridge as bridge_mod
    path = os.path.join(ROOT, "gui", "ui", "theme.slint")
    with open(path, encoding="utf-8") as fh:
        table = fh.read().split("out property <[Skin]> all:", 1)[1].split("];", 1)[0]
    assert bridge_mod.SKINS == len(re.findall(r"\{\s*name:", table))

    with open(path, encoding="utf-8") as fh:
        acc = fh.read().split("out property <[Accent]> all:", 1)[1].split("];", 1)[0]
    assert bridge_mod.ACCENTS == len(re.findall(r"\{\s*name:", acc))
