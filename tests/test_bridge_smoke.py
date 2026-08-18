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
from gui import bridge as bridge_mod    # noqa: E402
import moondrop_control as mc          # noqa: E402
from gui import nav                    # noqa: E402
from gui import shapes                 # noqa: E402
from gui import tuning                 # noqa: E402

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
    assert bridge.nav.top == nav.WHATSNEW
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
    assert bridge.nav.top != nav.WHATSNEW


def test_a_rollback_offer_does_not_call_itself_new(bridge):
    """Going back to stable offers an *older* release. "What's new in 1.1.0" over a
    1.2.0b1 would list changes that are already installed."""
    bridge.check_update(True)
    bridge._handle("update_result", dict(OFFER, version="0.9.0", rollback=True))
    assert bridge.update_state == "available"
    assert bridge.nav.top != nav.WHATSNEW


def test_asking_twice_does_not_leave_the_flag_armed(bridge):
    """A click that finds nothing must not make the *next* background check pop the
    panel open."""
    bridge.check_update(True)
    bridge._handle("update_result", None)
    assert not bridge.update_asked
    bridge._handle("update_result", OFFER)
    assert bridge.nav.top != nav.WHATSNEW


def test_dismissing_a_preview_goes_back_to_settings(bridge):
    """Nobody wrote this rule. The notes are opened from Settings, so Settings is
    what is under them, and one step back is Settings."""
    bridge.toggle_settings()
    bridge.update_found = OFFER
    bridge.preview_whatsnew()
    assert bridge.nav.top == nav.WHATSNEW    # the panel is over it, not beside it
    bridge.nav_pop()
    assert bridge.nav.top == nav.SETTINGS
    assert not bridge.whatsnew_preview


def test_dismissing_the_post_update_panel_does_not_open_settings(bridge):
    """The one shown once after an update is not something anybody navigated to,
    so there is nothing under it to go back to."""
    bridge.show_whatsnew()
    assert not bridge.whatsnew_preview
    bridge.nav_pop()
    assert bridge.nav.top == nav.EDITOR


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
    bridge.toggle_settings()
    bridge.toggle_devices()
    bridge.start_tour()
    assert bridge.nav.top == nav.EDITOR
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


# ── the device watch ──
#
# The reason these are here rather than exercised by hand: the only honest manual test
# is physically plugging a DAC in and pulling it out, which CI cannot do and which
# nobody will repeat for every change. `probe` is written so that the interesting part
# — what it decides — needs no hardware at all.

class _FakeDev:
    """Stands in for an open MoondropDevice; only `_close`'s effect matters here."""
    closed = False


def _watched(bridge, monkeypatch, found, holding=None):
    """Run one poll with `find_devices` answering `found`, and return what was posted."""
    posted = []
    worker = bridge.dev
    monkeypatch.setattr(mc, "find_devices", lambda: found)
    monkeypatch.setattr(worker, "_post", lambda kind, payload: posted.append(kind))
    monkeypatch.setattr(worker, "_close", lambda: setattr(worker, "_dev", None))
    worker._dev = holding
    worker.probe()
    return posted


def test_the_watch_says_nothing_when_nothing_has_changed(bridge, monkeypatch):
    """Silence is the normal case: this runs every two seconds forever, and a poll
    that posted on every tick would push the whole UI twice a second for no reason."""
    assert _watched(bridge, monkeypatch, found=[], holding=None) == []
    assert _watched(bridge, monkeypatch, found=[{"path": b"x"}],
                    holding=_FakeDev()) == []


def test_the_watch_reports_a_dac_that_turns_up(bridge, monkeypatch):
    assert _watched(bridge, monkeypatch, found=[{"path": b"x"}],
                    holding=None) == ["appeared"]


