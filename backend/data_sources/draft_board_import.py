# -*- coding: utf-8 -*-
"""
PAPER DRAFT-BOARD IMPORTER

Two of this app's three leagues drafted OFFLINE and the commissioner typed
the finished rosters into ESPN afterwards, so the overall_pick / round_pick
values on their `historical_picks` rows are FABRICATED (verified: 0 of 173
picks agreed with the real board). The real order survives only in the
paper draft-board spreadsheets those leagues kept on draft night.

THE KEY INSIGHT that shapes this whole module: the spreadsheet supplies
ORDER; the existing ESPN rows already supply IDENTITY (canonical_name,
position, historical_adp, member_guid, espn_team_id, is_keeper). So this
importer does NOT create picks from scratch and does not try to resolve
players against a global player database. It RE-ORDERS the league-season's
existing HistoricalPick rows to match the board, and marks the ones it
could place as verified.

Public surface:
  - parse_board(path, sheet_name=None) -> DraftBoard
  - overall_pick_for(round_num, slot, team_count, snake=True) -> int
  - normalize_name(text) -> str
  - match_board_to_picks(board, picks, snake=True) -> BoardMatchResult
  - apply_board(engine, espn_league_id, season, board_path, ...) -> dict

Module constants:
  - MIN_OWNER_OVERLAP (default 4)
  - MIN_MATCH_RATE (default 0.90)
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

import openpyxl

from models.sources import HistoricalPick

# -- module constants -------------------------------------------------------

# The minimum roster-overlap score a column must reach against its best
# owner before the owner map is trusted. Below this the column is too
# thin to anchor an identity and the whole import is refused.
MIN_OWNER_OVERLAP: int = 4

# The minimum fraction of board cells that must be placed before a write
# to the database is allowed. A partially-placed board must never be
# presented as fully verified.
MIN_MATCH_RATE: float = 0.90


# -- name normalization -----------------------------------------------------

# Generational suffixes never help identify a player across sources
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Trailing position/team tag words a commissioner tacks on after a name
_TRAILING_TAG_RE = re.compile(
    r"\s+(?:qb|rb|wr|te|k|pk|dst|d/st|def|defense|flex|dl|lb|db|ol)"
    r"(?:\s*/\s*(?:st|d|def))?"
    r"(?:\s+[A-Z]{2,4})?$",
    re.IGNORECASE,
)


def normalize_name(text: str) -> str:
    """
    Reduce a player name to a comparison key: casefolded, accent-folded,
    periods/apostrophes/hyphens/commas dropped, generational suffixes
    removed, any trailing parenthetical or trailing position/team tag
    stripped, whitespace collapsed.
    """
    if not text:
        return ""
    # Strip trailing parenthetical ("Player (COL)")
    t = re.sub(r"\s*\([^)]*\)\s*$", " ", str(text))
    # Strip trailing position/team tag ("Player WR" / "Player D/ST")
    t = _TRAILING_TAG_RE.sub(" ", t)
    # Accent-fold
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    # Casefold (more aggressive than lower for comparison keys)
    t = t.casefold()
    # Drop periods, apostrophes (A.J. -> aj, Ja'Marr -> jamarr)
    t = re.sub(r"[.'’`]", "", t)
    # Hyphens / commas / slashes -> space
    t = re.sub(r"[^a-z0-9]+", " ", t)
    tokens = t.split()
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# -- board parsing ----------------------------------------------------------

_ROUND_RE = re.compile(r"^round\s*#?\s*(\d+)", re.IGNORECASE)


def _looks_like_round_label(cell: str) -> bool:
    if not cell:
        return False
    s = cell.strip()
    if _ROUND_RE.match(s):
        return True
    # bare integer
    if s.isdigit():
        return True
    return False


@dataclass
class DraftBoard:
    """
    One parsed paper draft board.

    - team_names: the column-header team names, in left-to-right column
      order (slot 1 = index 0). The left-to-right order IS the round-1
      draft order.
    - cells: {(round_num, slot): player_text}, both 1-indexed, slot 1 =
      the leftmost team column. Empty board cells are simply absent.
    """

    team_names: list[str]
    cells: dict[tuple[int, int], str]


def parse_board(path: str, sheet_name: Optional[str] = None) -> DraftBoard:
    """
    Parse a paper draft-board spreadsheet into a DraftBoard.

    Detection rules (tolerant of how a human typed the sheet):
      - skip leading blank rows
      - the header row is the first row with at least 4 non-empty cells
        from column B rightward whose column A does NOT look like a
        round label
      - a round row is any row whose stripped column A matches
        case-insensitive `round\\s*#?\\s*(\\d+)` or is a bare integer
      - every cell is whitespace-stripped; empty/None -> no pick
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet_name] if sheet_name is not None else wb.active

    rows = list(ws.iter_rows(values_only=True))

    header_idx: Optional[int] = None
    for i, row in enumerate(rows):
        # skip leading blank rows
        if all((cell is None or str(cell).strip() == "") for cell in row):
            continue
        col_a = str(row[0]).strip() if row and row[0] is not None else ""
        if _looks_like_round_label(col_a):
            # a round row before any header -> no header; treat first row
            # as header fallback (shouldn't happen in practice)
            header_idx = i
            break
        # count non-empty cells from column B (index 1) rightward
        non_empty = sum(
            1 for j in range(1, len(row)) if row[j] is not None and str(row[j]).strip() != ""
        )
        if non_empty >= 4:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("draft board: no header row detected (need >= 4 team columns)")

    header = rows[header_idx]
    team_names: list[str] = []
    for j in range(1, len(header)):
        v = header[j]
        name = str(v).strip() if v is not None else ""
        if name == "":
            break
        team_names.append(name)

    team_count = len(team_names)
    if team_count == 0:
        raise ValueError("draft board: header row had no team names")

    cells: dict[tuple[int, int], str] = {}
    for i in range(header_idx + 1, len(rows)):
        row = rows[i]
        if not row:
            continue
        col_a = str(row[0]).strip() if row[0] is not None else ""
        if not col_a:
            # blank rows between rounds are fine
            continue
        m = _ROUND_RE.match(col_a)
        if m:
            round_num = int(m.group(1))
        elif col_a.isdigit():
            round_num = int(col_a)
        else:
            # not a round row; ignore
            continue
        for slot in range(1, team_count + 1):
            idx = slot  # column B is index 1 = slot 1
            if idx >= len(row):
                break
            v = row[idx]
            text = str(v).strip() if v is not None else ""
            if text == "":
                continue
            cells[(round_num, slot)] = text

    return DraftBoard(team_names=team_names, cells=cells)


