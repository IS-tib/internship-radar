"""
README rendering.

The table now shows *how much we trust* each posting date rather than printing a
bare number that may be approximate or fabricated:

    2026-08-04       exact date from the source API
    ~2026-08-04      approximate (derived from "posted 4 days ago")
    ≥34d ago         source only said "30+ days ago" — a floor, not a date
    listed since 2016-02   evergreen requisition, open for years
    unknown          the source publishes no posting date at all
"""

from __future__ import annotations

import datetime as dt

from .classify import SHORT, CATEGORIES
from .dates import DateInfo, TRUSTED

NEW_DAYS = 7
TABLE_CAP = 300
FRESH_CAP = 120


def _date_cell(row, today):
    p = row.get("posted") or {}
    info = DateInfo(p.get("value", ""), p.get("precision", "unknown"), p.get("field", ""))
    return info.label(today)


def _is_new(row, today):
    """Only trusted dates earn the 🆕 badge — never an approximation."""
    p = row.get("posted") or {}
    if p.get("precision") not in TRUSTED or not p.get("value"):
        return False
    try:
        age = (today - dt.date.fromisoformat(p["value"][:10])).days
    except ValueError:
        return False
    return 0 <= age <= NEW_DAYS


def _row(x, today):
    tag = " 🆕" if _is_new(x, today) else ""
    apply = f"[apply]({x['url']})" if x.get("url") else "—"
    dl = (x.get("deadline") or {}).get("value") or "—"
    term = x.get("term", "")
    if x.get("term_inferred"):
        term = f"~{term}"
    level = "Intern" if x.get("level") == "intern" else "New grad"
    return (f"| {x.get('company','')} | {x.get('title','')}{tag} | "
            f"{SHORT.get(x.get('category',''), '—')} | {level} | {term} | "
            f"{x.get('location') or '—'} | {_date_cell(x, today)} | {dl} | {apply} |")


HEAD = ("| Company | Role | Type | Level | Term | Location | Posted | Deadline | Apply |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |")

BIG_TECH = [
    ("Google", "https://www.google.com/about/careers/applications/jobs/results/?employment_type=INTERN"),
    ("Amazon", "https://www.amazon.jobs/content/en/career-programs/university"),
    ("Meta", "https://www.metacareers.com/jobs?roles[0]=Internship"),
    ("Apple", "https://jobs.apple.com/en-us/search?team=internships-STDNT-INTRN"),
    ("Microsoft", "https://careers.microsoft.com/v2/global/en/students"),
    ("Uber", "https://www.uber.com/us/en/careers/teams/university/"),
    ("TikTok", "https://lifeattiktok.com/search"),
    ("Netflix", "https://explore.jobs.netflix.net/careers"),
]
FINANCE = [
    ("JPMorgan", "https://careers.jpmorgan.com/us/en/students-and-graduates"),
    ("Morgan Stanley", "https://www.morganstanley.com/careers/students-graduates"),
    ("Goldman Sachs", "https://www.goldmansachs.com/careers/students/"),
    ("Citadel", "https://www.citadel.com/careers/students-and-graduates/"),
    ("Two Sigma", "https://careers.twosigma.com/careers/"),
    ("Hudson River Trading", "https://www.hudsonrivertrading.com/careers/"),
    ("SIG", "https://careers.sig.com/campus"),
    ("D. E. Shaw", "https://www.deshaw.com/careers/students"),
]


