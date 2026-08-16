"""Version ordering, install detection, and the refusals the updater must make.

Nothing here reaches the network — conftest forbids it. The manifest is written to a
temporary cache directory and read back, which is the same path a real check takes
once the fetch has happened.
"""
import json
import os
import urllib.error

import pytest

from gui import updater as U

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    """A kind nothing can ever be downloaded for — pip installs from a git URL, so
    there is no artefact and the only useful answer is the command."""
    monkeypatch.setattr(U, "install_kind", lambda: "pip")
    cached("stable", manifest("1.2.0"))
    got = U.check("stable", current="1.1.0")
    assert got["can_install"] is False and got["can_fetch"] is False
    assert got["hint"] == U.MANUAL_HINT["pip"]
    assert "package manager" in got["reason"]


def test_a_fetchable_kind_with_a_package_explains_the_root_prompt(cached, monkeypatch):
    """When the release does carry the package, the reason is about authorisation —
    not about where to find a file, because the app is fetching it."""
    monkeypatch.setattr(U, "install_kind", lambda: "deb")
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    cached("stable", manifest("1.2.0", **{"deb-package": {
        "url": "https://example.invalid/hub-moon.deb", "sha256": "a" * 64, "size": 9}}))
    got = U.check("stable", current="1.1.0")
    assert got["can_fetch"] is True
    assert "root" in got["reason"]
    # A placeholder, not a path: the real one is not known until the download has
    # finished, and the exact line with the path in it replaces this afterwards.
    assert got["hint"] == "sudo apt install <the downloaded file>"


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


# ── what's new ───────────────────────────────────────────────────────────────

def test_release_notes_come_from_the_cache(cached):
    cached("stable", dict(manifest("1.2.0"), notes=["one", "two"]))
    assert U.release_notes("stable", "1.2.0") == ["one", "two"]


def test_release_notes_match_a_version_spelled_either_way(cached):
    """`v1.2.0-beta.1` and `1.2.0b1` are the same release. PEP 440 spells it one way,
    and Arch's pkgver forbids the hyphen in the other — so the tag and __version__ can
    legitimately differ in text, and matching on text would show an empty panel."""
    cached("beta", dict(manifest("1.2.0-beta.1"), notes=["hello"]))
    assert U.release_notes("beta", "1.2.0b1") == ["hello"]
    assert U.release_notes("beta", "1.2.0-beta.1") == ["hello"]


def test_release_notes_for_another_version_do_not_come_from_its_manifest(cached):
    """The one thing that must never happen: 1.2.0's notes shown for 1.1.0."""
    cached("stable", dict(manifest("1.2.0"), notes=["one"]))
    assert "one" not in U.release_notes("stable", "1.1.0")


def test_release_notes_with_no_manifest_fall_back_to_this_build(tmp_path, monkeypatch):
    """The beta case, and the reason gui/notes.py exists.

    A build from the repo — `makepkg -si`, a wheel from a git URL, a beta whose release
    has not been published — has no manifest to read and never will. Announcing a new
    version and then showing an empty panel is worse than not announcing it.
    """
    monkeypatch.setattr(U.mc, "cache_dir", lambda: str(tmp_path))
    from gui.notes import NOTES
    assert U.release_notes("beta", "1.1.0") == NOTES["1.1.0"]


def test_a_manifest_that_carries_no_notes_falls_back_too(cached):
    """Matching the version is not the same as having something to say about it."""
    cached("stable", dict(manifest("1.1.0"), notes=[]))
    assert U.release_notes("stable", "1.1.0")


def test_release_notes_with_nothing_anywhere_are_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(U.mc, "cache_dir", lambda: str(tmp_path))
    assert U.release_notes("stable", "9.9.9") == []


def test_bundled_notes_match_a_version_spelled_either_way(tmp_path, monkeypatch):
    monkeypatch.setattr(U.mc, "cache_dir", lambda: str(tmp_path))
    from gui.notes import NOTES
    tag = next(t for t in NOTES if "b" in t)          # e.g. 1.2.0b1
    assert U.bundled_notes(tag) == NOTES[tag]
    assert U.bundled_notes(tag.replace("b", "-beta.")) == NOTES[tag]


def test_this_version_has_notes_compiled_in():
    """A version bump with no `python3 tools/build-release-notes.py` after it ships a
    build that cannot say what is in it. That is invisible until somebody updates."""
    assert U.bundled_notes(U.mc.__version__), (
        "no notes for %s — run tools/build-release-notes.py" % U.mc.__version__)


def test_release_notes_finds_the_other_channel(cached):
    """A beta user who moved to stable still gets the notes for what they installed."""
    cached("beta", dict(manifest("1.2.0b1"), notes=["from beta"]))
    assert U.release_notes("stable", "1.2.0b1") == ["from beta"]


# ── packages we fetch but never install ──────────────────────────────────────