# -- overall pick math ------------------------------------------------------


def overall_pick_for(
    round_num: int, slot: int, team_count: int, snake: bool = True
) -> int:
    """
    Convert a (round, slot) board position to an overall pick number.

    Snake: odd rounds run left-to-right so overall = (round-1)*team_count
    + slot; EVEN rounds run right-to-left so overall = (round-1)*team_count
    + (team_count - slot + 1). Non-snake is always the odd-round formula.

    Sanity (team_count=12): round 2 slot 12 -> 13; round 2 slot 1 -> 24.
    """
    base = (round_num - 1) * team_count
    if snake and round_num % 2 == 0:
        return base + (team_count - slot + 1)
    return base + slot


def _round_pick_for(round_num: int, slot: int, team_count: int, snake: bool) -> int:
    """Position within the round, 1-indexed from the round's start."""
    if snake and round_num % 2 == 0:
        return team_count - slot + 1
    return slot


# -- matching ---------------------------------------------------------------


@dataclass
class Placement:
    """One board cell matched to one ESPN HistoricalPick."""

    pick: Any
    overall_pick: int
    round_num: int
    round_pick: int
    slot: int
    cell_text: str
    method: str


@dataclass
class UnmatchedCell:
    """A board cell that no remaining ESPN pick could resolve to."""

    round_num: int
    slot: int
    text: str


@dataclass
class BoardMatchResult:
    """Full outcome of matching a board against a league-season's picks."""

    owner_map: dict[int, str] = field(default_factory=dict)
    owner_confidence: dict[int, float] = field(default_factory=dict)
    placements: list[Placement] = field(default_factory=list)
    unmatched_cells: list[UnmatchedCell] = field(default_factory=list)
    unmatched_picks: list[Any] = field(default_factory=list)
    match_rate: float = 0.0
    errors: list[str] = field(default_factory=list)


