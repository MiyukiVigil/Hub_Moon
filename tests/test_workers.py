"""The 1.0.0 quit crash, kept dead.

`gui/bridge.py` imports slint, which is an optional extra and absent from the older
interpreters in the CI matrix — so rather than importing the module, this lifts the
two definitions that matter straight out of its source. That is not a workaround for
the import: it is the point. The bug was a name collision with `threading.Thread`,
and reproducing it needs the *real* class body run on the *real* interpreter, which
is exactly what this does on every Python the matrix covers.

Why a version matrix is not optional here: CPython 3.13 deleted
`Thread._wait_for_tstate_lock`, which is what called the shadowed `_stop`. On 3.13+
the bug cannot fire at all. It was fatal on the 3.12 the release builds use, and
invisible on the 3.14 it was written on.
"""
import ast
import os
import queue
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "gui", "bridge.py")

RESERVED = {
    # Everything on threading.Thread that CPython itself calls. Shadowing any of
    # these with an instance attribute is the same class of bug as `_stop` was.
    "_stop", "_start", "_bootstrap", "_bootstrap_inner", "_delete", "_set_ident",
    "_reset_internal_locks", "_wait_for_tstate_lock", "_set_tstate_lock", "run",
    "start", "join", "is_alive", "_handle",
}


def load_worker():
    """Exec just `_QUIT` and `class _Worker` from bridge.py, with no slint anywhere."""
    tree = ast.parse(open(BRIDGE, encoding="utf-8").read())
    wanted = [n for n in tree.body
              if (isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "_QUIT" for t in n.targets))
              or (isinstance(n, ast.ClassDef) and n.name == "_Worker")]
    assert len(wanted) == 2, "bridge.py no longer defines _QUIT and _Worker"
    ns = {"threading": threading, "queue": queue}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), BRIDGE, "exec"), ns)
    return ns["_Worker"], ns["_QUIT"]


def test_join_after_shutdown_does_not_raise():
    """The exact sequence Bridge.stop() performs. On 3.12 this used to raise
    TypeError: 'object' object is not callable."""
    Worker, _ = load_worker()
    ran = []
    w = Worker("testworker")
    w.submit(lambda: ran.append(1))
    w.start()
    time.sleep(0.2)
    w.shutdown()
    w.join(timeout=2.0)                      # <- the call that raised
    assert ran == [1], "the queued job never ran"
    assert not w.is_alive(), "the sentinel did not stop the thread"


def test_worker_shadows_nothing_threading_owns():
    """A guard against the next one. Any instance attribute named after a Thread
    internal is the same bug wearing a different name."""
    Worker, _ = load_worker()
    w = Worker("probe")
    clashes = sorted(set(vars(w)) & RESERVED)
    assert not clashes, "these shadow threading.Thread internals: %s" % clashes


def test_sentinel_is_not_an_instance_attribute():
    """It lives at module scope precisely so it cannot collide again."""
    Worker, quit_sentinel = load_worker()
    w = Worker("probe")
    assert "_stop" not in vars(w)
    assert quit_sentinel is not None


def test_jobs_run_in_order():
    Worker, _ = load_worker()
    seen = []
    w = Worker("ordered")
    for i in range(20):
        w.submit(seen.append, i)
    w.start()
    w.shutdown()
    w.join(timeout=5.0)
    assert seen == list(range(20))


def test_a_failing_job_does_not_kill_the_thread():
    """`on_crash` exists so one bad job cannot take the worker with it."""
    Worker, _ = load_worker()
    crashes, after = [], []

    class W(Worker):
        def on_crash(self, exc):
            crashes.append(exc)

    w = W("resilient")
    w.submit(lambda: 1 / 0)
    w.submit(lambda: after.append("still here"))
    w.start()
    w.shutdown()
    w.join(timeout=5.0)
    assert len(crashes) == 1 and isinstance(crashes[0], ZeroDivisionError)
    assert after == ["still here"]


@pytest.mark.skipif(sys.version_info >= (3, 13),
                    reason="CPython 3.13 removed _wait_for_tstate_lock, so the "
                           "shadowing bug cannot fire on this interpreter")
def test_this_interpreter_would_have_caught_the_original_bug():
    """Proves the matrix is doing its job: on 3.9-3.12 the old code really does
    raise, so a green run on those versions is evidence and not luck."""
    class Broken(threading.Thread):
        def __init__(self):
            super().__init__(daemon=True)
            self._stop = object()            # what 1.0.0 did
        def run(self):
            pass

    b = Broken()
    b.start()
    time.sleep(0.1)
    with pytest.raises(TypeError, match="not callable"):
        b.join(timeout=2.0)