def test_the_watch_drops_the_stale_handle_before_announcing_the_unplug(bridge,
                                                                      monkeypatch):
    """Order matters: the handle is on a device that is already gone, and leaving it
    open means the next plug-in of the same DAC opens a second one."""
    worker = bridge.dev
    assert _watched(bridge, monkeypatch, found=[], holding=_FakeDev()) == ["vanished"]
    assert worker._dev is None


def test_an_unplug_does_not_throw_the_welcome_screen_over_the_work(bridge):
    """`no_device` opens the welcome screen on the session's first miss, which is
    right at startup and wrong for a cable somebody just pulled out themselves."""
    bridge._greeted = False
    bridge.welcome_open = False
    bridge._handle("vanished", None)
    assert not bridge.welcome_open
    assert not bridge.connected


# ── notices ──

def test_a_replaced_notice_cannot_clear_its_successor(bridge, monkeypatch):
    """The expiry that arrives late is the whole reason toasts carry a generation."""
    fired = []
    monkeypatch.setattr(slint.Timer, "single_shot",
                        staticmethod(lambda delay, cb: fired.append((delay, cb))))
    bridge.toast("first")
    bridge.toast("second")
    fired[0][1]()                       # the first notice's expiry, arriving late
    assert bridge.win.toast_text == "second"
    fired[1][1]()                       # its own expiry
    assert bridge.win.toast_text == ""


def test_an_error_is_left_up_longer_than_a_confirmation(bridge, monkeypatch):
    fired = []
    monkeypatch.setattr(slint.Timer, "single_shot",
                        staticmethod(lambda delay, cb: fired.append(delay)))
    bridge.toast("saved")
    bridge.toast("that did not work", True)
    assert fired[1] > fired[0]


def test_clearing_a_notice_arms_nothing(bridge, monkeypatch):
    """`toast("")` is how the app takes a message down itself; it must not leave a
    timer behind to clear whatever is on screen five seconds later."""
    fired = []
    monkeypatch.setattr(slint.Timer, "single_shot",
                        staticmethod(lambda delay, cb: fired.append(cb)))
    bridge.toast("")
    assert fired == []


# ── the community library ──

def raw(uuid, *, own=True, likes=0, downloads=0, score=0.0, rated=0, title="x"):
    """A row shaped like the index really is, including the two product keys whose
    only difference is the case of one letter."""
    return {"uuid": uuid, "title": title, "username": "someone", "desc": "",
            "file": "peq-config-file/%s.txt" % uuid, "like": str(likes),
            "downloadcount": str(downloads), "score": score,
            "score_count": str(rated),
            "productuuid": "OURS" if own else "THEIRS", "productUuid": "OURS"}


def _hub(bridge, rows):
    from gui.bridge import HubWorker
    bridge.hub_rows = [HubWorker._slim(r, "OURS") for r in rows]
    bridge._filter_hub()
    return bridge


def test_the_family_filter_reads_the_lower_case_product_key(bridge):
    """The row a DAWN PRO2 query returns carries `productuuid` (who it was uploaded
    for) and `productUuid` (what we asked for). Reading the wrong one shows the whole
    pooled family — five sixths of it other people's hardware — or nothing at all."""
    _hub(bridge, [raw("a"), raw("b", own=False), raw("c")])
    assert [r["uuid"] for r in bridge.hub_visible] == ["a", "c"]
    bridge.community_own(False)
    assert len(bridge.hub_visible) == 3


def test_every_row_carries_its_file_ref(bridge):
    """Without this the prefetch queue is silently always empty, and no curve is ever
    classified or drawn — which is exactly what happened. `hub_resolve_file` answers
    the same question by scanning the whole 5 MB index, once per preset."""
    _hub(bridge, [raw("a"), raw("b")])
    assert all(r["file"] for r in bridge.hub_visible)
    assert len(bridge._to_classify) == 2


def test_the_order_is_the_users_and_survives_a_reload(bridge):
    _hub(bridge, [raw("a", likes=1, downloads=99), raw("b", likes=9, downloads=1)])
    assert [r["uuid"] for r in bridge.hub_visible] == ["b", "a"]     # Liked
    bridge.community_sort(1)                                        # Downloaded
    assert [r["uuid"] for r in bridge.hub_visible] == ["a", "b"]


