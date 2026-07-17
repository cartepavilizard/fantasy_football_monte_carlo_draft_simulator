# -*- coding: utf-8 -*-
"""
MANUAL GROK BRIDGE: PASTE-BACK PARSING (PHASE D, TASK D3)

Implements docs/specs/D3-grok-bridge-parsing.md verbatim. Read that
spec before touching this module; the summary below is orientation,
not the contract.

THE CORE DESIGN MOVE: the backend has no LLM/xAI access, by design.
So the generated prompt (build_grok_prompt) instructs Grok to end its
answer with a fenced, line-keyed ---GROK-NOTE--- block, and
parse_grok_paste is a deterministic, never-raising extraction of that
block — string templates out, regex parsing in. The unstructured prose
above the block is kept verbatim as raw_text; the block is the only
thing the parser trusts.

QUARANTINE (the load-bearing skepticism check, spec §4.3): verified is
ALWAYS False and no code path here ever sets it True. No module other
than this one and its API endpoints in inseason_api.py may import
PlayerNote — enforced by tests/test_player_notes.py's import-graph
test. A note renders for a human to read and decide on; nothing here
feeds E1's valuations, E4's triggers, or C1's warnings automatically.
"""
import datetime
import re
from typing import Dict, List, Optional

from odmantic import Model
from odmantic import Field as ODField
from odmantic import query

from .beat_writers import BeatWriter, get_beat_writer
from .config import GROK_STALE_HOURS
from .inseason import FreeAgentSnapshot, InjuryDesignation, PracticeReport, TeamWeekRoster

VALID_STATUS_SIGNALS = {"upgrade", "downgrade", "unchanged", "unclear"}
VALID_CONFIDENCE = {"reported", "rumored", "speculation"}
PROMPT_KINDS = {"beat_check", "injury_timeline", "usage_context"}

# Official designations urgent enough to contradict a claimed "upgrade"
# (spec §4.2's first conflict rule; "ir" is this model's spelling of
# the spec's prose "injury_reserve")
UPGRADE_CONTRADICTING_STATUSES = {"out", "ir"}


class UnknownPlayerError(Exception):
    """Raised by build_grok_prompt when the player matches no known
    roster/free-agent row anywhere in cached data (spec §6, 404)"""

    def __init__(self, player_name: str):
        self.player_name = player_name
        super().__init__(f"Unknown player: {player_name}")


# --- storage model (spec §3) --------------------------------------------------


class PlayerNote(Model):
    """
    One manually-pasted Grok answer about one player, for one week.
    kind/prompt_text/raw_text make every note auditable back to what
    was asked and what came back — that provenance is this model's
    whole identity.
    """

    model_config = {"collection": "player_notes"}

    season: int
    week: int
    player_name: str
    nfl_team: Optional[str] = None
    kind: str  # beat_check | injury_timeline | usage_context
    prompt_text: str
    raw_text: str
    summary: Optional[str] = None
    status_signal: Optional[str] = None  # upgrade|downgrade|unchanged|unclear
    grok_confidence: Optional[str] = None  # reported|rumored|speculation
    sources: List[str] = []
    # BSON has no date-only type (only datetime, like every other
    # date-like field in this codebase) — stored at midnight; the
    # parser's own contract still returns a plain datetime.date (spec
    # §5), converted at the save boundary (_as_stored_datetime below)
    newest_source_date: Optional[datetime.datetime] = None
    parsed_block: bool = False  # did the footer parse?
    stale_risk: bool = True  # skeptical until the parse proves freshness
    conflicts: List[str] = []
    # ALWAYS False; no code path in this codebase ever sets this True —
    # the human reading the note is the verification step (spec §4.3)
    verified: bool = False
    created_at: datetime.datetime = ODField(default_factory=datetime.datetime.now)


# --- parser (spec §5): pure, deterministic, never raises ---------------------

_GROK_NOTE_BLOCK_RE = re.compile(
    r"[>\s]*-{2,}\s*GROK-NOTE\s*-{2,}(.*?)[>\s]*-{2,}\s*END-GROK-NOTE\s*-{2,}",
    re.IGNORECASE | re.DOTALL,
)
_KEY_LINE_RE = re.compile(
    r"^(PLAYER|STATUS_SIGNAL|SUMMARY|SOURCES|NEWEST_SOURCE|CONFIDENCE)\s*:\s*(.*)$",
    re.IGNORECASE,
)

_EMPTY_PARSE = {
    "parsed_block": False,
    "player": None,
    "status_signal": None,
    "summary": None,
    "sources": [],
    "newest_source_date": None,
    "confidence": None,
}


