"""Update checking, and the parts of an install that can safely replace themselves.

Two things live here, and keeping them apart matters:

* **Checking** is the same everywhere. One HTTPS GET for a small manifest, cached on
  disk, compared against ``moondrop_control.__version__``. It never writes anything
  outside the cache directory and it is safe on every install there is.

* **Applying** is not the same everywhere, and on most installs it must not happen at
  all. Hub Moon ships as a Windows installer, a Windows zip, a macOS ``.app``, an
  AppImage, a tarball, a ``.deb``, an ``.rpm``, an Arch package, a Nix derivation and
  a wheel. Five of those own their own files and can be replaced in place. The other
  five are owned by a package manager, and an app that overwrites files ``dpkg``
  believes it owns has corrupted the system it was trying to update. So
  :func:`install_kind` works out how this copy actually got here, and
  :func:`can_self_update` is the gate — everything it says no to gets a command to
  copy instead of a button that lies.

**What this trusts.** The manifest is fetched over TLS from a host the author
controls, and every asset is checked against the SHA-256 recorded in it. That is the
whole security model: if the manifest is authentic, the download is. There is no code
signature on any platform — the Windows installer is unsigned and the macOS bundle is
ad-hoc signed — so this is not protection against an attacker who can serve you a
manifest. It is protection against a corrupted or truncated download, which is the
failure that actually happens.

**Channels.** ``stable`` is the tagged release; ``beta`` is whatever is being tested
next, published from the ``test`` branch. They are separate manifests rather than one
file with two keys, so a broken beta manifest cannot take stable down with it.

Going *backwards* is offered in exactly one situation: you are running a pre-release
and you switch to the stable channel. That is a person asking to get off the betas,
and stable being older than what they have is the normal state of affairs — so the
"is it newer" test would otherwise strand them. It comes back flagged ``rollback`` so
the UI can call it a return to stable instead of an update, because installing 1.1.0
over a 1.2.0b1 is a downgrade and saying otherwise is a lie they find out about
later. Nothing else here ever proposes going back.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

try:
    import moondrop_control as mc
except ImportError:  # running from a checkout, gui/ is a subdirectory of the repo
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

# Per channel: the site first, the git branch second. The site is a Cloudflare static
# asset and answers fastest; raw.githubusercontent is the fallback for the day the
# domain is the thing that is broken. `main` carries stable, `test` carries beta —
# the branch that holds the code holds the manifest that announces it.
MANIFESTS = {
    "stable": (
        "https://hubmoon.miyukivigil.tech/update.json",
        "https://raw.githubusercontent.com/MiyukiVigil/Hub_Moon/main/packaging/update.json",
    ),
    "beta": (
        "https://hubmoon.miyukivigil.tech/update-beta.json",
        "https://raw.githubusercontent.com/MiyukiVigil/Hub_Moon/test/packaging/update-beta.json",
    ),
}
CHANNELS = ("stable", "beta")

RELEASES_URL = "https://github.com/MiyukiVigil/Hub_Moon/releases"

# A day between checks. The manifest is a few hundred bytes, but a program that
# reaches for the network on every launch is a program people turn off.
CHECK_TTL = 24 * 3600
NET_TIMEOUT = 15

# Opt out without opening the app — for anyone scripting a deployment, and for the
# distro packagers who would otherwise have to patch it out.
ENV_OPT_OUT = "HUB_MOON_NO_UPDATE_CHECK"


# ── version ordering ─────────────────────────────────────────────────────────

_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2}
_VERSION_RE = re.compile(
    r"^(\d+(?:\.\d+)*)"                       # 1 / 1.2 / 1.2.3
    # Longest first, and that ordering is load-bearing: with `b` ahead of `beta`,
    # "1.1.0-beta.2" matches `b` and then fails to reach the number, so every beta
    # of a release compares equal and no beta ever supersedes another.
    r"(?:[-_.]?(alpha|beta|pre|rc|a|b|c)[-_.]?(\d+)?)?",
    re.IGNORECASE)


def parse_version(text):
    """Enough of PEP 440 to order our own tags, and no more.

    The one rule worth stating: a pre-release sorts *below* the release it leads to,
    so 1.2.0-beta.3 < 1.2.0. Getting that backwards would push every beta tester
    onto a build older than the one they are running and call it an update.
    """
    if not text:
        return None
    m = _VERSION_RE.match(str(text).strip().lstrip("vV"))
    if not m:
        return None
    rel = tuple(int(p) for p in m.group(1).split("."))
    rel = rel + (0,) * (3 - len(rel)) if len(rel) < 3 else rel
    if m.group(2):
        return (rel, 0, _PRE_RANK.get(m.group(2).lower(), 1), int(m.group(3) or 0))
    return (rel, 1, 0, 0)          # a final release outranks every pre-release of it


def is_newer(candidate, current):
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


# ── which Hub Moon is this ───────────────────────────────────────────────────

def _under(path, *prefixes):
    p = os.path.abspath(path)
    return any(p.startswith(os.path.abspath(x) + os.sep) for x in prefixes)


def _win_installed_by_inno():
    """Did the Inno installer put us here? Its uninstall key is the only honest
    answer — a path check would call a zip extracted into Program Files an install."""
    try:
        import winreg
    except ImportError:
        return False
    key = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
           r"\{9BAE6DAA-DA8D-421C-A240-7898856F9A53}_is1")
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for flag in (0, getattr(winreg, "KEY_WOW64_64KEY", 0)):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | flag):
                    return True
            except OSError:
                continue
    return False


def install_kind():
    """How this copy of Hub Moon got onto this machine.

    Ordered most specific first. The frozen cases are decided by what is on disk
    around the executable; the unfrozen ones by where the Python module was imported
    from, which is what actually distinguishes a distro package from a pipx venv.
    """
    if os.environ.get("APPIMAGE"):
        return "appimage"                      # set by the AppImage runtime itself

    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        if sys.platform == "win32":
            return "windows-installer" if _win_installed_by_inno() else "windows-portable"
        if sys.platform == "darwin":
            return "macos-app" if ".app/Contents/" in exe else "macos-bundle"
        return "linux-tarball"

    here = os.path.abspath(mc.__file__)
    # Checked before anything else unfrozen: a repo cloned into the home directory
    # otherwise looks exactly like a --user pip install, and offering to replace a
    # developer's working tree with a release tarball would be a memorable bug.
    if os.path.isdir(os.path.join(os.path.dirname(here), ".git")):
        return "source"
    if "/nix/store/" in here.replace("\\", "/"):
        return "nix"
    if _under(here, "/usr/lib", "/usr/lib64", "/usr/share", "/usr/local/lib"):
        # A system-wide install we did not make ourselves: deb, rpm or pacman. Which
        # one only matters for the sentence we print, so ask the package managers.
        for kind, argv in (("deb", ["dpkg", "-S", here]),
                           ("rpm", ["rpm", "-qf", here]),
                           ("pacman", ["pacman", "-Qo", here])):
            try:
                if subprocess.run(argv, capture_output=True,
                                  timeout=5).returncode == 0:
                    return kind
            except (OSError, subprocess.SubprocessError):
                continue
        return "system"
    if os.sep + "pipx" + os.sep in here or ".local/pipx" in here.replace("\\", "/"):
        return "pipx"
    slash = here.replace("\\", "/")
    if "site-packages/" in slash or "dist-packages/" in slash or sys.prefix != sys.base_prefix:
        return "pip"
    return "unknown"


#: Install kinds that own their own files and can be replaced in place.
SELF_UPDATABLE = frozenset({
    "windows-installer", "windows-portable", "macos-app", "appimage", "linux-tarball",
})

#: Manifest asset key per install kind. Two kinds share the macOS disk image.
ASSET_FOR = {
    "windows-installer": "windows-installer",
    "windows-portable": "windows-portable",
    "macos-app": "macos-dmg",
    "macos-bundle": "macos-dmg",
    "appimage": "appimage",
    "linux-tarball": "linux-tarball",
    # Package-manager installs. Present so the *download* can be offered — never so
    # the files can be replaced; see FETCHABLE.
    "pacman": "arch-package",
    "deb": "deb-package",
    "rpm": "rpm-package",
}

#: Kinds Hub Moon must not install, but **can** fetch and verify.
#:
#: The middle ground, and the honest one. Installing a system package needs root,
#: which this app will never ask for. Downloading the right file and checking its
#: SHA-256 needs nothing at all, and it is the tedious half — so the app does that
#: part and hands over one command with the real path already in it, instead of
#: naming a file and leaving somebody to go and find it.
FETCHABLE = frozenset({"pacman", "deb", "rpm"})

#: The command that installs a fetched package, given its path.
INSTALL_COMMAND = {
    "pacman": "sudo pacman -U %s",
    "deb": "sudo apt install %s",
    "rpm": "sudo dnf install %s",
}

#: What to tell someone whose install we must not touch.
#
#: These have to be commands that *work*, which is a sharper constraint than it looks.
#: Hub Moon is published on **neither PyPI nor the AUR** — 1.1.0 printed
#: ``pip install --upgrade hub-moon`` and ``yay -Syu hub-moon``, and both fail with
#: "no matching distribution" / "target not found". There is no apt or dnf repository
#: either: the .deb and .rpm are files you download, so ``apt install --only-upgrade``
#: had nothing to look in. Every line below mirrors what the install guide actually
#: tells people to run, and the ones needing a file first are listed in NEEDS_DOWNLOAD.
MANUAL_HINT = {
    "deb": "sudo apt install ./hub-moon_<version>_amd64.deb",
    "rpm": "sudo dnf install ./hub-moon-<version>.x86_64.rpm",
    "pacman": "makepkg -si        # from packaging/PKGBUILD in the repo",
    "nix": "nix profile upgrade hub-moon",
    "pipx": "pipx upgrade hub-moon",
    "pip": "pip install --upgrade 'hub-moon[gui] @ "
           "git+https://github.com/MiyukiVigil/Hub_Moon'",
    "system": "update it with whatever package manager installed it",
    "source": "git pull",
    "unknown": "download the new build from " + RELEASES_URL,
}

#: Kinds whose command operates on a file that has to be fetched from the release
#: page first — so the instruction has to say that, not just print the command.
NEEDS_DOWNLOAD = frozenset({"deb", "rpm"})


def asset_key(kind=None):
    """The manifest key for this install.

    macOS ships one .dmg per architecture, and handing an Intel Mac an arm64 bundle
    produces an app that installs perfectly and then will not launch — so the machine
    is part of the key there. `platform.machine()` reports the *interpreter's*
    architecture, which is the right one: a Python running under Rosetta is an x86_64
    build and wants the x86_64 download.
    """
    base = ASSET_FOR.get(kind or install_kind())
    if base == "macos-dmg":
        arm = platform.machine().lower() in ("arm64", "aarch64")
        return base + ("-arm64" if arm else "-x86_64")
    return base


def can_self_update(kind=None):
    return (kind or install_kind()) in SELF_UPDATABLE


def describe_install(kind=None):
    """One line for the settings panel: what this is, and how it updates."""
    kind = kind or install_kind()
    pretty = {
        "windows-installer": "installed with the Windows installer",
        "windows-portable": "Windows portable build",
        "macos-app": "macOS application bundle",
        "macos-bundle": "macOS build",
        "appimage": "AppImage",
        "linux-tarball": "portable Linux build",
        "deb": "Debian package", "rpm": "RPM package", "pacman": "Arch package",
        "nix": "Nix package", "pipx": "pipx install", "pip": "pip install",
        "system": "system package", "source": "source checkout",
        "unknown": "unrecognised install",
    }.get(kind, kind)
    if kind in SELF_UPDATABLE:
        return "%s — Hub Moon can update itself." % pretty
    return "%s — update it with:  %s" % (pretty, MANUAL_HINT.get(kind, ""))


# ── the check ────────────────────────────────────────────────────────────────

def _get(url, timeout=NET_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": mc.HUB_UA})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=mc._hub_ssl_context()) as r:
        return r.read()


def _cache_path(channel):
    return os.path.join(mc.cache_dir(), "update-%s.json" % channel)


def _read_cache(channel, ttl):
    path = _cache_path(channel)
    try:
        if time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(channel, data):
    try:
        os.makedirs(mc.cache_dir(), exist_ok=True)
        path = _cache_path(channel)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def fetch_manifest(channel, force=False, ttl=CHECK_TTL):
    """The channel's manifest, from cache when it is fresh enough.

    Tries each mirror in turn and keeps the first one that parses. A mirror that
    answers with something that is not a manifest is treated as a mirror that did not
    answer, because that is what a captive portal looks like.
    """
    if channel not in MANIFESTS:
        channel = "stable"
    if not force:
        cached = _read_cache(channel, ttl)
        if cached:
            return cached
    last = None
    for url in MANIFESTS[channel]:
        try:
            data = json.loads(_get(url).decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            last = exc
            continue
        if isinstance(data, dict) and data.get("version"):
            _write_cache(channel, data)
            return data
        last = ValueError("manifest at %s has no version" % url)
    if last:
        raise last
    return None


def bundled_notes(version=None):
    """What changed in `version`, from the notes compiled into this build.

    `gui/notes.py` is generated from CHANGELOG.md by tools/build-release-notes.py, so
    every build knows what is in it regardless of where it came from. That matters for
    exactly the builds a manifest cannot describe: a `makepkg -si` from the repo, a
    beta whose release has not been published, a wheel installed from a git URL.
    """
    want = parse_version(version or mc.__version__)
    if not want:
        return []
    try:
        from .notes import NOTES
    except Exception:                       # pragma: no cover - generated, always there
        return []
    for tag, lines in NOTES.items():
        if parse_version(tag) == want:
            return [str(n) for n in lines]
    return []


def release_notes(channel="stable", version=None):
    """What changed in `version` — the manifest on disk first, then this build's own.

    The manifest is preferred because it is written from the GitHub release body, so
    it can say things decided after the code was frozen. It is read from the cache
    rather than fetched, and that is the point: this is called the first time the app
    runs *after* an update, when the manifest describing that version is exactly what
    the check downloaded before installing it. So What's New works with no network at
    all — the common case, because the machine has just restarted.

    Falling back to the compiled-in notes is what makes this work off the beta channel
    at all. A beta is very often a build with no published release behind it, and
    "there is a new version" followed by an empty panel is worse than no panel.

    Returns [] when there is nothing to say, never raising.
    """
    want = parse_version(version or mc.__version__)
    for chan in ([channel] + [c for c in CHANNELS if c != channel]):
        data = _read_cache(chan, ttl=float("inf"))
        # Compared as parsed versions, not as strings. A tag of `v1.2.0-beta.1` and a
        # `__version__` of `1.2.0b1` are the same release written two ways — PEP 440
        # spells it one way and Arch's pkgver forbids the hyphen in the other — and
        # matching on text would quietly show an empty What's New for one of them.
        if data and want and parse_version(data.get("version")) == want:
            notes = [str(n) for n in (data.get("notes") or [])]
            if notes:
                return notes
            break        # the right manifest, and it has nothing to say
    return bundled_notes(version)


def check(channel="stable", current=None, force=False, ttl=CHECK_TTL):
    """Is there something newer? Returns a dict the UI can render, or None.

    ``None`` means exactly one thing: **there is nothing newer**. A manifest that
    could not be fetched or parsed *raises*, and it is the caller that decides
    whether to say so — a background check almost never should, a check the user
    clicked always should.

    That distinction is not pedantry. Folding the two together is what made an
    offline launch report "Hub Moon 1.1.0 is the latest on stable", which is a
    sentence the program had no evidence for.
    """
    if os.environ.get(ENV_OPT_OUT):
        return None
    current = current or mc.__version__
    man = fetch_manifest(channel, force=force, ttl=ttl)
    if not man:
        return None

    offered = man.get("version")
    forward = is_newer(offered, current)
    # Leaving the beta channel. Somebody running 1.2.0b1 who switches to stable is
    # asking to get off the betas, and stable is *older* than what they have — so the
    # normal "is it newer" test says no and strands them on a pre-release with no way
    # back. That is the one case where going backwards is the thing being asked for,
    # and it is offered as an explicit return rather than dressed up as an update.
    running = parse_version(current)
    rollback = bool(
        channel == "stable"
        and running and running[1] == 0          # what is running is a pre-release
        and offered and not forward
        and parse_version(offered))
    if not forward and not rollback:
        return None
    kind = install_kind()
    assets = man.get("assets") or {}
    key = asset_key(kind)
    # The arch-suffixed key first, the bare one second — a manifest that ships a
    # single universal .dmg is still readable rather than looking like no download.
    asset = assets.get(key) or assets.get(ASSET_FOR.get(kind, ""))
    can = bool(asset) and kind in SELF_UPDATABLE

    # There are two different reasons this app might not install an update for you,
    # and telling a Mac user their .app is "managed by a package manager" because a
    # release happened to be missing a .dmg would be a confusing lie. Say which.
    fetchable = bool(asset) and kind in FETCHABLE

    if can:
        hint = reason = ""
    elif fetchable:
        hint = INSTALL_COMMAND.get(kind, "") % "<the downloaded file>"
        if can_elevate(kind):
            reason = ("Installing a system package needs root. Hub Moon will download "
                      "it, check it, and ask your desktop for permission — the "
                      "password goes to the system, never through this app.")
        else:
            reason = ("Installing a system package needs root, and there is no polkit "
                      "agent here to ask with. Hub Moon can download and verify the "
                      "right one, and then you run:")
    elif kind in SELF_UPDATABLE:
        hint = RELEASES_URL
        reason = ("That release has no download for this platform yet — the build may "
                  "still be running. You can get it from:")
    elif kind in FETCHABLE:
        # A package-manager install *with no package in that release*. Blaming the
        # package manager here would be misleading: the reason there is no button is
        # that there is nothing to download, which is what an older release looks
        # like once a new package format has been added to the build.
        hint = MANUAL_HINT.get(kind, RELEASES_URL)
        reason = ("That release does not include a package for this platform, so "
                  "there is nothing to download. Update it with:")
    elif kind in NEEDS_DOWNLOAD:
        hint = MANUAL_HINT[kind]
        reason = ("This copy is managed by a package manager, so Hub Moon will not "
                  "replace its own files. Download the new package from %s, then:"
                  % RELEASES_URL)
    else:
        hint = MANUAL_HINT.get(kind, RELEASES_URL)
        reason = ("This copy is managed by a package manager, so Hub Moon will not "
                  "replace its own files. Update it with:")

    return {
        "version": man["version"],
        # True when this is a return to stable from a pre-release, not an upgrade.
        # The UI has to say so: "install 1.1.0" over a 1.2.0b1 is a downgrade, and
        # calling it an update would be a lie the user finds out about afterwards.
        "rollback": rollback,
        "notes": [str(n) for n in (man.get("notes") or [])],
        "channel": channel,
        "date": man.get("date", ""),
        "summary": man.get("summary", ""),
        "notes_url": man.get("notes_url") or RELEASES_URL,
        "install_kind": kind,
        "can_install": can,
        "can_fetch": fetchable,
        "can_elevate": fetchable and can_elevate(kind),
        "asset": asset,
        "hint": hint,
        "reason": reason,
    }


# ── downloading ──────────────────────────────────────────────────────────────

class UpdateError(RuntimeError):
    pass


def download(asset, dest_dir=None, on_progress=None):
    """Fetch an asset and verify it. Returns the path, or raises UpdateError.

    The hash is not optional. An asset with no ``sha256`` in the manifest is refused
    rather than trusted, because the only thing standing between a truncated download
    and an installer being run is this check.
    """
    url, want = asset.get("url"), (asset.get("sha256") or "").lower()
    if not url:
        raise UpdateError("that build has no download for this platform")
    if len(want) != 64:
        raise UpdateError("that build has no checksum — refusing to install it")

    dest_dir = dest_dir or tempfile.mkdtemp(prefix="hubmoon-update-")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, os.path.basename(url.split("?")[0]) or "hubmoon-update")

    digest = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": mc.HUB_UA})
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT,
                                    context=mc._hub_ssl_context()) as r, \
                open(path, "wb") as fh:
            total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
            done = 0
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError("download failed: %s" % exc) from exc

    got = digest.hexdigest()
    if got != want:
        try:
            os.remove(path)
        except OSError:
            pass
        raise UpdateError("checksum mismatch — the download was corrupted or tampered "
                          "with (expected %s…, got %s…)" % (want[:12], got[:12]))
    return path


# ── applying ─────────────────────────────────────────────────────────────────
#
# Every path below has the same shape, because the constraint is the same: a running
# program cannot replace its own files while it is using them. So each one writes a
# small helper script, starts it detached, and returns — the caller then quits, the
# helper waits for this process to actually be gone, swaps the files, and starts the
# new build. The helper deletes itself last.

def _detach(argv, cwd=None):
    kw = {"cwd": cwd, "close_fds": True}
    if sys.platform == "win32":
        # Without this a windowed build flashes a console window at the user on the
        # way out, which reads as a crash rather than an update.
        kw["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                               | getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        kw["start_new_session"] = True
    subprocess.Popen(argv, **kw)


def _write_script(text, suffix):
    fd, path = tempfile.mkstemp(prefix="hubmoon-apply-", suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return path


def _sh_wait_for_exit(pid):
    """POSIX: block until `pid` is gone, then give the filesystem a beat."""
    return ('i=0\n'
            'while kill -0 %d 2>/dev/null && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done\n'
            'sleep 0.4\n' % pid)


def apply_appimage(path):
    """The easy one: an AppImage is a single file, so the update is a rename.

    Written next to the original rather than into it, then moved over — a move within
    a filesystem is atomic, so an interrupted update leaves the old AppImage intact
    rather than half of a new one.
    """
    target = os.environ.get("APPIMAGE")
    if not target or not os.path.exists(target):
        raise UpdateError("cannot find the running AppImage to replace")
    target = os.path.abspath(target)
    if not os.access(os.path.dirname(target), os.W_OK):
        raise UpdateError("no permission to write to %s" % os.path.dirname(target))
    os.chmod(path, 0o755)
    script = _write_script(
        "#!/bin/sh\n" + _sh_wait_for_exit(os.getpid()) +
        'mv -f "%s" "%s" || exit 1\n' % (path, target) +
        'chmod +x "%s"\n' % target +
        '"%s" &\n' % target +
        'rm -f "$0"\n', ".sh")
    _detach(["/bin/sh", script])


def apply_linux_tarball(path):
    """Swap the unpacked directory the frozen binary lives in."""
    root = os.path.dirname(os.path.abspath(sys.executable))
    parent = os.path.dirname(root)
    if not os.access(parent, os.W_OK):
        raise UpdateError("no permission to replace %s" % root)
    staging = tempfile.mkdtemp(prefix="hubmoon-new-", dir=parent)
    shutil.unpack_archive(path, staging)
    # the tarball holds a single `hub-moon/` directory; use it if it is there
    entries = [os.path.join(staging, e) for e in os.listdir(staging)]
    newroot = entries[0] if len(entries) == 1 and os.path.isdir(entries[0]) else staging
    script = _write_script(
        "#!/bin/sh\n" + _sh_wait_for_exit(os.getpid()) +
        'rm -rf "%s.old" && mv "%s" "%s.old" || exit 1\n' % (root, root, root) +
        'mv "%s" "%s" || { mv "%s.old" "%s"; exit 1; }\n' % (newroot, root, root, root) +
        'rm -rf "%s.old" "%s"\n' % (root, staging) +
        '"%s" &\n' % os.path.join(root, os.path.basename(sys.executable)) +
        'rm -f "$0"\n', ".sh")
    _detach(["/bin/sh", script])


def apply_macos_app(path):
    """Mount the .dmg, take the bundle out, and swap it for the running one.

    Two things are done to the copy before it is installed, and both are about
    Gatekeeper rather than about the update. The quarantine attribute is stripped,
    because a bundle marked as quarantined that is *not* notarized will simply refuse
    to open — the user would be left with an app that no longer starts and no way to
    tell why. It is then re-signed ad-hoc, because copying a bundle around breaks the
    ad-hoc signature it shipped with, and a broken signature is refused where an
    ad-hoc one is merely warned about. This is the same posture as the .dmg the user
    would have installed by hand; it is not a substitute for notarization.
    """
    mount = tempfile.mkdtemp(prefix="hubmoon-dmg-")
    try:
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly",
                        "-mountpoint", mount, path], check=True,
                       capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError("could not open the disk image: %s" % exc) from exc

    try:
        src = next((os.path.join(mount, e) for e in os.listdir(mount)
                    if e.endswith(".app")), None)
        if not src:
            raise UpdateError("no application found in the disk image")

        exe = os.path.abspath(sys.executable)
        target = exe.split(".app/Contents/")[0] + ".app"
        staging = tempfile.mkdtemp(prefix="hubmoon-new-")
        new = os.path.join(staging, os.path.basename(target))
        subprocess.run(["ditto", src, new], check=True, capture_output=True, timeout=300)
    finally:
        subprocess.run(["hdiutil", "detach", mount, "-force"],
                       capture_output=True, check=False)

    subprocess.run(["xattr", "-dr", "com.apple.quarantine", new],
                   capture_output=True, check=False)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", new],
                   capture_output=True, check=False)

    swap = ('rm -rf "%s.old" && mv "%s" "%s.old" || exit 1\n' % (target, target, target) +
            'ditto "%s" "%s" || { mv "%s.old" "%s"; exit 1; }\n'
            % (new, target, target, target) +
            'rm -rf "%s.old"\n' % target)
    # /Applications is writable by admins without a prompt on a normal Mac; ask for
    # rights only if it turns out not to be, rather than prompting every user.
    if not os.access(os.path.dirname(target), os.W_OK):
        inner = _write_script("#!/bin/sh\n" + swap, ".sh")
        swap = ('osascript -e %s\n'
                % json.dumps('do shell script "/bin/sh %s" with administrator '
                             'privileges' % inner))
    script = _write_script(
        "#!/bin/sh\n" + _sh_wait_for_exit(os.getpid()) + swap +
        'rm -rf "%s"\n' % staging +
        'open "%s"\n' % target +
        'rm -f "$0"\n', ".sh")
    _detach(["/bin/sh", script])


def apply_windows_installer(path):
    """Hand over to Inno, which already knows how to upgrade itself.

    The installer carries the same AppId as the one that is installed, so it upgrades
    in place, keeps the Start-menu entries and the uninstaller, and elevates itself —
    which is why nothing here tries to write to Program Files. /SILENT shows the
    progress bar without the wizard; the app is closed by us a moment later, and
    /RESTARTAPPLICATIONS brings it back.
    """
    _detach([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
             "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"])


def apply_windows_portable(path):
    """Swap the extracted folder, from a batch file that outlives us."""
    root = os.path.dirname(os.path.abspath(sys.executable))
    parent = os.path.dirname(root)
    staging = tempfile.mkdtemp(prefix="hubmoon-new-", dir=parent)
    shutil.unpack_archive(path, staging)
    entries = [os.path.join(staging, e) for e in os.listdir(staging)]
    newroot = entries[0] if len(entries) == 1 and os.path.isdir(entries[0]) else staging

    # No `taskkill`: we are quitting on our own. The loop is what waits for Windows to
    # release the .exe, which it does a moment after the process is actually gone.
    # `del "%~f0"` behind `(goto) 2>nul` is the one reliable way a .cmd deletes itself.
    cmd = "\r\n".join([
        "@echo off",
        "setlocal enabledelayedexpansion",
        "set /a n=0",
        ":wait",
        'tasklist /fi "PID eq {pid}" 2>nul | find "{pid}" >nul || goto swap',
        "ping -n 2 127.0.0.1 >nul",
        "set /a n+=1",
        "if !n! lss 60 goto wait",
        ":swap",
        'rmdir /s /q "{root}.old" 2>nul',
        'move "{root}" "{root}.old" || exit /b 1',
        'move "{new}" "{root}" || (move "{root}.old" "{root}" & exit /b 1)',
        'rmdir /s /q "{root}.old" 2>nul',
        'rmdir /s /q "{staging}" 2>nul',
        'start "" "{exe}"',
        '(goto) 2>nul & del "%~f0"',
        "",
    ]).format(pid=os.getpid(), root=root, new=newroot, staging=staging,
              exe=os.path.join(root, os.path.basename(sys.executable)))
    _detach(["cmd", "/c", _write_script(cmd, ".cmd")])


#: install kind -> the function that installs its asset
APPLIERS = {
    "windows-installer": apply_windows_installer,
    "windows-portable": apply_windows_portable,
    "macos-app": apply_macos_app,
    "macos-bundle": apply_macos_app,
    "appimage": apply_appimage,
    "linux-tarball": apply_linux_tarball,
}


def fetch(update, on_progress=None, dest_dir=None):
    """Download and verify a package this app must not install itself.

    Lands in the user's Downloads directory where they can find it — not a temp
    directory that gets swept, because the whole point is that *they* run the next
    command, possibly not immediately. Returns ``(path, command)``.
    """
    if dest_dir is None:
        downloads = os.path.expanduser("~/Downloads")
        dest_dir = downloads if os.path.isdir(downloads) else os.path.expanduser("~")
    path = download(update["asset"], dest_dir=dest_dir, on_progress=on_progress)
    kind = update.get("install_kind") or install_kind()
    template = INSTALL_COMMAND.get(kind)
    if not template:
        raise UpdateError("no install command is known for a %s install" % kind)
    # Quoted: a Downloads path can contain spaces, and a command that has to be
    # edited before it runs is not a command that was handed over.
    return path, template % ("'%s'" % path if " " in path else path)


# ── installing a system package, with authorisation ──────────────────────────
#
# This is the one place Hub Moon runs anything as root, and the rules it follows are
# worth stating because they are what make it defensible:
#
# * The file is one **this** program downloaded and checked against a SHA-256 from a
#   manifest fetched over TLS. Nothing else is ever passed.
# * It is an **argv list**, never a shell string, so a path cannot become an argument
#   and an argument cannot become a command.
# * Authorisation goes through **polkit**, which means the desktop's own agent draws
#   the prompt, the password never passes through this process, and the policy of who
#   may do it belongs to the system rather than to this app.
# * Declining is a normal outcome, not an error. The command is still shown so it can
#   be run by hand.

#: The exact argv, minus the elevation wrapper, that installs a fetched package.
ELEVATED_ARGV = {
    "pacman": ["pacman", "-U", "--noconfirm"],
    "deb": ["apt-get", "install", "-y", "--allow-downgrades"],
    "rpm": ["dnf", "install", "-y", "--allowerasing"],
}

#: pkexec's own exit codes for "the user said no" and "there is no agent".
_PKEXEC_DISMISSED = 126
_PKEXEC_NOT_FOUND = 127


def can_elevate(kind=None):
    """Can we ask for authorisation graphically for this kind of install?"""
    kind = kind or install_kind()
    if kind not in ELEVATED_ARGV:
        return False
    tool = ELEVATED_ARGV[kind][0]
    return bool(shutil.which("pkexec") and shutil.which(tool))


def install_elevated(kind, path, timeout=1800):
    """Install a fetched package as root, prompting through polkit.

    Returns ``(ok, message)``. ``ok`` is False for a decline as well as a failure —
    the caller shows the command either way — and `message` says which, because
    "you cancelled" and "the package manager refused" need different next steps.
    """
    argv = ELEVATED_ARGV.get(kind)
    if not argv:
        return False, "This kind of install cannot be updated automatically."
    if not shutil.which("pkexec"):
        return False, ("No polkit agent is available to ask for a password, so this "
                       "has to be run in a terminal.")
    full = ["pkexec"] + argv + [path]
    try:
        done = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "The installer took too long and was stopped."
    except OSError as exc:
        return False, "Could not start the installer: %s" % exc

    if done.returncode == 0:
        return True, "Installed."
    if done.returncode == _PKEXEC_DISMISSED:
        return False, "Cancelled — nothing was changed."
    if done.returncode == _PKEXEC_NOT_FOUND:
        return False, "Could not run the package manager."
    tail = (done.stderr or done.stdout or "").strip().splitlines()
    return False, (tail[-1][:200] if tail else
                   "The package manager exited with code %d." % done.returncode)


def relaunch():
    """Start a fresh copy once this one has exited.

    Needed after a package install replaces the files underneath a running process:
    the code already in memory keeps working, but it is the old code.
    """
    if getattr(sys, "frozen", False):
        argv = [sys.executable]
    else:
        argv = [sys.executable] + list(sys.argv)
    quoted = " ".join("'%s'" % a.replace("'", "'\\''") for a in argv)
    script = _write_script(
        "#!/bin/sh\n" + _sh_wait_for_exit(os.getpid()) +
        "%s &\n" % quoted +
        'rm -f "$0"\n', ".sh")
    _detach(["/bin/sh", script])


def install(update, on_progress=None):
    """Download and stage an update. Returns once the handover is armed.

    The caller must quit immediately after this returns — the helper is already
    waiting for this process to exit before it touches anything.
    """
    kind = update.get("install_kind") or install_kind()
    fn = APPLIERS.get(kind)
    if not fn:
        raise UpdateError("this install has to be updated with:  %s"
                          % MANUAL_HINT.get(kind, "the package manager"))
    fn(download(update["asset"], on_progress=on_progress))
