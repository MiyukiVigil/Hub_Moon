#!/usr/bin/env python3
"""Run the *frozen* bundle and check the things only a frozen bundle can get wrong.

    pyinstaller --noconfirm packaging/hub-moon.spec
    python3 tools/smoke-frozen.py dist/hub-moon/hub-moon      # Linux
    python3 tools/smoke-frozen.py "dist/Hub Moon.app"         # macOS (or the inner binary)
    python  tools/smoke-frozen.py dist/hub-moon/hub-moon.exe  # Windows

Every bug this exists for was invisible to the test suite by construction. The suite
imports the source tree, where `sys.frozen` is unset, the library path is whatever the
shell had, and the entry point is a function rather than a bootloader. A build that
fails all of the below still passes 285 tests.

That is not hypothetical — 1.2.0b3 and b4 both shipped an `install_kind` fix that
could not run, because the bundle's library path broke every system program the app
spawned. Four betas went out before anybody ran the binary and looked.

The bundle reports on itself through `--selftest FILE` rather than through stdout,
because the Windows build is windowed (`console=False`) and therefore has no console
for anything to read. The file is the only channel all three platforms share.

Exit code is 0 if the bundle is sound, 1 otherwise. Intended for CI, straight after
PyInstaller and before anything is packaged from it, so a broken bundle never becomes
a release.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

TIMEOUT = 120

# What a dynamic linker says when a binary loads the wrong libraries. Per-platform
# because the messages share no wording: matching only glibc's phrasing would have made
# this check pass unconditionally on macOS, which is where the same bug silently broke
# `hdiutil`, `ditto`, `xattr` and `codesign` — the whole of the .dmg updater.
LINKER_TELLS = {
    "linux": ("not found (required by", "cannot open shared object file",
              "undefined symbol:", "version `GLIBC"),
    "darwin": ("Library not loaded:", "Symbol not found:", "image not found",
               "incompatible library version"),
}


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, **kw)


def check(name, ok, detail=""):
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          "" if ok else "\n        " + detail.replace("\n", "\n        ")))
    return ok


def resolve(path):
    """Accept an .app bundle and find the executable inside it.

    So the macOS workflow can name the thing it just signed rather than repeating
    PyInstaller's internal layout.
    """
    path = os.path.abspath(path)
    if path.endswith(".app") and os.path.isdir(path):
        macos = os.path.join(path, "Contents", "MacOS")
        for name in sorted(os.listdir(macos)) if os.path.isdir(macos) else []:
            full = os.path.join(macos, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    return path


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__.strip())
        return 2
    exe = resolve(argv[0])
    if not os.path.exists(exe):
        print("no such bundle: %s" % exe)
        return 2

    windows = os.name == "nt"
    print("smoke-testing %s" % exe)
    ok = True

    # ── the bundle's own report ──────────────────────────────────────────────
    # Asked for first, because on Windows it is the only thing the build can say.
    fd, report_path = tempfile.mkstemp(suffix=".json", prefix="hub-moon-selftest-")
    os.close(fd)
    try:
        got = run([exe, "--selftest", report_path])
        try:
            with open(report_path, encoding="utf-8") as fh:
                rep = json.load(fh)
        except (OSError, ValueError) as exc:
            check("`--selftest` writes a report", False,
                  "%s\nrc=%s out=%r err=%r"
                  % (exc, got.returncode, got.stdout[:200], got.stderr[:300]))
            return 1
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass

    ok &= check("`--selftest` answers", got.returncode == 0,
                "rc=%s err=%r" % (got.returncode, got.stderr[:300]))

    ok &= check("the build knows it is frozen", rep.get("frozen") is True,
                "frozen=%r — the spec is not producing a bundle" % rep.get("frozen"))

    ok &= check("the bundle reports the version it was built as",
                bool(rep.get("version")), "version=%r" % rep.get("version"))

    # ── THE one ──────────────────────────────────────────────────────────────
    # Children must not inherit the bundle's library directory, or every system program
    # the app starts loads its OpenSSL and dies. Asserted by asking a child what it
    # actually sees, because a probe that runs some tool and hopes proves nothing — `sh`
    # does not link against OpenSSL and survives a build that breaks `pacman`.
    #
    # Windows has no equivalent variable, so the bundle reports the check as n/a there
    # rather than inventing a result. That is not a gap being waved through: the failure
    # mode itself does not exist on Windows, where the loader resolves DLLs from the
    # directory the .exe lives in.
    spawn = rep.get("spawn") or {}
    ok &= check("children do not inherit the bundle's library path"
                + (" (n/a on Windows)" if windows else ""),
                spawn.get("ok") is True,
                "leaked=%s child_path=%s" % (spawn.get("leaked_bundle_path"),
                                             (spawn.get("child_path") or "")[:200]))

    ok &= check("install detection returns something real",
                isinstance(rep.get("install_kind"), str)
                and not rep["install_kind"].startswith("error:"),
                "install_kind=%r" % rep.get("install_kind"))

    # ── the CLI exists at all ────────────────────────────────────────────────
    # Until 1.2.0b5 the frozen entry called the GUI and nothing else, so every packaged
    # install shipped a window and no command line. Read from stdout, so it only means
    # anything where the build has one — the Windows bundle is windowed and prints into
    # the void, and `--selftest` above has already proved the CLI runs.
    if not windows:
        got = run([exe, "--version"])
        ok &= check("`--version` answers from the bundle",
                    got.returncode == 0 and "hub-moon" in got.stdout.lower(),
                    "rc=%s out=%r err=%r"
                    % (got.returncode, got.stdout[:200], got.stderr[:200]))

        # A second look at the library path from the other end: run something real and
        # see whether the loader complained. `--json` shells out for device access, so
        # it exercises the spawn path rather than asking about it.
        tells = LINKER_TELLS.get(sys.platform if sys.platform in LINKER_TELLS
                                 else "linux", ())
        got = run([exe, "--json"])
        hit = [t for t in tells if t in (got.stderr or "")]
        ok &= check("`--json` runs without a library-path failure",
                    not hit, "matched %r in:\n%s" % (hit, got.stderr[:400]))

    print("        install_kind=%s  version=%s  python=%s"
          % (rep.get("install_kind"), rep.get("version"), rep.get("python")))

    print()
    print("bundle looks sound" if ok else "BUNDLE IS BROKEN — do not package this")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
