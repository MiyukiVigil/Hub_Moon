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
file with two keys, so a broken beta manifest cannot take stable down with it. Moving
from beta back to stable is offered whenever stable is *newer*; this never proposes a
downgrade on its own.
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
}

#: What to tell someone whose install we must not touch.
MANUAL_HINT = {
    "deb": "sudo apt update && sudo apt install --only-upgrade hub-moon",
    "rpm": "sudo dnf upgrade hub-moon",
    "pacman": "yay -Syu hub-moon",
    "nix": "nix profile upgrade hub-moon",
    "pipx": "pipx upgrade hub-moon",
    "pip": "pip install --upgrade 'hub-moon[gui]'",
    "system": "update through the package manager that installed it",
    "source": "git pull",
    "unknown": "download the new build from " + RELEASES_URL,
}


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
    if not man or not is_newer(man.get("version"), current):
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
    if can:
        hint = reason = ""
    elif kind in SELF_UPDATABLE:
        hint = RELEASES_URL
        reason = ("That release has no download for this platform yet — the build may "
                  "still be running. You can get it from:")
    else:
        hint = MANUAL_HINT.get(kind, RELEASES_URL)
        reason = ("This copy is managed by a package manager, so Hub Moon will not "
                  "replace its own files. Update it with:")

    return {
        "version": man["version"],
        "channel": channel,
        "date": man.get("date", ""),
        "summary": man.get("summary", ""),
        "notes_url": man.get("notes_url") or RELEASES_URL,
        "install_kind": kind,
        "can_install": can,
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
        raise UpdateError("download failed: %s" % exc)

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
        raise UpdateError("could not open the disk image: %s" % exc)

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
