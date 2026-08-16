"""The release tool's failure messages.

A build tool that answers a mistyped tag with a urllib traceback has made the person
running it read a stack to learn that they typed a tag that does not exist. These
pin the sentences instead.
"""
import importlib.util
import os
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    path = os.path.join(ROOT, "tools", "build-update-manifest.py")
    spec = importlib.util.spec_from_file_location("manifest_tool", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tool():
    return load()


def http(code, headers=None):
    return urllib.error.HTTPError("https://api.example/x", code, "reason",
                                  headers or {}, None)


def test_a_missing_tag_says_so(tool):
    got = str(tool._explain(http(404), "u"))
    assert "No release found" in got
    assert "git push origin" in got and "releases" in got
    assert "Traceback" not in got


def test_rate_limiting_says_when_it_resets_and_how_to_avoid_it(tool, monkeypatch):
    import time
    headers = {"X-RateLimit-Remaining": "0",
               "X-RateLimit-Reset": str(int(time.time()) + 600)}
    got = str(tool._explain(http(403, headers), "u"))
    assert "rate limit" in got.lower()
    assert "minute" in got
    assert "GITHUB_TOKEN" in got


def test_a_forbidden_that_is_not_rate_limiting_is_not_mislabelled(tool):
    got = str(tool._explain(http(403, {"X-RateLimit-Remaining": "57"}), "u"))
    assert "rate limit" not in got.lower()


def test_other_statuses_still_name_the_url(tool):
    assert "https://api.example/x" in str(tool._explain(http(500), "https://api.example/x"))


def test_the_token_is_optional(tool, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert tool._token() == ""
    monkeypatch.setenv("GH_TOKEN", "  abc  ")
    assert tool._token() == "abc"


def test_asset_names_map_to_the_keys_the_app_asks_for(tool):
    from gui import updater as U
    known = set(U.ASSET_FOR.values()) | {"macos-dmg-arm64", "macos-dmg-x86_64"}
    for name, expect in [
        ("HubMoon-Setup-1.2.0.exe", "windows-installer"),
        ("HubMoon-1.2.0-windows-portable.zip", "windows-portable"),
        ("HubMoon-1.2.0-macOS-arm64.dmg", "macos-dmg-arm64"),
        ("HubMoon-1.2.0-x86_64.AppImage", "appimage"),
        ("HubMoon-1.2.0-linux-x86_64.tar.gz", "linux-tarball"),
    ]:
        got = tool.classify(name)
        assert got == expect, name
        assert got in known, "%s is a key the app never looks for" % got


def test_checksum_files_are_not_downloads(tool):
    assert tool.classify("SHA256SUMS-linux.txt") is None


@pytest.mark.parametrize("name,expect", [
    ("hub-moon_1.2.0b1-1_amd64.deb", "deb-package"),
    ("hub-moon-1.2.0b1-1.x86_64.rpm", "rpm-package"),
    ("hub-moon-1.2.0b1-1-x86_64.pkg.tar.zst", "arch-package"),
])
def test_system_packages_are_offered_as_downloads_only(tool, name, expect):
    """These are in the manifest so the app can fetch and verify one, and hand over
    the command that installs it. They must never become something it installs
    itself — that is the difference between helping and corrupting a package
    database, so the invariant is asserted rather than assumed."""
    from gui import updater as U
    assert tool.classify(name) == expect
    kind = next(k for k, v in U.ASSET_FOR.items() if v == expect)
    assert kind in U.FETCHABLE
    assert kind not in U.SELF_UPDATABLE
    assert kind not in U.APPLIERS


# ── notes ────────────────────────────────────────────────────────────────────

def test_the_release_body_is_used_when_there_is_one(tool):
    got = tool.release_notes("- **A thing.** With an explanation after it.\n"
                             "- Another thing.\n")
    assert got == ["A thing. With an explanation after it.", "Another thing."]


def test_an_empty_release_body_falls_back_to_the_changelog(tool):
    """The normal case, and the reason this exists: the build workflows publish a
    release with an empty body, so every manifest written before this shipped
    `"notes": []` — and the app, asked to preview a version it has not installed,
    correctly had nothing to show."""
    import moondrop_control as mc
    assert tool.release_notes("") == []
    got = tool.changelog_notes(mc.__version__)
    assert got, "CHANGELOG.md has no notes for the running version"
    assert all(isinstance(n, str) and n.strip() for n in got)


def test_the_fallback_matches_a_version_spelled_either_way(tool):
    """A tag of v1.2.0-beta.1 and a changelog heading of 1.2.0b1 are the same release
    written two legal ways — PEP 440 spells it one way and Arch's pkgver forbids the
    hyphen in the other."""
    from gui import updater as U
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        heads = [ln for ln in fh if ln.startswith("## [")]
    tag = next((h.split("[")[1].split("]")[0] for h in heads if "b" in h.split("]")[0]), None)
    if not tag:
        pytest.skip("no pre-release in the changelog to check against")
    assert U.parse_version(tag) == U.parse_version(tag.replace("b", "-beta."))
    assert tool.changelog_notes(tag) == tool.changelog_notes(tag.replace("b", "-beta."))


def test_an_unknown_version_gets_no_notes_rather_than_the_wrong_ones(tool):
    """Showing one version's notes under another's heading is worse than showing
    none — it is the app stating something false about what you are about to install."""
    assert tool.changelog_notes("9.9.9") == []
