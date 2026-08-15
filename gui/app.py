"""Entry point for the Hub Moon desktop GUI.

Run via ``python3 moondrop_control.py --gui`` (preferred) or ``python3 -m gui``.
Kept import-light at module load so ``--gui`` can lazy-import it without dragging the
UI toolkit into the plain CLI.
"""
from __future__ import annotations

import gc
import os
import signal
import sys
from datetime import timedelta

# How often the collector runs, once it has been taken off automatic. Nothing here
# generates cycles at any rate, so this is a safety net rather than a workload.
_GC_EVERY = timedelta(seconds=5)


def _res_dir(sub):
    """Locate a bundled resource dir (``ui``), whether the app runs from source, an
    installed wheel, or a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
        return os.path.join(base, "gui", sub)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), sub)


def _pin_gc_to_ui_thread():
    """Take the cyclic collector off automatic and run it on the UI thread instead.

    This is not a tuning knob. Without it the app aborts, and the way it aborts is
    worth understanding, because it is a trap anyone touching the worker threads can
    fall into again.

    Every struct handed to Slint — each row of `bands`, `presets`, `hub-rows` —
    becomes a `PyStruct`, which pyo3 marks *unsendable*: it records the thread that
    created it and panics on any access from another one. All of ours are created on
    the UI thread, which is correct and by design.

    The cyclic collector, though, does not run on a thread of its choosing. It runs on
    whichever thread happens to trip the allocation threshold. So a worker doing
    something ordinary and allocation-heavy — ``import ssl`` on the way to fetching the
    community library is enough — starts a collection *on the worker thread*, that
    collection walks a `PyStruct` the UI thread made, and pyo3 aborts the process.
    Not an exception: a Rust panic, straight to a core dump, with a message about
    thread ids that says nothing about what the program was doing.

    Disabling automatic collection makes the choice of thread ours, and a repeating
    timer on the event loop then collects where it is safe to. Refcounting still frees
    everything acyclic the moment it is dropped, which is nearly all of it.
    """
    import slint

    gc.disable()
    timer = slint.Timer()
    timer.start(slint.TimerMode.Repeated, _GC_EVERY, gc.collect)
    return timer   # dropping this stops the timer, so the caller has to hold it


def main(argv=None):
    # No font plumbing here on purpose. The icons used to be a bundled Material Symbols
    # subset drawn as ligature text, which only ever worked on machines that already had
    # the font installed — Slint's Python API has no way to register one, and
    # SLINT_DEFAULT_FONT sets the default family rather than making a name resolvable.
    # They are vector outlines now (gui/ui/icons.slint), so there is nothing to find.
    import slint

    from .bridge import Bridge

    ui_file = os.path.join(_res_dir("ui"), "app.slint")
    window = slint.load_file(ui_file).MainWindow()

    gc_timer = _pin_gc_to_ui_thread()   # noqa: F841 — held so the timer keeps firing
    bridge = Bridge(window)

    # Ctrl-C should close the DAC handle rather than leaving it open on exit.
    def _bye(*_):
        slint.quit_event_loop()
    signal.signal(signal.SIGINT, _bye)

    bridge.start()
    try:
        # window.run() — not show() + run_event_loop(). The loop exits as soon as no
        # window is shown, and show() does not keep it alive on its own, so the pair
        # returns instantly with exit code 0 and no window ever appears.
        window.run()
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
