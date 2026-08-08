"""
The normalised posting record plus URL/text normalisation helpers.

Every adapter emits `Posting` objects, so downstream code (classification,
dedupe, lifecycle, rendering) never has to care which ATS a row came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .dates import DateInfo, UNKNOWN_DATE

#: Query parameters that carry identity (keep) rather than tracking noise (drop).
_KEEP_QS = {"gh_jid", "jobId", "id", "job_id", "postingId", "lever-origin"}
_DROP_QS_PREFIX = ("utm_", "gh_src", "ref", "source", "src", "trk", "_ga")


def normalize_url(url: str) -> str:
    """Canonicalise a job URL so the same posting reached by different paths
    collapses to one identity.

    Lowercases the host, drops the fragment, strips tracking query parameters
    while keeping the ones that actually identify the job, and removes a
    trailing slash. Returns "" for falsy input.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not parts.netloc:
        return url.strip()

    qs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k in _KEEP_QS or not k.lower().startswith(_DROP_QS_PREFIX)
    ]
    qs.sort()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((
        parts.scheme.lower() or "https",
        parts.netloc.lower(),
        path,
        urlencode(qs),
        "",
    ))


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]+")

#: Noise that varies between postings of the same underlying role.
_TITLE_NOISE = re.compile(
    r"\b(?:20\d{2}|summer|spring|winter|fall|autumn|intern(?:ship)?s?|co-?op|"
    r"remote|hybrid|onsite|us|usa|uk|emea|apac|f/m/d|m/f/d|all genders|"
    r"[ivx]+|i{1,3})\b",
    re.I,
)


def norm_text(s: str) -> str:
    """Aggressive lowercase/alphanumeric normalisation for comparisons."""
    if not s:
        return ""
    s = _PUNCT.sub(" ", s.lower())
    return _WS.sub(" ", s).strip()


def norm_company(s: str) -> str:
    """Company name normalisation: drop legal suffixes and punctuation."""
    s = norm_text(s)
    s = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|co|gmbh|labs?|technologies|"
               r"technology|group|holdings|the)\b", " ", s)
    return _WS.sub(" ", s).strip()


def norm_title(s: str) -> str:
    """Title normalisation used for cross-source duplicate detection."""
    s = norm_text(s)
    s = _TITLE_NOISE.sub(" ", s)
    return _WS.sub(" ", s).strip()


_CITY_SPLIT = re.compile(r"[;,/|]| or | and ")


def norm_location(s: str) -> str:
    """Reduce a location string to its first, most specific component."""
    if not s:
        return ""
    first = _CITY_SPLIT.split(s)[0]
    return norm_text(first)


@dataclass
class Posting:
    """One job posting, normalised across every source."""

    company: str
    title: str
    url: str
    source: str                     # adapter label, e.g. "greenhouse"
    source_name: str = ""           # human label, e.g. "Greenhouse · Stripe"
    location: str = ""
    posted: DateInfo = field(default_factory=lambda: UNKNOWN_DATE)
    deadline: DateInfo = field(default_factory=lambda: UNKNOWN_DATE)
    ats_job_id: str = ""            # stable per-ATS identifier when available
    remote: bool | None = None
    department: str = ""
    description: str = ""           # plain text, used for near-duplicate checks
    is_first_party: bool = True     # False for community/aggregator feeds

    # Filled in by later stages.
    category: str = ""
    term: str = ""
    term_priority: int = 9
    level: str = ""                 # "intern" | "new_grad"
    identity: str = ""
    first_seen: str = ""
    last_seen: str = ""

    def __post_init__(self):
        self.company = (self.company or "").strip()
        self.title = _WS.sub(" ", (self.title or "").strip())
        self.location = (self.location or "").strip()
        self.url = (self.url or "").strip()

    # -- derived -----------------------------------------------------------
    @property
    def canonical_url(self) -> str:
        return normalize_url(self.url)

    @property
    def key_company(self) -> str:
        return norm_company(self.company)

    @property
    def key_title(self) -> str:
        return norm_title(self.title)

    @property
    def key_location(self) -> str:
        return norm_location(self.location)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["posted"] = self.posted.as_dict()
        d["deadline"] = self.deadline.as_dict()
        d["canonical_url"] = self.canonical_url
        # description is only needed in-process for similarity checks; keeping it
        # would balloon listings.json by megabytes.
        d.pop("description", None)
        return d
