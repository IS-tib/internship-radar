#!/usr/bin/env python3
"""
Verify every board token in sources.json actually resolves, and optionally prune
or disable the ones that don't.

This exists because a dead token is invisible in normal operation: the scraper
skips it and the board just quietly gets smaller. Twenty-six Ashby boards were
configured at one point and yielded six roles between them, because a chunk of
the tokens had been renamed and nothing ever reported it.

    python tools/verify_sources.py                 # report only
    python tools/verify_sources.py --check acme    # test one token, any adapter
    python tools/verify_sources.py --disable-dead  # mark 404s as enabled:false
    python tools/verify_sources.py --prune         # remove 404s outright
    python tools/verify_sources.py --json report.json

Exit code is non-zero when dead sources are found, so CI can fail on regressions.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from radar import adapters  # noqa: E402
from radar.classify import classify_level  # noqa: E402
from radar.http import FetchError, NotFound  # noqa: E402

SOURCES = os.path.join(ROOT, "sources.json")


def probe(source):
    """Fetch one source and summarise what came back."""
    name = source.get("name", "?")
    spec = adapters.get(source.get("ats", ""))
    if spec is None:
        return {"name": name, "status": "unknown_adapter", "count": 0, "early": 0}
    missing = spec.validate(source)
    if missing:
        return {"name": name, "status": "bad_config", "count": 0, "early": 0,
                "error": f"missing {missing}"}
    try:
        rows = spec(source)
    except NotFound as e:
        return {"name": name, "status": "dead", "count": 0, "early": 0, "error": str(e)}
    except FetchError as e:
        return {"name": name, "status": "error", "count": 0, "early": 0, "error": str(e)}
    except Exception as e:
        return {"name": name, "status": "adapter_error", "count": 0, "early": 0,
                "error": f"{type(e).__name__}: {e}"}

    early = sum(1 for r in rows if classify_level(r.title))
    dated = sum(1 for r in rows if r.posted.known)
    return {"name": name, "status": "ok" if rows else "empty", "count": len(rows),
            "early": early, "dated": dated,
            "ats": source.get("ats"), "token": source.get("token", "")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=SOURCES)
    ap.add_argument("--check", default="", help="probe a single token across adapters")
    ap.add_argument("--ats", default="", help="restrict --check to one adapter")
    ap.add_argument("--disable-dead", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)

    if args.check:
        kinds = [args.ats] if args.ats else ["greenhouse", "lever", "ashby",
                                             "workable", "recruitee", "breezy"]
        for kind in kinds:
            r = probe({"name": f"{args.check} ({kind})", "ats": kind, "token": args.check})
            flag = "OK  " if r["status"] == "ok" else "    "
            print(f"{flag}{kind:<16} {r['status']:<14} jobs={r['count']:<5} "
                  f"early-career={r['early']}")
        return 0

    with open(args.sources) as f:
        sources = json.load(f)

    print(f"probing {len(sources)} sources…\n")
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(probe, s): s for s in sources}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            if r["status"] not in ("ok",):
                print(f"  {r['status']:<15} {r['name']}"
                      + (f"  — {r.get('error','')}" if r.get("error") else ""))

    by = {}
    for r in results:
        by[r["status"]] = by.get(r["status"], 0) + 1
    total_jobs = sum(r["count"] for r in results)
    total_early = sum(r["early"] for r in results)

    print("\n── summary ──")
    for k, v in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<16} {v}")
    print(f"  {'total jobs':<16} {total_jobs}")
    print(f"  {'early-career':<16} {total_early}")

    # Boards that work but never carry early-career roles are noise; surface them
    # so the list can be trimmed deliberately rather than growing forever.
    barren = sorted(r["name"] for r in results
                    if r["status"] == "ok" and r["early"] == 0)
    if barren:
        print(f"\n  {len(barren)} healthy boards with 0 early-career roles right now")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"results": results, "summary": by}, f, indent=2)
        print(f"\nwrote {args.json}")

    dead_names = {r["name"] for r in results if r["status"] in ("dead", "unknown_adapter")}
    if dead_names and (args.disable_dead or args.prune):
        if args.prune:
            kept = [s for s in sources if s.get("name") not in dead_names]
        else:
            kept = []
            for s in sources:
                if s.get("name") in dead_names:
                    s = {**s, "enabled": False, "disabled_reason": "token 404"}
                kept.append(s)
        with open(args.sources, "w") as f:
            json.dump(kept, f, indent=2, ensure_ascii=False)
        action = "pruned" if args.prune else "disabled"
        print(f"{action} {len(dead_names)} dead source(s) in {args.sources}")

    return 1 if dead_names else 0


if __name__ == "__main__":
    raise SystemExit(main())
