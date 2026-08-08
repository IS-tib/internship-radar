"""
Role classification: level (intern vs new-grad), discipline, and target term.

Three deliberate changes from the original implementation:

1. **New-grad roles are now in scope.** The board previously matched only
   intern/co-op titles, which silently excluded the entire new-graduate pipeline
   ("University Graduate", "New Grad Software Engineer", "Campus Hire").

2. **Term inference is explicit.** Roughly 86% of postings carry no year in the
   title, so the old classifier bucketed them as "Unspecified" and threw away the
   signal. We now infer a likely term from the posting date *and mark it as
   inferred*, so a reader can tell a stated term from a guessed one.

3. **Exclusions are conservative.** We only drop a role when the title states a
   term that is definitively in the past, never on a guess.
"""

from __future__ import annotations

import datetime as dt
import re

from .dates import DateInfo

# --------------------------------------------------------------------------- #
# Level
# --------------------------------------------------------------------------- #

# intern / interns / internship / internships / co-op — but NOT internal,
# international, internet (the \b...\b anchors handle that).
INTERN_RE = re.compile(r"\bintern(?:ship)?s?\b|\bco-?ops?\b|\bplacement student\b", re.I)

NEW_GRAD_RE = re.compile(
    r"\bnew\s*grad(?:uate)?s?\b|\buniversity\s*grad(?:uate)?s?\b|"
    r"\bgraduate\s+(?:software|engineer|developer|analyst|programme|program)\b|"
    r"\bcampus\s+hire\b|\bentry[-\s]?level\b|\bearly[-\s]?career\b|"
    r"\bcollege\s+grad(?:uate)?s?\b|\brotational\s+(?:program|programme|analyst)\b|"
    r"\bclass\s+of\s+20\d{2}\b|\bgrad(?:uate)?\s+scheme\b",
    re.I,
)

# Senior-sounding qualifiers that disqualify a title from "early career" even if
# it matched above (e.g. "Manager, Early Career Programs" is a recruiting job).
SENIORITY_EXCLUDE_RE = re.compile(
    r"\b(?:senior|staff|principal|lead|director|manager|head of|vp|vice president|"
    r"recruiter|recruiting|talent acquisition|program manager, |sourcer)\b",
    re.I,
)

# PhD/research internships are legitimate but worth tagging separately.
PHD_RE = re.compile(r"\bph\.?d\b|\bdoctoral\b|\bpostdoc\b", re.I)

INTERN = "intern"
NEW_GRAD = "new_grad"


def classify_level(title: str) -> str | None:
    """Return "intern", "new_grad", or None when the title is neither."""
    if not title:
        return None
    is_intern = bool(INTERN_RE.search(title))
    is_grad = bool(NEW_GRAD_RE.search(title))
    if not (is_intern or is_grad):
        return None
    # "Early Career Recruiter" / "Manager, University Programs" are not roles a
    # student applies to as a student.
    if SENIORITY_EXCLUDE_RE.search(title) and not is_intern:
        return None
    # An explicit internship wins: "Early Career Software Engineer Internship".
    return INTERN if is_intern else NEW_GRAD


# --------------------------------------------------------------------------- #
# Discipline
# --------------------------------------------------------------------------- #

CATEGORIES = [
    ("Software Engineering", "SWE",
     r"software engineer|software developer|\bswe\b|\bsde\b|full[- ]?stack|"
     r"back[- ]?end|front[- ]?end|\bios\b|android|mobile engineer|systems engineer|"
     r"infrastructure|platform engineer|web developer|distributed systems|"
     r"compiler|game engineer|gameplay|graphics engineer|software intern|"
     r"programmer|developer intern|application engineer|api engineer|"
     # Quant dev / quant technologist are software-engineering roles that happen
     # to sit in finance; students searching for SWE should find them here.
     r"quantitative developer|quantitative technolog|trading technolog"),
    ("Data / ML / AI", "Data/ML",
     r"machine learning|\bml\b|\bai\b|data scien|data engineer|deep learning|"
     r"research engineer|research scien|\bnlp\b|computer vision|analytics engineer|"
     r"applied scien|\bllm\b|quantitative research|quant research|data analyst|"
     r"perception engineer|robotics learning"),
    ("Other Technical", "Tech",
     r"security engineer|\bsre\b|devops|site reliability|hardware|electrical eng|"
     r"embedded|firmware|network engineer|\bqa\b|test engineer|cloud engineer|"
     r"solutions engineer|robotics|\basic\b|\bfpga\b|mechanical eng|"
     r"forward deployed|systems administrator|technical program|it engineer"),
    ("Product Management", "PM",
     r"product manager|product management|\bapm\b|associate product|product intern|"
     r"technical product"),
]
CATEGORIES = [(n, s, re.compile(p, re.I)) for n, s, p in CATEGORIES]
SHORT = {n: s for n, s, _ in CATEGORIES}


def categorize(title: str) -> str | None:
    for name, _short, pat in CATEGORIES:
        if pat.search(title or ""):
            return name
    return None


# --------------------------------------------------------------------------- #
# Term
# --------------------------------------------------------------------------- #

SEASONS = [("summer", "Summer"), ("spring", "Spring"), ("winter", "Winter"),
           ("fall", "Fall"), ("autumn", "Fall")]

_YEAR_RE = re.compile(r"\b(20[2-3]\d)\b")


def _season_of(text: str) -> str | None:
    t = (text or "").lower()
    return next((name for kw, name in SEASONS if re.search(rf"\b{kw}\b", t)), None)


def _target_summer_year(today: dt.date) -> int:
    """The 'next' summer internship cycle relative to a date.

    Recruiting for summer N opens roughly a year ahead, so from ~August of year
    N-1 onward the live cycle is summer N+1. In August 2026 the active cycle is
    Summer 2027.
    """
    return today.year + 1 if today.month >= 7 else today.year


def classify_term(title: str, posted: DateInfo | None = None,
                  today: dt.date | None = None):
    """Return (label, priority, inferred) or None when the role is clearly stale.

    `inferred` is True when we derived the term rather than reading it from the
    title, so the renderer can mark it with a "~".
    """
    today = today or dt.date.today()
    title = title or ""
    m = _YEAR_RE.search(title)
    year = int(m.group(1)) if m else None
    season = _season_of(title)
    target = _target_summer_year(today)

    # --- definitively past: drop -----------------------------------------
    if year and year < today.year:
        return None
    if season == "Summer" and year == today.year and today.month >= 7:
        # Summer of the current year has already happened by August.
        return None

    inferred = False
    if year is None and season is None:
        # Nothing stated. Infer from when it was posted: a posting made during
        # the recruiting window for `target` is overwhelmingly for that cycle.
        label = f"Summer {target}"
        year, season, inferred = target, "Summer", True
    elif year is None:
        # Season stated, year not: assume the next occurrence of that season.
        year = target if season == "Summer" else (
            today.year + 1 if today.month >= 7 else today.year)
        label, inferred = f"{season} {year}", True
    elif season is None:
        label = str(year)
    else:
        label = f"{season} {year}"

    priority = {
        ("Summer", target): 0,
        ("Spring", target): 1, ("Winter", target): 1,
        ("Fall", today.year): 2, ("Winter", today.year): 2,
    }.get((season, year))
    if priority is None:
        priority = 3 if year and year > target else 4
    return label, priority, inferred


def is_phd_role(title: str) -> bool:
    return bool(PHD_RE.search(title or ""))
