# -*- coding: utf-8 -*-
"""
CANONICAL PLAYER NAME RESOLUTION

The simulator looks players up by exact name string, but every external
source spells players differently ("A.J. Brown" / "AJ Brown", "DJ Moore" /
"D.J. Moore", "Kenneth Walker III" / "Kenneth Walker", "Cowboys" /
"Dallas Cowboys D/ST"). Every record entering the system must therefore be
resolved to the canonical spelling used by the league's player pool first.

Resolution order (first hit wins):
1. manual override map (persisted in the player_aliases collection)
2. defense/special-teams resolution via the NFL team alias table
3. exact normalized-name match, tie-broken by position then NFL team
4. fuzzy match (stdlib difflib), position-gated, only accepted when there
   is a single unambiguous winner above the cutoff

A resolution that cannot be made safely comes back with method
"unresolved" or "ambiguous" and canonical_name=None — callers must treat
those as review-queue items, never guess.
"""
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
import re
from typing import Optional
import unicodedata

from .nfl_teams import ABBREV_TO_NICKNAME, normalize_team_abbrev, team_alias_to_abbrev

# Generational suffixes never help identify a player across sources
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Position spellings seen across sources -> the simulator's position keys
_POSITION_ALIASES = {
    "d/st": "dst",
    "def": "dst",
    "dst": "dst",
    "pk": "k",
}

# Tokens that carry no identity in defense names ("Cowboys D/ST", "Bears Defense")
_DST_FILLER_TOKENS = {"d", "st", "dst", "def", "defense", "special", "teams"}


def normalize_name(name: str) -> str:
    """
    Reduce a player name to a comparison key: accent-folded, lowercased,
    punctuation stripped, generational suffixes removed
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[.'’`]", "", text)  # A.J. -> aj, Ja'Marr -> jamarr
    text = re.sub(r"[^a-z0-9]+", " ", text)  # hyphens, slashes, commas -> space
    tokens = text.split()
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def normalize_position(position) -> Optional[str]:
    """Lowercase a position and fold source-specific spellings (D/ST, DEF, PK)"""
    if not position:
        return None
    cleaned = position.strip().lower()
    return _POSITION_ALIASES.get(cleaned, cleaned)


def dst_alias_to_abbrev(raw_name: str) -> Optional[str]:
    """
    Resolve a defense named in prose to a team abbreviation, tolerating
    D/ST decorations: "Cowboys D/ST" -> DAL, "Dallas Cowboys" -> DAL
    """
    tokens = [
        token
        for token in normalize_name(raw_name).split()
        if token not in _DST_FILLER_TOKENS
    ]
    if not tokens:
        return None
    return team_alias_to_abbrev(" ".join(tokens))


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one raw name against the canonical pool"""

    canonical_name: Optional[str]
    method: str  # override | dst | exact | fuzzy | ambiguous | unresolved
    confidence: float

    @property
    def resolved(self) -> bool:
        return self.canonical_name is not None


@dataclass(frozen=True)
class _Entry:
    name: str  # canonical spelling, exactly as the player pool stores it
    position: Optional[str]
    team: Optional[str]


def _read_player(player) -> _Entry:
    """Accept Player models, dicts, or (name, position, team) tuples"""
    if isinstance(player, dict):
        name = player.get("name") or player.get("Player")
        position = player.get("position") or player.get("Pos")
        team = player.get("nfl_team") or player.get("Team")
    elif isinstance(player, (tuple, list)):
        name, position, team = (list(player) + [None, None])[:3]
    else:
        name = player.name
        position = getattr(player, "position", None)
        team = getattr(player, "nfl_team", None)
    return _Entry(
        name=name,
        position=normalize_position(position),
        team=normalize_team_abbrev(team),
    )