def _strip_quote_marker(line: str) -> str:
    """Tolerate leading '>' blockquote markers (spec §5)"""
    return re.sub(r"^\s*>+\s*", "", line)


def _parse_flexible_date(value: str) -> Optional[datetime.date]:
    """YYYY-MM-DD, MM/DD/YYYY, or 'Month D, YYYY' (spec §5); unparseable -> None"""
    value = value.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_grok_paste(raw_text: str) -> dict:
    """
    Locate the LAST ---GROK-NOTE--- ... ---END-GROK-NOTE--- span (Grok
    sometimes echoes the instruction before answering) and extract its
    six keys. Never raises — worst case returns the no-block shape.
    """
    try:
        text = raw_text or ""
        matches = list(_GROK_NOTE_BLOCK_RE.finditer(text))
        if not matches:
            return dict(_EMPTY_PARSE, sources=[])

        block = matches[-1].group(1)
        values: Dict[str, List[str]] = {}
        current_key: Optional[str] = None
        for raw_line in block.splitlines():
            line = _strip_quote_marker(raw_line).strip()
            if not line or line.startswith("```"):
                continue
            key_match = _KEY_LINE_RE.match(line)
            if key_match:
                current_key = key_match.group(1).upper()
                rest = key_match.group(2).strip()
                values.setdefault(current_key, [])
                if rest:
                    values[current_key].append(rest)
            elif current_key == "SOURCES":
                # multi-line SOURCES collects until the next known key
                values[current_key].append(line)
            # continuation lines under any other key, or before any key
            # is seen, are ignored (unknown keys are ignored per spec)

        player = (values.get("PLAYER") or [None])[0]

        status_signal = None
        raw_signal = (values.get("STATUS_SIGNAL") or [None])[0]
        if raw_signal and raw_signal.strip().lower() in VALID_STATUS_SIGNALS:
            status_signal = raw_signal.strip().lower()

        summary = " ".join(values.get("SUMMARY", [])).strip() or None
        sources = values.get("SOURCES", [])

        newest_source_date = None
        raw_newest = (values.get("NEWEST_SOURCE") or [None])[0]
        if raw_newest:
            newest_source_date = _parse_flexible_date(raw_newest)

        confidence = None
        raw_confidence = (values.get("CONFIDENCE") or [None])[0]
        if raw_confidence and raw_confidence.strip().lower() in VALID_CONFIDENCE:
            confidence = raw_confidence.strip().lower()

        return {
            "parsed_block": True,
            "player": player,
            "status_signal": status_signal,
            "summary": summary,
            "sources": sources,
            "newest_source_date": newest_source_date,
            "confidence": confidence,
        }
    except Exception:
        return dict(_EMPTY_PARSE, sources=[])


# --- skepticism (spec §4; #3 quarantine is structural, see module docstring) --


async def _latest_official_status(
    engine, season: int, week: int, player_name: str
) -> Optional[str]:
    """
    D2's designation for this player-week, lowercased, or "active" when
    no designation row exists — D2 only ever writes a row for players
    who appear on the injury report, so absence means healthy.
    """
    designation = await engine.find_one(
        InjuryDesignation,
        (InjuryDesignation.season == season)
        & (InjuryDesignation.week == week)
        & (InjuryDesignation.player_name == player_name),
        sort=query.desc(InjuryDesignation.updated_at),
    )
    if designation is None:
        return "active"
    return designation.designation.lower()


async def _latest_practice_participation(
    engine, season: int, week: int, player_name: str
) -> Optional[str]:
    report = await engine.find_one(
        PracticeReport,
        (PracticeReport.season == season)
        & (PracticeReport.week == week)
        & (PracticeReport.player_name == player_name),
        sort=query.desc(PracticeReport.report_date),
    )
    return report.participation if report is not None else None


async def compute_skepticism(
    engine, season: int, week: int, requested_player: str, parsed: dict
) -> dict:
    """
    Three independent checks (spec §4); returns {stale_risk, conflicts}.
    Always computed server-side, even for the parse-preview round-trip
    (the save endpoint never trusts a client-supplied preview result).
    """
    newest = parsed.get("newest_source_date")
    if newest is None:
        stale_risk = True
    else:
        age_hours = (datetime.datetime.now().date() - newest).days * 24
        stale_risk = age_hours > GROK_STALE_HOURS

    conflicts: List[str] = []
    status_signal = parsed.get("status_signal")

    official_status = await _latest_official_status(
        engine, season, week, requested_player
    )
    if status_signal == "upgrade" and official_status in UPGRADE_CONTRADICTING_STATUSES:
        conflicts.append(
            f"claims upgrade but official status is {official_status} (as of sync)"
        )
    elif status_signal == "downgrade" and official_status == "active":
        participation = await _latest_practice_participation(
            engine, season, week, requested_player
        )
        if participation == "full":
            conflicts.append(
                "claims downgrade but official status is active and the latest "
                "practice report is full participation — ahead of official "
                "reports OR wrong"
            )

    if parsed.get("confidence") == "speculation":
        conflicts.append("Grok labeled this speculation — no source")

    if parsed.get("parsed_block"):
        parsed_player = (parsed.get("player") or "").strip().lower()
        if parsed_player and parsed_player != (requested_player or "").strip().lower():
            conflicts.append(f"Grok answered about {parsed.get('player')}")

    return {"stale_risk": stale_risk, "conflicts": conflicts}


