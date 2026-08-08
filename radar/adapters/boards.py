"""
Adapters for the ATS platforms that expose a public, unauthenticated job board.

Each adapter documents the *authoritative* posting-date field for its platform.
Those choices were verified against live API responses rather than taken from
documentation, because several platforms expose more than one date and the
obvious-looking one is often "last modified" rather than "published".

  Greenhouse      first_published   (updated_at is last-modified — NOT the post date)
  Lever           createdAt         (epoch ms; the only date the API exposes)
  Ashby           publishedAt
  SmartRecruiters releasedDate
  Workable        published_on      (created_at is when the req was drafted)
  Recruitee       published_at
  Breezy HR       published_date
  Rippling ATS    createdOn         (detail endpoint only — see note below)
"""

from __future__ import annotations

from .. import dates
from ..http import get_json, NotFound
from ..models import Posting
from . import register


def _loc(*parts):
    return ", ".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# Greenhouse
# --------------------------------------------------------------------------- #

@register("greenhouse", requires=("token",))
def fetch_greenhouse(source):
    """Greenhouse board API.

    `boards-api.greenhouse.io` is canonical regardless of which frontend host
    (boards. / job-boards. / job-boards.eu.) the company links to publicly, so we
    only ever need the token. EU-resident tenants can be flagged with
    `"region": "eu"`, which switches to the EU API host.
    """
    token = source["token"]
    host = ("boards-api.eu.greenhouse.io" if source.get("region") == "eu"
            else "boards-api.greenhouse.io")
    data = get_json(f"https://{host}/v1/boards/{token}/jobs")

    out = []
    for j in data.get("jobs", []):
        posted = dates.pick(
            dates.from_iso(j.get("first_published"), "first_published"),
            # Deliberately NOT falling back to updated_at as a posting date: it
            # reflects the last edit, which makes long-open reqs look fresh.
        )
        out.append(Posting(
            company=source["name"],
            title=j.get("title", ""),
            url=j.get("absolute_url", ""),
            source="greenhouse",
            source_name=f"Greenhouse · {source['name']}",
            location=(j.get("location") or {}).get("name", ""),
            posted=posted,
            deadline=dates.from_iso(j.get("application_deadline"), "application_deadline"),
            ats_job_id=str(j.get("id") or ""),
            department=_first_dept(j),
        ))
    return out


def _first_dept(j):
    for m in j.get("metadata") or []:
        if m.get("name") in ("Department", "Career Page Posting Category"):
            v = m.get("value")
            if isinstance(v, list):
                return v[0] if v else ""
            return v or ""
    return ""


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #

@register("lever", requires=("token",))
def fetch_lever(source):
    """Lever postings API.

    `createdAt` (epoch ms) is the only timestamp Lever exposes publicly. For
    companies that keep evergreen requisitions open — Palantir is the classic
    example, with reqs created in 2016 still accepting applicants — this is an
    honest value that would be misleading if presented as "posted recently".
    The dates layer flags those via DateInfo.is_evergreen rather than hiding them.
    """
    token = source["token"]
    data = get_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
    if not isinstance(data, list):
        return []

    out = []
    for j in data:
        cats = j.get("categories") or {}
        out.append(Posting(
            company=source["name"],
            title=j.get("text", ""),
            url=j.get("hostedUrl") or j.get("applyUrl", ""),
            source="lever",
            source_name=f"Lever · {source['name']}",
            location=cats.get("location", "") or j.get("country", ""),
            posted=dates.from_timestamp(j.get("createdAt"), "createdAt", "ms"),
            ats_job_id=str(j.get("id") or ""),
            department=cats.get("team", "") or cats.get("department", ""),
            remote=(j.get("workplaceType") == "remote") or None,
            description=(j.get("descriptionPlain") or "")[:4000],
        ))
    return out


# --------------------------------------------------------------------------- #
# Ashby
# --------------------------------------------------------------------------- #

@register("ashby", requires=("token",))
def fetch_ashby(source):
    """Ashby public job-board API. `publishedAt` is authoritative."""
    token = source["token"]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")

    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        out.append(Posting(
            company=source["name"],
            title=j.get("title", ""),
            url=j.get("jobUrl") or j.get("applyUrl", ""),
            source="ashby",
            source_name=f"Ashby · {source['name']}",
            location=j.get("locationName") or j.get("location", ""),
            posted=dates.from_iso(j.get("publishedAt"), "publishedAt"),
            ats_job_id=str(j.get("id") or ""),
            department=j.get("department", "") or j.get("team", ""),
            remote=j.get("isRemote"),
            description=(j.get("descriptionPlain") or "")[:4000],
        ))
    return out


# --------------------------------------------------------------------------- #
# SmartRecruiters
# --------------------------------------------------------------------------- #

@register("smartrecruiters", requires=("token",), paginated=True)
def fetch_smartrecruiters(source):
    """SmartRecruiters postings API. `releasedDate` is documented as the date the
    posting was released, which is what we want (not `createdOn`)."""
    token = source["token"]
    out, offset, limit = [], 0, 100
    while offset < 1000:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{token}"
                        f"/postings?limit={limit}&offset={offset}")
        content = data.get("content") or []
        if not content:
            break
        for j in content:
            loc = j.get("location") or {}
            out.append(Posting(
                company=source["name"],
                title=j.get("name", ""),
                url=f"https://jobs.smartrecruiters.com/{token}/{j.get('id', '')}",
                source="smartrecruiters",
                source_name=f"SmartRecruiters · {source['name']}",
                location=_loc(loc.get("city"), loc.get("region"), loc.get("country")),
                posted=dates.pick(
                    dates.from_iso(j.get("releasedDate"), "releasedDate"),
                    dates.from_iso(j.get("createdOn"), "createdOn"),
                ),
                ats_job_id=str(j.get("id") or ""),
                department=(j.get("department") or {}).get("label", ""),
                remote=(loc.get("remote") is True) or None,
            ))
        total = data.get("totalFound", len(content))
        offset += limit
        if offset >= total:
            break
    return out


