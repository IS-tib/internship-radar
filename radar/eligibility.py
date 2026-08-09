"""
Undergraduate eligibility filtering.

The goal is a dataset a university undergraduate can actually apply to. That
means excluding roles restricted to PhD or Master's candidates, and excluding
experienced/senior postings — without stripping out legitimate undergrad roles
that merely *contain* a scary-looking word.

Two real examples from a production run show why naive keyword matching fails:

    "Product Manager Intern"                 <- contains "Manager", but is an
                                                ordinary undergrad internship
    "Machine Learning Engineer Intern - Lead Ads"
                                             <- "Lead" is the name of the
                                                product surface, not a seniority

The rule that resolves both: **seniority language is only meaningful on a
non-internship posting.** An internship is by definition not a staff-level role,
so for intern postings we check the degree requirement only. For new-grad
postings, where "Senior Software Engineer" would be a genuine mismatch, we apply
the seniority and experience checks too.

Every exclusion returns a machine-readable reason so a run can report *why* a
role was dropped rather than silently shrinking the board.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Advanced-degree requirements  (exclude at any level)
# --------------------------------------------------------------------------- #

PHD_RE = re.compile(
    r"\bph\.?\s?-?\s?d\.?\b|\bphd\b|\bdoctoral\b|\bdoctorate\b|\bpost[-\s]?doc",
    re.I,
)

MASTERS_ONLY_RE = re.compile(
    # "Master's Data Science Internship", "MS/PhD", "MS or PhD", "M.S. students"
    r"\bmaster'?s?\b(?!\s*(?:of\s+ceremonies))|"
    r"\bm\.\s?s\.\b|\bmsc\b|\bm\.?eng\b|\bmba\b|"
    r"\bms\s*/\s*phd\b|\bms\s+or\s+phd\b",
    re.I,
)

GRAD_STUDENT_RE = re.compile(
    r"\bgraduate\s+student|\bgrad\s+student|\bgraduate[-\s]level\b|"
    r"\bgraduate\s+研究|\bgraduate\s+program\s*\(\s*phd",
    re.I,
)

#: Positive undergraduate signals — these override a soft exclusion.
UNDERGRAD_RE = re.compile(
    r"\bundergraduate\b|\bundergrad\b|\bbachelor'?s?\b|\bb\.?s\.?c?\.?\b(?=\s|$)|"
    r"\bfreshman\b|\bsophomore\b|\bjunior\s+year\b|\brising\s+(?:junior|senior|sophomore)\b|"
    r"\bfirst[-\s]year\b|\bsecond[-\s]year\b|\bclass\s+of\s+20\d{2}\b",
    re.I,
)

# --------------------------------------------------------------------------- #
# Seniority / experience  (only applied to non-internship postings)
# --------------------------------------------------------------------------- #

SENIOR_RE = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\bdistinguished\b|"
    r"\bdirector\b|\bvice\s+president\b|\bvp\b|\bhead\s+of\b|"
    r"\bmanager,\s|\bengineering\s+manager\b|\bteam\s+lead\b|\btech\s+lead\b|"
    r"\blead\s+(?:software|engineer|developer|data|ml|machine)\b|"
    r"\b(?:ii|iii|iv)\b|\blevel\s*[3-9]\b|\bl[4-9]\b",
    re.I,
)

EXPERIENCE_RE = re.compile(
    r"\b(\d+)\s*\+?\s*(?:-\s*\d+\s*)?year(?:s)?\b[^.]{0,40}?"
    r"(?:experience|industry|professional|working)|"
    r"(?:experience|background)[^.]{0,30}?\b(\d+)\s*\+\s*year",
    re.I,
)

#: Years of experience at or above this bar means the role is not entry-level.
MAX_YEARS_FOR_ENTRY = 2


def _mentions_advanced_degree(text: str):
    """Return a reason string when the text demands a graduate degree."""
    if not text:
        return None
    if PHD_RE.search(text):
        return "phd_required"
    if GRAD_STUDENT_RE.search(text):
        return "graduate_students_only"
    if MASTERS_ONLY_RE.search(text):
        return "masters_required"
    return None


def _experience_years(text: str):
    """Largest 'N years experience' figure in the text, or None."""
    best = None
    for m in EXPERIENCE_RE.finditer(text or ""):
        for g in m.groups():
            if g and g.isdigit():
                best = max(best or 0, int(g))
    return best


def undergrad_eligible(title: str, level: str = "intern", description: str = ""):
    """Decide whether an undergraduate could apply.

    Returns (eligible: bool, reason: str). `reason` is "ok" when eligible and a
    short machine-readable code otherwise, so the pipeline can report a
    breakdown of what it filtered and why.
    """
    title = title or ""
    desc = description or ""

    # A stated undergraduate audience settles it, even if the posting also
    # mentions graduate students as *also* welcome.
    title_says_undergrad = bool(UNDERGRAD_RE.search(title))

    # --- degree requirements ------------------------------------------------
    reason = _mentions_advanced_degree(title)
    if reason and not title_says_undergrad:
        return False, reason

    # The description is only consulted when the title is silent, and only for
    # unambiguous phrasing, because plenty of postings mention a PhD as a
    # "nice to have" or describe the team rather than the candidate.
    if not reason and desc:
        strict = re.search(
            r"(?:must|required|require[sd]?|only|seeking|candidates?\s+must)"
            r"[^.]{0,60}?(?:ph\.?d|doctoral|master'?s)|"
            r"(?:ph\.?d|master'?s)[^.]{0,25}?(?:students?\s+only|candidates?\s+only|"
            r"required|is\s+required)",
            desc, re.I)
        if strict and not UNDERGRAD_RE.search(desc):
            return False, "advanced_degree_in_description"

    # --- seniority / experience --------------------------------------------
    # Skipped for internships: an internship is never a staff-level role, and
    # applying these patterns to titles like "Product Manager Intern" or
    # "ML Engineer Intern - Lead Ads" would wrongly discard valid roles.
    if level != "intern":
        if SENIOR_RE.search(title):
            return False, "senior_role"
        years = _experience_years(title) or _experience_years(desc)
        if years is not None and years > MAX_YEARS_FOR_ENTRY:
            return False, "experience_required"

    return True, "ok"


def is_undergrad_targeted(title: str) -> bool:
    """True when a title explicitly names an undergraduate audience."""
    return bool(UNDERGRAD_RE.search(title or ""))
