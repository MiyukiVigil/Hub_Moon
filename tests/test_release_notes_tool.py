"""The changelog-to-notes extractor.

Everything here is a line that was wrong on screen before it was a test. A note is
read by somebody deciding whether to install a version, so a mangled flag name or a
sentence that begins "— , shown once" is not a cosmetic problem — it is the only
description of the release they are being offered.
"""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    path = os.path.join(ROOT, "tools", "build-release-notes.py")
    spec = importlib.util.spec_from_file_location("notes_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load()


def one(tool, bullet, section="Fixed"):
    """The single note produced by a single bullet."""
    got = tool.parse("## [9.9.9] - 2026-01-01\n\n### %s\n\n%s\n" % (section, bullet))
    return got["9.9.9"][0]


def test_a_bold_lead_that_is_a_sentence_is_the_whole_note(tool):
    assert one(tool, "- **The window had no icon on Windows.** It was built without "
                     "one, and the taskbar showed the generic Python feather.") \
        == "The window had no icon on Windows."


def test_a_short_bold_lead_keeps_the_clause_that_completes_it(tool):
    """"Saved profiles." on its own says nothing about what was saved."""
    got = one(tool, "- **Saved profiles.** Name the curve on screen and keep it. "
                    "Stored in the config directory.", "Added")
    assert got == "Saved profiles. Name the curve on screen and keep it."


def test_a_lead_running_into_its_own_sentence_is_not_broken_by_a_dash(tool):
    """The source reads `**What's New**, shown once after an update.` — joining that
    with " — " produced "What's New — , shown once after an update"."""
    got = one(tool, "- **What's New**, shown once after an update. The notes come "
                    "from the manifest.", "Added")
    assert got == "What's New, shown once after an update."


def test_a_dash_after_the_lead_is_not_confused_with_a_long_option(tool):
    """`- **Backup and restore** — `--export-json` / …` lost both leading hyphens of
    the flag when the dash strip was a plain lstrip, leaving "export-json"."""
    got = one(tool, "- **Backup and restore** — `--export-json` / `--import-json` "
                    "for a full device snapshot.", "Added")
    assert got == ("Backup and restore — --export-json / --import-json for a full "
                   "device snapshot.")


def test_code_spans_keep_their_underscores(tool):
    """Stripping `_` as emphasis turned parse_peq_text() into parsepeqtext(), a name
    that exists nowhere and cannot be searched for."""
    got = one(tool, "- **Extracted.** `parse_peq_text()` and `fit_peq_to_bands()` "
                    "are real functions now.")
    assert "parse_peq_text()" in got and "fit_peq_to_bands()" in got


def test_links_keep_their_text_and_lose_their_target(tool):
    got = one(tool, "- A rule lives in [the install guide](https://example.com/x).")
    assert got == "A rule lives in the install guide."
    assert "http" not in got


def test_a_bullet_with_no_bold_lead_is_its_first_sentence(tool):
    got = one(tool, "- Ruff finds bugs rather than restyling. It flags 170 strings.",
              "Changed")
    assert got == "Ruff finds bugs rather than restyling."


def test_a_wrapped_bullet_is_one_note(tool):
    got = tool.parse("## [9.9.9] - 2026-01-01\n\n### Fixed\n\n"
                     "- **One thing that broke and was then fixed.** The explanation\n"
                     "  wraps across three source lines\n"
                     "  and is still one bullet.\n")
    assert got["9.9.9"] == ["One thing that broke and was then fixed."]


def test_sections_are_ordered_fixed_then_added_then_changed(tool):
    """What broke and now works is what somebody deciding whether to update wants
    first; housekeeping is what they want last."""
    got = tool.parse("## [9.9.9] - 2026-01-01\n\n"
                     "### Changed\n\n- Third thing changed.\n\n"
                     "### Added\n\n- Second thing added.\n\n"
                     "### Fixed\n\n- First thing fixed.\n")
    assert got["9.9.9"] == ["First thing fixed.", "Second thing added.",
                            "Third thing changed."]


def test_reference_sections_are_left_out(tool):
    """"Protocol notes" and "Verified on hardware" are repo reference material, not
    lines in a panel with room for twelve."""
    got = tool.parse("## [9.9.9] - 2026-01-01\n\n"
                     "### Fixed\n\n- A real fix.\n\n"
                     "### Protocol notes\n\n- Register 0x0b is the pregain.\n")
    assert got["9.9.9"] == ["A real fix."]


def test_a_version_is_capped_at_twelve_notes(tool):
    body = "\n".join("- Fix number %d." % i for i in range(30))
    got = tool.parse("## [9.9.9] - 2026-01-01\n\n### Fixed\n\n%s\n" % body)
    assert len(got["9.9.9"]) == tool.MAX_NOTES == 12


def test_a_long_note_is_elided_rather_than_wrapped_forever(tool):
    got = one(tool, "- " + "word " * 120 + "end.")
    assert len(got) <= tool.MAX_LEN
    assert got.endswith("…")


def test_the_real_changelog_produces_notes_for_the_running_version(tool):
    """The generated module and CHANGELOG.md agreeing is what makes the What's New
    panel honest; they drift the moment somebody bumps a version and forgets."""
    import moondrop_control as mc
    from gui.notes import NOTES

    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        fresh = tool.parse(fh.read())
    assert mc.__version__ in fresh, "CHANGELOG.md has no section for the running version"
    assert NOTES == fresh, "gui/notes.py is stale — run tools/build-release-notes.py"