def render(rows, metrics, health, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    updated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    fresh = [r for r in rows if _is_new(r, today)]
    counts = {name: sum(1 for r in rows if r.get("category") == name)
              for name, _s, _p in CATEGORIES}
    interns = metrics["by_level"].get("intern", 0)
    grads = metrics["by_level"].get("new_grad", 0)

    L = []
    L.append("# internship-radar\n")
    L.append("A self-updating board of **software, data/ML, and product roles for "
             "students and new graduates**, scraped straight from company job "
             "boards and ranked **newest-first**.\n")
    L.append(f"**{len(rows)} open roles** · **{metrics['companies']} companies** · "
             f"**{metrics['sources_ok']}/{metrics['sources_configured']} sources healthy** · "
             f"updated {updated}  \n"
             f"{interns} internships · {grads} new-grad · "
             + " · ".join(f"{SHORT[n]} {counts[n]}" for n, _s, _p in CATEGORIES) + "\n")

    L.append("> **On dates.** Every row shows the real posting date from the "
             "source API where one exists. Where a platform only publishes "
             "something vague, we say so instead of inventing precision:\n"
             ">\n"
             "> | Shown | Means |\n"
             "> | --- | --- |\n"
             "> | `2026-08-04` | exact publish timestamp from the source |\n"
             "> | `~2026-08-04` | approximate — source said e.g. \"posted 4 days ago\" |\n"
             "> | `≥34d ago` | source said \"30+ days ago\"; this is a floor, not a date |\n"
             "> | `listed since 2016-02` | evergreen requisition, open for years |\n"
             "> | `unknown` | the platform publishes no posting date |\n"
             ">\n"
             f"> Only exact dates earn the 🆕 badge. A `~` before a term "
             "(e.g. `~Summer 2027`) means the term was inferred from the posting "
             "date because the title didn't state one.\n")

    if fresh:
        L.append(f"## 🆕 Just posted — last {NEW_DAYS} days ({len(fresh)})\n")
        L.append(HEAD)
        L += [_row(x, today) for x in fresh[:FRESH_CAP]]
        if len(fresh) > FRESH_CAP:
            L.append(f"\n_+{len(fresh) - FRESH_CAP} more in the full list below._")
        L.append("")

    L.append(f"## 📋 All open roles — newest first ({len(rows)})\n")
    L.append(HEAD)
    L += [_row(x, today) for x in rows[:TABLE_CAP]]
    if len(rows) > TABLE_CAP:
        L.append(f"\n_Showing the newest {TABLE_CAP} of {len(rows)} — the complete, "
                 f"machine-readable set is in_ `listings.json`.")
    L.append("")

    L.append("## 🏢 Direct portals (no public API — apply on their sites)\n")
    L.append("**Big tech:** " + " · ".join(f"[{n}]({u})" for n, u in BIG_TECH) + "\n")
    L.append("**Finance & quant:** " + " · ".join(f"[{n}]({u})" for n, u in FINANCE) + "\n")

    L.append("---\n")
    L.append("### Data quality this run\n")
    L.append(f"- **{metrics['dates_trusted_pct']}%** of roles carry an exact "
             f"posting timestamp from the source ({metrics['dates_trusted']}/{len(rows)}).\n"
             f"- **{metrics['first_party_pct']}%** come from a company's own board "
             f"rather than a community feed.\n"
             f"- **{metrics['dedupe']['merged']}** duplicates merged, "
             f"**{metrics['dedupe']['near_dupes']}** near-duplicates collapsed.\n"
             f"- **{metrics['roles_closed_tracked']}** recently-closed roles tracked "
             f"(so a filled role disappears deliberately, not silently).\n")

    dead = [n for n, h in health.items() if h["status"] == "not_found"]
    if dead:
        L.append(f"<details><summary>{len(dead)} source(s) returned 404 this run "
                 f"(token likely renamed)</summary>\n\n" +
                 "\n".join(f"- {n}" for n in sorted(dead)) + "\n\n</details>\n")

    L.append("### How it works\n")
    L.append("`scraper.py` reads `sources.json` and fans out across the public "
             "job-board APIs of every configured employer — **Greenhouse, Lever, "
             "Ashby, Workday, SmartRecruiters, Workable, Recruitee, Breezy, and "
             "Rippling** — plus community feeds for employers with no public API. "
             "Results are classified (intern vs new-grad, discipline, target term), "
             "de-duplicated across sources, reconciled against the previous run to "
             "track openings and closures, and rendered here. Standard library "
             "only; a GitHub Action runs it twice daily and commits the diff.\n")
    L.append("### Add a company\n")
    L.append("Append an entry to `sources.json`:\n\n"
             "```json\n"
             '{ "name": "Acme", "ats": "greenhouse", "token": "acme" }\n'
             "```\n\n"
             "The token is the board slug in the careers URL "
             "(`job-boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`, "
             "`jobs.ashbyhq.com/<token>`, `apply.workable.com/<token>`). "
             "Run `python tools/verify_sources.py --check acme` to confirm it "
             "resolves before opening a PR.\n")
    L.append("### Credits\n")
    L.append("Community feeds: [SimplifyJobs](https://github.com/SimplifyJobs/Summer2026-Internships) "
             "and [vanshb03](https://github.com/vanshb03/Summer2027-Internships), both "
             "building on the Pitt CSC / Simplify ecosystem. Merged and deduped "
             "against first-party board data so nothing shows twice.\n")
    return "\n".join(L)