# match-stage ranks (higher = stronger)
_STAGE_EXACT = 5
_STAGE_LAST_TOKEN = 4
_STAGE_ACRONYM = 3
_STAGE_INITIAL_SURNAME = 2
_STAGE_FALLBACK = 1

_STAGE_NAMES = {
    _STAGE_EXACT: "exact",
    _STAGE_LAST_TOKEN: "last-token",
    _STAGE_ACRONYM: "acronym",
    _STAGE_INITIAL_SURNAME: "initial-surname",
    _STAGE_FALLBACK: "fallback",
}


def _pick_name(pick: Any) -> str:
    """The identity-carrying name for an ESPN pick."""
    if hasattr(pick, "canonical_name") and pick.canonical_name:
        return pick.canonical_name
    if hasattr(pick, "raw_player_name"):
        return pick.raw_player_name or ""
    if isinstance(pick, dict):
        return pick.get("canonical_name") or pick.get("raw_player_name") or ""
    return ""


def _pick_guid(pick: Any) -> Optional[str]:
    if hasattr(pick, "member_guid"):
        return pick.member_guid
    if isinstance(pick, dict):
        return pick.get("member_guid")
    return None


def _pick_owner_name(pick: Any) -> str:
    if hasattr(pick, "owner_display_name"):
        return pick.owner_display_name or ""
    if isinstance(pick, dict):
        return pick.get("owner_display_name") or ""
    return ""


def _rank_match(cell_norm: str, pick_norm: str) -> int:
    """
    Rank how well a normalized cell text matches a normalized pick name.
    Higher is stronger. 0 = no match.

    Stages mirror match_board_to_picks' priority order; the caller handles
    the stage-2 (last-token) UNIQUENESS check, since that depends on the
    full candidate pool rather than a single pick.
    """
    if not cell_norm or not pick_norm:
        return 0
    if cell_norm == pick_norm:
        return _STAGE_EXACT
    ct = cell_norm.split()
    pt = pick_norm.split()
    if not ct or not pt:
        return 0
    # last-token equality (uniqueness handled by caller)
    if ct[-1] == pt[-1]:
        return _STAGE_LAST_TOKEN
    # acronym: the cell is itself the acronym (e.g. "cmc"); match it
    # against the first letters of the pick's name words ("cm"). A cell
    # may carry extra middle initials the pick name omits, so we accept a
    # prefix relationship in either direction.
    cell_acro = "".join(c for c in cell_norm if c.isalpha())
    pick_acro = "".join(p[0] for p in pt if p)
    if (
        len(cell_acro) >= 2
        and len(cell_acro) <= 4
        and (
            cell_acro == pick_acro
            or pick_acro.startswith(cell_acro)
            or cell_acro.startswith(pick_acro)
        )
    ):
        return _STAGE_ACRONYM
    # initial + surname: "j gibbs" vs "jahmyr gibbs"
    if len(ct) == 2 and len(ct[0]) == 1 and len(pt) >= 2:
        if ct[0] == pt[0][0] and ct[1] == pt[-1]:
            return _STAGE_INITIAL_SURNAME
    # token-overlap / edit-distance fallback (must clear a floor)
    overlap = len(set(ct) & set(pt))
    if ct and overlap / len(ct) >= 0.5:
        return _STAGE_FALLBACK
    ratio = SequenceMatcher(None, cell_norm, pick_norm).ratio()
    if ratio >= 0.75:
        return _STAGE_FALLBACK
    return 0


