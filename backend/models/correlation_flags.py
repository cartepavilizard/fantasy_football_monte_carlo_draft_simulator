# -*- coding: utf-8 -*-
"""
F1 + F3 STRATEGY AWARENESS FLAGS (PHASE F)

Two display-only lenses over a roster, both **contextual flags, never
hard rules** — the spec's invariant (docs/specs/F1-stacking-correlation.md
§7) holds for both: removing every flag this module emits changes NO
ranking, NO valuation, NO verdict, NO projection. They decorate only.

F1 — STACKING (same-NFL-team QB + pass-catcher correlation):
the fixed rho table, sigma = SIGMA_CV * weekly projection, and the
"extra weekly swing" display math are taken verbatim from the spec. A
stack raises the VARIANCE of a pair's weekly sum (higher ceiling when
they connect, lower floor when the passing game stalls), not its mean —
so the flag quotes added swing in points and says "upside play, not a
value edge." It is never added to anything or compared to anything.

F3 — ANTI-CORRELATION (same-backfield RBs competing for touches):
the inverted lens over C7's handcuff/depth relationships (models/
handcuffs.py). C7 flags a starter's *direct backup* as insurance worth
rostering; F3 flags the OPPOSITE situation — two RBs on the same NFL
team who are NOT a curated starter->handcuff pair, i.e. a committee
where touches are split and weekly outcomes move against each other.
The deliberate handcuff case (C7's table) is EXCLUDED from F3 by
construction: that pairing is insurance, not competition. Same flag-only
discipline — no effect on any value or verdict.

PURITY / NO-MUTATION INVARIANT:
every function here is pure. Inputs (dicts, lists, model objects) are
read only and never written; the returned flags are fresh dicts. The
test suite asserts this explicitly (inputs unchanged after flagging).

POSITIONS: only QB/RB/WR/TE ever flag. DST/K return None even if a
future edit adds them to the table (spec §5 last edge). Same-backfield
RB+RB is F3's lens and is explicitly OUT of F1's rho table (spec §2).

This module imports nothing from data_sources, directly or transitively
(it imports nothing outside the standard library). That keeps it inside
inseason_api's cached-only read path; flags_api.py wires it to Mongo.
"""
import math
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

# --- F1: the fixed rho table (spec §2, verbatim) -----------------------------
#
# Same-NFL-team weekly fantasy-point correlations. Round mid-points of
# the public DFS/season-long research consensus; display weights, not
# fitted parameters. Module constants with this table's rationale — NOT
# env vars (spec §2: definitional, no data feed to tune against; an env
# knob would be a dial connected to nothing).
#
# Pairs are unordered: the key is the sorted tuple so ("QB","WR") and
# ("WR","QB") resolve to the same weight.
STACK_CORRELATION: Dict[Tuple[str, str], float] = {
    ("QB", "WR"): 0.40,  # headline stack
    ("QB", "TE"): 0.35,  # headline stack
    ("QB", "RB"): 0.10,  # pass-catching backs only barely correlate
    ("WR", "WR"): 0.10,  # same passing offense, mild
    ("WR", "TE"): 0.05,
}

# Per-player weekly standard deviation, approximated from the weekly
# projection (no distributional data in the app; spec §3). K/DST never
# flag so their wilder CVs don't matter.
SIGMA_CV = 0.45

# stack_grade is "strong" at rho >= this threshold, else "mild" (spec §2:
# the sub-0.2 rows exist so the flag can say "mild" instead of overclaiming).
STACK_GRADE_STRONG_THRESHOLD = 0.30

# Only these positions may ever flag (spec §5: DST/K return None even if
# someone edits the table).
FLAG_POSITIONS = {"QB", "RB", "WR", "TE"}

# Slots excluded from "starters" when scanning a synced roster — bench
# and IR depth don't make a same-backfield competition real for a
# starting lineup. (F3 scans starters only.)
BENCH_SLOTS = {"BE", "IR"}


# --- input extraction helpers (read-only) ------------------------------------


def _norm_pos(pos: Any) -> Optional[str]:
    if pos is None:
        return None
    return str(pos).strip().upper() or None


def _norm_team(team: Any) -> Optional[str]:
    if team is None:
        return None
    t = str(team).strip().upper()
    return t or None


def _name(player: Dict[str, Any]) -> Optional[str]:
    name = player.get("name")
    if name is None:
        return None
    name = str(name).strip()
    return name or None


