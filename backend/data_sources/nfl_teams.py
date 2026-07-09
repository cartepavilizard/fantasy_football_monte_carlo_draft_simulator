# -*- coding: utf-8 -*-
"""
NFL TEAM ALIAS TABLE

Sources disagree on team abbreviations (JAX/JAC, WAS/WSH, GB/GNB) and on
how defenses are named ("Cowboys", "Dallas Cowboys", "Cowboys D/ST").
Historical draft data additionally uses relocated franchises (SD, STL, OAK).
Everything funnels through normalize_team_abbrev / team_alias_to_abbrev so
both sides of any comparison land on the same canonical abbreviation.
"""

# (canonical abbrev, city, nickname, extra aliases)
NFL_TEAMS = [
    ("ARI", "Arizona", "Cardinals", ["ARZ"]),
    ("ATL", "Atlanta", "Falcons", []),
    ("BAL", "Baltimore", "Ravens", []),
    ("BUF", "Buffalo", "Bills", []),
    ("CAR", "Carolina", "Panthers", []),
    ("CHI", "Chicago", "Bears", []),
    ("CIN", "Cincinnati", "Bengals", []),
    ("CLE", "Cleveland", "Browns", []),
    ("DAL", "Dallas", "Cowboys", []),
    ("DEN", "Denver", "Broncos", []),
    ("DET", "Detroit", "Lions", []),
    ("GB", "Green Bay", "Packers", ["GNB"]),
    ("HOU", "Houston", "Texans", []),
    ("IND", "Indianapolis", "Colts", []),
    ("JAX", "Jacksonville", "Jaguars", ["JAC"]),
    ("KC", "Kansas City", "Chiefs", ["KAN"]),
    ("LV", "Las Vegas", "Raiders", ["OAK", "Oakland Raiders", "Oakland"]),
    ("LAC", "Los Angeles", "Chargers", ["SD", "SDG", "San Diego Chargers", "San Diego"]),
    ("LAR", "Los Angeles", "Rams", ["LA", "STL", "St Louis Rams", "St. Louis Rams"]),
    ("MIA", "Miami", "Dolphins", []),
    ("MIN", "Minnesota", "Vikings", []),
    ("NE", "New England", "Patriots", ["NWE"]),
    ("NO", "New Orleans", "Saints", ["NOR"]),
    ("NYG", "New York", "Giants", []),
    ("NYJ", "New York", "Jets", []),
    ("PHI", "Philadelphia", "Eagles", []),
    ("PIT", "Pittsburgh", "Steelers", []),
    ("SF", "San Francisco", "49ers", ["SFO", "Niners"]),
    ("SEA", "Seattle", "Seahawks", []),
    ("TB", "Tampa Bay", "Buccaneers", ["TAM", "Bucs"]),
    ("TEN", "Tennessee", "Titans", []),
    ("WAS", "Washington", "Commanders", ["WSH", "Redskins", "Football Team"]),
]

# Cities shared by two franchises can't identify a team on their own
_AMBIGUOUS_CITIES = {"los angeles", "new york"}


def _loose(text: str) -> str:
    """Lowercase and strip punctuation for alias lookups"""
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").strip()


# Alternate abbreviation -> canonical abbreviation (identity included)
ABBREV_ALIASES = {}
# Normalized name/city/nickname -> canonical abbreviation
TEAM_NAME_LOOKUP = {}
# Canonical abbreviation -> nickname
ABBREV_TO_NICKNAME = {}

for _abbrev, _city, _nickname, _aliases in NFL_TEAMS:
    ABBREV_ALIASES[_abbrev] = _abbrev
    ABBREV_TO_NICKNAME[_abbrev] = _nickname
    TEAM_NAME_LOOKUP[_loose(_nickname)] = _abbrev
    TEAM_NAME_LOOKUP[_loose(f"{_city} {_nickname}")] = _abbrev
    if _loose(_city) not in _AMBIGUOUS_CITIES:
        TEAM_NAME_LOOKUP[_loose(_city)] = _abbrev
    for _alias in _aliases:
        if _alias.isupper() and " " not in _alias:
            ABBREV_ALIASES[_alias] = _abbrev
        else:
            # Historical nicknames also appear with the city ("Washington
            # Redskins"), so index both forms
            TEAM_NAME_LOOKUP[_loose(_alias)] = _abbrev
            TEAM_NAME_LOOKUP[_loose(f"{_city} {_alias}")] = _abbrev


def normalize_team_abbrev(abbrev):
    """
    Map any known abbreviation variant (JAC, WSH, OAK, ...) to the canonical
    abbreviation; unknown values pass through uppercased, None stays None
    """
    if not abbrev:
        return None
    cleaned = abbrev.strip().upper()
    return ABBREV_ALIASES.get(cleaned, cleaned)


def team_alias_to_abbrev(text):
    """
    Resolve a team named in prose ("Dallas Cowboys", "Cowboys", "Dallas",
    "WSH") to its canonical abbreviation, or None if unrecognized/ambiguous
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.upper() in ABBREV_ALIASES:
        return ABBREV_ALIASES[cleaned.upper()]
    return TEAM_NAME_LOOKUP.get(_loose(cleaned))
