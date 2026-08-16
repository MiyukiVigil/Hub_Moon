"""The bridge against the real window.

Skipped without slint, and run in CI by the job that installs it. Everything in here
is a bug class the unit tests structurally cannot catch: they never construct a
Bridge, so a property the .slint does not declare, a callback bound to a name that
does not exist, or a field `push()` reads but `__init__` never set is invisible to
them and fatal on launch. Each has happened.
"""
import pytest

slint = pytest.importorskip("slint", reason="the GUI extra is not installed")

from gui.bridge import Bridge          # noqa: E402 — must not import before the skip
import moondrop_control as mc          # noqa: E402


OFFER = {
    "version": "9.9.9", "rollback": False, "notes": ["first note", "second note"],
    "channel": "beta", "date": "2026-01-01", "summary": "A summary line.",
    "notes_url": "https://example.invalid/notes", "install_kind": "appimage",
    "can_install": True, "can_fetch": False, "can_elevate": False,
    "asset": {"name": "x.AppImage"}, "hint": "", "reason": "",
}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A Bridge on a real window, writing to a throwaway config directory."""
    monkeypatch.setattr(mc, "config_dir", lambda: str(tmp_path))
    monkeypatch.setattr(mc, "cache_dir", lambda: str(tmp_path / "cache"))
    window = slint.load_file("gui/ui/app.slint").MainWindow()
    b = Bridge(window)
    try:
        yield b
    finally:
        b.stop()


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
