"""Where you are in the app, as a stack instead of a boolean per screen.

Every full-screen surface used to carry its own `*_open` flag, and every one of them
had to remember to switch off all the others. `bridge.py` held 44 separate assignments
to those flags — eight of `settings_open = False` alone — and the invariant "at most
one is true" was enforced by hand at each site. Adding a tenth surface meant editing
nine places and hoping.

It is one stack now. Opening a screen pushes it; the view draws `top` and nothing else,
so mutual exclusion is a property of the structure rather than a rule anyone has to
follow. Nothing can be half-open and nothing can be doubly open.

The stack also answers a question the booleans could not: *where does back go?* Opening
Settings from the EQ page and opening the release notes from inside Settings are the
same operation here, one push each, and both come back to where they started. The old
code faked that one case with a `whatsnew_preview` flag that reopened Settings by hand,
and could not fake it anywhere else — which is why "community → preview → back" did not
exist.

The EQ page is deliberately not on the stack. It is what the stack sits on: an empty
stack means the editor, which is where the app is when it is not somewhere else.

Depth is bounded without a counter. `open` on a screen that is already in the stack
truncates back to it rather than pushing a second copy, so no name appears twice and
the stack cannot be deeper than there are screens.
"""
from __future__ import annotations

# The names the view knows these by. They are strings and not an enum because they
# cross into Slint as a string property — the view compares `root.nav == "settings"` —
# and a value that has to survive that trip is better off being what it will become.
HELP = "help"
SETTINGS = "settings"
# One screen for saved curves, recent ones, the community index and the AutoEQ
# catalogue. They were three, and each was the same screen — see LibrarySheet.
LIBRARY = "library"
DEVICES = "devices"
WHATSNEW = "whatsnew"

SCREENS = (HELP, SETTINGS, LIBRARY, DEVICES, WHATSNEW)

# The EQ page. Not a screen — the absence of one.
EDITOR = ""


class Nav:
    """The screen stack. `top` is what is on screen; `""` is the editor."""

    def __init__(self):
        self._stack = []

    def __repr__(self):
        return "Nav(%s)" % (" > ".join(self._stack) if self._stack else "editor")

    # ── what is on screen ──
    @property
    def top(self):
        return self._stack[-1] if self._stack else EDITOR

    @property
    def under(self):
        """What `back` would return to. `""` is the editor."""
        return self._stack[-2] if len(self._stack) > 1 else EDITOR

    @property
    def depth(self):
        return len(self._stack)

    @property
    def trail(self):
        """The whole stack, oldest first. For tests and for logging."""
        return tuple(self._stack)

    def at(self, name):
        """Is `name` the screen on top? Not "is it in the stack" — a screen with
        another one over it is not the one you are looking at, and code that polls
        for work to do (thumbnails, classification) must not run for it."""
        return self.top == name

    # ── moving ──
    def open(self, name):
        """Show `name`, over whatever is already there.

        Revisiting a screen that is already open goes *back* to it rather than
        stacking a second copy: Settings → notes → Settings is one screen deep, not
        three, and the second visit is the same visit.
        """
        self._check(name)
        if name in self._stack:
            del self._stack[self._stack.index(name) + 1:]
        else:
            self._stack.append(name)
        return True

    def toggle(self, name):
        """Open `name`, or close it if it is already the screen you are on.

        Returns whether it is open afterwards, which is what the caller wants to
        know: the side effects of opening a screen — loading a catalogue, re-reading
        the profiles — belong to the opening and not to the closing.
        """
        self._check(name)
        if self.at(name):
            self.back()
            return False
        self.open(name)
        return True

    def back(self):
        """One step out. Returns the screen that was left, `""` if there was none."""
        return self._stack.pop() if self._stack else EDITOR

    def reset(self, name=EDITOR):
        """Straight to the editor, or straight to one screen with nothing under it.

        For the things that are not navigation: applying a curve (you asked to see
        the result, so the result is what you should be looking at), starting the
        tour, and an interrupt that takes the whole window.
        """
        self._stack = []
        if name:
            self.open(name)

    @staticmethod
    def _check(name):
        if name not in SCREENS:
            raise ValueError("no such screen: %r" % (name,))
