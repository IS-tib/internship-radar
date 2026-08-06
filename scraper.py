#!/usr/bin/env python3
"""
internship-radar — scrapes tech/PM internship postings from company applicant-
tracking systems (Greenhouse, Lever, Ashby), classifies them by role and term,
and regenerates listings.json + README.md.

Run it directly (`python scraper.py`); GitHub Actions runs it on a schedule and
commits the diff, so the repo stays current on its own. Standard library only —
no dependencies to install.
"""

import datetime as dt
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
COMPANIES = os.path.join(HERE, "companies.json")
LISTINGS = os.path.join(HERE, "listings.json")
README = os.path.join(HERE, "README.md")
NEW_DAYS = 3  # a listing is flagged 🆕 for this many days after first seen
UA = {"User-Agent": "internship-radar/1.0 (+github actions)"}

TODAY = dt.datetime.now(dt.timezone.utc).date()


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

INTERN = re.compile(r"\b(intern|internship|co-?op)\b", re.I)

# Ordered: the first pattern that matches wins the category.
CATEGORIES = [
    ("Software Engineering",
     r"software engineer|software developer|\bswe\b|full[- ]?stack|back[- ]?end|"
     r"front[- ]?end|\bios\b|android|mobile engineer|systems engineer|"
     r"infrastructure|platform engineer|web developer|distributed systems"),
    ("Data / ML / AI",
     r"machine learning|\bml\b|\bai\b|data scien|data engineer|deep learning|"
     r"research engineer|research scien|nlp|computer vision|analytics engineer|"
     r"applied scien"),
    ("Other Technical",
     r"security engineer|\bsre\b|devops|site reliability|hardware|electrical eng|"
     r"embedded|firmware|network engineer|qa engineer|test engineer|"
     r"cloud engineer|solutions engineer|robotics|\basic\b|fpga"),
    ("Product Management",
     r"product manager|product management|\bapm\b|associate product|product intern"),
]
CATEGORIES = [(name, re.compile(pat, re.I)) for name, pat in CATEGORIES]

SEASONS = [("summer", "Summer"), ("spring", "Spring"),
           ("winter", "Winter"), ("fall", "Fall"), ("autumn", "Fall")]


def categorize(title):
    for name, pat in CATEGORIES:
        if pat.search(title):
            return name
    return None


def classify_term(title):
    """Return (label, priority) or None to exclude. Lower priority sorts first.
    Summer 2027 is the focus; Winter 2026 / Spring 2027 are included; anything
    clearly older (<=2025 or Summer 2026) is dropped."""
    t = title.lower()
    ym = re.search(r"\b20(2[5-9])\b", title)
    year = int(ym.group(0)) if ym else None
    season = next((n for kw, n in SEASONS if kw in t), None)

    if year and year <= 2025:
        return None
    if season == "Summer" and year == 2026:
        return None

    label = (f"{season} {year}" if season and year
             else season or (str(year) if year else "Unspecified"))
    priority = {
        ("Summer", 2027): 0,
        ("Spring", 2027): 1, ("Winter", 2027): 1,
        ("Winter", 2026): 2, ("Fall", 2026): 2,
    }.get((season, year))
    if priority is None:
        priority = 3 if label == "Unspecified" else 4
    return label, priority


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #

def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _iso(value):
    """Normalize a posting date (ISO string or epoch-ms) to YYYY-MM-DD."""
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).date().isoformat()
    return str(value)[:10]


def fetch_greenhouse(company, token):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return [_raw(company, j.get("title", ""), (j.get("location") or {}).get("name", ""),
                 j.get("absolute_url", ""), _iso(j.get("updated_at")), "Greenhouse")
            for j in data.get("jobs", [])]


def fetch_lever(company, token):
    data = _get(f"https://api.lever.co/v0/postings/{token}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append(_raw(company, j.get("text", ""), cats.get("location", ""),
                        j.get("hostedUrl", ""), _iso(j.get("createdAt")), "Lever"))
    return out


def fetch_ashby(company, token):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false")
    return [_raw(company, j.get("title", ""), j.get("locationName") or j.get("location", ""),
                 j.get("jobUrl") or j.get("applyUrl", ""), _iso(j.get("publishedAt")), "Ashby")
            for j in data.get("jobs", [])]


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def _raw(company, title, location, url, posted, source):
    return {"company": company, "title": title.strip(), "location": location.strip(),
            "url": url, "posted": posted, "source": source}