def test_a_fetchable_install_is_offered_the_download(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "pacman")
    cached("stable", manifest("1.2.0", **{"arch-package": {
        "url": "https://example.invalid/hub-moon-1.2.0-1-x86_64.pkg.tar.zst",
        "sha256": "a" * 64, "size": 10}}))
    got = U.check("stable", current="1.1.0")
    assert got["can_install"] is False, "root is never taken"
    assert got["can_fetch"] is True
    assert "root" in got["reason"] and "pacman -U" in got["hint"]


def test_fetchable_falls_back_to_a_command_when_the_asset_is_absent(cached, monkeypatch):
    """No Arch package in that release — say what to run, do not offer a button."""
    monkeypatch.setattr(U, "install_kind", lambda: "pacman")
    cached("stable", manifest("1.2.0"))          # appimage only
    got = U.check("stable", current="1.1.0")
    assert got["can_fetch"] is False and got["can_install"] is False
    assert got["hint"] == U.MANUAL_HINT["pacman"]


def test_nothing_fetchable_is_ever_self_installable():
    """The invariant the whole design rests on."""
    assert not (U.FETCHABLE & U.SELF_UPDATABLE)
    for kind in U.FETCHABLE:
        assert kind not in U.APPLIERS
        assert kind in U.INSTALL_COMMAND
        assert kind in U.ASSET_FOR


def test_the_handover_command_quotes_a_path_with_spaces(tmp_path, monkeypatch):
    payload = tmp_path / "my downloads"
    payload.mkdir()
    monkeypatch.setattr(U, "download", lambda a, dest_dir=None, on_progress=None:
                        str(payload / "hub-moon-1.2.0-1-x86_64.pkg.tar.zst"))
    path, command = U.fetch({"install_kind": "pacman", "asset": {}},
                            dest_dir=str(payload))
    assert command.startswith("sudo pacman -U '")
    assert command.endswith("'")


# ── getting off the beta channel ─────────────────────────────────────────────

def test_switching_to_stable_from_a_beta_offers_the_way_back(cached, monkeypatch):
    """Without this a beta tester is stranded: stable is older than what they run, so
    the ordinary "is it newer" test says nothing is available and the channel switch
    does nothing at all."""
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.1.0"))
    got = U.check("stable", current="1.2.0b1")
    assert got is not None
    assert got["version"] == "1.1.0"
    assert got["rollback"] is True