def _weekly_projection(player: Dict[str, Any]) -> Optional[float]:
    """Read the weekly projection off a player dict, trying the common
    field names. Returns None when absent/non-positive (spec §5: no
    sigma-of-zero weirdness — a None/0 projection yields no flag)."""
    for key in ("weekly_projection", "projected_points", "projection"):
        value = player.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _player_view(player: Any) -> Dict[str, Any]:
    """Coerce a dict or an attribute-bearing object (Player,
    RosterSlotEntry) into the plain dict the pure functions read. Reads
    only — the original object is never touched."""
    if isinstance(player, dict):
        return player
    # attribute-bearing model object: pull the fields we care about
    view: Dict[str, Any] = {}
    for attr, key in (
        ("name", "name"),
        ("position", "position"),
        ("nfl_team", "nfl_team"),
        ("weekly_projection", "weekly_projection"),
        ("projected_points", "projected_points"),
    ):
        value = getattr(player, attr, None)
        if value is not None:
            view[key] = value
    return view


# --- F1: the math (spec §3) --------------------------------------------------


def extra_weekly_swing(
    proj_a: float, proj_b: float, rho: float
) -> Optional[float]:
    """
    Points of added weekly swing for a correlated pair vs the same two
    players treated as independent. Pure display number — never added
    to a projection, never compared to a value.

        sigma_pair  = sqrt(sa^2 + sb^2 + 2*rho*sa*sb)
        sigma_indep = sqrt(sa^2 + sb^2)
        extra_swing = sigma_pair - sigma_indep

    where sa = SIGMA_CV * proj_a, sb = SIGMA_CV * proj_b. Returns None
    if either projection is non-positive (no sigma-of-zero weirdness).
    """
    if not proj_a or not proj_b or proj_a <= 0 or proj_b <= 0:
        return None
    sa = SIGMA_CV * proj_a
    sb = SIGMA_CV * proj_b
    sigma_pair = math.sqrt(sa * sa + sb * sb + 2 * rho * sa * sb)
    sigma_indep = math.sqrt(sa * sa + sb * sb)
    return sigma_pair - sigma_indep


def _grade(rho: float) -> str:
    return "strong" if rho >= STACK_GRADE_STRONG_THRESHOLD else "mild"


def _stack_note(
    grade: str, rho: float, swing: float, name_b: str, pos_b: str
) -> str:
    """
    The flag's quantitative vocabulary is exactly "rho~X" and "~Y pts of
    weekly swing" (spec §7: no probability/boom-rate claims). Phrased for
    a roster flag — "Pairs with your <pos> <name>".
    """
    return (
        f"Pairs with your {pos_b} {name_b} — a {grade} stack "
        f"(rho~{rho:.2f}) adding ~{swing:.1f} pts of weekly swing. "
        "Upside play, not a value edge."
    )


def stack_flag(
    pos_a: Any,
    proj_a: Optional[float],
    pos_b: Any,
    proj_b: Optional[float],
    name_b: Optional[str],
) -> Optional[dict]:
    """
    Build one F1 stack flag for the pair (a, b), or None if the pair is
    not a stack per the rho table / a position is outside QB/RB/WR/TE /
    a projection is missing or non-positive. Pure: inputs are read only.

    The returned dict carries, per spec §4.1:
      with         — name_b (the rostered teammate the flag pairs against)
      positions    — [pos_a, pos_b], uppercased, in the order given
      correlation  — rho from the table
      grade        — "strong" (rho>=0.30) | "mild"
      extra_swing  — points of added weekly swing (rounded to 2dp)
      note         — the display copy
    """
    pa = _norm_pos(pos_a)
    pb = _norm_pos(pos_b)
    if pa is None or pb is None:
        return None
    if pa not in FLAG_POSITIONS or pb not in FLAG_POSITIONS:
        return None  # DST/K guard (spec §5)
    key = tuple(sorted((pa, pb)))
    rho = STACK_CORRELATION.get(key)
    if rho is None:
        return None  # not a stack (e.g. RB+RB same team is F3's lens)
    try:
        pa_proj = float(proj_a) if proj_a is not None else None
        pb_proj = float(proj_b) if proj_b is not None else None
    except (TypeError, ValueError):
        return None
    if pa_proj is None or pb_proj is None or pa_proj <= 0 or pb_proj <= 0:
        return None  # no sigma-of-zero weirdness (spec §5)
    if not name_b:
        return None
    swing = extra_weekly_swing(pa_proj, pb_proj, rho)
    if swing is None:
        return None
    grade = _grade(rho)
    return {
        "with": name_b,
        "positions": [pa, pb],
        "correlation": rho,
        "grade": grade,
        "extra_swing": round(swing, 2),
        "note": _stack_note(grade, rho, swing, name_b, pb),
    }