def test_an_unrated_preset_cannot_outrank_a_rated_one(bridge):
    _hub(bridge, [raw("unrated", score=5.0, rated=0), raw("rated", score=4.2, rated=80)])
    bridge.community_sort(2)                                        # Rated
    assert bridge.hub_visible[0]["uuid"] == "rated"


def test_shape_counts_are_taken_before_the_shape_filter(bridge):
    """Otherwise picking a chip zeroes every other chip and there is no way back."""
    _hub(bridge, [raw("a"), raw("b"), raw("c")])
    bridge._shape_of = {"a": shapes.BASS, "b": shapes.BASS, "c": shapes.BRIGHT}
    bridge._filter_hub()
    assert bridge.hub_counts == {shapes.BASS: 2, shapes.BRIGHT: 1}
    bridge.community_shape(shapes.BASS)
    assert [r["uuid"] for r in bridge.hub_visible] == ["a", "b"]
    assert bridge.hub_counts == {shapes.BASS: 2, shapes.BRIGHT: 1}


def test_clicking_the_chip_already_in_force_clears_it(bridge):
    _hub(bridge, [raw("a"), raw("b")])
    bridge._shape_of = {"a": shapes.WARM}
    bridge.community_shape(shapes.WARM)
    assert bridge.hub_shape == shapes.WARM
    bridge.community_shape(shapes.WARM)
    assert bridge.hub_shape == ""
    assert len(bridge.hub_visible) == 2


def test_the_summary_says_what_the_filter_is_hiding(bridge):
    """A filter that silently drops most of a library looks exactly like a library
    that is nearly empty."""
    _hub(bridge, [raw("a"), raw("b", own=False), raw("c", own=False)])
    bridge._hub_summary()
    assert "1 for your DAC" in bridge.hub_note
    assert "2 more in the family" in bridge.hub_note


# ── provenance ──

def test_applying_a_preset_labels_the_curve(bridge):
    bridge.apply_preset(1)
    assert bridge.source is not None
    label = tuning.describe(bridge.source, bridge.bands)
    assert label.startswith("Preset · ")
    assert "(edited)" not in label


def test_dragging_a_band_makes_the_label_say_so(bridge):
    """The label is a claim about the bands as they are now, so it has to stop being
    true the moment one moves — without the name being lost."""
    bridge.apply_preset(1)
    name = bridge.source["name"]
    bridge.bands[0]["gain"] = round(bridge.bands[0]["gain"] + 2.0, 1)
    label = tuning.describe(bridge.source, bridge.bands)
    assert label.endswith("(edited)")
    assert name in label


def test_what_was_applied_can_be_put_back(bridge):
    bridge.apply_preset(1)
    first = tuning.fingerprint(bridge.bands)
    bridge.apply_preset(2)
    assert tuning.fingerprint(bridge.bands) != first
    bridge.history_apply(first)
    assert tuning.fingerprint(bridge.bands) == first


def test_going_back_does_not_invent_a_new_identity(bridge):
    """Re-applying from history reuses the entry, so the ring holds one of each curve
    rather than a new row every time somebody flips between two."""
    bridge.apply_preset(1)
    bridge.apply_preset(2)
    bridge.history_apply(tuning.fingerprint(bridge.history[1]["bands"]))
    prints = [e["fingerprint"] for e in bridge.history]
    assert len(prints) == len(set(prints)) == 2


def test_keeping_a_recent_curve_writes_a_profile_and_leaves_it_in_history(bridge):
    bridge.apply_preset(1)
    entry = bridge.history[0]
    bridge.history_keep(entry["fingerprint"])
    assert any(r["name"] == entry["name"] for r in bridge.prof_rows)
    assert bridge.history[0]["fingerprint"] == entry["fingerprint"]