def collect(companies):
    """Fetch every board concurrently; boards that error (dead token, etc.) are
    skipped with a logged warning."""
    raw = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(FETCHERS[c["ats"]], c["name"], c["token"]): c
                   for c in companies if c["ats"] in FETCHERS}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                rows = fut.result()
                raw += rows
                print(f"  ok   {c['name']:<22} {len(rows):>4} postings")
            except Exception as e:
                print(f"  skip {c['name']:<22} ({type(e).__name__})")
    return raw


# --------------------------------------------------------------------------- #
# Refine + merge
# --------------------------------------------------------------------------- #

def refine(raw):
    """Keep intern/co-op roles in our categories and target terms; dedupe."""
    out, seen = [], set()
    for r in raw:
        title = r["title"]
        if not INTERN.search(title):
            continue
        category = categorize(title)
        if not category:
            continue
        term = classify_term(title)
        if term is None:
            continue
        key = r["url"] or f"{r['company']}|{title}|{r['location']}"
        if key in seen:
            continue
        seen.add(key)
        out.append({**r, "key": key, "category": category,
                    "term": term[0], "priority": term[1]})
    return out


def merge(current):
    """Preserve first_seen for roles we've seen before; today for new ones.
    Roles that vanished from the boards (filled/closed) simply drop off."""
    prev = {}
    if os.path.exists(LISTINGS):
        with open(LISTINGS) as f:
            for item in json.load(f).get("listings", []):
                prev[item["key"]] = item.get("first_seen", TODAY.isoformat())

    for item in current:
        item["first_seen"] = prev.get(item["key"], TODAY.isoformat())
        age = (TODAY - dt.date.fromisoformat(item["first_seen"])).days
        item["is_new"] = age <= NEW_DAYS
    return current


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def _newest_first(iso):
    try:
        return -dt.date.fromisoformat(iso).toordinal()
    except ValueError:
        return 0


def _sort(items):
    # by term priority, then most-recently-added, then company name
    return sorted(items, key=lambda x: (x["priority"], _newest_first(x["first_seen"]), x["company"]))


def _row(x):
    tag = " 🆕" if x["is_new"] else ""
    apply = f"[apply]({x['url']})" if x["url"] else "—"
    return (f"| {x['company']} | {x['title']}{tag} | {x['term']} | "
            f"{x['location'] or '—'} | {x['first_seen']} | {apply} |")


def render_readme(items):
    order = ["Software Engineering", "Data / ML / AI", "Other Technical", "Product Management"]
    by_cat = {c: [x for x in items if x["category"] == c] for c in order}
    new = _sort([x for x in items if x["is_new"]])
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    L = []
    L.append("# internship-radar\n")
    L.append("Auto-updated listings of **tech and product internships** "
             "(Summer 2027 focus; also Winter 2026 / Spring 2027), scraped straight "
             "from company job boards every few hours. New roles are flagged 🆕.\n")
    L.append(f"**{len(items)} open roles** · updated {updated} · "
             + " · ".join(f"{c.split()[0]}: {len(by_cat[c])}" for c in order) + "\n")
    L.append("> Deadlines are shown only when a posting states one — most tech "
             "internships close when filled, so the **Added** date (when this repo "
             "first saw the role) is the signal to apply early.\n")

    if new:
        L.append(f"## 🆕 Just added (last {NEW_DAYS} days)\n")
        L.append("| Company | Role | Term | Location | Added | Apply |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        L += [_row(x) for x in new[:40]]
        L.append("")

    for c in order:
        rows = _sort(by_cat[c])
        if not rows:
            continue
        L.append(f"## {c} ({len(rows)})\n")
        L.append("| Company | Role | Term | Location | Added | Apply |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        L += [_row(x) for x in rows]
        L.append("")

    L.append("---\n")
    L.append("### How it works\n")
    L.append("A Python scraper hits the public job-board APIs of the companies in "
             "`companies.json` (Greenhouse, Lever, Ashby), filters for intern/co-op "
             "roles in software, data/ML, other technical fields, and product, and "
             "regenerates this file. A GitHub Action runs it on a schedule and commits "
             "the changes. To add a company, add its board token to `companies.json`.\n")
    return "\n".join(L)


def main():
    with open(COMPANIES) as f:
        companies = json.load(f)
    print(f"scanning {len(companies)} company boards…")
    items = merge(refine(collect(companies)))
    items.sort(key=lambda x: (x["priority"], x["company"]))

    with open(LISTINGS, "w") as f:
        json.dump({"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "count": len(items), "listings": items}, f, indent=2)
    with open(README, "w") as f:
        f.write(render_readme(items))
    print(f"\n{len(items)} matching roles → listings.json + README.md")


if __name__ == "__main__":
    main()
