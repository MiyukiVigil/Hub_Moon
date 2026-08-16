#!/usr/bin/env python3
"""Build the update manifest that gui/updater.py reads.

    python3 tools/build-update-manifest.py v1.1.0
    python3 tools/build-update-manifest.py v1.2.0-beta.1 --site ../self-website/hubmoon

Run it after the three build workflows have finished and attached their assets to
the GitHub release for that tag. It reads the release through the public API — no
token, no login — takes the SHA-256 of every asset from the `SHA256SUMS-*.txt` files
the workflows publish alongside them, and writes the small JSON the app fetches.

**Which file it writes is decided by the release, not by a flag.** A release marked
as a pre-release is a beta and lands in `update-beta.json`; anything else is stable
and lands in `update.json`. Those two files belong on the two branches the app looks
at — `test` and `main` respectively — which is what keeps a beta from ever being
offered to somebody on the stable channel.

Nothing here downloads a 200 MB installer to hash it. The workflows already hashed
their own assets on the runner that built them, which is both faster and the only
place the bytes are known to be the ones that were built.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = "MiyukiVigil/Hub_Moon"
API = "https://api.github.com/repos/%s/releases/tags/%s"
NOTES = "https://hubmoon.miyukivigil.tech/changelog.html"

# asset filename -> the key gui/updater.py asks for. Order matters: the first pattern
# that matches wins, and the portable zip has to be tested before anything looser.
PATTERNS = [
    (r"^HubMoon-Setup-.*\.exe$",                 "windows-installer"),
    (r"^HubMoon-.*-windows-portable\.zip$",      "windows-portable"),
    (r"^HubMoon-.*-macOS-arm64\.dmg$",           "macos-dmg-arm64"),
    (r"^HubMoon-.*-macOS-(x86_64|intel)\.dmg$",  "macos-dmg-x86_64"),
    (r"^HubMoon-.*\.AppImage$",                  "appimage"),
    (r"^HubMoon-.*-linux-x86_64\.tar\.gz$",      "linux-tarball"),
]


def get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "hub-moon-manifest",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def classify(name):
    for pattern, key in PATTERNS:
        if re.match(pattern, name):
            return key
    return None


def checksums(assets):
    """{filename: sha256} from every SHA256SUMS-*.txt attached to the release."""
    out = {}
    for a in assets:
        if not a["name"].lower().startswith("sha256sums"):
            continue
        for line in get(a["browser_download_url"]).decode("utf-8", "replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and len(parts[0]) == 64:
                # `sha256sum` writes "<hash>  <name>" and prefixes binary mode with *
                out[os.path.basename(parts[1].lstrip("*"))] = parts[0].lower()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag", help="the release tag, e.g. v1.1.0")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--summary", default="", help="one line shown in the app's settings panel")
    ap.add_argument("--site", metavar="DIR",
                    help="also write a copy here (the website's hubmoon/ directory)")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="write nothing; dump the manifest to stdout")
    args = ap.parse_args()

    rel = json.loads(get(API % (args.repo, args.tag)))
    version = rel["tag_name"].lstrip("vV")
    beta = bool(rel.get("prerelease"))
    sums = checksums(rel.get("assets", []))

    assets, missing = {}, []
    for a in rel.get("assets", []):
        key = classify(a["name"])
        if not key:
            continue
        digest = sums.get(a["name"])
        if not digest:
            # Refused rather than shipped: the app declines an asset with no checksum,
            # so writing one here would only move the failure to the user's machine.
            missing.append(a["name"])
            continue
        assets[key] = {"url": a["browser_download_url"],
                       "sha256": digest,
                       "size": a["size"],
                       "name": a["name"]}

    manifest = {
        "schema": 1,
        "version": version,
        "channel": "beta" if beta else "stable",
        "tag": rel["tag_name"],
        "date": (rel.get("published_at") or "")[:10],
        "summary": args.summary or (rel.get("name") or "").strip(),
        "notes_url": "%s#v%s" % (NOTES, version.replace(".", "-")),
        "assets": assets,
    }

    if missing:
        print("warning: no checksum for %s — left out of the manifest"
              % ", ".join(missing), file=sys.stderr)
    if not assets:
        print("error: the release has no recognised assets; is the build finished?",
              file=sys.stderr)
        return 1

    text = json.dumps(manifest, indent=2) + "\n"
    if args.show:
        sys.stdout.write(text)
        return 0

    name = "update-beta.json" if beta else "update.json"
    targets = [os.path.join(ROOT, "packaging", name)]
    if args.site:
        targets.append(os.path.join(args.site, name))
    for path in targets:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote %s  (%s %s, %d assets)"
              % (path, manifest["channel"], version, len(assets)))

    branch = "test" if beta else "main"
    print("\nthis file has to reach the %s branch — that is where the %s channel looks:"
          % (branch, manifest["channel"]))
    print("  git add packaging/%s && git commit -m 'manifest: %s' && git push origin %s"
          % (name, version, branch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
