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
from radar.http import FetchError, get_json, get_text  # noqa: E402

SOURCES = os.path.join(ROOT, "sources.json")

YC_HIRING = "https://raw.githubusercontent.com/yc-oss/api/main/companies/hiring.json"

# --------------------------------------------------------------------------- #
# URL harvesting — the highest-yield discovery mechanism
# --------------------------------------------------------------------------- #
#
# Community feeds already link straight at employers' ATS pages, and those URLs
# encode the board token. Harvesting them converts a second-hand community row
# into a first-party source we can read directly — better dates, better links,
# and it scales to a whole platform instead of one hand-added company at a time.
#
#   https://boards.greenhouse.io/acme/jobs/123   -> greenhouse token "acme"
#   https://jobs.lever.co/acme/uuid              -> lever token "acme"
#   https://jobs.ashbyhq.com/acme/uuid           -> ashby token "acme"
#
URL_PATTERNS = [
    ("greenhouse", re.compile(
        r"https?://(?:www\.)?(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/"
        r"(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"https?://jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"https?://(?:apply|jobs)\.workable\.com/([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(
        r"https?://(?:jobs|careers)\.smartrecruiters\.com/([a-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"https?://([a-z0-9_-]+)\.recruitee\.com", re.I)),
    ("breezy", re.compile(r"https?://([a-z0-9_-]+)\.breezy\.hr", re.I)),
    ("rippling", re.compile(r"https?://ats\.rippling\.com/(?:[a-z-]{2,5}/)?([a-z0-9_-]+)", re.I)),
]

#: Path segments that are part of the platform's own routing, not a company slug.
NOT_TOKENS = {"embed", "job", "jobs", "api", "www", "en", "en-us", "search",
              "companies", "board", "o", "p", "j", "careers", "apply"}


def harvest_from_urls(urls):
    """Extract (ats, token) pairs from a pile of job URLs."""
    found = {}
    for url in urls:
        if not url:
            continue
        for ats, pat in URL_PATTERNS:
            m = pat.search(url)
            if not m:
                continue
            token = m.group(1)
            if not token or token.lower() in NOT_TOKENS or len(token) < 2:
                continue
            found.setdefault((ats, token), 0)
            found[(ats, token)] += 1
            break
    return found

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


def seed_urls(limit):
    """Harvest board tokens from every job URL we have already collected.

    Returns pseudo-seeds of the form ("<Company>", "", ats, token) so the caller
    can probe the exact token rather than guessing it from the company name —
    far more reliable than name-based slug guessing.
    """
    listings = os.path.join(ROOT, "listings.json")
    urls, names = [], {}
    if os.path.exists(listings):
        with open(listings) as f:
            data = json.load(f)
        for r in data.get("listings", []) + data.get("closed", []):
            u = r.get("url") or ""
            urls.append(u)
            for ats, pat in URL_PATTERNS:
                m = pat.search(u)
                if m:
                    names[(ats, m.group(1))] = r.get("company", "") or m.group(1)
                    break

    pairs = harvest_from_urls(urls)
    ranked = sorted(pairs.items(), key=lambda kv: -kv[1])
    out = []
    for (ats, token), _count in ranked:
        out.append((names.get((ats, token), token), "", ats, token))
    return out[:limit] if limit else out


#: An open dataset mapping ~80k companies to the ATS they use and their board
#: slug, across 65 platforms. Enormous discovery value, but it is a third-party
#: dataset whose terms of use we have not reviewed, so it is strictly opt-in via
#: `--seed jobhive` and never consulted by default. Every candidate it yields is
#: still verified against the live board before being suggested.
JOBHIVE_ATS = "https://storage.stapply.ai/jobhive/v1/{ats}/companies.csv"


def seed_jobhive(limit, platforms=("greenhouse", "lever", "ashby")):
    """Pull company/slug pairs for platforms we already support."""
    import csv
    import io

    out = []
    for ats in platforms:
        try:
            text = get_text(JOBHIVE_ATS.format(ats=ats))
        except Exception as e:
            print(f"  (skipping {ats}: {e})")
            continue
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            slug = (row.get("slug") or "").strip()
            name = (row.get("name") or slug).strip()
            if slug:
                out.append((name, "", ats, slug))
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
    ap.add_argument("--seed", choices=("urls", "yc", "community", "jobhive", "file"),
                    default="urls",
                    help="urls = harvest ATS tokens from job links we already have "
                         "(highest yield); yc = probe Y Combinator companies; "
                         "community = upgrade feed-only companies; file = your list")
    ap.add_argument("--file", default="")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--append", action="store_true",
                    help="append newly found boards to sources.json")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    exact = []          # (name, ats, token) — token already known, just verify
    if args.seed == "urls":
        exact = [(n, a, t) for n, _w, a, t in seed_urls(args.limit)]
        seeds = []
    elif args.seed == "jobhive":
        exact = [(n, a, t) for n, _w, a, t in seed_jobhive(args.limit)]
        seeds = []
    elif args.seed == "yc":
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

    exact = [(n, a, t) for n, a, t in exact
             if (a, t.lower()) not in known_tokens]
    seeds = [(n, w) for n, w in seeds if n.lower() not in known_names]

    if exact:
        print(f"harvested {len(exact)} board token(s) from job URLs that are not "
              f"yet configured; verifying…\n")
    else:
        print(f"probing {len(seeds)} candidate companies "
              f"({len(known_names)} already configured)…\n")

    def verify_exact(name, ats, token):
        spec = adapters.get(ats)
        if spec is None:
            return None
        try:
            rows = spec({"name": name, "ats": ats, "token": token})
        except FetchError:
            return None
        except Exception:
            return None
        early = [r for r in rows if classify_level(r.title)]
        if not early:
            return None
        return {"name": name, "ats": ats, "token": token, "total": len(rows),
                "early": len(early), "sample": early[0].title[:70]}

    found = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        if exact:
            futs = {pool.submit(verify_exact, n, a, t): n for n, a, t in exact}
        else:
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
