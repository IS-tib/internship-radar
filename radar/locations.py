"""
US location detection and normalisation.

Job boards express location in wildly inconsistent ways. A sample of real rows
from one production run:

    San Jose, CA              US, CA, Santa Clara        Remote in USA
    NYC                       Chicago, United States     Remote in Canada
    London, UK                Toronto, ON, Canada        2 Locations
    Boston, MA; Seattle, WA   London, UK; Paris, France  Remote

So the classifier has to handle state abbreviations, full state names, reversed
"country, state, city" ordering, city-only shorthand (NYC/SF/LA/DC), multi-value
strings, and remote phrasing — and it must distinguish "Remote in USA" from a
bare "Remote".

The rule for ambiguity is deliberate and conservative: **only claim a role is US
based on positive evidence.** A bare "Remote" or an opaque "2 Locations" returns
None (unknown), not True. Filtering on `is_us(...) is True` therefore excludes
the unknowns rather than silently importing international roles.
"""

from __future__ import annotations

import re
import unicodedata


def _fold(s: str) -> str:
    """Strip accents so 'Montréal' matches 'montreal' and 'São Paulo' matches.

    Without this, accented spellings fall through every pattern and end up
    classified as "unknown" — the right outcome by luck, but for the wrong
    reason, and it would misclassify an accented *US* city too.
    """
    if not s:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
}
NAME_TO_ABBR = {v.lower(): k for k, v in STATES.items()}
NAME_TO_ABBR["washington dc"] = "DC"
NAME_TO_ABBR["washington d c"] = "DC"

#: Shorthand that appears bare in real postings, mapped to a canonical form.
CITY_ALIASES = {
    "nyc": ("New York", "NY"), "new york city": ("New York", "NY"),
    "sf": ("San Francisco", "CA"), "sfo": ("San Francisco", "CA"),
    "san fran": ("San Francisco", "CA"),
    "bay area": ("San Francisco Bay Area", "CA"),
    "sf bay area": ("San Francisco Bay Area", "CA"),
    "silicon valley": ("San Jose", "CA"),
    "la": ("Los Angeles", "CA"), "socal": ("Los Angeles", "CA"),
    "dc": ("Washington", "DC"), "d c": ("Washington", "DC"),
    "washington d.c.": ("Washington", "DC"),
    "boston": ("Boston", "MA"), "chicago": ("Chicago", "IL"),
    "seattle": ("Seattle", "WA"), "austin": ("Austin", "TX"),
    "atlanta": ("Atlanta", "GA"), "denver": ("Denver", "CO"),
    "philadelphia": ("Philadelphia", "PA"), "philly": ("Philadelphia", "PA"),
    "miami": ("Miami", "FL"), "dallas": ("Dallas", "TX"),
    "houston": ("Houston", "TX"), "phoenix": ("Phoenix", "AZ"),
    "portland": ("Portland", "OR"), "pittsburgh": ("Pittsburgh", "PA"),
    "san diego": ("San Diego", "CA"), "los angeles": ("Los Angeles", "CA"),
    "san francisco": ("San Francisco", "CA"), "san jose": ("San Jose", "CA"),
    "palo alto": ("Palo Alto", "CA"), "mountain view": ("Mountain View", "CA"),
    "sunnyvale": ("Sunnyvale", "CA"), "santa clara": ("Santa Clara", "CA"),
    "redmond": ("Redmond", "WA"), "bellevue": ("Bellevue", "WA"),
    "cambridge ma": ("Cambridge", "MA"), "menlo park": ("Menlo Park", "CA"),
    "new york": ("New York", "NY"),
}

US_COUNTRY = re.compile(
    r"\b(?:u\.?\s?s\.?\s?a\.?|u\.?\s?s\.?|united\s+states(?:\s+of\s+america)?)\b",
    re.I,
)

#: Countries/regions that positively mark a location as NOT US. Kept explicit
#: rather than "anything unrecognised is foreign" so a US city we haven't
#: catalogued isn't silently dropped.
NON_US = re.compile(
    r"\b(?:canada|ontario|quebec|british\s+columbia|alberta|toronto|vancouver|"
    r"montreal|ottawa|waterloo|mississauga|calgary|"
    r"united\s+kingdom|u\.?k\.?|england|scotland|wales|london|manchester|"
    r"edinburgh|cambridge,?\s+uk|oxford|bristol|"
    r"ireland|dublin|france|paris|germany|berlin|munich|hamburg|"
    r"netherlands|amsterdam|spain|madrid|barcelona|portugal|lisbon|"
    r"italy|milan|rome|switzerland|zurich|geneva|sweden|stockholm|"
    r"norway|oslo|denmark|copenhagen|finland|helsinki|poland|warsaw|krakow|"
    r"czech|prague|austria|vienna|belgium|brussels|greece|athens|"
    r"romania|bucharest|hungary|budapest|"
    r"india|bangalore|bengaluru|hyderabad|mumbai|delhi|pune|chennai|gurgaon|noida|"
    r"china|beijing|shanghai|shenzhen|hangzhou|guangzhou|"
    r"japan|tokyo|osaka|korea|seoul|singapore|malaysia|kuala\s+lumpur|"
    r"indonesia|jakarta|thailand|bangkok|vietnam|hanoi|ho\s+chi\s+minh|"
    r"philippines|manila|taiwan|taipei|hong\s+kong|"
    r"australia|sydney|melbourne|brisbane|new\s+zealand|auckland|"
    r"israel|tel\s+aviv|herzliya|uae|dubai|abu\s+dhabi|saudi|riyadh|"
    r"qatar|doha|turkey|istanbul|egypt|cairo|"
    r"brazil|sao\s+paulo|s[aã]o\s+paulo|mexico|guadalajara|mexico\s+city|"
    r"argentina|buenos\s+aires|chile|santiago|colombia|bogota|peru|lima|"
    r"south\s+africa|cape\s+town|johannesburg|nigeria|lagos|kenya|nairobi|"
    r"serbia|belgrade|novi\s+sad|croatia|zagreb|ukraine|kyiv|kiev|"
    r"bulgaria|sofia|slovakia|bratislava|slovenia|ljubljana|lithuania|vilnius|"
    r"latvia|riga|estonia|tallinn|iceland|reykjavik|luxembourg|malta|cyprus|"
    r"armenia|yerevan|georgia\s+\(country\)|tbilisi|kazakhstan|almaty|"
    r"pakistan|karachi|lahore|islamabad|bangladesh|dhaka|sri\s+lanka|colombo|"
    r"nepal|kathmandu|morocco|casablanca|tunisia|tunis|ghana|accra|"
    r"uruguay|montevideo|costa\s+rica|san\s+jos[eé],\s*costa\s+rica|panama|"
    r"ecuador|quito|bolivia|paraguay|venezuela|caracas|"
    r"emea|apac|latam|anz|europe|asia[-\s]pacific)\b",
    re.I,
)