def _derive_owner_map(
    board: DraftBoard, picks_by_guid: dict[str, list[Any]]
) -> tuple[dict[int, str], dict[int, float], list[str]]:
    """
    STAGE 1: derive slot -> member_guid from roster overlap.

    For each board column and each distinct guid, score = # of that
    column's cells that resolve to one of that guid's picks. Assign
    one-to-one greedily by descending score. Refuse if any column's best
    score is below MIN_OWNER_OVERLAP or ties its runner-up.

    Returns (owner_map, owner_confidence, errors). A refused map comes
    back empty with errors populated; the caller must NOT raise.
    """
    errors: list[str] = []
    team_count = len(board.team_names)

    # Precompute per-column cell-normalized texts
    column_cells: dict[int, list[str]] = {s: [] for s in range(1, team_count + 1)}
    for (round_num, slot), text in board.cells.items():
        n = normalize_name(text)
        if n:
            column_cells[slot].append(n)

    # Score every (slot, guid) pair
    scores: dict[tuple[int, str], int] = {}
    for slot in range(1, team_count + 1):
        cells = column_cells[slot]
        if not cells:
            continue
        for guid, guid_picks in picks_by_guid.items():
            pick_norms = [normalize_name(_pick_name(p)) for p in guid_picks]
            score = 0
            for cn in cells:
                if any(_rank_match(cn, pn) > 0 for pn in pick_norms):
                    score += 1
            scores[(slot, guid)] = score

    # Cheap confirmation: exact/normalized team-name equality. Recorded
    # but overlap always decides the assignment; we surface it only as a
    # tiebreaker hint when scores are exactly equal (which the refuse rule
    # already rejects), so in practice it just confirms.
    guids = list(picks_by_guid.keys())

    # Greedy one-to-one assignment by descending score
    assigned_slots: set[int] = set()
    assigned_guids: set[str] = set()
    owner_map: dict[int, str] = {}
    owner_confidence: dict[int, float] = {}

    ordered = sorted(
        ((s, g, sc) for (s, g), sc in scores.items() if sc > 0),
        key=lambda t: (-t[2], t[0], t[1]),
    )

    for slot, guid, sc in ordered:
        if slot in assigned_slots or guid in assigned_guids:
            continue
        owner_map[slot] = guid
        owner_confidence[slot] = sc / max(1, len(column_cells[slot]))
        assigned_slots.add(slot)
        assigned_guids.add(guid)

    # Validate each assigned column: best score must clear the floor and
    # must not tie its runner-up among unassigned (at assignment time we
    # had no unassigned competitor, but we re-check globally here).
    for slot, guid in list(owner_map.items()):
        best = scores.get((slot, guid), 0)
        if best < MIN_OWNER_OVERLAP:
            errors.append(
                f"slot {slot} -> guid {guid}: overlap {best} below "
                f"MIN_OWNER_OVERLAP={MIN_OWNER_OVERLAP}"
            )
            owner_map.pop(slot)
            owner_confidence.pop(slot)
            continue
        # runner-up: next-best score for this slot among other guids
        runner = 0
        for g2 in guids:
            if g2 == guid:
                continue
            runner = max(runner, scores.get((slot, g2), 0))
        if runner >= best and runner > 0:
            errors.append(
                f"slot {slot}: best overlap {best} tied with runner-up "
                f"{runner} (guid {guid}) -> ambiguous owner map"
            )
            owner_map.pop(slot)
            owner_confidence.pop(slot)

    # If any column could not be cleanly assigned, refuse the WHOLE map:
    # an ambiguous or too-thin owner map silently corrupts every
    # downstream metric, so we do not guess.
    missing = [s for s in range(1, team_count + 1) if s not in owner_map]
    if missing and not any("ambiguous" in e or "below" in e for e in errors):
        errors.append(
            f"owner map refused: columns {missing} could not be assigned "
            f"(no positive roster overlap)"
        )
    if errors:
        owner_map.clear()
        owner_confidence.clear()

    return owner_map, owner_confidence, errors