def test_an_unknown_curve_is_not_called_hand_drawn(bridge):
    """A device somebody tuned on another machine, or with the vendor app."""
    assert tuning.describe(bridge.source, bridge.bands) == ""


def test_the_settings_path_follows_the_config_dir(tmp_path, monkeypatch):
    """Resolved per call, not captured at import.

    As a module constant this was fixed before any test could redirect it, so every
    `save_settings` in the suite wrote to the real settings file of whoever ran it —
    which is how a test run flipped a developer's update channel underneath them.
    """
    from gui import bridge as bridge_mod
    monkeypatch.setattr(mc, "config_dir", lambda: str(tmp_path))
    assert bridge_mod.settings_path().startswith(str(tmp_path))


# ── headphone corrections ──

def _aeq(bridge, rows):
    bridge.aeq_found = rows
    bridge._filter_aeq()
    return bridge


def aeqrow(model, source, form="over-ear"):
    return {"model": model, "source": source, "form": form}


def test_a_correction_is_identified_by_who_measured_it(bridge):
    """AutoEQ publishes no ids, and the same headphone comes back a dozen times — so
    the model name alone cannot key a curve to a row."""
    from gui.bridge import aeq_key
    a = aeqrow("Sennheiser HD 600", "oratory1990")
    b = aeqrow("Sennheiser HD 600", "crinacle")
    assert aeq_key(a) != aeq_key(b)
    assert aeq_key(a) == aeq_key(dict(a))


def test_measurer_counts_are_taken_before_the_measurer_filter(bridge):
    _aeq(bridge, [aeqrow("HD 600", "oratory1990"), aeqrow("HD 650", "oratory1990"),
                  aeqrow("HD 600", "crinacle")])
    assert bridge.aeq_counts == {"oratory1990": 2, "crinacle": 1}
    bridge.autoeq_source("crinacle")
    assert [r["model"] for r in bridge.aeq_rows] == ["HD 600"]
    assert bridge.aeq_counts == {"oratory1990": 2, "crinacle": 1}


def test_clicking_the_measurer_already_in_force_clears_it(bridge):
    _aeq(bridge, [aeqrow("HD 600", "oratory1990"), aeqrow("HD 600", "crinacle")])
    bridge.autoeq_source("crinacle")
    assert len(bridge.aeq_rows) == 1
    bridge.autoeq_source("crinacle")
    assert bridge.aeq_source == ""
    assert len(bridge.aeq_rows) == 2


# ── the library ──

def test_each_button_opens_the_library_on_its_own_tab(bridge):
    """Three panels became one screen, and the three buttons that opened them still
    mean three different things. Landing on a source nobody asked for — one click from
    the one they did — would not be an improvement."""
    bridge.toggle_community()
    assert bridge.nav.top == nav.LIBRARY
    assert bridge.lib_tab == bridge_mod.LIB_COMMUNITY
    bridge.toggle_profiles()
    assert bridge.lib_tab == bridge_mod.LIB_SAVED
    bridge.toggle_autoeq()
    assert bridge.lib_tab == bridge_mod.LIB_HEADPHONES


def test_the_button_you_came_in_by_closes_the_library(bridge):
    bridge.toggle_community()
    bridge.toggle_community()
    assert bridge.nav.top == nav.EDITOR


def test_a_different_button_switches_tab_rather_than_closing(bridge):
    """Both of these used to be a panel, so pressing the second one while the first was
    up closed one and opened the other. Now it is a tab change, and the library stays
    exactly as deep in the stack as it was."""
    bridge.toggle_community()
    depth = bridge.nav.depth
    bridge.toggle_profiles()
    assert bridge.nav.top == nav.LIBRARY
    assert bridge.nav.depth == depth
    assert bridge.lib_tab == bridge_mod.LIB_SAVED


