#!/usr/bin/env python3
"""
internship-radar — a self-updating board of tech and product internships.

It reads the *public* job-board APIs of the companies in companies.json
(Greenhouse, Lever, Ashby), keeps the intern/co-op roles that match the target
seasons, and regenerates README.md + listings.json. A GitHub Action runs it on a
schedule and commits the diff, so the board stays current with no manual work.

Design notes:
  * Standard library only — nothing to install, runs anywhere Python 3.9+ runs.
  * Every role carries its REAL posting date, taken straight from the source API
    (Greenhouse first_published, Ashby publishedAt, Lever createdAt) — not the
    date this repo happened to see it. Roles are ranked newest-first.
  * Where a company publishes an actual application deadline (Greenhouse exposes
    one), it's shown; most tech internships just close when filled, so the Posted
    date is the real signal to apply early.
  * Boards that error (renamed token, downtime) are skipped with a warning rather
    than failing the whole run.
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

NEW_DAYS = 7          # a role posted within this many days is flagged 🆕
TIMEOUT = 25
UA = {"User-Agent": "internship-radar/2.0 (+https://github.com; job-board aggregator)"}
TODAY = dt.datetime.now(dt.timezone.utc).date()


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

# Matches intern / interns / internship / internships (but NOT internal,
# international, internet) plus co-op and early-career program titles.
INTERN = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b|\bearly career\b", re.I)

# Ordered: the first pattern that matches wins the category.
CATEGORIES = [
    ("Software Engineering", "SWE",
     r"software engineer|software developer|\bswe\b|full[- ]?stack|back[- ]?end|"
     r"front[- ]?end|\bios\b|android|mobile engineer|systems engineer|"
     r"infrastructure|platform engineer|web developer|distributed systems|"
     r"compiler|game engineer|gameplay|graphics engineer"),
    ("Data / ML / AI", "Data/ML",
     r"machine learning|\bml\b|\bai\b|data scien|data engineer|deep learning|"
     r"research engineer|research scien|\bnlp\b|computer vision|analytics engineer|"
     r"applied scien|\bllm\b"),
    ("Other Technical", "Tech",
     r"security engineer|\bsre\b|devops|site reliability|hardware|electrical eng|"
     r"embedded|firmware|network engineer|\bqa\b|test engineer|cloud engineer|"
     r"solutions engineer|robotics|\basic\b|\bfpga\b|mechanical eng|"
     r"forward deployed"),
    ("Product Management", "PM",
     r"product manager|product management|\bapm\b|associate product|product intern"),
]
CATEGORIES = [(name, short, re.compile(pat, re.I)) for name, short, pat in CATEGORIES]
SHORT = {name: short for name, short, _ in CATEGORIES}

SEASONS = [("summer", "Summer"), ("spring", "Spring"),
           ("winter", "Winter"), ("fall", "Fall"), ("autumn", "Fall")]


def categorize(title):
    for name, _short, pat in CATEGORIES:
        if pat.search(title):
            return name
    return None


def classify_term(title):
    """Return (label, priority) or None to exclude. Summer 2027 is the focus;
    Winter 2026 / Spring 2027 are included; anything clearly older (<=2025 or
    Summer 2026) is dropped. Lower priority sorts first when we tie-break."""
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

def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _iso(value):
    """Normalize a date (ISO string or epoch-ms) to YYYY-MM-DD, or '' if absent."""
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).date().isoformat()
    return str(value)[:10]


def _raw(company, title, location, url, posted, deadline, source):
    return {"company": company, "title": title.strip(),
            "location": (location or "").strip(), "url": url,
            "posted": _iso(posted), "deadline": _iso(deadline), "source": source}


def _post(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        **UA, "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_greenhouse(c):
    # The plain jobs endpoint already carries first_published + application_deadline.
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs")
    return [_raw(c["name"], j.get("title", ""), (j.get("location") or {}).get("name", ""),
                 j.get("absolute_url", ""), j.get("first_published") or j.get("updated_at"),
                 j.get("application_deadline"), "Greenhouse")
            for j in data.get("jobs", [])]


def fetch_lever(c):
    data = _get(f"https://api.lever.co/v0/postings/{c['token']}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append(_raw(c["name"], j.get("text", ""), cats.get("location", ""),
                        j.get("hostedUrl", ""), j.get("createdAt"), None, "Lever"))
    return out


def fetch_ashby(c):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}")
    return [_raw(c["name"], j.get("title", ""), j.get("locationName") or j.get("location", ""),
                 j.get("jobUrl") or j.get("applyUrl", ""), j.get("publishedAt"),
                 None, "Ashby")
            for j in data.get("jobs", [])]


def _workday_date(text):
    """Workday reports 'Posted Today' / 'Posted 5 Days Ago' / 'Posted 30+ Days Ago'
    rather than a timestamp; convert that to an approximate YYYY-MM-DD."""
    t = (text or "").lower()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    else:
        m = re.search(r"(\d+)\s*\+?\s*day", t)
        days = int(m.group(1)) if m else None
    if days is None:
        return ""
    return (TODAY - dt.timedelta(days=days)).isoformat()


def fetch_workday(c):
    """Workday's job feed is a paginated POST. Each company needs its tenant host
    and career-site slug — both visible in the company's myworkdayjobs.com URL,
    e.g. https://boeing.wd1.myworkdayjobs.com/EXTERNAL_CAREERS ->
    host 'boeing.wd1.myworkdayjobs.com', site 'EXTERNAL_CAREERS'."""
    host, site = c["host"], c["site"]
    tenant = c.get("tenant") or host.split(".")[0]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out, offset, total = [], 0, None
    while offset < 400:  # hard cap so a huge board can't run away
        data = _post(api, {"appliedFacets": {}, "limit": 20,
                           "offset": offset, "searchText": "intern"})
        posts = data.get("jobPostings") or []
        if not posts:
            break
        for j in posts:
            path = j.get("externalPath", "")
            url = f"https://{host}/en-US/{site}{path}" if path else ""
            out.append(_raw(c["name"], j.get("title", ""), j.get("locationsText", ""),
                            url, _workday_date(j.get("postedOn", "")), None, "Workday"))
        if total is None:
            total = data.get("total", len(posts))
        offset += 20
        if offset >= total:
            break
    return out


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever,
            "ashby": fetch_ashby, "workday": fetch_workday}


def collect(companies):
    """Fetch every board concurrently; boards that error are skipped, logged."""
    raw = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(FETCHERS[c["ats"]], c): c
                   for c in companies if c["ats"] in FETCHERS}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                rows = fut.result()
                raw += rows
                print(f"  ok   {c['name']:<20} {len(rows):>4} postings")
            except Exception as e:
                print(f"  skip {c['name']:<20} ({type(e).__name__})")
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
    """Track first_seen (when the radar first saw a role) for history, but the
    date we DISPLAY and rank by is the role's real posting date from the source.
    Roles that vanished from the boards (filled/closed) drop off."""
    prev = {}
    if os.path.exists(LISTINGS):
        with open(LISTINGS) as f:
            for item in json.load(f).get("listings", []):
                prev[item["key"]] = item.get("first_seen", TODAY.isoformat())

    for item in current:
        item["first_seen"] = prev.get(item["key"], TODAY.isoformat())
        # Rank/display by real posting date; fall back to first_seen if the
        # source didn't give one.
        item["date"] = item["posted"] or item["first_seen"]
        item["is_new"] = _age_days(item["date"]) <= NEW_DAYS
    return current


def _age_days(iso):
    try:
        return (TODAY - dt.date.fromisoformat(iso[:10])).days
    except (ValueError, TypeError):
        return 9999


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def _newest_first(items):
    # Most recent posting date first; unknown dates sink to the bottom.
    return sorted(items, key=lambda x: (x["date"] or "0000", x["company"]), reverse=True)


def _row(x):
    tag = " 🆕" if x["is_new"] else ""
    apply = f"[apply]({x['url']})" if x["url"] else "—"
    deadline = x["deadline"] or "—"
    return (f"| {x['company']} | {x['title']}{tag} | {SHORT[x['category']]} | "
            f"{x['term']} | {x['location'] or '—'} | {x['date']} | {deadline} | {apply} |")


HEAD = ("| Company | Role | Type | Term | Location | Posted | Deadline | Apply |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |")

BIG_TECH = [
    ("Google", "https://www.google.com/about/careers/applications/jobs/results/?employment_type=INTERN"),
    ("Amazon", "https://www.amazon.jobs/content/en/career-programs/university"),
    ("Meta", "https://www.metacareers.com/jobs?roles[0]=Internship"),
    ("Apple", "https://jobs.apple.com/en-us/search?team=internships-STDNT-INTRN"),
    ("Microsoft", "https://careers.microsoft.com/v2/global/en/students"),
    ("Uber", "https://www.uber.com/us/en/careers/teams/university/"),
    ("TikTok", "https://lifeattiktok.com/search"),
    ("Netflix", "https://explore.jobs.netflix.net/careers"),
    ("Snap", "https://careers.snap.com/jobs?type=Internship"),
    ("LinkedIn", "https://careers.linkedin.com/students"),
    ("Shopify", "https://www.shopify.com/careers/early-careers"),
]

# Finance / quant shops on custom or Oracle/Eightfold systems (no public API).
FINANCE = [
    ("JPMorgan", "https://careers.jpmorgan.com/us/en/students-and-graduates"),
    ("American Express", "https://www.americanexpress.com/en-us/careers/"),
    ("Capital One", "https://www.capitalonecareers.com/search-jobs"),
    ("Citadel", "https://www.citadel.com/careers/students-and-graduates/"),
    ("Two Sigma", "https://careers.twosigma.com/careers/"),
    ("Hudson River Trading", "https://www.hudsonrivertrading.com/careers/"),
]


def render_readme(items):
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ranked = _newest_first(items)
    fresh = [x for x in ranked if x["is_new"]]
    counts = {name: sum(1 for x in items if x["category"] == name)
              for name, _s, _p in CATEGORIES}

    L = []
    L.append("# internship-radar\n")
    L.append("A self-updating board of **tech & product internships**, scraped "
             "straight from company job boards and ranked **newest-first**. "
             "Focus: **Summer 2027** (plus Winter 2026 / Spring 2027). "
             f"Roles posted in the last {NEW_DAYS} days are flagged 🆕.\n")
    L.append(f"**{len(items)} open roles** across **{_company_count(items)} companies** · "
             f"updated {updated}  \n"
             + " · ".join(f"{SHORT[n]} {counts[n]}" for n, _s, _p in CATEGORIES) + "\n")
    L.append("> **Posted** is the role's *real* publish date from the source API, so "
             "the top of the list is genuinely the freshest. **Deadline** shows a date "
             "only when the company publishes one — most tech internships simply close "
             "when filled, so treat a fresh Posted date as the cue to apply early.\n")

    if fresh:
        L.append(f"## 🆕 Just posted — last {NEW_DAYS} days ({len(fresh)})\n")
        L.append(HEAD)
        L += [_row(x) for x in fresh]
        L.append("")

    L.append(f"## 📋 All open roles — newest first ({len(ranked)})\n")
    L.append(HEAD)
    L += [_row(x) for x in ranked]
    L.append("")

    L.append("## 🏢 Direct portals (no public API — apply on their sites)\n")
    L.append("These employers run custom/Oracle/Eightfold systems that can't be "
             "auto-scraped, so apply through their early-career portals directly.\n")
    L.append("**Big tech:** " + " · ".join(f"[{n}]({u})" for n, u in BIG_TECH) + "\n")
    L.append("**Finance & quant:** " + " · ".join(f"[{n}]({u})" for n, u in FINANCE) + "\n")

    L.append("---\n")
    L.append("### How it works\n")
    L.append("`scraper.py` (Python standard library only) calls the public job-board "
             "APIs of every company in `companies.json` — **Greenhouse, Lever, Ashby, and "
             "Workday** — concurrently, filters for intern/co-op roles in software, data/ML, "
             "other technical fields, and product, tags each with its real posting date and "
             "target term, and regenerates this file. A GitHub Action runs it every two "
             "hours and commits any changes.\n")
    L.append("### Add a company\n")
    L.append("For Greenhouse/Lever/Ashby, append `{ \"name\": \"...\", "
             "\"ats\": \"greenhouse|lever|ashby\", \"token\": \"...\" }` — the token is the "
             "board slug in the careers URL (`boards.greenhouse.io/<token>`, "
             "`jobs.lever.co/<token>`, `jobs.ashbyhq.com/<token>`). For Workday, use "
             "`{ \"name\": \"...\", \"ats\": \"workday\", \"host\": \"tenant.wdN.myworkdayjobs.com\", "
             "\"site\": \"CareerSiteSlug\" }` — both parts are visible in the company's "
             "myworkdayjobs.com URL. Open a PR.\n")
    L.append("### Roadmap\n")
    L.append("Workday/SmartRecruiters adapters (to reach more large employers), an "
             "optional web dashboard with filters, and email/RSS alerts on new 🆕 roles.\n")
    return "\n".join(L)


def _company_count(items):
    return len({x["company"] for x in items})


def main():
    with open(COMPANIES) as f:
        companies = json.load(f)
    print(f"scanning {len(companies)} company boards…")
    items = merge(refine(collect(companies)))
    items = _newest_first(items)

    with open(LISTINGS, "w") as f:
        json.dump({"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "count": len(items), "listings": items}, f, indent=2)
    with open(README, "w") as f:
        f.write(render_readme(items))
    print(f"\n{len(items)} matching roles → listings.json + README.md")


if __name__ == "__main__":
    main()
