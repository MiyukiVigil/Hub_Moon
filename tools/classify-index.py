#!/usr/bin/env python3
"""Classify the cached community index and print the histogram.

    python3 tools/classify-index.py --limit 200
    python3 tools/classify-index.py --limit 400 --all-family

This is how the thresholds in `gui/shapes.py` get chosen from the library rather than
from taste. Run it after changing them: a rule that files 80% of a real library under
one label is not a filter, and a rule that leaves half of it in "Other" is not either.

The index is read from the on-disk cache and never re-fetched. The curve files *are*
fetched, once each, into the permanent curve cache — which is exactly what the app's
own prefetch does, so a run of this also warms what the community tab will want.
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moondrop_control as mc      # noqa: E402
from gui import shapes             # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", default="011d", help="product id in hex (default 011d)")
    ap.add_argument("--limit", type=int, default=200, help="how many presets to read")
    ap.add_argument("--all-family", action="store_true",
                    help="include presets uploaded for other devices in the family")
    ap.add_argument("--workers", type=int, default=4,
                    help="concurrent fetches; keep this small, it is someone's CDN")
    args = ap.parse_args(argv)

    uuid = mc.PRODUCT_UUIDS[int(args.pid, 16)]
    path = os.path.join(mc.cache_dir(), "presets-%s.json" % uuid)
    if not os.path.exists(path):
        print("no cached index at %s — open the community tab once first" % path)
        return 1

    import json
    with open(path, encoding="utf-8") as fh:
        rows = json.load(fh)
    if not args.all_family:
        rows = [r for r in rows if r.get("productuuid") == uuid]

    def likes(r):
        try:
            return int(float(r.get("like") or 0))
        except (TypeError, ValueError):
            return 0

    rows.sort(key=likes, reverse=True)
    rows = [r for r in rows if r.get("file")][:args.limit]
    print("%d presets, %s" % (len(rows), "whole family" if args.all_family else "this device"))

    def one(row):
        try:
            bands, _dropped = mc.hub_preset_bands(row["file"], 8)
            return shapes.classify(bands), row
        except Exception as exc:                       # noqa: BLE001
            return "FAILED: %s" % type(exc).__name__, row

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(one, rows))

    counts = {}
    examples = {}
    for label, row in results:
        counts[label] = counts.get(label, 0) + 1
        examples.setdefault(label, []).append(row.get("title", "").strip()[:44])

    total = len(results) or 1
    print()
    for label in list(shapes.LABELS) + sorted(k for k in counts if k not in shapes.LABELS):
        n = counts.get(label, 0)
        if not n:
            continue
        print("%-14s %4d  %5.1f%%  %s" % (label, n, 100.0 * n / total,
                                          " · ".join(examples[label][:2])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