# --------------------------------------------------------------------------- #
# Workable
# --------------------------------------------------------------------------- #

@register("workable", requires=("token",))
def fetch_workable(source):
    """Workable's public widget account endpoint.

    `published_on` is the publication date; `created_at` is when the req was
    drafted and can predate publication by weeks.
    """
    token = source["token"]
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}")

    out = []
    for j in data.get("jobs", []):
        out.append(Posting(
            company=source["name"],
            title=j.get("title", ""),
            url=j.get("url") or j.get("application_url", ""),
            source="workable",
            source_name=f"Workable · {source['name']}",
            location=_loc(j.get("city"), j.get("state"), j.get("country")),
            posted=dates.pick(
                dates.from_iso(j.get("published_on"), "published_on"),
                dates.from_iso(j.get("created_at"), "created_at"),
            ),
            ats_job_id=str(j.get("shortcode") or j.get("id") or ""),
            department=j.get("department", ""),
            remote=(str(j.get("telecommuting", "")).lower() == "true") or None,
        ))
    return out


# --------------------------------------------------------------------------- #
# Recruitee
# --------------------------------------------------------------------------- #

@register("recruitee", requires=("token",))
def fetch_recruitee(source):
    """Recruitee offers API. Dates arrive as 'YYYY-MM-DD HH:MM:SS UTC'."""
    token = source["token"]
    data = get_json(f"https://{token}.recruitee.com/api/offers/")

    out = []
    for j in data.get("offers", []):
        if j.get("status") not in (None, "published"):
            continue
        out.append(Posting(
            company=source["name"],
            title=j.get("title", ""),
            url=j.get("careers_url") or j.get("careers_apply_url", ""),
            source="recruitee",
            source_name=f"Recruitee · {source['name']}",
            location=_loc(j.get("city"), j.get("country")),
            posted=dates.from_iso(j.get("published_at"), "published_at"),
            ats_job_id=str(j.get("id") or ""),
            department=j.get("department", ""),
        ))
    return out


# --------------------------------------------------------------------------- #
# Breezy HR
# --------------------------------------------------------------------------- #

@register("breezy", requires=("token",))
def fetch_breezy(source):
    """Breezy returns a bare JSON array. An empty array means 'no open reqs',
    not a dead token, so we do not treat it as an error."""
    token = source["token"]
    data = get_json(f"https://{token}.breezy.hr/json")
    if not isinstance(data, list):
        return []

    out = []
    for j in data:
        loc = j.get("location") or {}
        city = (loc.get("city") if isinstance(loc, dict) else "") or ""
        country = ""
        if isinstance(loc, dict):
            country = (loc.get("country") or {}).get("name", "") if isinstance(
                loc.get("country"), dict) else (loc.get("country") or "")
        out.append(Posting(
            company=source["name"],
            title=j.get("name", ""),
            url=j.get("url", ""),
            source="breezy",
            source_name=f"Breezy · {source['name']}",
            location=_loc(city, country),
            posted=dates.from_iso(j.get("published_date"), "published_date"),
            ats_job_id=str(j.get("id") or j.get("friendly_id") or ""),
            department=(j.get("department") or {}).get("name", "") if isinstance(
                j.get("department"), dict) else (j.get("department") or ""),
        ))
    return out


# --------------------------------------------------------------------------- #
# Rippling ATS
# --------------------------------------------------------------------------- #

@register("rippling", requires=("token",))
def fetch_rippling(source):
    """Rippling's board API.

    The list endpoint carries no dates at all — `createdOn` only appears on the
    per-job detail endpoint. Rather than fabricate a date we fetch detail for a
    bounded number of jobs whose titles look early-career, and leave the posting
    date unknown for the rest. `detail_budget` caps the extra requests.
    """
    token = source["token"]
    budget = int(source.get("detail_budget", 25))
    listed = get_json(f"https://ats.rippling.com/api/v2/board/{token}/jobs")
    items = listed if isinstance(listed, list) else listed.get("jobs", []) or []

    from ..classify import classify_level  # local import avoids a cycle

    out = []
    for j in items:
        jid = str(j.get("uuid") or j.get("id") or "")
        title = j.get("name") or j.get("title", "")
        posted = dates.UNKNOWN_DATE
        if jid and budget > 0 and classify_level(title):
            try:
                d = get_json(
                    f"https://ats.rippling.com/api/v2/board/{token}/jobs/{jid}")
                posted = dates.from_iso(d.get("createdOn"), "createdOn")
                budget -= 1
            except NotFound:
                budget -= 1
            except Exception:
                budget -= 1
        wl = j.get("workLocation") or {}
        out.append(Posting(
            company=source["name"],
            title=title,
            url=j.get("url") or f"https://ats.rippling.com/{token}/jobs/{jid}",
            source="rippling",
            source_name=f"Rippling · {source['name']}",
            location=_loc(wl.get("city"), wl.get("state"), wl.get("country")),
            posted=posted,
            ats_job_id=jid,
            department=j.get("department", "") or "",
        ))
    return out