class PlayerResolver:
    """
    Resolves raw player names from any source to the canonical spellings
    of a seeded player pool (normally league.players)
    """

    FUZZY_CUTOFF = 0.85
    # Two fuzzy candidates closer than this are indistinguishable -> ambiguous
    FUZZY_MARGIN = 0.02

    def __init__(self, players, overrides: Optional[dict] = None):
        self._by_normalized = {}
        self._dst_by_abbrev = {}
        for player in players:
            entry = _read_player(player)
            self._by_normalized.setdefault(normalize_name(entry.name), []).append(
                entry
            )
            if entry.position == "dst":
                abbrev = entry.team or dst_alias_to_abbrev(entry.name)
                if abbrev:
                    self._dst_by_abbrev.setdefault(abbrev, entry)
        self._overrides = {
            normalize_name(alias): canonical
            for alias, canonical in (overrides or {}).items()
        }

    def add_override(self, alias: str, canonical_name: str):
        """Register a manual alias -> canonical mapping (highest precedence)"""
        self._overrides[normalize_name(alias)] = canonical_name

    def resolve(
        self,
        raw_name: str,
        position: Optional[str] = None,
        nfl_team: Optional[str] = None,
    ) -> Resolution:
        normalized = normalize_name(raw_name)
        pos = normalize_position(position)
        team = normalize_team_abbrev(nfl_team)

        # 1. Manual overrides always win
        override = self._overrides.get(normalized)
        if override is not None:
            return Resolution(override, "override", 1.0)

        # 2. Defenses match through the team table, not through spelling
        if pos == "dst" or (pos is None and dst_alias_to_abbrev(raw_name)):
            abbrev = team or dst_alias_to_abbrev(raw_name)
            entry = self._dst_by_abbrev.get(abbrev) if abbrev else None
            if entry:
                return Resolution(entry.name, "dst", 1.0)
            if pos == "dst":
                return Resolution(None, "unresolved", 0.0)

        # 3. Exact normalized match, tie-broken by position then team
        candidates = self._by_normalized.get(normalized, [])
        if candidates:
            match = self._pick_candidate(candidates, pos, team)
            if match:
                return Resolution(match.name, "exact", 1.0)
            return Resolution(None, "ambiguous", 0.0)

        # 4. Fuzzy fallback, position-gated, unambiguous winners only
        return self._resolve_fuzzy(normalized, pos, team)

    def _pick_candidate(self, candidates, pos, team) -> Optional[_Entry]:
        """Narrow same-normalized-name candidates; None means ambiguous"""
        pool = candidates
        if pos:
            pool = [entry for entry in pool if entry.position == pos]
        if len(pool) == 1:
            return pool[0]
        if len(pool) > 1 and team:
            by_team = [entry for entry in pool if entry.team == team]
            if len(by_team) == 1:
                return by_team[0]
        return None

    def _resolve_fuzzy(self, normalized, pos, team) -> Resolution:
        pool = {
            key: entries
            for key, entries in self._by_normalized.items()
            if pos is None or any(entry.position == pos for entry in entries)
        }
        matches = get_close_matches(
            normalized, pool.keys(), n=2, cutoff=self.FUZZY_CUTOFF
        )
        if not matches:
            return Resolution(None, "unresolved", 0.0)
        best = matches[0]
        ratio = SequenceMatcher(None, normalized, best).ratio()
        if len(matches) > 1:
            runner_up = SequenceMatcher(None, normalized, matches[1]).ratio()
            if ratio - runner_up < self.FUZZY_MARGIN:
                return Resolution(None, "ambiguous", round(ratio, 3))
        match = self._pick_candidate(pool[best], pos, team)
        if not match:
            return Resolution(None, "ambiguous", round(ratio, 3))
        return Resolution(match.name, "fuzzy", round(ratio, 3))


async def load_alias_overrides(engine) -> dict:
    """
    Load the persisted manual override map from the player_aliases
    collection, ready to pass as PlayerResolver(overrides=...)
    """
    from models.sources import PlayerAlias

    aliases = await engine.find(PlayerAlias)
    return {alias.alias: alias.canonical_name for alias in aliases}
