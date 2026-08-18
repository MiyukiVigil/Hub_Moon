#!/usr/bin/env python3
"""Turn the icons the UI names into vector paths, and generate the two files that
carry them.

    python3 tools/build-icons.py            # regenerate
    python3 tools/build-icons.py --list     # just show what the UI asks for

Run it after adding or removing an icon in the UI.

Why this replaces the font subsetter it grew out of
---------------------------------------------------
The previous approach shipped a subset of Material Symbols Outlined (~8 KB) and drew
icons as ligature text. That works only if the toolkit can be told about a font file,
and **Slint's Python API has no font-registration call at all** — no
`register_font_from_path`, nothing on the module, nothing on the component. The
documented `SLINT_DEFAULT_FONT` sets the *default* family; it does not make a family
resolvable by name.

This was tested rather than assumed: running the app under a fontconfig sandbox with
Material Symbols removed rendered every icon as its own name — "gr" for graphic_eq,
"he" for headphones, "rem"/"add" on every stepper. It only ever looked right because
this machine happens to have the font installed system-wide, which almost no user's
will. The shipped package declares no font dependency, so a clean install would have
looked broken.

So the font goes. Glyph outlines are extracted here and emitted as SVG path commands,
which Slint's `Path` element draws natively. The result is smaller than the font it
replaces, has no runtime dependency of any kind, and cannot silently fall back.

Requires fontTools:  pip install fonttools
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, ".cache", "MaterialSymbolsOutlined-full.ttf")
OUT_SLINT = os.path.join(ROOT, "gui", "ui", "icons.slint")
OUT_PY = os.path.join(ROOT, "gui", "icons.py")

# Material Symbols ships from material-design-icons, NOT from google/fonts — there is no
# materialsymbols directory in the latter, and asking for one 404s.
UPSTREAM = ("https://raw.githubusercontent.com/google/material-design-icons/master/"
            "variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf")

# Where icon names can appear. The Python side has to be scanned too: the preset table
# in bridge.py carries an icon per row.
UI_GLOBS = ["gui/ui/*.slint", "gui/bridge.py"]

# The font is drawn on a 960 grid with the y axis pointing up; SVG and Slint both point
# it down. `viewbox-height: 960` plus this flip puts every icon in the same 0..960 box.
GRID = 960


def ligatures(font):
    """{"usb_off": <glyph>} — the sequence of glyph names a name shapes from, to the
    single icon glyph it shapes to. Material Symbols wraps these lookups in Extension
    subtables, so the ligature dict is one level below where you would first look."""
    cmap = font.getBestCmap()
    gname = {chr(cp): n for cp, n in cmap.items()}
    out = {}
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for sub in lookup.SubTable:
            sub = getattr(sub, "ExtSubTable", sub)
            for first, ligs in getattr(sub, "ligatures", {}).items():
                for lig in ligs:
                    out[first + "".join(lig.Component)] = lig.LigGlyph
    return out, gname


def scan_ui(ligs, gname):
    """Every snake_case string literal in the UI, intersected with the font's ligature
    table.

    Two shapes, because there are two: the view refers to `Icons.usb-off` (compile-
    checked, so a typo is a build error rather than a blank icon), while the preset
    table in bridge.py still names icons as plain strings. Both are collected and
    intersected with the real ligature table, which cannot miss one whatever syntax
    carries it.

    The Python side is read as the table it is, not as loose string literals. Taking
    every snake_case string in bridge.py used to be near enough — but the file has
    grown strings like "settings", "source", "clear" and "verified" that mean nothing
    of the kind, and each one silently baked a kilobyte of unused path data into the
    build. Matching the table's own shape — name, then icon — cannot drift with the
    prose around it.
    """
    def seq(t):
        """A name as the glyph sequence it shapes from, which is the key the ligature
        table is built on. NUL for a character the font has no glyph for, so an
        unmappable name simply fails to match rather than half-matching."""
        return "".join(gname.get(ch, "\0") for ch in t)

    tokens = set()
    for pattern in UI_GLOBS:
        for path in glob.glob(os.path.join(ROOT, pattern), recursive=True):
            text = open(path, encoding="utf-8").read()
            # `Icons.usb-off` in the .slint files. Slint identifiers use dashes where
            # the font's ligature names use underscores, so they are mapped back.
            tokens |= {m.replace("-", "_")
                       for m in re.findall(r"\bIcons\.([a-z][a-z0-9-]{2,30})", text)}
            # the preset table's second column: ("Bass", "graphic_eq", …)
            tokens |= set(re.findall(
                r'^\s*\("[^"]+",\s*"([a-z][a-z0-9_]{2,30})"', text, re.M))
    return sorted(t for t in tokens if seq(t) in ligs), seq


def source_font():
    if os.path.exists(CACHE):
        return CACHE
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    sys.stderr.write("fetching Material Symbols Outlined (~10 MB, cached in tools/.cache)...\n")
    urllib.request.urlretrieve(UPSTREAM, CACHE)
    return CACHE


def outline(glyphset, name):
    """One glyph as SVG path commands, flipped into a y-down 0..960 box."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen

    pen = SVGPathPen(glyphset)
    glyphset[name].draw(TransformPen(pen, (1, 0, 0, -1, 0, GRID)))
    return pen.getCommands()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print the icons the UI names, then exit")
    args = ap.parse_args()

    from fontTools.ttLib import TTFont

    font = TTFont(source_font())
    ligs, gname = ligatures(font)
    names, seq = scan_ui(ligs, gname)

    if args.list:
        print("\n".join(names))
        return 0
    if not names:
        sys.stderr.write("no icon names found in the UI — refusing to write empty files\n")
        return 1

    glyphset = font.getGlyphSet()
    paths = {}
    for n in names:
        d = outline(glyphset, ligs[seq(n)])
        if not d.strip():
            sys.stderr.write("warning: %s has an empty outline, skipping\n" % n)
            continue
        paths[n] = d

    banner = ("// GENERATED by tools/build-icons.py — do not edit.\n"
              "// Material Symbols Outlined (Apache-2.0), glyph outlines only.\n"
              "// Every icon is drawn on a 960 grid with the y axis pointing down;\n"
              "// pair these with viewbox-width: 960 and viewbox-height: 960.\n")

    with open(OUT_SLINT, "w", encoding="utf-8") as fh:
        fh.write(banner)
        fh.write("\nexport global Icons {\n")
        for n in sorted(paths):
            fh.write('    out property <string> %s: "%s";\n' % (n.replace("_", "-"), paths[n]))
        fh.write("}\n")

    with open(OUT_PY, "w", encoding="utf-8") as fh:
        fh.write('"""GENERATED by tools/build-icons.py — do not edit.\n\n'
                 "Material Symbols Outlined (Apache-2.0), glyph outlines only. Same data as\n"
                 "gui/ui/icons.slint; this copy exists so the preset table can hand the view a\n"
                 "finished path instead of a name the view would have to look up.\n"
                 '"""\n\nGRID = %d\n\nPATHS = {\n' % GRID)
        for n in sorted(paths):
            fh.write('    "%s":\n        "%s",\n' % (n, paths[n]))
        fh.write("}\n")

    total = sum(len(v) for v in paths.values())
    print("%d icons, %.1f KB of path data" % (len(paths), total / 1024))
    print("  " + " ".join(sorted(paths)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
