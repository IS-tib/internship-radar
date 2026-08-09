"""
Posting-date handling with explicit provenance and precision.

The single most important rule in this module: **never invent precision we do not
have.** A job board that says "Posted 30+ Days Ago" tells us the job is *at least*
30 days old — it does not tell us the job was posted exactly 30 days ago. The old
scraper collapsed those into a concrete date, which made stale roles look freshly
dated and put fabricated timestamps in the output.

Every date we emit therefore carries:

  value      the best ISO date we can defend (or "" when we truly do not know)
  precision  how much that value can be trusted (see Precision below)
  field      the *source field name* the value came from, so the number is auditable
  raw        the untouched source value, for debugging and re-parsing

Precision levels
----------------
exact        a real timestamp from the source (Greenhouse first_published,
             Ashby publishedAt, Lever createdAt, SmartRecruiters releasedDate).
day          the source gave a date with no time component.
approximate  derived from relative text with a definite number ("5 days ago").
at_least     derived from an open-ended relative string ("30+ days ago"). The
             value is the *newest* the job could be; it may be much older.
unknown      the source exposed nothing usable. value is "".

Ranking and the "new" badge only ever trust `exact` and `day`. Everything else is
displayed with a qualifier so a reader is never misled.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field as _field

UTC = dt.timezone.utc

EXACT = "exact"
DAY = "day"
APPROXIMATE = "approximate"
AT_LEAST = "at_least"
UNKNOWN = "unknown"

#: Precisions we are willing to rank and badge on.
TRUSTED = frozenset({EXACT, DAY})

#: A posting older than this that is *still listed* is treated as an evergreen
#: requisition (see DateInfo.is_evergreen) rather than a genuinely old posting.
#: Palantir, for example, keeps Lever reqs open for years; their createdAt is
#: honest but says nothing about the current hiring cycle.
EVERGREEN_DAYS = 400


@dataclass(frozen=True)
class DateInfo:
    """A posting date plus everything needed to audit it."""

    value: str = ""            # ISO YYYY-MM-DD, or "" when unknown
    precision: str = UNKNOWN
    field: str = ""            # source field name, e.g. "first_published"
    raw: object = None         # untouched source value

    @property
    def known(self) -> bool:
        return bool(self.value) and self.precision != UNKNOWN

    @property
    def trusted(self) -> bool:
        """True when the value is precise enough to rank/badge on."""
        return self.known and self.precision in TRUSTED

    def age_days(self, today: dt.date | None = None) -> int | None:
        if not self.value:
            return None
        today = today or dt.datetime.now(UTC).date()
        try:
            return (today - dt.date.fromisoformat(self.value[:10])).days
        except ValueError:
            return None

    def is_evergreen(self, today: dt.date | None = None) -> bool:
        """An old-but-still-listed requisition. Honest date, misleading as 'posted'."""
        age = self.age_days(today)
        return age is not None and age >= EVERGREEN_DAYS

    def label(self, today: dt.date | None = None) -> str:
        """Human-facing rendering that never overstates what we know."""
        if not self.known:
            return "unknown"
        if self.precision == AT_LEAST:
            age = self.age_days(today)
            return f"≥{age}d ago" if age is not None else "unknown"
        if self.precision == APPROXIMATE:
            return f"~{self.value}"
        if self.is_evergreen(today):
            # Show month granularity — the day is real but meaningless for an
            # evergreen req, and a precise-looking old date invites misreading.
            return f"listed since {self.value[:7]}"
        return self.value

    def days_ago(self, today: dt.date | None = None) -> str:
        """Relative age for display, e.g. "0 days ago" / "1 day ago" / "34 days ago".

        Always derived from the stored timestamp at render time, never persisted,
        so a listing ages from "0 days ago" to "1 day ago" on its own without the
        scraper rewriting the row.

        Edge cases, all deliberate:
          * missing/unparseable date  -> "Unknown" (we do not invent a date)
          * timestamp in the future   -> clamped to "0 days ago" within a day of
            now (clock skew and timezone rounding are normal), "Unknown" beyond
            that, because a far-future date is bad data rather than a fresh post
          * open-ended "30+ days ago" -> "≥30 days ago", preserving that the
            value is a floor rather than a measurement
          * approximate values        -> prefixed "~"
          * anything over a year      -> "365+ days ago", which is also how an
            evergreen requisition reads
        """
        return format_days_ago(self.age_days(today), self.precision, self.known)

    def as_dict(self) -> dict:
        return {"value": self.value, "precision": self.precision, "field": self.field}


UNKNOWN_DATE = DateInfo()

#: Beyond this we stop counting precisely; "412 days ago" is noise, and the
#: bucket doubles as the signal that a requisition has been open a very long time.
LONG_AGO_DAYS = 365

#: How far into the future a timestamp may sit before we treat it as bad data
#: rather than clock skew between us and the source's timezone.
FUTURE_TOLERANCE_DAYS = 1

UNKNOWN_LABEL = "Unknown"


def format_days_ago(age: int | None, precision: str = EXACT, known: bool = True) -> str:
    """Render an age in days as human-readable text with correct pluralisation."""
    if not known or age is None:
        return UNKNOWN_LABEL

    if age < 0:
        # A posting dated in the future. Within a day it is almost certainly a
        # timezone/rounding artefact, so show it as brand new; beyond that the
        # source data is wrong and we say so rather than print "-12 days ago".
        if age >= -FUTURE_TOLERANCE_DAYS:
            age = 0
        else:
            return UNKNOWN_LABEL

    if age > LONG_AGO_DAYS:
        text = f"{LONG_AGO_DAYS}+ days ago"
    elif age == 1:
        text = "1 day ago"
    else:
        text = f"{age} days ago"

    if precision == AT_LEAST:
        return f"≥{text}"
    if precision == APPROXIMATE:
        return f"~{text}"
    return text


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #

def from_timestamp(value, field: str, unit: str = "ms") -> DateInfo:
    """Epoch timestamp -> DateInfo. Lever uses milliseconds, most feeds seconds."""
    if value in (None, "", 0):
        return UNKNOWN_DATE
    try:
        n = float(value)
    except (TypeError, ValueError):
        return UNKNOWN_DATE
    if n <= 0:
        return UNKNOWN_DATE
    seconds = n / 1000.0 if unit == "ms" else n
    # Guard against a feed handing us seconds where we expected ms (or vice
    # versa) — a 1970 or year-5000 date is a parsing failure, not a real posting.
    try:
        d = dt.datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return UNKNOWN_DATE
    if not (1990 < d.year < 2100):
        return UNKNOWN_DATE
    return DateInfo(d.date().isoformat(), EXACT, field, value)


def from_iso(value, field: str) -> DateInfo:
    """ISO-8601 string -> DateInfo.

    A value carrying a time component is `exact`; a bare YYYY-MM-DD is `day`.
    Timestamps are normalised to UTC so an evening-UTC post does not read as
    tomorrow (or yesterday) depending on the source's offset.
    """
    if not value:
        return UNKNOWN_DATE
    s = str(value).strip()
    if not s:
        return UNKNOWN_DATE

    # Bare date, no time.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return DateInfo(s, DAY, field, value)

    cleaned = s.replace("Z", "+00:00")
    # "2026-08-04 05:24:50 UTC" (Recruitee) -> ISO-parseable
    cleaned = re.sub(r"\s+UTC$", "+00:00", cleaned)
    cleaned = cleaned.replace(" ", "T", 1) if " " in cleaned[:11] else cleaned
    try:
        d = dt.datetime.fromisoformat(cleaned)
    except ValueError:
        # Last resort: a leading date we can still trust at day precision.
        m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
        return DateInfo(m.group(1), DAY, field, value) if m else UNKNOWN_DATE
    if d.tzinfo is not None:
        d = d.astimezone(UTC)
    if not (1990 < d.year < 2100):
        return UNKNOWN_DATE
    return DateInfo(d.date().isoformat(), EXACT, field, value)


_REL_DAYS = re.compile(r"(\d+)\s*(\+)?\s*day", re.I)
_REL_MONTHS = re.compile(r"(\d+)\s*(\+)?\s*month", re.I)


def from_relative(text, field: str, today: dt.date | None = None) -> DateInfo:
    """Workday-style relative strings.

    "Posted Today"        -> approximate, today
    "Posted Yesterday"    -> approximate, today-1
    "Posted 5 Days Ago"   -> approximate, today-5
    "Posted 30+ Days Ago" -> AT_LEAST, today-30  (job is *at least* 30 days old)

    The '+' case is the one the previous implementation got wrong: it stamped an
    exact date 30 days back, so every long-open Workday role looked like it was
    posted on precisely the same day.
    """
    if not text:
        return UNKNOWN_DATE
    t = str(text).lower()
    today = today or dt.datetime.now(UTC).date()

    if "just posted" in t or "today" in t:
        return DateInfo(today.isoformat(), APPROXIMATE, field, text)
    if "yesterday" in t:
        return DateInfo((today - dt.timedelta(days=1)).isoformat(), APPROXIMATE, field, text)

    m = _REL_DAYS.search(t)
    if m:
        days, plus = int(m.group(1)), bool(m.group(2))
        value = (today - dt.timedelta(days=days)).isoformat()
        return DateInfo(value, AT_LEAST if plus else APPROXIMATE, field, text)

    m = _REL_MONTHS.search(t)
    if m:
        months, plus = int(m.group(1)), bool(m.group(2))
        value = (today - dt.timedelta(days=30 * months)).isoformat()
        return DateInfo(value, AT_LEAST if plus else APPROXIMATE, field, text)

    return UNKNOWN_DATE


def pick(*candidates: DateInfo) -> DateInfo:
    """Return the most trustworthy candidate, preferring earlier arguments on ties.

    Adapters pass their preferred field first, so a source that exposes both a
    true publish date and a last-modified date resolves to the publish date.
    """
    order = {EXACT: 0, DAY: 1, APPROXIMATE: 2, AT_LEAST: 3, UNKNOWN: 4}
    best = UNKNOWN_DATE
    for c in candidates:
        if c and c.known and order[c.precision] < order[best.precision]:
            best = c
    return best