# --- prompt templates (spec §2): pure string assembly, no fetch --------------

_GROK_FOOTER = """Finish your answer with exactly this block, filled in:

---GROK-NOTE---
PLAYER: <player name>
STATUS_SIGNAL: one of UPGRADE | DOWNGRADE | UNCHANGED | UNCLEAR
SUMMARY: <one factual sentence, max 30 words>
SOURCES: <who said it and where, one per line, each with a date>
NEWEST_SOURCE: <date of the most recent source, YYYY-MM-DD>
CONFIDENCE: one of REPORTED | RUMORED | SPECULATION
---END-GROK-NOTE---"""


def _beat_check_prompt(player_name: str, writer: Optional[BeatWriter], nfl_team: Optional[str]) -> str:
    if writer is not None:
        who = f"{writer.writer_name} ({writer.outlet})"
    elif nfl_team:
        who = f"{nfl_team}'s beat writers"
    else:
        who = "beat writers covering this player's team"
    body = (
        f"What has {who} said about {player_name} in the last 48 hours? "
        "Include exact quotes and dates. If they have said nothing in that "
        "window, say so explicitly — do not substitute older or national "
        "reporting."
    )
    return f"{body}\n\n{_GROK_FOOTER}"


def _injury_timeline_prompt(player_name: str, injury: Optional[str]) -> str:
    injury_phrase = injury or "injury"
    body = (
        f"What is the most recent reporting on {player_name}'s {injury_phrase} "
        "recovery timeline? Cite each source with its date; distinguish team "
        "statements from reporter speculation."
    )
    return f"{body}\n\n{_GROK_FOOTER}"


def _usage_context_prompt(player_name: str, context: Optional[str]) -> str:
    context_phrase = context or "role change"
    body = (
        f"Reporting from the last week on {player_name}'s role change "
        f"({context_phrase}) — is this coach-confirmed or observed only?"
    )
    return f"{body}\n\n{_GROK_FOOTER}"


async def _find_player(engine, player_name: str, season: int):
    """
    (found, nfl_team) from any current roster/free-agent row — pure
    Mongo read, no fetch (grok_prompt's contract, spec §2). found is
    tracked separately from nfl_team because a real row can carry a
    blank team (unresolved at sync time); only "no row at all" is
    "unknown player".
    """
    snapshots = await engine.find(
        FreeAgentSnapshot,
        FreeAgentSnapshot.season == season,
        sort=query.desc(FreeAgentSnapshot.synced_at),
    )
    for snapshot in snapshots:
        for entry in snapshot.entries:
            if entry.player_name == player_name:
                return True, entry.nfl_team
    rosters = await engine.find(
        TeamWeekRoster,
        TeamWeekRoster.season == season,
        sort=query.desc(TeamWeekRoster.synced_at),
    )
    for roster in rosters:
        for entry in roster.entries:
            if entry.player_name == player_name:
                return True, entry.nfl_team
    return False, None


async def _lookup_nfl_team(engine, player_name: str, season: int) -> Optional[str]:
    """nfl_team when the player is known, else None (save_player_note's
    contract — an unresolvable team on a save is fine, unlike a prompt)"""
    _found, nfl_team = await _find_player(engine, player_name, season)
    return nfl_team


async def build_grok_prompt(
    engine,
    player_name: str,
    kind: str,
    season: int,
    injury: Optional[str] = None,
    context: Optional[str] = None,
) -> dict:
    """
    Assemble one of the three prompt templates (spec §2). Raises
    UnknownPlayerError (-> 404) when the player matches no cached
    roster/free-agent row; degrades to team-level phrasing when the
    team is known but D1 has no writer for it.
    """
    if kind not in PROMPT_KINDS:
        raise ValueError(f"Unknown prompt kind: {kind}")

    found, nfl_team = await _find_player(engine, player_name, season)
    if not found:
        raise UnknownPlayerError(player_name)

    if kind == "beat_check":
        writer = await get_beat_writer(engine, nfl_team)
        prompt_text = _beat_check_prompt(player_name, writer, nfl_team)
    elif kind == "injury_timeline":
        prompt_text = _injury_timeline_prompt(player_name, injury)
    else:
        prompt_text = _usage_context_prompt(player_name, context)

    return {"prompt_text": prompt_text, "nfl_team": nfl_team, "kind": kind}