def stacks_for_roster(
    player: Any, roster_players: Iterable[Any]
) -> Optional[dict]:
    """
    Best (highest-rho) F1 stack for `player` against a roster, or None.
    Per spec §4.1: "Highest-correlation pair wins if several exist
    (report one, keep the flag readable)." When more than one rostered
    teammate forms a stack, the others are listed under `also_with`
    (spec §5 edge: "QB suggested when roster holds two of his receivers
    -> one flag, the higher-rho pairing named, also_with: [...]").

    Pure: `player` and `roster_players` are read only and never mutated;
    a fresh dict is returned. Tolerates both plain dicts and attribute-
    bearing model objects (Player, RosterSlotEntry).
    """
    pv = _player_view(player)
    pteam = _norm_team(pv.get("nfl_team"))
    ppos = _norm_pos(pv.get("position"))
    pproj = _weekly_projection(pv)
    pname = _name(pv)
    if not pteam or ppos is None or ppos not in FLAG_POSITIONS or not pproj:
        return None

    roster_views = [_player_view(r) for r in roster_players]
    candidates: List[Tuple[dict, str]] = []
    for rv in roster_views:
        # skip the same object (identity) and same-name self-pairs
        if rv is pv:
            continue
        if _norm_team(rv.get("nfl_team")) != pteam:
            continue
        rname = _name(rv)
        if rname is None or (pname is not None and rname == pname):
            continue
        flag = stack_flag(
            ppos,
            pproj,
            rv.get("position"),
            _weekly_projection(rv),
            rname,
        )
        if flag is None:
            continue
        candidates.append((flag, rname))

    if not candidates:
        return None
    # highest rho wins; tie-break by larger extra_swing, then name for determinism
    candidates.sort(
        key=lambda c: (c[0]["correlation"], c[0]["extra_swing"], c[1]),
        reverse=True,
    )
    best, best_name = candidates[0]
    also_with = [name for _flag, name in candidates[1:]]
    if also_with:
        best = dict(best)  # never mutate the dict we already built
        best["also_with"] = also_with
    return best


# --- F3: anti-correlation (inverted C7) --------------------------------------


def _handcuff_pair_key(a: Optional[str], b: Optional[str]) -> FrozenSet[str]:
    """Unordered pair key for the exclusion set (starter<->handcuff)."""
    return frozenset({a, b}) if (a and b) else frozenset()


def anticorrelation_flags(
    roster_players: Iterable[Any],
    handcuff_pairs: FrozenSet[FrozenSet[str]] = frozenset(),
) -> List[dict]:
    """
    F3: flag same-backfield RBs competing for touches — the inverted
    lens over C7's depth relationships. For each unordered pair of RBs
    on the same NFL team that is NOT a curated starter->handcuff pair,
    emit one flag. The deliberate handcuff case (C7's table) is excluded
    by passing its name-pairs as `handcuff_pairs`: that pairing is
    insurance, not competition.

    Same flag-only discipline as F1: no effect on any ranking, valuation,
    or verdict. Pure: inputs are read only; fresh dicts are returned.

    `roster_players` may be dicts or attribute-bearing model objects.
    Only starters are scanned (bench/IR depth doesn't make a committee
    real for a starting lineup); pass the full roster and the slots in
    BENCH_SLOTS are skipped.
    """
    views = [_player_view(r) for r in roster_players]
    rbs: List[Dict[str, Any]] = []
    for rv in views:
        if _norm_pos(rv.get("position")) != "RB":
            continue
        team = _norm_team(rv.get("nfl_team"))
        if not team:
            continue
        slot = _norm_pos(rv.get("lineup_slot")) or ""
        if slot in BENCH_SLOTS:
            continue
        name = _name(rv)
        if name is None:
            continue
        rbs.append({"name": name, "team": team})

    flags: List[dict] = []
    for i in range(len(rbs)):
        for j in range(i + 1, len(rbs)):
            a, b = rbs[i], rbs[j]
            if a["team"] != b["team"]:
                continue
            pair_key = _handcuff_pair_key(a["name"], b["name"])
            if pair_key and pair_key in handcuff_pairs:
                continue  # C7's deliberate insurance case — not F3's lens
            flags.append(
                {
                    "players": [a["name"], b["name"]],
                    "nfl_team": a["team"],
                    "note": (
                        f"{a['name']} and {b['name']} share the {a['team']} "
                        "backfield — touches compete. Anti-correlation "
                        "flag, not a value call."
                    ),
                }
            )
    # stable, deterministic order
    flags.sort(key=lambda f: (f["nfl_team"], tuple(f["players"])))
    return flags


def roster_stack_flags(
    roster_players: Iterable[Any],
) -> List[dict]:
    """
    Convenience: emit one F1 stack flag per rostered player who has a
    same-NFL-team stack teammate on the roster (best pairing each).
    De-duplicated so each unordered stack appears once. Pure.

    Used by flags_api's roster endpoint alongside anticorrelation_flags.
    """
    views = [_player_view(r) for r in roster_players]
    seen: set = set()
    out: List[dict] = []
    for rv in views:
        flag = stacks_for_roster(rv, views)
        if flag is None:
            continue
        key = frozenset({flag["with"], _name(rv)})
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        out.append(flag)
    out.sort(key=lambda f: (-(f["correlation"]), f["with"]))
    return out
