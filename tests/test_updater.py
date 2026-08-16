"""Version ordering, install detection, and the refusals the updater must make.

Nothing here reaches the network — conftest forbids it. The manifest is written to a
temporary cache directory and read back, which is the same path a real check takes
once the fetch has happened.
"""
import json
import urllib.error

import pytest

from gui import updater as U


# ── ordering ─────────────────────────────────────────────────────────────────

ORDERED = [
    "0.9.9", "1.0.0", "1.1.0-alpha.1", "1.1.0-alpha.2", "1.1.0-beta.1",
    "1.1.0-beta.2", "1.1.0-beta.10", "1.1.0-rc.1", "1.1.0", "1.1.1", "1.2.0", "2.0.0",
]


def test_versions_sort_correctly():
    import random
    shuffled = ORDERED[:]
    random.shuffle(shuffled)
    assert sorted(shuffled, key=U.parse_version) == ORDERED


def test_beta_numbers_are_compared_as_numbers():
    """The regression: with `b` ahead of `beta` in the alternation, every beta of a
    release parsed identically and no beta ever superseded another."""
    assert U.is_newer("1.1.0-beta.2", "1.1.0-beta.1")
    assert U.is_newer("1.1.0-beta.10", "1.1.0-beta.9")
    assert not U.is_newer("1.1.0-beta.1", "1.1.0-beta.2")


def test_a_prerelease_is_older_than_its_release():
    assert U.is_newer("1.2.0", "1.2.0-rc.1")
    assert not U.is_newer("1.2.0-rc.1", "1.2.0")


@pytest.mark.parametrize("a,b", [("garbage", "1.0.0"), ("1.0.0", "garbage"),
                                 ("", "1.0.0"), ("1.0.0", ""), (None, "1.0.0")])
def test_unparseable_versions_never_claim_to_be_newer(a, b):
    assert not U.is_newer(a, b)


def test_leading_v_is_accepted():
    assert U.is_newer("v1.2.0", "1.1.0")


def test_short_versions_pad():
    assert U.parse_version("1.2") == U.parse_version("1.2.0")


# ── install kinds ────────────────────────────────────────────────────────────

def test_package_manager_installs_never_self_update():
    for kind in ("deb", "rpm", "pacman", "nix", "pip", "pipx", "system", "source"):
        assert not U.can_self_update(kind), kind
        assert U.MANUAL_HINT.get(kind), "%s must offer a command instead" % kind


def test_installs_we_ship_can_self_update():
    for kind in ("windows-installer", "windows-portable", "macos-app",
                 "appimage", "linux-tarball"):
        assert U.can_self_update(kind), kind
        assert kind in U.APPLIERS, "%s has no applier" % kind
        assert kind in U.ASSET_FOR, "%s has no manifest key" % kind


def test_every_self_updatable_kind_has_an_applier():
    assert set(U.SELF_UPDATABLE) <= set(U.APPLIERS)


def test_macos_asset_key_is_architecture_specific(monkeypatch):
    """An arm64 bundle installs cleanly on an Intel Mac and then will not launch."""
    monkeypatch.setattr(U.platform, "machine", lambda: "arm64")
    assert U.asset_key("macos-app") == "macos-dmg-arm64"
    monkeypatch.setattr(U.platform, "machine", lambda: "x86_64")
    assert U.asset_key("macos-app") == "macos-dmg-x86_64"


def test_describe_install_carries_the_real_command():
    for kind, hint in U.MANUAL_HINT.items():
        assert hint in U.describe_install(kind), kind
    assert "itself" in U.describe_install("appimage")


def test_hints_do_not_name_registries_hub_moon_is_not_on():
    """Hub Moon is published on neither PyPI nor the AUR. 1.1.0 printed
    `pip install --upgrade hub-moon` and `yay -Syu hub-moon`, and both fail — so
    any hint that installs by bare package name is a hint that does not work."""
    assert "yay" not in U.MANUAL_HINT["pacman"]
    for kind in ("pip", "pipx"):
        h = U.MANUAL_HINT[kind]
        assert "git+" in h or h.startswith("pipx upgrade"), h
    for kind in ("deb", "rpm"):
        assert "./" in U.MANUAL_HINT[kind], "%s must install a downloaded file" % kind
        assert "--only-upgrade" not in U.MANUAL_HINT[kind]


# ── manifests ────────────────────────────────────────────────────────────────

def manifest(version="1.2.0", **assets):
    return {"schema": 1, "version": version, "date": "2026-09-01",
            "summary": "test", "notes_url": "https://example.invalid/n",
            "assets": assets or {"appimage": {
                "url": "https://example.invalid/x.AppImage",
                "sha256": "a" * 64, "size": 10}}}