def test_a_source_is_only_loaded_when_somebody_opens_it(bridge):
    """The community index is 5.4 MB and the AutoEQ catalogue is 8,827 models. Fetching
    both to show one would be the unification making the app slower than the three
    panels it replaced."""
    calls = []
    bridge.community_reload = lambda *a: calls.append("hub")
    bridge.aeq.submit = lambda *a: calls.append("aeq")
    bridge.open_library(bridge_mod.LIB_SAVED)
    assert calls == []
    bridge.library_tab(bridge_mod.LIB_HEADPHONES)
    assert calls == ["aeq"]


def test_showing_needs_both_the_screen_and_the_tab(bridge):
    """Background work — thumbnails, curve classification — stops for a tab that is not
    selected and for a library that has something on top of it, and those are two
    different ways of being invisible."""
    bridge.toggle_community()
    assert bridge.showing(bridge_mod.LIB_COMMUNITY)
    bridge.library_tab(bridge_mod.LIB_SAVED)
    assert not bridge.showing(bridge_mod.LIB_COMMUNITY)
    bridge.library_tab(bridge_mod.LIB_COMMUNITY)
    bridge.nav.open(nav.SETTINGS)
    assert not bridge.showing(bridge_mod.LIB_COMMUNITY)


def test_an_out_of_range_tab_does_not_leave_the_sheet_blank(bridge):
    """The index crosses from the view, and every branch in the sheet is written
    against 0-3. A fourth tab would draw a panel with no list and no controls in it."""
    bridge.toggle_community()
    bridge.library_tab(99)
    assert bridge.lib_tab == bridge_mod.LIB_TABS - 1
    bridge.library_tab(-4)
    assert bridge.lib_tab == 0


def test_the_title_note_says_nothing_when_a_list_is_empty(bridge):
    """It used to state "Nothing saved yet." in the header and again in the middle of
    the empty list, so the sheet explained itself to you twice."""
    bridge.open_library(bridge_mod.LIB_SAVED)
    assert bridge._lib_note() == ""


def test_the_search_matches_the_shape_as_well_as_the_words(bridge):
    """Most of this library is titled in Chinese. A substring search over the title is
    no use to somebody who cannot type it, and "bass" is a thing a curve *is* even when
    no author wrote the word down."""
    rows = [{"uuid": "a", "title": "V2 final", "author": "someone", "desc": "",
             "own": True, "likes": 0, "downloads": 0, "rating": 0.0, "ratings": 0,
             "file": "x"},
            {"uuid": "b", "title": "bass please", "author": "someone", "desc": "",
             "own": True, "likes": 0, "downloads": 0, "rating": 0.0, "ratings": 0,
             "file": "y"}]
    bridge.hub_rows = rows
    bridge._shape_of = {"a": shapes.BASS}
    bridge.hub_query = "bass"
    bridge._filter_hub()
    assert sorted(r["uuid"] for r in bridge.hub_visible) == ["a", "b"]


def test_an_unclassified_row_is_not_excluded_by_a_shape_word(bridge):
    """It has one fewer field to match on, the same as a row with no description — not
    a row that gets filtered out for not having been downloaded yet."""
    rows = [{"uuid": "a", "title": "bass please", "author": "s", "desc": "",
             "own": True, "likes": 0, "downloads": 0, "rating": 0.0, "ratings": 0,
             "file": "x"}]
    bridge.hub_rows = rows
    bridge._shape_of = {}
    bridge.hub_query = "bass"
    bridge._filter_hub()
    assert [r["uuid"] for r in bridge.hub_visible] == ["a"]


