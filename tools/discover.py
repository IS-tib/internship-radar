#!/usr/bin/env python3
"""
Automated board discovery: turn company seed lists into new, verified sources.

This is the recursive half of coverage. Rather than hand-curating tokens forever,
this walks seed lists of companies, guesses the board token from the company
name/domain, probes the ATS APIs, and emits only the ones that actually resolve
*and* currently carry early-career roles.

Seeds available today:

  yc          Y Combinator's directory of companies currently hiring
              (yc-oss/api mirror — ~6k companies, refreshed daily)
  community   companies already appearing in the community feeds; if a company
              shows up there with real openings, it very likely has a public
              board we could read first-party instead
  file        a newline-delimited list of company names you supply

    python tools/discover.py --seed yc --limit 300
    python tools/discover.py --seed community
    python tools/discover.py --seed yc --limit 500 --append

Nothing is written to sources.json unless --append is passed, and only boards
that returned HTTP 200 with at least one early-career role are ever suggested —
so this cannot introduce invented companies or dead tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from radar import adapters  # noqa: E402
from radar.classify import classify_level  # noqa: E402
from radar.http import FetchError, get_json  # noqa: E402

SOURCES = os.path.join(ROOT, "sources.json")

YC_HIRING = "https://raw.githubusercontent.com/yc-oss/api/main/companies/hiring.json"

#: Order matters: cheapest/most common platform first so we stop early.
PROBE_ORDER = ("greenhouse", "ashby", "lever")


def token_candidates(name: str, website: str = "") -> list[str]:
    """Plausible board slugs for a company, most likely first."""
    base = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    cands = [base]

    # Domain stem is often the real token when the display name has extra words.
    m = re.search(r"https?://(?:www\.)?([a-z0-9-]+)\.", (website or "").lower())
    if m:
        stem = m.group(1).replace("-", "")
        if stem and stem != base:
            cands.append(stem)

    # "Acme Labs" -> "acme"; "Acme AI" -> "acme"
    trimmed = re.sub(r"(labs?|technologies|technology|inc|io|ai|hq|app|software)$", "", base)
    if trimmed and trimmed != base and len(trimmed) > 2:
        cands.append(trimmed)

    seen, out = set(), []
    for c in cands:
        if c and len(c) > 2 and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:3]


def probe_company(name, website=""):
    """Try each platform x candidate token; return the first live board."""
    for token in token_candidates(name, website):
        for ats in PROBE_ORDER:
            spec = adapters.get(ats)
            try:
                rows = spec({"name": name, "ats": ats, "token": token})
            except FetchError:
                continue
            except Exception:
                continue
            if not rows:
                continue
            early = [r for r in rows if classify_level(r.title)]
            if early:
                return {"name": name, "ats": ats, "token": token,
                        "total": len(rows), "early": len(early),
                        "sample": early[0].title[:70]}
    return None


def seed_yc(limit):
    data = get_json(YC_HIRING)
    rows = data if isinstance(data, list) else data.get("companies", [])
    out = []
    for c in rows:
        nm = c.get("name")
        if nm:
            out.append((nm, c.get("website", "")))
    return out[:limit] if limit else out


def seed_community(limit):
    """Companies seen in community feeds — candidates for first-party upgrade."""
    listings = os.path.join(ROOT, "listings.json")
    if not os.path.exists(listings):
        return []
    with open(listings) as f:
        data = json.load(f)
    names = []
    seen = set()
    for r in data.get("listings", []):
        if r.get("is_first_party"):
            continue
        nm = r.get("company", "").strip()
        k = nm.lower()
        if nm and k not in seen:
            seen.add(k)
            names.append((nm, ""))
    return names[:limit] if limit else names


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", choices=("yc", "community", "file"), default="community")
    ap.add_argument("--file", default="")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--append", action="store_true",
                    help="append newly found boards to sources.json")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    if args.seed == "yc":
        seeds = seed_yc(args.limit)
    elif args.seed == "community":
        seeds = seed_community(args.limit)
    else:
        with open(args.file) as f:
            seeds = [(line.strip(), "") for line in f if line.strip()][:args.limit]

    with open(SOURCES) as f:
        existing = json.load(f)
    known_tokens = {(s.get("ats"), (s.get("token") or "").lower()) for s in existing}
    known_names = {s.get("name", "").lower() for s in existing}

    seeds = [(n, w) for n, w in seeds if n.lower() not in known_names]
    print(f"probing {len(seeds)} candidate companies "
          f"({len(known_names)} already configured)…\n")

    found = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(probe_company, n, w): n for n, w in seeds}
        for fut in as_completed(futs):
            r = fut.result()
            if not r:
                continue
            if (r["ats"], r["token"].lower()) in known_tokens:
                continue
            found.append(r)
            print(f"  FOUND {r['ats']:<12} {r['name'][:28]:<28} "
                  f"{r['early']:>3} early-career / {r['total']:>4}  “{r['sample']}”")

    print(f"\n{len(found)} new board(s) with live early-career roles")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(found, f, indent=2)
        print(f"wrote {args.json}")

    if args.append and found:
        for r in found:
            existing.append({"name": r["name"], "ats": r["ats"], "token": r["token"],
                             "provenance": "auto-discovered"})
        with open(SOURCES, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        print(f"appended {len(found)} source(s) to sources.json")
    elif found:
        print("(re-run with --append to add these to sources.json)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
