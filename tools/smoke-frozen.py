#!/usr/bin/env python3
"""Run the *frozen* bundle and check the things only a frozen bundle can get wrong.

    pyinstaller --noconfirm packaging/hub-moon.spec
    python3 tools/smoke-frozen.py dist/hub-moon/hub-moon

Every bug this exists for was invisible to the test suite by construction. The suite
imports the source tree, where `sys.frozen` is unset, `LD_LIBRARY_PATH` is whatever
the shell had, and the entry point is a function rather than a bootloader. A build
that fails all of the below still passes 285 tests.

That is not hypothetical — 1.2.0b3 and b4 both shipped an `install_kind` fix that
could not run, because the bundle's library path broke every system program the app
spawned. Four betas went out before anybody ran the binary and looked.

Exit code is 0 if the bundle is sound, 1 otherwise. Intended for CI, straight after
PyInstaller and before anything is packaged from it, so a broken bundle never becomes
a release.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

TIMEOUT = 120


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, **kw)


def check(name, ok, detail=""):
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          "" if ok else "\n        " + detail.replace("\n", "\n        ")))
    return ok


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print(__doc__.strip())
        return 2
    exe = os.path.abspath(argv[0])
    if not os.path.exists(exe):
        print("no such bundle: %s" % exe)
        return 2

    print("smoke-testing %s" % exe)
    ok = True

    # ── the CLI exists at all ────────────────────────────────────────────────
    # Until 1.2.0b5 the frozen entry called the GUI and nothing else, so every
    # packaged install shipped a window and no command line.
    got = run([exe, "--version"])
    ok &= check("`--version` answers from the bundle",
                got.returncode == 0 and "hub-moon" in got.stdout.lower(),
                "rc=%s out=%r err=%r" % (got.returncode, got.stdout[:200], got.stderr[:200]))

    # ── the bundle can spawn a system program ────────────────────────────────
    # The one that mattered. PyInstaller points LD_LIBRARY_PATH at the bundle, so a
    # system binary loads its libraries instead of the distribution's and dies. The
    # app shells out for install detection, elevation, file dialogs and log folders.
    got = run([exe, "--json"])
    ok &= check("`--json` runs without a library-path failure",
                "not found (required by" not in (got.stderr or ""),
                got.stderr[:400])

    # ── the bundle's own report ──────────────────────────────────────────────
    got = run([exe, "--selftest"])
    if not check("`--selftest` answers", got.returncode == 0,
                 "rc=%s err=%r" % (got.returncode, got.stderr[:300])):
        return 1
    try:
        rep = json.loads(got.stdout)
    except ValueError:
        check("`--selftest` prints JSON", False, got.stdout[:300])
        return 1

    ok &= check("the build knows it is frozen", rep.get("frozen") is True,
                "frozen=%r — the spec is not producing a bundle" % rep.get("frozen"))

    # THE one. Children must not inherit the bundle's library directory, or every
    # system program the app starts loads its OpenSSL and dies. Asserted by asking a
    # child what it actually sees, because a probe that runs `sh` proves nothing —
    # `sh` does not link against OpenSSL and survives a build that breaks `pacman`.
    spawn = rep.get("spawn") or {}
    ok &= check("children do not inherit the bundle's library path",
                spawn.get("ok") is True,
                "leaked=%s child_path=%s" % (spawn.get("leaked_bundle_path"),
                                             spawn.get("child_path", "")[:200]))

    ok &= check("install detection returns something real",
                isinstance(rep.get("install_kind"), str)
                and not rep["install_kind"].startswith("error:"),
                "install_kind=%r" % rep.get("install_kind"))
    print("        install_kind=%s  version=%s  python=%s"
          % (rep.get("install_kind"), rep.get("version"), rep.get("python")))

    print()
    print("bundle looks sound" if ok else "BUNDLE IS BROKEN — do not package this")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
