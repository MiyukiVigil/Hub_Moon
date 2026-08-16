#!/usr/bin/env python3
"""Build the update manifest that gui/updater.py reads.

    python3 tools/build-update-manifest.py v1.1.0
    python3 tools/build-update-manifest.py v1.2.0-beta.1 --site ../self-website/hubmoon

Run it after the three build workflows have finished and attached their assets to
the GitHub release for that tag. It reads the release through the public API, takes
the SHA-256 of every asset from the `SHA256SUMS-*.txt` files the workflows publish
alongside them, and writes the small JSON the app fetches.

No login is needed. If `GITHUB_TOKEN` (or `GH_TOKEN`) happens to be set it is used,
which raises the API's limit from 60 requests an hour to 5,000 — worth having only on
a day when something else on the machine has already spent the anonymous budget.

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
import time
import urllib.error
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
    # Packages a package manager installs. Hub Moon never replaces these files
    # itself — they are listed so the app can fetch and verify the right one and
    # hand over the command that installs it.
    (r"^hub-moon.*\.pkg\.tar\.zst$",            "arch-package"),
    (r"^hub-moon.*\.deb$",                       "deb-package"),
    (r"^hub-moon.*\.rpm$",                       "rpm-package"),
]


class Fail(SystemExit):
    """An error the person running this can act on, without a traceback."""

    def __init__(self, *lines):
        super().__init__("\n".join(lines))


def _token():
    """A GitHub token, if one is around.

    Entirely optional — everything here works unauthenticated. It only raises the
    rate limit from 60 requests an hour to 5,000, which matters on a day when
    something else on this machine has already spent the anonymous budget.
    """
    return (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()


def get(url):
    headers = {"User-Agent": "hub-moon-manifest",
               "Accept": "application/vnd.github+json"}
    token = _token()
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        raise _explain(exc, url) from exc
    except urllib.error.URLError as exc:
        raise Fail("Could not reach GitHub: %s" % exc.reason) from exc


def _explain(exc, url):
    """Turn an HTTP status into the sentence that says what to do about it."""
    if exc.code == 404:
        return Fail(
            "No release found for that tag.",
            "",
            "Either the tag has not been pushed yet, or its build workflows have not",
            "finished and published a release. Check:",
            "  git push origin <tag>",
            "  https://github.com/%s/releases" % REPO,
        )
    if exc.code in (403, 429):
        reset = exc.headers.get("X-RateLimit-Reset")
        remaining = exc.headers.get("X-RateLimit-Remaining")
        if remaining == "0" and reset:
            try:
                mins = max(0, int((int(reset) - time.time()) / 60) + 1)
            except ValueError:
                mins = None
            return Fail(
                "GitHub's API rate limit is used up for this IP address.",
                "",
                "Unauthenticated callers get 60 requests an hour. It resets in about "
                "%s." % ("%d minute%s" % (mins, "" if mins == 1 else "s")
                         if mins is not None else "an hour"),
                "",
                "To raise it to 5,000, export a token with no scopes at all — this only",
                "reads public releases:",
                "  https://github.com/settings/tokens",
                "  export GITHUB_TOKEN=ghp_…",
            )
        return Fail("GitHub refused the request (%s). %s" % (exc.code, exc.reason))
    return Fail("GitHub answered %s %s for %s" % (exc.code, exc.reason, url))


MAX_NOTES = 14
MAX_NOTE_LEN = 160


def release_notes(body):
    """The GitHub release body as a short list of plain lines.

    Markdown headings, bullets and emphasis are stripped rather than rendered: the
    panel showing these is a list of sentences, and half-rendered markdown reads worse
    than none. Links keep their text and lose their target.
    """
    out, fenced = [], False
    for raw in str(body).splitlines():
        line = raw.strip()
        # A fence has to toggle state, not just be skipped: matching the ``` lines
        # alone let everything *between* them through as if it were prose.
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith(("---", "<!--", "|")):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)             # headings
        line = re.sub(r"^[-*+]\s+", "", line)              # bullets
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)  # links
        line = re.sub(r"[*_`]{1,2}", "", line)             # emphasis, code spans
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_NOTE_LEN:
            line = line[:MAX_NOTE_LEN - 1].rstrip() + "…"
        out.append(line)
        if len(out) >= MAX_NOTES:
            break
    return out


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
    if rel.get("draft"):
        raise Fail("That release is still a draft — its assets are not public yet.",
                   "Publish it on GitHub, then run this again.")
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
        # The release body, trimmed to the lines worth showing in a small panel. The
        # app reads these from its cache after updating, so What's New works offline —
        # which is the normal case, since the machine has just restarted.
        "notes": release_notes(rel.get("body") or ""),
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
