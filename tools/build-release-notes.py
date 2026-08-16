#!/usr/bin/env python3
"""Turn CHANGELOG.md into the short note list the What's New screen shows.

    python3 tools/build-release-notes.py            # rewrite gui/notes.py
    python3 tools/build-release-notes.py --print    # show what it would write

Run it after editing CHANGELOG.md, and commit the result.

**Why this is generated into a .py file rather than read at runtime.** What's New
normally reads its notes from the update manifest the updater already downloaded, and
that works well for anything installed through a release. It does nothing at all for a
build that never came from a release — a `makepkg -si` from the repo, a beta that has
not been published yet, a wheel installed from a git URL — and those are exactly the
builds a beta channel is made of. So the notes for whatever is running are baked into
the package.

A Python module, not a JSON data file, because a module needs no packaging changes: it
is picked up by setuptools' package discovery, by PyInstaller's import analysis, and by
every distro package built from either — where a `gui/notes.json` would have to be
declared, separately and correctly, in five places before it shipped.

The extraction rule is that a changelog bullet already begins with its own headline:

    - **AutoEQ shelves were importing as peaking filters.** `--import-rew` knew …

so the bold lead is the line, and the paragraph explaining it is not. Bullets with no
bold lead fall back to their first sentence.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "CHANGELOG.md")
TARGET = os.path.join(ROOT, "gui", "notes.py")

MAX_NOTES = 12
MAX_LEN = 190

VERSION_RE = re.compile(r"^##\s+\[([^\]]+)\]")
SECTION_RE = re.compile(r"^###\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^-\s+(.*)$")
LEAD_RE = re.compile(r"^\*\*(.+?)\*\*", re.DOTALL)

# Sections worth showing, in the order a reader wants them: what broke and now works
# comes before what is new to learn, and housekeeping comes last. Anything not named
# here — "Protocol notes", "Verified on hardware" — is reference material for the repo,
# not a line in a panel that has room for twelve.
SECTIONS = ["Fixed", "Added", "Changed"]


def _flatten(md):
    """Markdown inline syntax removed, whitespace collapsed.

    Code spans are unwrapped before emphasis is stripped, and that ordering is the
    whole reason this is a function: stripping `_` everywhere turns `parse_peq_text()`
    into `parsepeqtext()`, a name that exists nowhere and cannot be searched for.
    """
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)     # links keep their text
    md = re.sub(r"`([^`]*)`", r"\1", md)                 # code spans keep their code
    md = re.sub(r"\*{1,2}", "", md)                      # bold and italic markers
    return re.sub(r"\s+", " ", md).strip()


def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]


def _headline(body):
    """The one sentence of a bullet that belongs on screen."""
    lead = LEAD_RE.match(body)
    if not lead:
        text = _first_sentence(_flatten(body))
    else:
        text = _flatten(lead.group(1))
        # A bold lead short enough to be a title rather than a sentence ("Saved
        # profiles", "Packaging") is a fragment on its own, so it keeps what follows
        # it — joined the way the source punctuated it, which is the difference
        # between "What's New, shown once after an update" and "What's New — , shown".
        if len(text) < 34:
            rest = _flatten(body[lead.end():])
            dash = re.match(r"^[—–-]+\s+", rest)
            if rest[:1] in ",;:":
                # "**What's New**, shown once…" — the lead is the sentence's subject,
                # so its trailing full stop (if any) has to go with the join.
                joiner, text = "", text.rstrip(".")
            elif dash:
                # One dash, and only when a space follows it: `\s+` is what keeps
                # "— `--export-json` /…" from being stripped down to "export-json".
                joiner, rest = " — ", rest[dash.end():]
                text = text.rstrip(".")
            else:
                joiner = " "         # a new sentence; the lead keeps its punctuation
            rest = _first_sentence(rest)
            if rest:
                text = text + joiner + rest
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN - 1].rstrip(" ,;—-") + "…"
    return text


def parse(text):
    """{version: [note, …]} for every release in the changelog."""
    out, version, section, bullet = {}, None, None, None

    def flush():
        nonlocal bullet
        if version and bullet and section in SECTIONS:
            out.setdefault(version, {}).setdefault(section, []).append(_headline(bullet))
        bullet = None

    for raw in text.splitlines():
        head = VERSION_RE.match(raw)
        if head:
            flush()
            version, section = head.group(1), None
            continue
        sec = SECTION_RE.match(raw)
        if sec:
            flush()
            section = sec.group(1)
            continue
        top = BULLET_RE.match(raw)
        if top:
            flush()
            bullet = top.group(1)
            continue
        # A continuation is indented; an indented `-` is a nested bullet, which is
        # detail under the headline rather than a headline of its own, so it is
        # folded into the text and then dropped with the rest of the paragraph.
        if bullet is not None and raw.startswith(" ") and raw.strip():
            bullet += " " + raw.strip()
            continue
        if not raw.strip():
            continue
        flush()
    flush()

    return {v: [n for s in SECTIONS for n in groups.get(s, [])][:MAX_NOTES]
            for v, groups in out.items()}


HEADER = '''"""Release notes, generated from CHANGELOG.md — do not edit by hand.

    python3 tools/build-release-notes.py

Read by gui/updater.release_notes() when no downloaded manifest describes the version
that is running, which is the normal case for anything built from the repo rather than
installed from a release.
"""
'''


def render(notes):
    out = [HEADER, "", "NOTES = {"]
    # Changelog order, not a sort. Keep a Changelog is already newest-first, and a
    # numeric sort would put 1.2.0b1 *above* 1.2.0 — the pre-release outranking the
    # release it precedes, which is the one ordering that must never be wrong here.
    for version, lines in notes.items():
        if not lines:
            continue
        out.append("    %r: [" % version)
        for line in lines:
            out.append("        %r," % line)
        out.append("    ],")
    out.append("}\n")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--print", dest="show", action="store_true",
                    help="write nothing; dump the module to stdout")
    args = ap.parse_args()

    with open(SOURCE, encoding="utf-8") as fh:
        notes = parse(fh.read())
    if not notes:
        print("error: no version headings found in %s" % SOURCE, file=sys.stderr)
        return 1

    text = render(notes)
    if args.show:
        sys.stdout.write(text)
        return 0
    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("wrote %s  (%d releases, %d notes)"
          % (TARGET, len(notes), sum(len(v) for v in notes.values())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