def test_a_prefetched_curve_arrives_already_drawn(bridge):
    """The thumbnail is drawn on the fetching thread, not where the result lands. At
    3.1 ms a curve it was 25 ms of dead frames per chunk of eight, fifty times over
    while a page filled in — so the payload carries a path, not bands."""
    bands = [{"index": i, "type": "peaking", "frequency": 100 * (i + 1),
              "gain": 2.0, "q": 1.0} for i in range(8)]
    thumb = bridge_mod._thumbnail(bands)
    assert thumb.startswith("M")
    bridge._handle("hub_shapes", [("u1", shapes.BASS, thumb)])
    assert bridge._shape_of["u1"] == shapes.BASS
    assert bridge._thumb_of["u1"] == thumb
    bridge._handle("aeq_shapes", [("k1", shapes.BRIGHT, thumb)])
    assert bridge._aeq_thumb_of["k1"] == thumb


def test_the_prefetch_workers_hand_back_paths_not_bands(bridge):
    """Both prefetchers, checked at the seam rather than by reading them: whatever they
    post must be what the handler above expects, and a mismatch is a blank thumbnail on
    every row with nothing logged."""
    import inspect
    for fn in (bridge_mod.HubWorker.prefetch, bridge_mod.AutoEqWorker.prefetch):
        src = inspect.getsource(fn)
        assert "_thumbnail(bands)" in src, fn.__qualname__
        assert "svg_curve" not in src, fn.__qualname__


# ── the DAC's own EQ profiles ──

def _snap(bridge, active, clean=True, bands=None):
    """A device reading, as the worker posts one."""
    return bridge._handle("snapshot", ({
        "connected": True, "deviceName": "DAWN PRO2", "productId": 0x011D,
        "firmware": "1.5", "activeProfile": active, "peqIndex": 7,
        "supportsPregain": True, "pregain": -4.5, "globalGain": 0.0,
        "bandCount": 8,
        "bands": bands or [{"index": i, "type": "peaking", "frequency": 1000,
                            "gain": 0.0, "q": 1.0} for i in range(8)],
    }, clean))


def test_the_profile_number_is_what_the_device_reported(bridge):
    """Not what was asked for. A DAWN PRO2 accepts 0-4 and silently ignores anything
    above, so a control counting its own clicks displayed profiles the DAC had refused —
    and once it had walked down to 4 there was no way back up."""
    asked = []
    bridge.dev.submit = lambda fn, *a: asked.append(a)
    _snap(bridge, 4)
    bridge.step_slot(1)
    assert asked == [(5,)]              # the request goes out…
    assert bridge.slot == 4             # …and nothing moves until the device answers
    bridge._handle("slot", 4)           # the device refused
    assert bridge.slot == 4


def test_a_refused_step_does_not_mark_anything_unsaved(bridge):
    bridge.dev.submit = lambda fn, *a: None
    _snap(bridge, 4)
    bridge.dirty = False
    bridge._handle("slot", 4)
    assert not bridge.dirty
    bridge._handle("slot", 3)
    assert bridge.dirty


def test_the_custom_profile_index_is_learned_from_a_write_not_guessed(bridge):
    """A plain refresh cannot tell you: somebody who cycled to a built-in with the volume
    buttons and then launched the app would have that built-in adopted as "mine". Only a
    reading taken after this app wrote bands is proof, because writing the custom store is
    what selects it."""
    _snap(bridge, 2, clean=True)                 # launched on a built-in
    assert bridge.custom_slot is None
    assert not bridge.on_builtin                 # no claim either way
    assert bridge.slot_note() == "as reported"
    _snap(bridge, 9, clean=False)                # we wrote; the DAC says 9
    assert bridge.custom_slot == 9
    assert not bridge.on_builtin


def test_a_built_in_profile_is_named_as_the_dac_s_own(bridge):
    _snap(bridge, 9, clean=False)                # learn that custom is 9
    _snap(bridge, 2, clean=True)                 # then the volume buttons moved it
    assert bridge.on_builtin
    assert "DAC" in bridge.slot_note()


def test_the_learned_index_survives_a_relaunch(bridge):
    """It is a fact about the hardware, not about the session — so the app can say "that
    is the DAC's preset" the first time you look at it, rather than after you edit."""
    _snap(bridge, 9, clean=False)
    assert bridge.settings["custom_slots"][str(0x011D)] == 9
    reloaded = bridge_mod.load_settings()
    assert reloaded["custom_slots"][str(0x011D)] == 9


