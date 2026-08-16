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


def test_checksum_files_and_packages_are_not_treated_as_downloads(tool):
    for name in ("SHA256SUMS-linux.txt", "hub-moon_1.2.0-1_amd64.deb",
                 "hub-moon-1.2.0-1.x86_64.rpm"):
        assert tool.classify(name) is None, name
