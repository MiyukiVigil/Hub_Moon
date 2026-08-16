"""The AutoEQ catalogue — 8,827 headphone corrections, searchable from the app.

AutoEQ (github.com/jaakkopasanen/AutoEq, MIT) publishes a parametric EQ for almost
every headphone that has been measured, computed against a target curve. Its export
format is REW's, which ``moondrop_control.parse_peq_text`` already reads — so the
work here is finding the right file, not understanding it.

**Why an index file rather than GitHub's API.** Listing AutoEQ's tree costs one
unauthenticated API request, and those are limited to 60 an hour *per IP*. Two people
behind the same NAT would start getting 403s; building this feature rate-limited the
machine that wrote it. So ``tools/build-autoeq-index.py`` flattens the tree to
``[model, source, form]`` triples and that 55 KB file is served from the site. One
request, cached for a week, and AutoEQ's own hosting is only touched when a profile
is actually applied.

**Two things this does not hide.** AutoEQ publishes a profile per *measurement*, so a
popular headphone appears several times under different sources — those are different
measurements of the same hardware and they do not agree, which is why the source is on
every row rather than being resolved to one "best" answer. And every AutoEQ profile
has **ten** filters where no Moondrop DAC here has more than eight, so applying one
always drops two; ``fit`` returns exactly which, for the caller to show.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

try:
    import moondrop_control as mc
except ImportError:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import moondrop_control as mc  # noqa: E402

INDEX_URLS = (
    "https://hubmoon.miyukivigil.tech/autoeq-index.json",
    "https://raw.githubusercontent.com/MiyukiVigil/Hub_Moon/main/packaging/autoeq-index.json",
)
# A week. The catalogue grows by a handful of headphones a month; re-fetching it more
# often than that spends someone's bandwidth to learn nothing.
INDEX_TTL = 7 * 24 * 3600
NET_TIMEOUT = 25

CREDIT = "AutoEQ by Jaakko Pasanen · MIT · github.com/jaakkopasanen/AutoEq"

# When one headphone has been measured by several people, this is the order the
# results are offered in. It is a **preference, not a verdict** — every row carries
# its source precisely so the choice stays the reader's. oratory1990 and crinacle are
# first because their rigs and methodology are the most widely reported on; everything
# unlisted keeps its alphabetical place behind them.
SOURCE_ORDER = ["oratory1990", "crinacle", "Rtings", "Innerfidelity",
                "Super Review", "Headphone.com Legacy"]


def _cache_path(name):
    return os.path.join(mc.cache_dir(), name)


def _get(url, timeout=NET_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": mc.HUB_UA})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=mc._hub_ssl_context()) as r:
        return r.read()


# ── the catalogue ────────────────────────────────────────────────────────────

def load_index(refresh=False, ttl=INDEX_TTL):
    """The catalogue, from disk when it is fresh enough.

    Raises if it cannot be had at all. A stale cache is preferred to a failure: a
    catalogue from last month still finds the HD 600, and refusing to open the browser
    because a CDN is down would be a worse answer than an slightly old list.
    """
    path = _cache_path("autoeq-index.json")
    if not refresh:
        try:
            if time.time() - os.path.getmtime(path) < ttl:
                with open(path, encoding="utf-8") as fh:
                    return json.load(fh)
        except (OSError, json.JSONDecodeError):
            pass

    last = None
    for url in INDEX_URLS:
        try:
            data = json.loads(_get(url).decode("utf-8", "replace"))
        except Exception as exc:
            last = exc
            continue
        if isinstance(data, dict) and data.get("models"):
            try:
                os.makedirs(mc.cache_dir(), exist_ok=True)
                with open(path + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                os.replace(path + ".tmp", path)
            except OSError:
                pass
            return data
        last = ValueError("no models in the index at %s" % url)

    try:                                    # stale beats nothing
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        raise (last or RuntimeError("could not load the AutoEQ catalogue")) from last


def rows(index):
    """The catalogue as dicts, which is what everything downstream wants."""
    return [{"model": m[0], "source": m[1], "form": m[2]}
            for m in (index.get("models") or []) if len(m) >= 3]


def search(index, query, limit=200):
    """Find headphones. Ranked, because 8,827 rows need it.

    Three tiers, most specific first:

    0. the model name *starts* with the query — "sennheiser" before anything that
       merely mentions it,
    1. the query starts a word inside the name — "hd 600" finds "Sennheiser HD 600",
    2. it appears anywhere in the name, or names the measurer — "rtings".

    Within a tier the order is alphabetical by model and then SOURCE_ORDER, so the
    six published measurements of the HD 600 arrive together, oratory1990 first.

    Note what tier 1 does *not* do: it does not prefer a better-known brand. "aria"
    returns the CVJ Aria before the Moondrop Aria, because both match equally well and
    C sorts before M. Ranking by brand would mean holding an opinion about which
    manufacturer a search meant, and there is no honest way to hold that opinion.
    """
    q = " ".join(str(query or "").lower().split())
    found = []
    for r in rows(index):
        name = r["model"].lower()
        if not q:
            rank = 3
        elif name.startswith(q):
            rank = 0
        elif (" " + name).find(" " + q) >= 0:
            rank = 1
        elif q in name or q in r["source"].lower():
            rank = 2
        else:
            continue
        src = SOURCE_ORDER.index(r["source"]) if r["source"] in SOURCE_ORDER \
            else len(SOURCE_ORDER)
        found.append((rank, r["model"].lower(), src, r))
    found.sort(key=lambda t: t[:3])
    return [t[3] for t in found[:limit]] if limit else [t[3] for t in found]


def profile_url(index, row):
    """Rebuild the file's URL. Every one of the 8,827 paths follows this shape —
    checked against the whole tree rather than assumed, see the index builder."""
    base = index.get("base") or INDEX_URLS[0]
    suffix = index.get("suffix") or " ParametricEQ.txt"
    quote = urllib.parse.quote
    return "%s%s/%s/%s/%s" % (base, quote(row["source"]), quote(row["form"]),
                              quote(row["model"]), quote(row["model"] + suffix))


def fetch_profile(index, row):
    """The raw ParametricEQ text for one headphone, cached on disk."""
    key = "autoeq-%s.txt" % "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in "%s-%s-%s" % (row["source"], row["form"], row["model"]))[:120]
    path = _cache_path(key)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        pass
    text = _get(profile_url(index, row)).decode("utf-8", "replace")
    try:
        os.makedirs(mc.cache_dir(), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError:
        pass
    return text


def fit(text, band_count):
    """Parse a profile and cut it down to what the device can hold.

    Returns ``(bands, preamp, dropped, warnings)``. `dropped` is never hidden from the
    caller: AutoEQ always publishes ten filters, so on an eight-band DAC two of them
    are always left behind, and a user who is told "applied" without being told that
    has been told half the truth.
    """
    parsed = mc.parse_peq_text(text)
    kept, dropped = mc.fit_peq_to_bands(parsed["filters"], band_count)
    bands = [{"index": f["index"], "type": f["type"],
              "frequency": float(f["frequency"]), "gain": float(f["gain"]),
              "q": float(f["q"])} for f in kept]
    # Pad the unused slots so the device is written a complete profile rather than
    # keeping whatever the previous curve had in the tail.
    for i in range(len(bands), band_count):
        bands.append({"index": i, "type": "disabled", "frequency": 1000.0,
                      "gain": 0.0, "q": 1.0})
    return bands, parsed["preamp"], dropped, parsed["warnings"]
