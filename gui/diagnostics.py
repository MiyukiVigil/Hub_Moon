"""A log file, and somewhere for a crash to go.

A windowed build has no stderr. On Windows `console=False` means `sys.stderr` is
`None`; a macOS `.app` sends it to the unified log where nobody looks. So through
1.0.0 the only trace an exception left was PyInstaller's own "Unhandled exception in
script" box — a screenshot of a truncated traceback, with the frames that mattered
below the fold. That is what diagnosing the 1.0.0 quit crash actually cost.

This module fixes the reporting rather than any one bug:

* everything goes to a rotating file in the platform's log directory, which the
  settings panel can open,
* `sys.excepthook` and `threading.excepthook` are installed, so a crash on a worker
  thread is recorded instead of vanishing,
* and a fatal error gets a **native** message box — `MessageBoxW`, `osascript`,
  `zenity` — deliberately not a Slint window, because the failure this has to survive
  is the one where the toolkit is what did not come up.

Import-light on purpose: `logging` and the stdlib, nothing else. It is installed
before the UI exists and has to work when nothing else does.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import subprocess
import sys
import threading
import traceback

try:
    import moondrop_control as mc
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

LOG_NAME = "hub-moon.log"
MAX_BYTES = 512 * 1024
BACKUPS = 3

log = logging.getLogger("hubmoon")
_installed = False


def log_path():
    return os.path.join(mc.log_dir(), LOG_NAME)


def install(level=logging.INFO):
    """Wire up the log and the hooks. Safe to call twice; the second call is a no-op.

    Failing to open the log is not itself a reason to fail to start — a read-only
    home directory is unusual but not fatal — so this degrades to logging nowhere.
    """
    global _installed
    if _installed:
        return log
    _installed = True
    log.setLevel(level)
    log.propagate = False

    try:
        os.makedirs(mc.log_dir(), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(handler)
    except OSError:
        log.addHandler(logging.NullHandler())

    # A console build still deserves its output on the terminal.
    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.WARNING)
        stream.setFormatter(logging.Formatter("hub-moon: %(levelname)s: %(message)s"))
        log.addHandler(stream)

    _install_hooks()
    _banner()
    return log


def _banner():
    """Every session opens with the facts a bug report otherwise has to ask for."""
    from . import updater
    try:
        kind = updater.install_kind()
    except Exception:
        kind = "unknown"
    log.info("─" * 62)
    log.info("Hub Moon %s starting", mc.__version__)
    log.info("python %s | %s %s | %s", platform.python_version(), platform.system(),
             platform.release(), platform.machine())
    log.info("install: %s | frozen: %s", kind, bool(getattr(sys, "frozen", False)))
    log.info("config: %s", mc.config_dir())


def _install_hooks():
    def on_exception(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("unhandled exception on the main thread",
                     exc_info=(exc_type, exc, tb))
        fatal("Hub Moon has stopped",
              "".join(traceback.format_exception(exc_type, exc, tb)))

    def on_thread_exception(args):
        # SystemExit from a thread is a deliberate exit, not a fault.
        if issubclass(args.exc_type, SystemExit):
            return
        log.error("unhandled exception on thread %s",
                  getattr(args.thread, "name", "?"),
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception


# ── native message boxes ─────────────────────────────────────────────────────

def _no_window():
    """Keep a console from flashing up behind a windowed build's dialog."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def message_box(title, text, critical=False):
    """Show `text` using whatever the platform has. Returns True if something did.

    Never raises: this is what gets called when things have already gone wrong.
    """
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, text, title, 0x10 if critical else 0x40)
            return True
        if sys.platform == "darwin":
            import json as _json
            subprocess.run(
                ["osascript", "-e", "display alert %s message %s%s"
                 % (_json.dumps(title), _json.dumps(text),
                    " as critical" if critical else "")],
                check=False, timeout=120, capture_output=True, env=mc.system_env())
            return True
        for argv in (["zenity", "--error" if critical else "--info",
                      "--title", title, "--text", text, "--width", "540"],
                     ["kdialog", "--error" if critical else "--msgbox", text,
                      "--title", title]):
            try:
                subprocess.run(argv, check=False, timeout=120,
                               capture_output=True, env=mc.system_env())
                return True
            except (OSError, subprocess.SubprocessError):
                continue
    except Exception:
        pass
    return False


def fatal(title, detail):
    """Report a crash to the user in the terms they can act on.

    The traceback is trimmed to its last frames — the ones naming the code that
    actually failed. The whole thing is in the log, and the path to it is the most
    useful line in the box, so it goes last where it will not be scrolled past.
    """
    lines = [ln for ln in detail.strip().splitlines() if ln.strip()]
    tail = "\n".join(lines[-6:]) if len(lines) > 6 else "\n".join(lines)
    body = ("Hub Moon ran into an error it could not recover from.\n\n"
            "%s\n\n"
            "The full details are in:\n%s\n\n"
            "Please report this at:\n"
            "https://github.com/MiyukiVigil/Hub_Moon/issues" % (tail, log_path()))
    if not message_box(title, body, critical=True) and sys.stderr is not None:
        print(body, file=sys.stderr)


def reveal_logs():
    """Open the log directory in the platform's file manager."""
    path = mc.log_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    try:
        if sys.platform == "win32":
            os.startfile(path)                                   # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path], env=mc.system_env(), **_no_window())
        else:
            subprocess.Popen(["xdg-open", path], env=mc.system_env(), **_no_window())
        return True
    except (OSError, AttributeError) as exc:
        log.warning("could not open %s: %s", path, exc)
        return False