@pytest.fixture
def cached(tmp_path, monkeypatch):
    monkeypatch.setattr(U.mc, "cache_dir", lambda: str(tmp_path))

    def put(channel, data):
        with open(tmp_path / ("update-%s.json" % channel), "w") as fh:
            json.dump(data, fh)
    return put


def test_newer_version_is_offered(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.2.0"))
    got = U.check("stable", current="1.1.0")
    assert got["version"] == "1.2.0" and got["can_install"] is True


def test_same_or_older_returns_none(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.2.0"))
    assert U.check("stable", current="1.2.0") is None
    assert U.check("stable", current="9.9.9") is None


def test_unreachable_manifest_raises_rather_than_saying_up_to_date(tmp_path, monkeypatch):
    """Folding "offline" into "up to date" is what made the app assert something it
    had no evidence for. The caller decides whether to report it; check() must not
    swallow it.

    The failure is injected at `_get` rather than by pointing at a dead host, so the
    test asserts the contract — an unreachable manifest propagates — instead of
    whatever the resolver on the runner happens to raise.
    """
    monkeypatch.setattr(U.mc, "cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(U, "MANIFESTS", {"stable": ("https://example.invalid/none",)})

    def unreachable(url, timeout=None):
        raise urllib.error.URLError("no route to host")
    monkeypatch.setattr(U, "_get", unreachable)

    with pytest.raises(urllib.error.URLError):
        U.check("stable", current="1.0.0", force=True)


def test_a_manifest_that_is_not_json_is_treated_as_unreachable(tmp_path, monkeypatch):
    """A captive portal answers 200 with a login page. That is not a manifest, and
    accepting it as one would be worse than a refused connection."""
    monkeypatch.setattr(U.mc, "cache_dir", lambda: str(tmp_path))
    monkeypatch.setattr(U, "MANIFESTS", {"stable": ("https://example.invalid/none",)})
    monkeypatch.setattr(U, "_get", lambda url, timeout=None: b"<html>sign in</html>")
    with pytest.raises(Exception):  # noqa: B017 - any refusal is correct here
        U.check("stable", current="1.0.0", force=True)


def test_package_manager_install_is_told_the_command(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "pacman")
    cached("stable", manifest("1.2.0"))
    got = U.check("stable", current="1.1.0")
    assert got["can_install"] is False
    assert got["hint"] == U.MANUAL_HINT["pacman"]
    assert "package manager" in got["reason"]


def test_a_downloadable_package_is_told_where_to_get_it(cached, monkeypatch):
    """`apt install ./file.deb` is useless without saying where the file comes from."""
    monkeypatch.setattr(U, "install_kind", lambda: "deb")
    cached("stable", manifest("1.2.0"))
    got = U.check("stable", current="1.1.0")
    assert U.RELEASES_URL in got["reason"] and "./" in got["hint"]


def test_missing_asset_is_not_blamed_on_a_package_manager(cached, monkeypatch):
    """A .dmg missing from a release is a build that has not finished, not dpkg."""
    monkeypatch.setattr(U, "install_kind", lambda: "macos-app")
    cached("stable", manifest("1.2.0"))            # only carries an appimage
    got = U.check("stable", current="1.1.0")
    assert got["can_install"] is False
    assert "package manager" not in got["reason"]
    assert U.RELEASES_URL in got["hint"]


def test_env_opt_out_silences_everything(cached, monkeypatch):
    monkeypatch.setenv(U.ENV_OPT_OUT, "1")
    cached("stable", manifest("9.9.9"))
    assert U.check("stable", current="1.0.0") is None


def test_channels_are_separate_files(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.2.0"))
    cached("beta", manifest("1.3.0-beta.1"))
    assert U.check("stable", current="1.1.0")["version"] == "1.2.0"
    assert U.check("beta", current="1.1.0")["version"] == "1.3.0-beta.1"


# ── downloads must be verified ───────────────────────────────────────────────

def test_asset_without_a_checksum_is_refused(tmp_path):
    with pytest.raises(U.UpdateError, match="checksum"):
        U.download({"url": "https://example.invalid/x"}, dest_dir=str(tmp_path))


def test_asset_with_a_malformed_checksum_is_refused(tmp_path):
    with pytest.raises(U.UpdateError, match="checksum"):
        U.download({"url": "https://example.invalid/x", "sha256": "abc"},
                   dest_dir=str(tmp_path))


def test_asset_without_a_url_is_refused(tmp_path):
    with pytest.raises(U.UpdateError):
        U.download({"sha256": "a" * 64}, dest_dir=str(tmp_path))


def test_install_refuses_a_kind_it_must_not_touch():
    with pytest.raises(U.UpdateError, match="makepkg"):
        U.install({"install_kind": "pacman", "asset": {}})