REMOTE_RE = re.compile(r"\bremote|\bwork\s+from\s+home\b|\bwfh\b|\bdistributed\b", re.I)

#: Strings that carry no geographic information at all.
OPAQUE_RE = re.compile(
    r"^\s*(?:\d+\s*(?:\+)?\s*locations?|multiple\s+locations?|various|"
    r"multiple|tbd|flexible|anywhere|global|worldwide|n/?a|-|—)\s*$", re.I)

SPLIT_RE = re.compile(r"\s*(?:;|\||/|\bor\b|\band\b)\s*|\s{2,}")

_ABBR_RE = re.compile(r"\b([A-Z]{2})\b")


def _components(raw: str) -> list[str]:
    """Split a location string into independently classifiable parts."""
    if not raw:
        return []
    parts = [p.strip() for p in SPLIT_RE.split(raw) if p and p.strip()]
    return parts or [raw.strip()]


def _classify_part(part: str):
    """Return True (US), False (non-US), or None (no evidence) for one component."""
    if not part or OPAQUE_RE.match(part):
        return None

    folded = _fold(part)

    # Non-US evidence wins over a bare state-like abbreviation: "Ontario, CA"
    # is Canada, and "CA" there is a country code, not California.
    if NON_US.search(folded):
        return False
    if US_COUNTRY.search(folded):
        return True

    low = re.sub(r"[.,]", " ", folded.lower())
    low = re.sub(r"\s+", " ", low).strip()

    # Bare city shorthand ("NYC", "SF") or a known US city name.
    if low in CITY_ALIASES:
        return True
    # "Remote in USA" already matched US_COUNTRY; a remote string with a US city
    # ("Remote - Austin") also counts.
    for alias in CITY_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return True

    # Full state name anywhere in the component.
    for name in NAME_TO_ABBR:
        if re.search(rf"\b{re.escape(name)}\b", low):
            return True

    # Two-letter state abbreviation, uppercase in the original string.
    for m in _ABBR_RE.finditer(part):
        if m.group(1) in STATES:
            return True

    return None


def is_us(raw: str):
    """True / False / None for a whole location string.

    True when any component is positively US (a role open in Boston *and*
    London is reachable by a US undergraduate). False when every component is
    positively non-US. None when there is no evidence either way — a bare
    "Remote", "2 Locations", or an unrecognised city.
    """
    parts = _components(raw)
    if not parts:
        return None
    verdicts = [_classify_part(p) for p in parts]
    if any(v is True for v in verdicts):
        return True
    if verdicts and all(v is False for v in verdicts):
        return False
    return None


def normalize(raw: str) -> str:
    """Tidy a location for display without inventing information."""
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw).strip()
    parts = _components(raw)

    out = []
    for p in parts[:3]:
        low = re.sub(r"[.,]", " ", p.lower())
        low = re.sub(r"\s+", " ", low).strip()
        if low in CITY_ALIASES:
            city, st = CITY_ALIASES[low]
            out.append(f"{city}, {st}")
            continue
        if REMOTE_RE.search(p) and US_COUNTRY.search(p):
            out.append("Remote (US)")
            continue
        # "US, CA, Santa Clara" -> "Santa Clara, CA"
        m = re.match(r"^\s*(?:US|USA|United States)\s*,\s*([A-Z]{2})\s*,\s*(.+)$",
                     p, re.I)
        if m and m.group(1).upper() in STATES:
            out.append(f"{m.group(2).strip()}, {m.group(1).upper()}")
            continue
        out.append(p)

    seen, uniq = set(), []
    for o in out:
        if o.lower() not in seen:
            seen.add(o.lower())
            uniq.append(o)
    text = "; ".join(uniq)
    if len(parts) > 3:
        text += f" +{len(parts) - 3} more"
    return text


def is_remote(raw: str) -> bool:
    return bool(raw and REMOTE_RE.search(raw))