def test_getting_back_to_your_curve_is_a_rewrite_not_a_profile_change(bridge):
    """There is no index that selects the custom profile, so the only route back is
    writing the bands — and writing them is what selects it. It goes through the same
    apply path as a preset, so undo, provenance and history all see it."""
    jobs = []
    mine = [dict(b, gain=4.0, frequency=250) for b in bridge.bands]
    tuning.save_state(0x011D, tuning.source("profile", "Mine", mine, pregain=-3.0))
    _snap(bridge, 9, clean=False, bands=mine)
    _snap(bridge, 2, clean=True)
    bridge.dev.submit = lambda fn, *a: jobs.append((fn.__name__, a))
    bridge.reselect_custom()
    assert [j[0] for j in jobs] == ["apply_bands"]
    assert [b["gain"] for b in jobs[0][1][0]] == [4.0] * bridge.band_count


def test_reselecting_never_writes_the_built_ins_bands_as_your_curve(bridge):
    """The bands on screen while a built-in is playing *are* that built-in's, read back
    from the device. The first version of this wrote them, so "my curve" put the DAC's
    own preset into the custom profile and called it yours."""
    mine = [dict(b, gain=4.0) for b in bridge.bands]
    tuning.save_state(0x011D, tuning.source("profile", "Mine", mine))
    _snap(bridge, 9, clean=False, bands=mine)
    builtin = [dict(b, gain=-6.0, frequency=200) for b in bridge.bands]
    _snap(bridge, 2, clean=True, bands=builtin)
    assert [b["gain"] for b in bridge.bands] == [-6.0] * bridge.band_count
    written = []
    bridge.dev.submit = lambda fn, *a: written.append(a[0] if a else None)
    bridge.reselect_custom()
    assert [b["gain"] for b in written[0]] == [4.0] * bridge.band_count


def test_reselecting_says_so_when_this_machine_has_no_record(bridge):
    """The custom profile cannot be read without selecting it, and selecting it means
    writing — so with nothing recorded there is genuinely nothing to restore."""
    _snap(bridge, 9, clean=False)
    _snap(bridge, 2, clean=True)
    tuning.save_state(0x011D, None)
    jobs = []
    bridge.dev.submit = lambda fn, *a: jobs.append(fn.__name__)
    bridge.reselect_custom()
    assert jobs == []
    assert "No curve saved" in bridge.win.toast_text
    assert bridge.win.toast_error


def test_reselecting_does_nothing_when_you_are_already_on_your_curve(bridge):
    jobs = []
    bridge.dev.submit = lambda fn, *a: jobs.append(fn.__name__)
    _snap(bridge, 9, clean=False)
    jobs.clear()
    bridge.reselect_custom()
    assert jobs == []


def test_a_built_in_profile_is_not_labelled_as_your_edited_curve(bridge):
    """The bands on screen are the DAC's, so naming them after the last thing this app
    applied read as "your community curve, edited" over eight bands it never wrote.

    Every snapshot reloads the provenance from `state.json`, which is exactly why this
    happened: the name outlives the curve it belonged to on purpose, so that unplugging
    and replugging does not lose it.
    """
    ours = [dict(b, gain=3.0) for b in bridge.bands]
    tuning.save_state(0x011D, tuning.source("hub", "Somebody's curve", ours))
    _snap(bridge, 9, clean=False, bands=ours)
    assert bridge.source is not None
    assert tuning.describe(bridge.source, bridge.bands) != ""

    _snap(bridge, 2, clean=True)                 # the volume buttons moved it
    assert bridge.on_builtin
    # kept rather than cleared, so cycling back to custom gets the name back…
    assert bridge.source is not None
    # …but the view is told to say nothing about these bands
    bridge.push()
    assert bridge.win.source_label == ""