def test_a_normal_update_is_not_flagged_as_a_rollback(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.3.0"))
    assert U.check("stable", current="1.2.0b1")["rollback"] is False


def test_stable_users_are_never_offered_a_downgrade(cached, monkeypatch):
    """The rollback path must not fire for somebody already on a final release."""
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.1.0"))
    assert U.check("stable", current="1.2.0") is None


def test_the_beta_channel_never_rolls_back(cached, monkeypatch):
    """Staying on beta means staying on beta; only switching to stable goes back."""
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("beta", manifest("1.1.0"))
    assert U.check("beta", current="1.2.0b1") is None


def test_a_beta_superseded_by_its_own_release_is_a_normal_update(cached, monkeypatch):
    """Once 1.2.0 final ships, a 1.2.0b1 user gets an ordinary upgrade, not a rollback."""
    monkeypatch.setattr(U, "install_kind", lambda: "appimage")
    cached("stable", manifest("1.2.0"))
    got = U.check("stable", current="1.2.0b1")
    assert got["version"] == "1.2.0" and got["rollback"] is False


# ── installing with authorisation ────────────────────────────────────────────

def test_only_package_installs_are_elevatable(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    for kind in U.FETCHABLE:
        assert U.can_elevate(kind), kind
    for kind in ("appimage", "linux-tarball", "source", "pip", "nix"):
        assert not U.can_elevate(kind), kind


def test_no_polkit_means_no_elevation(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: None if n == "pkexec" else "/x/" + n)
    assert not U.can_elevate("pacman")


def test_no_package_manager_means_no_elevation(monkeypatch):
    """apt-get is not on an Arch box, and pacman is not on a Debian one."""
    monkeypatch.setattr(U.shutil, "which",
                        lambda n: "/usr/bin/pkexec" if n == "pkexec" else None)
    assert not U.can_elevate("pacman")


def test_the_elevated_command_is_argv_never_a_shell_string(monkeypatch):
    """A path is data. Passing it through a shell would let a filename become an
    argument, or an argument become a command."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["shell"] = kw.get("shell", False)
        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(U.subprocess, "run", fake_run)
    ok, _ = U.install_elevated("pacman", "/tmp/a b;rm -rf ~/hub-moon.pkg.tar.zst")
    assert ok is True
    assert isinstance(seen["argv"], list) and seen["shell"] is False
    assert seen["argv"][0] == "pkexec"
    assert seen["argv"][-1] == "/tmp/a b;rm -rf ~/hub-moon.pkg.tar.zst"


def test_declining_the_prompt_is_not_an_error(monkeypatch):
    """Saying no is an ordinary answer — the caller falls back to showing a command."""
    class R:
        returncode = 126
        stdout = stderr = ""
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **k: R())
    ok, message = U.install_elevated("pacman", "/tmp/x.pkg.tar.zst")
    assert ok is False and "ancel" in message


def test_a_failing_package_manager_reports_its_own_last_line(monkeypatch):
    class R:
        returncode = 1
        stdout = ""
        stderr = "error: could not open file\nerror: failed to commit transaction"
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **k: R())
    ok, message = U.install_elevated("pacman", "/tmp/x.pkg.tar.zst")
    assert ok is False and "failed to commit" in message


def test_elevation_is_never_offered_for_something_self_updatable(monkeypatch):
    """The two paths must stay disjoint: a kind that replaces its own files must
    never also try to run a package manager over itself."""
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    for kind in U.SELF_UPDATABLE:
        assert not U.can_elevate(kind), kind
    assert not (set(U.ELEVATED_ARGV) & U.SELF_UPDATABLE)


def test_a_release_without_a_package_says_so_rather_than_blaming_the_manager(cached, monkeypatch):
    """1.1.0 predates the Arch package, so a pacman install has nothing to download.
    Saying "Hub Moon will not replace its own files" is true but not the reason, and
    sends the reader looking for a setting that does not exist."""
    monkeypatch.setattr(U, "install_kind", lambda: "pacman")
    cached("stable", manifest("1.3.0"))          # appimage only, no arch-package
    got = U.check("stable", current="1.2.0b1")
    assert got["can_fetch"] is False and got["can_elevate"] is False
    assert "does not include a package" in got["reason"]
    assert "will not replace" not in got["reason"]
    assert got["hint"] == U.MANUAL_HINT["pacman"]


def test_a_release_with_a_package_still_offers_the_button(cached, monkeypatch):
    monkeypatch.setattr(U, "install_kind", lambda: "pacman")
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    cached("stable", manifest("1.3.0", **{"arch-package": {
        "url": "https://example.invalid/x.pkg.tar.zst", "sha256": "a" * 64, "size": 9}}))
    got = U.check("stable", current="1.2.0b1")
    assert got["can_fetch"] is True and got["can_elevate"] is True


# ── a frozen bundle a package manager owns ───────────────────────────────────

def _frozen_linux(monkeypatch, exe="/opt/hub-moon/hub-moon"):
    monkeypatch.setattr(U.sys, "frozen", True, raising=False)
    monkeypatch.setattr(U.sys, "executable", exe)
    monkeypatch.setattr(U.sys, "platform", "linux")
    monkeypatch.delenv("APPIMAGE", raising=False)


@pytest.mark.parametrize("owner", ["pacman", "deb", "rpm"])
def test_a_packaged_frozen_bundle_is_not_called_a_tarball(monkeypatch, owner):
    """The .deb, the .rpm and the Arch package all ship the same PyInstaller tree
    into /opt. Calling that "linux-tarball" put it in SELF_UPDATABLE, so the app
    would overwrite files the package manager owns and leave its database describing
    something no longer on disk. Observed: a pacman install logging
    `install: linux-tarball`."""
    _frozen_linux(monkeypatch)
    monkeypatch.setattr(U, "_package_owner", lambda p: owner)
    kind = U.install_kind()
    assert kind == owner
    assert kind not in U.SELF_UPDATABLE
    assert kind in U.FETCHABLE


def test_an_unowned_frozen_bundle_is_still_a_tarball(monkeypatch):
    """Somebody who unpacked the tarball into /opt by hand owns their own files."""
    _frozen_linux(monkeypatch)
    monkeypatch.setattr(U, "_package_owner", lambda p: None)
    assert U.install_kind() == "linux-tarball"
    assert "linux-tarball" in U.SELF_UPDATABLE


def test_the_appimage_is_never_mistaken_for_a_package(monkeypatch):
    """APPIMAGE is set by the runtime and is checked before anything else — an
    AppImage run on a machine whose /opt happens to be packaged is still an AppImage."""
    _frozen_linux(monkeypatch)
    monkeypatch.setenv("APPIMAGE", "/home/me/HubMoon.AppImage")
    monkeypatch.setattr(U, "_package_owner", lambda p: "pacman")
    assert U.install_kind() == "appimage"


# ── the udev rule ────────────────────────────────────────────────────────────

def test_the_udev_rule_covers_both_hidapi_backends():
    """hidapi has two Linux backends and this project ships builds that use both.

    A distro's python-hidapi is hidraw-backed and opens /dev/hidrawN; the manylinux
    wheel every PyInstaller build here bundles is libusb-backed and opens
    /dev/bus/usb/BBB/DDD instead, needing write access to it. Shipping only the
    hidraw line meant every binary release — tarball, AppImage, deb, rpm, Arch —
    reported no DAC while a source install on the same machine worked.
    """
    path = os.path.join(ROOT, "packaging", "70-moondrop.rules")
    with open(path, encoding="utf-8") as fh:
        rules = [ln.strip() for ln in fh if ln.strip()
                 and not ln.lstrip().startswith("#")]
    subsystems = {ln.split('SUBSYSTEM=="')[1].split('"')[0] for ln in rules}
    assert subsystems == {"hidraw", "usb"}, subsystems
    for line in rules:
        assert 'ATTRS{idVendor}=="35d8"' in line, line
        assert 'TAG+="uaccess"' in line, line
