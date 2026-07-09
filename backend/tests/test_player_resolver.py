# -*- coding: utf-8 -*-
"""
Canonical player resolver: normalization, matching tiers, and safety
(never guess on ambiguity). Seeded from the real sample players.csv so the
tests exercise real multi-source spelling variants.
"""
import csv

from conftest import DATA_DIR
from data_sources.resolver import (
    PlayerResolver,
    dst_alias_to_abbrev,
    normalize_name,
    normalize_position,
)


def sample_players():
    with open(DATA_DIR / "players.csv", encoding="utf-8-sig") as file:
        return [
            {"name": row["Player"], "position": row["Pos"], "nfl_team": row["Team"]}
            for row in csv.DictReader(file)
        ]


def make_resolver(**kwargs) -> PlayerResolver:
    return PlayerResolver(sample_players(), **kwargs)


# --- normalization ---------------------------------------------------------


def test_normalize_strips_punctuation_suffixes_and_accents():
    assert normalize_name("A.J. Brown") == "aj brown"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Michael Pittman Jr.") == "michael pittman"
    assert normalize_name("Aarón Rodgers") == "aaron rodgers"
    assert normalize_name("  Odell  Beckham   Jr ") == "odell beckham"


def test_normalize_position_folds_source_spellings():
    assert normalize_position("D/ST") == "dst"
    assert normalize_position("DEF") == "dst"
    assert normalize_position("PK") == "k"
    assert normalize_position("WR") == "wr"
    assert normalize_position(None) is None


# --- exact matching against the real sample pool ---------------------------


def test_exact_match_across_punctuation_variants():
    resolver = make_resolver()
    # Canonical pool spells him "A.J. Brown"; sources often say "AJ Brown"
    result = resolver.resolve("AJ Brown", position="WR")
    assert result.canonical_name == "A.J. Brown"
    assert result.method == "exact"
    # Canonical pool spells him "DJ Moore"; sources often say "D.J. Moore"
    result = resolver.resolve("D.J. Moore", position="WR", nfl_team="CHI")
    assert result.canonical_name == "DJ Moore"


def test_suffix_variants_resolve_to_unsuffixed_canonical():
    resolver = make_resolver()
    # Pool stores "Michael Pittman"; many sources append the suffix
    result = resolver.resolve("Michael Pittman Jr.", position="WR")
    assert result.canonical_name == "Michael Pittman"
    assert result.method == "exact"


def test_position_gate_rejects_wrong_position():
    resolver = make_resolver()
    assert resolver.resolve("Patrick Mahomes", position="QB").resolved
    assert not resolver.resolve("Patrick Mahomes", position="TE").resolved


def test_same_name_different_positions_needs_position():
    resolver = PlayerResolver(
        sample_players()
        + [{"name": "Josh Allen", "position": "LB", "nfl_team": "JAX"}]
    )
    # Without a position hint, two Josh Allens are ambiguous — never guess
    ambiguous = resolver.resolve("Josh Allen")
    assert not ambiguous.resolved
    assert ambiguous.method == "ambiguous"
    # A position hint disambiguates
    assert resolver.resolve("Josh Allen", position="QB").canonical_name == "Josh Allen"


def test_team_tiebreak_when_positions_tie():
    players = [
        {"name": "Lamar Jackson", "position": "CB", "nfl_team": "NYJ"},
        {"name": "Lamar Jackson", "position": "CB", "nfl_team": "DEN"},
    ]
    resolver = PlayerResolver(players)
    result = resolver.resolve("Lamar Jackson", position="CB", nfl_team="DEN")
    assert result.resolved and result.method == "exact"
    assert not resolver.resolve("Lamar Jackson", position="CB").resolved


# --- fuzzy fallback --------------------------------------------------------


def test_fuzzy_catches_typos_within_position():
    resolver = make_resolver()
    result = resolver.resolve("Patric Mahomes", position="QB")
    assert result.canonical_name == "Patrick Mahomes"
    assert result.method == "fuzzy"
    assert result.confidence > 0.9


def test_fuzzy_does_not_bridge_similar_family_names():
    resolver = make_resolver()
    # Christian (RB) and Luke (WR) McCaffrey: the position gate must keep a
    # WR-side typo from landing on the RB
    result = resolver.resolve("Luke McCafrey", position="WR")
    assert result.canonical_name == "Luke McCaffrey"


def test_unknown_player_is_unresolved_not_guessed():
    resolver = make_resolver()
    result = resolver.resolve("Totally Fabricated Player", position="RB")
    assert not result.resolved
    assert result.method == "unresolved"
    assert result.confidence == 0.0


# --- defenses --------------------------------------------------------------


def test_dst_aliases_map_to_team():
    assert dst_alias_to_abbrev("Cowboys D/ST") == "DAL"
    assert dst_alias_to_abbrev("Dallas Cowboys") == "DAL"
    assert dst_alias_to_abbrev("San Francisco 49ers D/ST") == "SF"
    assert dst_alias_to_abbrev("Washington Redskins") == "WAS"  # pre-2020 data
    assert dst_alias_to_abbrev("not a team") is None


def test_dst_resolution_against_nickname_only_pool():
    resolver = make_resolver()  # pool stores defenses as "Cowboys", "49ers", ...
    for raw, expected in [
        ("Dallas Cowboys D/ST", "Cowboys"),
        ("Cowboys", "Cowboys"),
        ("San Francisco 49ers", "49ers"),
    ]:
        result = resolver.resolve(raw, position="D/ST")
        assert result.canonical_name == expected, raw
        assert result.method == "dst"
    # Abbreviation-only defenses resolve through the team hint
    result = resolver.resolve("PHI D/ST", position="DST", nfl_team="PHI")
    assert result.canonical_name == "Eagles"


# --- overrides -------------------------------------------------------------


def test_manual_override_wins_over_everything():
    resolver = make_resolver(overrides={"Scary Terry": "Terry McLaurin"})
    result = resolver.resolve("Scary Terry", position="WR")
    assert result.canonical_name == "Terry McLaurin"
    assert result.method == "override"
    assert result.confidence == 1.0


def test_add_override_at_runtime():
    resolver = make_resolver()
    assert not resolver.resolve("The Chef", position="QB").resolved
    resolver.add_override("The Chef", "Patrick Mahomes")
    assert resolver.resolve("The Chef", position="QB").method == "override"


# --- whole-pool sanity -----------------------------------------------------


def test_every_canonical_player_resolves_to_itself():
    """The pool fed back through the resolver must map 1:1 (except true
    duplicates, which the upload endpoint already rejects)"""
    players = sample_players()
    resolver = PlayerResolver(players)
    for player in players:
        result = resolver.resolve(
            player["name"], position=player["position"], nfl_team=player["nfl_team"]
        )
        assert result.canonical_name == player["name"], player["name"]
