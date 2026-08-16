#!/usr/bin/env python3
"""Build the AutoEQ catalogue the app searches.

    python3 tools/build-autoeq-index.py --site ../self-website/hubmoon

AutoEQ publishes 8,827 parametric profiles across 6,015 headphones and 23 measurement
sources. The app needs to search that list, and there are three ways it could get it —
only one of them is reasonable:

* **GitHub's API, per user.** Unauthenticated callers get 60 requests an hour *per IP*,
  and listing the tree is one of them. Two people behind the same NAT running Hub Moon
  on the same afternoon would start getting 403s. (This was not theoretical: building
  this file rate-limited the machine writing it.)
* **The git tree, per user.** ~4 MB of JSON to find a headphone.
* **A slim index, built here, served from the site.** 441 KB, and 62 KB over the wire
  once Cloudflare gzips it. One request, cached for a week.

So this generates the third. It takes the file list from a **blobless shallow clone**
rather than the API — `--filter=blob:none --no-checkout` fetches the trees and none of
the contents, which is 3.4 MB and about ten seconds, and has no rate limit at all.

Every profile lives at a path of the form

    results/<source>/<form>/<model>/<model> ParametricEQ.txt

and that holds for all 8,827 with no exceptions, verified rather than assumed — so the
index stores `[model, source, form]` and the app rebuilds the URL. Storing the URLs
themselves would quadruple the file to say nothing new.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = "https://github.com/jaakkopasanen/AutoEq"
RAW = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results/"
SUFFIX = " ParametricEQ.txt"


def file_list(ref="master"):
    """Every path in the AutoEQ repo, without downloading any file contents."""
    tmp = tempfile.mkdtemp(prefix="autoeq-tree-")
    try:
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
             "--branch", ref, "--quiet", REPO, tmp],
            check=True, capture_output=True, timeout=600)
        out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "HEAD"],
                             cwd=tmp, check=True, capture_output=True,
                             text=True, timeout=300)
        return out.stdout.splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(paths):
    models, skipped = [], []
    for p in paths:
        if not p.endswith(SUFFIX):
            continue
        parts = p.split("/")
        # results / <source> / <form> / <model> / <model> ParametricEQ.txt
        if len(parts) < 5 or parts[0] != "results" or parts[-1] != parts[3] + SUFFIX:
            skipped.append(p)
            continue
        models.append([parts[3], parts[1], parts[2]])
    models.sort(key=lambda m: (m[0].lower(), m[1].lower()))
    return models, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", metavar="DIR",
                    help="also write a copy here (the website's hubmoon/ directory)")
    ap.add_argument("--ref", default="master", help="AutoEQ branch to index")
    ap.add_argument("--stats", action="store_true", help="print a breakdown and stop")
    args = ap.parse_args()

    print("fetching the AutoEQ file tree (blobless clone, no API)…", file=sys.stderr)
    try:
        paths = file_list(args.ref)
    except FileNotFoundError:
        return sys.exit("git is not installed — this tool needs it to read the tree")
    except subprocess.CalledProcessError as exc:
        return sys.exit("could not read the AutoEQ repo: %s"
                        % (exc.stderr or b"").decode("utf-8", "replace")[:200])

    models, skipped = build(paths)
    if not models:
        return sys.exit("no profiles found — has AutoEQ reorganised results/ ?")

    if args.stats:
        by_source = collections.Counter(m[1] for m in models)
        by_form = collections.Counter(m[2] for m in models)
        print("%d profiles, %d distinct headphones, %d sources"
              % (len(models), len({m[0] for m in models}), len(by_source)))
        for name, n in by_source.most_common():
            print("   %-28s %5d" % (name, n))
        print("forms:", dict(by_form))
        return 0

    index = {
        "schema": 1,
        "source": REPO,
        "license": "MIT",
        "base": RAW,
        "suffix": SUFFIX,
        "count": len(models),
        # [model, measurement source, form factor]; the URL is
        #   base + source + "/" + form + "/" + model + "/" + model + suffix
        "models": models,
    }
    text = json.dumps(index, separators=(",", ":"), ensure_ascii=False) + "\n"

    targets = [os.path.join(ROOT, "packaging", "autoeq-index.json")]
    if args.site:
        targets.append(os.path.join(args.site, "autoeq-index.json"))
    for path in targets:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s  (%d profiles, %.0f KB)" % (path, len(models), len(text) / 1024))

    if skipped:
        print("\n%d path(s) did not match the expected layout and were left out:"
              % len(skipped), file=sys.stderr)
        for p in skipped[:5]:
            print("   " + p, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