def _match_column(
    slot: int,
    guid: str,
    picks_by_guid: dict[str, list[Any]],
    consumed: set[int],
    board: DraftBoard,
    team_count: int,
    snake: bool,
) -> tuple[list[Placement], list[UnmatchedCell], list[int]]:
    """
    STAGE 2 for one column: match cells to that owner's REMAINING picks.

    Returns (placements, unmatched_cells, consumed_pick_ids) where
    consumed_pick_ids are the indices (into picks_by_guid[guid]) that
    got matched. Each ESPN pick may be consumed AT MOST ONCE.
    """
    placements: list[Placement] = []
    unmatched_cells: list[UnmatchedCell] = []
    consumed_local: list[int] = []

    # candidate pool: this owner's picks, with normalized names + a local
    # index so we can remove matched ones
    pool: list[tuple[int, Any, str]] = [
        (i, p, normalize_name(_pick_name(p)))
        for i, p in enumerate(picks_by_guid[guid])
        if i not in consumed
    ]

    # iterate cells in round order
    column_cells = sorted(
        (rn, s) for (rn, s) in board.cells if s == slot
    )
    for round_num, _slot in column_cells:
        text = board.cells[(round_num, slot)]
        cn = normalize_name(text)
        if not cn:
            continue

        # rank every remaining pick
        ranked: list[tuple[int, int, Any]] = []  # (rank, pool_index, pick)
        for pi, (_orig_i, pick, pn) in enumerate(pool):
            r = _rank_match(cn, pn)
            if r > 0:
                ranked.append((r, pi, pick))

        if not ranked:
            unmatched_cells.append(UnmatchedCell(round_num=round_num, slot=slot, text=text))
            continue

        best = max(r for r, _, _ in ranked)

        # stage-2 (last-token) UNIQUENESS check: if the best rank is
        # last-token and multiple picks tie there, demote them and
        # recompute among the rest.
        if best == _STAGE_LAST_TOKEN:
            best_indices = [pi for r, pi, _ in ranked if r == best]
            if len(best_indices) > 1:
                ranked = [(r, pi, p) for r, pi, p in ranked if r != best]
                if not ranked:
                    unmatched_cells.append(
                        UnmatchedCell(round_num=round_num, slot=slot, text=text)
                    )
                    continue
                best = max(r for r, _, _ in ranked)

        best_picks = [(pi, pick) for r, pi, pick in ranked if r == best]

        if len(best_picks) != 1:
            # ambiguous at the best rank; do not guess
            unmatched_cells.append(UnmatchedCell(round_num=round_num, slot=slot, text=text))
            continue

        pi, pick = best_picks[0]
        overall = overall_pick_for(round_num, slot, team_count, snake)
        rp = _round_pick_for(round_num, slot, team_count, snake)
        placements.append(
            Placement(
                pick=pick,
                overall_pick=overall,
                round_num=round_num,
                round_pick=rp,
                slot=slot,
                cell_text=text,
                method=_STAGE_NAMES[best],
            )
        )
        # consume: remove from pool AND mark the global orig index
        orig_i = pool[pi][0]
        consumed_local.append(orig_i)
        pool.pop(pi)

    return placements, unmatched_cells, consumed_local


def match_board_to_picks(
    board: DraftBoard, picks: Iterable[Any], snake: bool = True
) -> BoardMatchResult:
    """
    Match a parsed board against a league-season's HistoricalPick rows.

    STAGE 1: derive slot -> member_guid from roster overlap. Refused
    (errors recorded, not raised) if any column's best overlap is below
    MIN_OWNER_OVERLAP or ties its runner-up.

    STAGE 2: within each column's assigned owner, match cells to picks
    in priority order: exact normalized; unique last-token; acronym;
    initial+surname; token-overlap/edit-distance fallback. Each ESPN
    pick may be consumed AT MOST ONCE. Never matches across owners on a
    bare surname.
    """
    result = BoardMatchResult()
    team_count = len(board.team_names)

    picks_list = list(picks)

    # group picks by guid (skip picks with no guid)
    picks_by_guid: dict[str, list[Any]] = {}
    for p in picks_list:
        g = _pick_guid(p)
        if not g:
            continue
        picks_by_guid.setdefault(g, []).append(p)

    # STAGE 1: owner map
    owner_map, owner_confidence, errs = _derive_owner_map(board, picks_by_guid)
    result.owner_map = owner_map
    result.owner_confidence = owner_confidence
    result.errors.extend(errs)

    if not owner_map:
        # Refused: do not attempt placement; every pick is unmatched.
        result.unmatched_picks = list(picks_list)
        # cells unmatched too
        for (rn, s), text in board.cells.items():
            result.unmatched_cells.append(
                UnmatchedCell(round_num=rn, slot=s, text=text)
            )
        return result

    # STAGE 2: per-column matching
    # STAGE 2: per-column matching. Track consumed pick indices per guid so
    # each ESPN pick is consumed AT MOST ONCE across the whole board.
    consumed_per_guid: dict[str, set[int]] = {g: set() for g in picks_by_guid}

    for slot in range(1, team_count + 1):
        guid = owner_map.get(slot)
        if not guid:
            continue
        placements, unmatched_cells, consumed_local = _match_column(
            slot,
            guid,
            picks_by_guid,
            consumed_per_guid[guid],
            board,
            team_count,
            snake,
        )
        result.placements.extend(placements)
        result.unmatched_cells.extend(unmatched_cells)
        consumed_per_guid[guid].update(consumed_local)

    # unmatched picks = any pick not in a placement
    matched_ids = {id(pl.pick) for pl in result.placements}
    result.unmatched_picks = [p for p in picks_list if id(p) not in matched_ids]

    # match rate over non-empty board cells
    total_cells = len(board.cells)
    placed = len(result.placements)
    result.match_rate = (placed / total_cells) if total_cells else 0.0

    return result