def _as_stored_datetime(value: Optional[datetime.date]) -> Optional[datetime.datetime]:
    """PlayerNote.newest_source_date is stored as datetime (BSON has no
    date-only type); midnight loses no information the parser extracted"""
    if value is None:
        return None
    return datetime.datetime.combine(value, datetime.time.min)


# --- CRUD (spec §6) -----------------------------------------------------------


async def preview_player_note(
    engine,
    raw_text: str,
    player_name: Optional[str] = None,
    season: Optional[int] = None,
    week: Optional[int] = None,
) -> dict:
    """
    Parse + skepticism, without saving — the UI's confirm screen. When
    the caller hasn't yet resolved player/season/week (still on the
    generate-prompt step), the official-signal and player-mismatch
    conflict checks are skipped; staleness and the speculation-confidence
    conflict never need that context and always run.
    """
    parsed = parse_grok_paste(raw_text)
    if player_name is not None and season is not None and week is not None:
        skepticism = await compute_skepticism(engine, season, week, player_name, parsed)
    else:
        newest = parsed.get("newest_source_date")
        if newest is None:
            stale_risk = True
        else:
            age_hours = (datetime.datetime.now().date() - newest).days * 24
            stale_risk = age_hours > GROK_STALE_HOURS
        conflicts = []
        if parsed.get("confidence") == "speculation":
            conflicts.append("Grok labeled this speculation — no source")
        skepticism = {"stale_risk": stale_risk, "conflicts": conflicts}
    return {**parsed, **skepticism}


async def save_player_note(
    engine,
    *,
    season: int,
    week: int,
    player_name: str,
    kind: str,
    prompt_text: str,
    raw_text: str,
    summary: Optional[str] = None,
    status_signal: Optional[str] = None,
) -> PlayerNote:
    """
    Re-runs parse + skepticism server-side (never trusts a client-supplied
    preview) and saves. summary/status_signal are the user's manual
    fallback when the block didn't parse (spec §5) — the parsed value
    always wins when present.
    """
    parsed = parse_grok_paste(raw_text)
    skepticism = await compute_skepticism(engine, season, week, player_name, parsed)

    final_status = parsed.get("status_signal")
    if final_status is None and status_signal is not None:
        candidate = status_signal.strip().lower()
        final_status = candidate if candidate in VALID_STATUS_SIGNALS else None

    final_summary = parsed.get("summary") or summary
    nfl_team = await _lookup_nfl_team(engine, player_name, season)

    note = PlayerNote(
        season=season,
        week=week,
        player_name=player_name,
        nfl_team=nfl_team,
        kind=kind,
        prompt_text=prompt_text,
        raw_text=raw_text,
        summary=final_summary,
        status_signal=final_status,
        grok_confidence=parsed.get("confidence"),
        sources=parsed.get("sources", []),
        newest_source_date=_as_stored_datetime(parsed.get("newest_source_date")),
        parsed_block=parsed.get("parsed_block", False),
        stale_risk=skepticism["stale_risk"],
        conflicts=skepticism["conflicts"],
        verified=False,
    )
    await engine.save(note)
    return note


async def list_player_notes(
    engine,
    player_name: Optional[str] = None,
    week: Optional[int] = None,
    season: Optional[int] = None,
) -> List[PlayerNote]:
    """Newest first; no dedupe — research accumulates (spec §7)"""
    criteria = None
    for condition in (
        PlayerNote.player_name == player_name if player_name is not None else None,
        PlayerNote.week == week if week is not None else None,
        PlayerNote.season == season if season is not None else None,
    ):
        if condition is None:
            continue
        criteria = condition if criteria is None else (criteria & condition)

    # id as a tiebreaker: two notes saved within the same clock tick
    # would otherwise sort arbitrarily on created_at alone (same pattern
    # as FreeAgentSnapshot's synced_at/id sort in inseason_api.py)
    sort_order = (query.desc(PlayerNote.created_at), query.desc(PlayerNote.id))
    if criteria is None:
        return await engine.find(PlayerNote, sort=sort_order)
    return await engine.find(PlayerNote, criteria, sort=sort_order)


async def delete_player_note(engine, note) -> None:
    """Notes are user data; full delete, no soft-delete ceremony"""
    await engine.delete(note)