# -- apply to the database --------------------------------------------------


async def apply_board(
    engine,
    espn_league_id: int,
    season: int,
    board_path: str,
    sheet_name: Optional[str] = None,
    snake: bool = True,
    dry_run: bool = True,
    force: bool = False,
) -> dict:
    """
    Load a league-season's HistoricalPick rows, match them against the
    board, and (only when dry_run is False) write back the new
    overall_pick / round_num / round_pick AND draft_order_verified=True
    for MATCHED picks only. Unmatched picks keep their existing values
    and stay unverified.

    Two hard guards before any write, overridable only by force=True:
      - match_rate < MIN_MATCH_RATE
      - resulting overall_pick values not unique within the league-season

    Always returns the full report whether or not it wrote.
    """
    board = parse_board(board_path, sheet_name=sheet_name)
    picks = await engine.find(
        HistoricalPick,
        (HistoricalPick.espn_league_id == espn_league_id)
        & (HistoricalPick.season == season),
    )
    picks = list(picks)

    result = match_board_to_picks(board, picks, snake=snake)

    report: dict = {
        "espn_league_id": espn_league_id,
        "season": season,
        "team_count": len(board.team_names),
        "team_names": board.team_names,
        "owner_map": {str(k): v for k, v in result.owner_map.items()},
        "owner_confidence": {str(k): v for k, v in result.owner_confidence.items()},
        "placements": [
            {
                "overall_pick": pl.overall_pick,
                "round_num": pl.round_num,
                "round_pick": pl.round_pick,
                "slot": pl.slot,
                "cell_text": pl.cell_text,
                "method": pl.method,
                "player": _pick_name(pl.pick),
                "member_guid": _pick_guid(pl.pick),
            }
            for pl in result.placements
        ],
        "unmatched_cells": [
            {"round_num": u.round_num, "slot": u.slot, "text": u.text}
            for u in result.unmatched_cells
        ],
        "unmatched_picks": [
            {"player": _pick_name(p), "member_guid": _pick_guid(p)}
            for p in result.unmatched_picks
        ],
        "match_rate": result.match_rate,
        "errors": list(result.errors),
        "dry_run": dry_run,
        "written": False,
        "refused": False,
        "refusal_reasons": [],
    }

    # Guards
    refusal_reasons: list[str] = []
    if result.errors:
        refusal_reasons.extend(result.errors)
    if result.match_rate < MIN_MATCH_RATE:
        refusal_reasons.append(
            f"match_rate {result.match_rate:.3f} < MIN_MATCH_RATE {MIN_MATCH_RATE}"
        )
    # uniqueness of resulting overall_pick among placements
    seen: dict[int, int] = {}
    dup = False
    for pl in result.placements:
        seen[pl.overall_pick] = seen.get(pl.overall_pick, 0) + 1
        if seen[pl.overall_pick] > 1:
            dup = True
    if dup:
        refusal_reasons.append("duplicate overall_pick values among placements")

    if refusal_reasons and not force:
        report["refused"] = True
        report["refusal_reasons"] = refusal_reasons
        return report

    if dry_run:
        return report

    # Write back matched picks only; unmatched picks untouched & unverified.
    placements_by_pick_id = {id(pl.pick): pl for pl in result.placements}
    to_save = []
    for pick in picks:
        pl = placements_by_pick_id.get(id(pick))
        if pl is None:
            # leave existing values; ensure NOT verified
            pick.draft_order_verified = False
            continue
        pick.overall_pick = pl.overall_pick
        pick.round_num = pl.round_num
        pick.round_pick = pl.round_pick
        pick.draft_order_verified = True
        to_save.append(pick)

    if to_save:
        await engine.save_all(to_save)
    # also persist the unverified flag on unmatched picks
    unmatched_picks = [p for p in picks if id(p) not in placements_by_pick_id]
    if unmatched_picks:
        await engine.save_all(unmatched_picks)

    report["written"] = True
    return report
