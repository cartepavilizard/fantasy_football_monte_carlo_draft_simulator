# -*- coding: utf-8 -*-
"""
D3: the manual Grok bridge. docs/specs/D3-grok-bridge-parsing.md is the
contract; this file covers the parser (pure, deterministic, never
raises), the three skepticism checks, the prompt templates joining
D1's writer directory, the five endpoints, and the structural
quarantine test that is this feature's load-bearing guarantee.
"""
import asyncio
import datetime
import random
from pathlib import Path

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.beat_writers import upsert_beat_writer
from models.inseason import (
    FreeAgentEntry,
    FreeAgentSnapshot,
    InjuryDesignation,
    PracticeReport,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.player_notes import (
    UnknownPlayerError,
    build_grok_prompt,
    compute_skepticism,
    delete_player_note,
    list_player_notes,
    parse_grok_paste,
    preview_player_note,
    save_player_note,
)

SEASON = 2024
WEEK = 5


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-player-notes")


VALID_BLOCK = """Some prose from Grok about the player.

---GROK-NOTE---
PLAYER: Test Player
STATUS_SIGNAL: UPGRADE
SUMMARY: Beat writer reports Test Player ran with the first team all week.
SOURCES: J. Reporter (Team Blog), 2024-10-07
NEWEST_SOURCE: 2024-10-07
CONFIDENCE: REPORTED
---END-GROK-NOTE---
"""


# --- parser: pure, deterministic, never raises (spec §5) ---------------------


def test_parse_valid_block():
    parsed = parse_grok_paste(VALID_BLOCK)
    assert parsed["parsed_block"] is True
    assert parsed["player"] == "Test Player"
    assert parsed["status_signal"] == "upgrade"
    assert parsed["summary"].startswith("Beat writer reports")
    assert parsed["sources"] == ["J. Reporter (Team Blog), 2024-10-07"]
    assert parsed["newest_source_date"] == datetime.date(2024, 10, 7)
    assert parsed["confidence"] == "reported"


def test_parse_no_block_found_returns_empty_shape():
    parsed = parse_grok_paste("just some prose, no footer at all")
    assert parsed == {
        "parsed_block": False,
        "player": None,
        "status_signal": None,
        "summary": None,
        "sources": [],
        "newest_source_date": None,
        "confidence": None,
    }


def test_parse_empty_or_whitespace_input():
    for text in ("", "   ", "\n\n\t"):
        parsed = parse_grok_paste(text)
        assert parsed["parsed_block"] is False


def test_parse_last_block_wins_when_grok_echoes_the_instruction():
    doubled = (
        "Sure, I'll finish with:\n\n---GROK-NOTE---\nPLAYER: Wrong Echo\n"
        "STATUS_SIGNAL: UNCLEAR\n---END-GROK-NOTE---\n\n"
        "Actual answer prose here.\n\n" + VALID_BLOCK
    )
    parsed = parse_grok_paste(doubled)
    assert parsed["player"] == "Test Player"
    assert parsed["status_signal"] == "upgrade"


def test_parse_invalid_status_signal_enum_is_none():
    text = VALID_BLOCK.replace("STATUS_SIGNAL: UPGRADE", "STATUS_SIGNAL: probably fine")
    parsed = parse_grok_paste(text)
    assert parsed["parsed_block"] is True
    assert parsed["status_signal"] is None


def test_parse_invalid_confidence_enum_is_none():
    text = VALID_BLOCK.replace("CONFIDENCE: REPORTED", "CONFIDENCE: pretty sure")
    parsed = parse_grok_paste(text)
    assert parsed["confidence"] is None


def test_parse_negative_information_is_a_valid_informative_note():
    text = """
---GROK-NOTE---
PLAYER: Quiet Player
STATUS_SIGNAL: UNCLEAR
SUMMARY: No recent comments found from beat writers this week.
SOURCES: no recent comments found
NEWEST_SOURCE: 2024-10-05
CONFIDENCE: REPORTED
---END-GROK-NOTE---
"""
    parsed = parse_grok_paste(text)
    assert parsed["status_signal"] == "unclear"
    assert parsed["sources"] == ["no recent comments found"]
    assert parsed["newest_source_date"] == datetime.date(2024, 10, 5)


def test_parse_multiline_sources_collected_until_next_key():
    text = """
---GROK-NOTE---
PLAYER: Multi Source
STATUS_SIGNAL: UNCHANGED
SUMMARY: Nothing new.
SOURCES: J. Reporter, 2024-10-01
Team presser, 2024-10-02
NEWEST_SOURCE: 2024-10-02
CONFIDENCE: RUMORED
---END-GROK-NOTE---
"""
    parsed = parse_grok_paste(text)
    assert parsed["sources"] == [
        "J. Reporter, 2024-10-01",
        "Team presser, 2024-10-02",
    ]


def test_parse_tolerates_quoting_code_fences_and_case():
    text = """
> ```
> ---grok-note---
> PLAYER: Quoted Player
> STATUS_SIGNAL: downgrade
> SUMMARY: quoted paste from an email client.
> SOURCES: someone, 2024-10-03
> NEWEST_SOURCE: 2024-10-03
> CONFIDENCE: rumored
> ---end-grok-note---
> ```
"""
    parsed = parse_grok_paste(text)
    assert parsed["parsed_block"] is True
    assert parsed["player"] == "Quoted Player"
    assert parsed["status_signal"] == "downgrade"


def test_parse_date_formats_tolerated():
    for date_str, expected in [
        ("2024-10-07", datetime.date(2024, 10, 7)),
        ("10/07/2024", datetime.date(2024, 10, 7)),
        ("October 7, 2024", datetime.date(2024, 10, 7)),
        ("Oct 7, 2024", datetime.date(2024, 10, 7)),
    ]:
        text = VALID_BLOCK.replace(
            "NEWEST_SOURCE: 2024-10-07", f"NEWEST_SOURCE: {date_str}"
        )
        parsed = parse_grok_paste(text)
        assert parsed["newest_source_date"] == expected, date_str


def test_parse_unparseable_date_is_none_and_therefore_stale():
    text = VALID_BLOCK.replace(
        "NEWEST_SOURCE: 2024-10-07", "NEWEST_SOURCE: sometime last week"
    )
    parsed = parse_grok_paste(text)
    assert parsed["newest_source_date"] is None


def test_parse_unknown_keys_ignored():
    text = """
---GROK-NOTE---
PLAYER: Test Player
EXTRA_FIELD: should be ignored
STATUS_SIGNAL: UPGRADE
SUMMARY: fine.
SOURCES: someone, 2024-10-07
NEWEST_SOURCE: 2024-10-07
CONFIDENCE: REPORTED
---END-GROK-NOTE---
"""
    parsed = parse_grok_paste(text)
    assert parsed["parsed_block"] is True
    assert parsed["status_signal"] == "upgrade"


def test_parse_never_raises_on_arbitrary_garbage():
    """Property-style fuzz standing in for hypothesis (not a dependency
    here): no adversarial input should ever raise (spec §5)."""
    random.seed(42)
    chars = "---GROKNOTEND:\n>```  \t€\U0001f3c8日本語أ"
    for _ in range(500):
        length = random.randint(0, 200)
        garbage = "".join(random.choice(chars) for _ in range(length))
        parsed = parse_grok_paste(garbage)  # must not raise
        assert isinstance(parsed, dict)
        assert "parsed_block" in parsed


def test_parse_emoji_markdown_rtl_gnarly_fixture_still_parses_keys():
    text = """
---GROK-NOTE---
PLAYER: Emoji Player \U0001f3c8
STATUS_SIGNAL: UPGRADE
SUMMARY: **Bold** reporting: صباح الخير — good morning, RTL text included.
SOURCES: Reporter \U0001f3a4, 2024-10-07
NEWEST_SOURCE: 2024-10-07
CONFIDENCE: REPORTED
---END-GROK-NOTE---
"""
    parsed = parse_grok_paste(text)
    assert parsed["parsed_block"] is True
    assert parsed["player"] == "Emoji Player \U0001f3c8"


# --- skepticism: staleness, official-signal conflicts, speculation, mismatch --


def _parsed(**overrides):
    base = {
        "parsed_block": True,
        "player": None,
        "status_signal": None,
        "summary": None,
        "sources": [],
        "newest_source_date": None,
        "confidence": None,
    }
    base.update(overrides)
    return base


def test_staleness_true_when_no_newest_source_date():
    engine = make_engine()
    result = asyncio.run(
        compute_skepticism(engine, SEASON, WEEK, "Anybody", _parsed())
    )
    assert result["stale_risk"] is True


def test_staleness_false_within_72_hours():
    engine = make_engine()
    today = datetime.datetime.now().date()
    parsed = _parsed(newest_source_date=today)
    result = asyncio.run(compute_skepticism(engine, SEASON, WEEK, "Anybody", parsed))
    assert result["stale_risk"] is False


def test_staleness_true_beyond_72_hours():
    engine = make_engine()
    old = datetime.datetime.now().date() - datetime.timedelta(days=5)
    parsed = _parsed(newest_source_date=old)
    result = asyncio.run(compute_skepticism(engine, SEASON, WEEK, "Anybody", parsed))
    assert result["stale_risk"] is True


def test_conflict_upgrade_vs_official_out():
    engine = make_engine()

    async def go():
        await engine.save(
            InjuryDesignation(
                season=SEASON, week=WEEK, player_name="Hurt Guy", designation="out"
            )
        )
        parsed = _parsed(
            newest_source_date=datetime.datetime.now().date(),
            status_signal="upgrade",
            confidence="reported",
            player="Hurt Guy",
        )
        return await compute_skepticism(engine, SEASON, WEEK, "Hurt Guy", parsed)

    result = asyncio.run(go())
    assert any("upgrade" in c and "out" in c for c in result["conflicts"])


def test_conflict_downgrade_vs_active_and_full_practice():
    engine = make_engine()

    async def go():
        await engine.save(
            PracticeReport(
                season=SEASON,
                week=WEEK,
                player_name="Healthy Guy",
                report_date=datetime.datetime.now(),
                participation="full",
            )
        )
        parsed = _parsed(
            newest_source_date=datetime.datetime.now().date(),
            status_signal="downgrade",
            confidence="reported",
            player="Healthy Guy",
        )
        return await compute_skepticism(engine, SEASON, WEEK, "Healthy Guy", parsed)

    result = asyncio.run(go())
    assert any("downgrade" in c for c in result["conflicts"])


def test_no_downgrade_conflict_without_a_practice_report_on_file():
    engine = make_engine()
    parsed = _parsed(
        newest_source_date=datetime.datetime.now().date(),
        status_signal="downgrade",
        confidence="reported",
        player="Nobody Reported",
    )
    result = asyncio.run(
        compute_skepticism(engine, SEASON, WEEK, "Nobody Reported", parsed)
    )
    assert result["conflicts"] == []


def test_conflict_speculation_confidence():
    engine = make_engine()
    parsed = _parsed(
        newest_source_date=datetime.datetime.now().date(),
        status_signal="unclear",
        confidence="speculation",
        player="Some Guy",
    )
    result = asyncio.run(compute_skepticism(engine, SEASON, WEEK, "Some Guy", parsed))
    assert "Grok labeled this speculation — no source" in result["conflicts"]


def test_conflict_player_mismatch_saves_under_requested_player():
    engine = make_engine()
    parsed = _parsed(
        newest_source_date=datetime.datetime.now().date(),
        status_signal="unchanged",
        confidence="reported",
        player="Some Other Guy",
    )
    result = asyncio.run(
        compute_skepticism(engine, SEASON, WEEK, "Requested Guy", parsed)
    )
    assert "Grok answered about Some Other Guy" in result["conflicts"]


def test_no_player_mismatch_conflict_when_block_didnt_parse():
    engine = make_engine()
    parsed = _parsed(parsed_block=False)
    result = asyncio.run(
        compute_skepticism(engine, SEASON, WEEK, "Requested Guy", parsed)
    )
    assert result["conflicts"] == []


# --- prompt templates (spec §2): pure string assembly, joins D1 -------------


def test_build_prompt_unknown_player_raises():
    engine = make_engine()
    try:
        asyncio.run(build_grok_prompt(engine, "Nobody At All", "beat_check", SEASON))
        assert False, "expected UnknownPlayerError"
    except UnknownPlayerError:
        pass


def test_build_grok_prompt_invalid_kind_raises_value_error():
    engine = make_engine()
    try:
        asyncio.run(build_grok_prompt(engine, "Whoever", "not_a_kind", SEASON))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_build_beat_check_prompt_uses_writer_directory():
    engine = make_engine()

    async def go():
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=1,
                season=SEASON,
                week=WEEK,
                entries=[
                    FreeAgentEntry(player_id=1, player_name="Some WR", nfl_team="SEA")
                ],
            )
        )
        await upsert_beat_writer(engine, "SEA", "Bob Condotta", "Seattle Times")
        return await build_grok_prompt(engine, "Some WR", "beat_check", SEASON)

    result = asyncio.run(go())
    assert result["nfl_team"] == "SEA"
    assert "Bob Condotta" in result["prompt_text"]
    assert "Seattle Times" in result["prompt_text"]
    assert "---GROK-NOTE---" in result["prompt_text"]
    assert "last 48 hours" in result["prompt_text"]


def test_build_beat_check_prompt_degrades_without_a_writer():
    engine = make_engine()

    async def go():
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=1,
                season=SEASON,
                week=WEEK,
                entries=[
                    FreeAgentEntry(player_id=1, player_name="Some WR", nfl_team="ATL")
                ],
            )
        )
        return await build_grok_prompt(engine, "Some WR", "beat_check", SEASON)

    result = asyncio.run(go())
    assert "ATL" in result["prompt_text"]
    assert "beat writers" in result["prompt_text"].lower()


def test_build_injury_timeline_and_usage_context_prompts():
    engine = make_engine()

    async def go():
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=1,
                season=SEASON,
                week=WEEK,
                entries=[
                    FreeAgentEntry(player_id=1, player_name="Some RB", nfl_team="KC")
                ],
            )
        )
        timeline = await build_grok_prompt(
            engine, "Some RB", "injury_timeline", SEASON, injury="hamstring"
        )
        usage = await build_grok_prompt(
            engine, "Some RB", "usage_context", SEASON, context="snap share spike"
        )
        return timeline, usage

    timeline, usage = asyncio.run(go())
    assert "hamstring" in timeline["prompt_text"]
    assert "snap share spike" in usage["prompt_text"]


def test_lookup_finds_player_from_roster_when_not_a_free_agent():
    engine = make_engine()

    async def go():
        await engine.save(
            TeamWeekRoster(
                espn_league_id=1,
                season=SEASON,
                week=WEEK,
                espn_team_id=1,
                entries=[
                    RosterSlotEntry(
                        player_id=1,
                        player_name="Rostered Guy",
                        nfl_team="BUF",
                        lineup_slot="WR",
                    )
                ],
            )
        )
        return await build_grok_prompt(engine, "Rostered Guy", "beat_check", SEASON)

    result = asyncio.run(go())
    assert result["nfl_team"] == "BUF"


# --- save/preview/list/delete (spec §6, §7) -----------------------------------


def test_save_note_stores_parsed_fields_and_quarantines_verified():
    engine = make_engine()

    async def go():
        return await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="Test Player",
            kind="beat_check",
            prompt_text="the generated prompt",
            raw_text=VALID_BLOCK,
        )

    note = asyncio.run(go())
    assert note.parsed_block is True
    assert note.status_signal == "upgrade"
    assert note.summary.startswith("Beat writer reports")
    assert note.verified is False  # always; no code path sets this True


def test_save_note_falls_back_to_manual_summary_and_status_when_no_block():
    engine = make_engine()

    async def go():
        return await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="No Block Guy",
            kind="beat_check",
            prompt_text="prompt",
            raw_text="just some prose, no footer",
            summary="manual summary",
            status_signal="unclear",
        )

    note = asyncio.run(go())
    assert note.parsed_block is False
    assert note.summary == "manual summary"
    assert note.status_signal == "unclear"


def test_save_note_ignores_invalid_manual_status_signal_override():
    engine = make_engine()

    async def go():
        return await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="No Block Guy",
            kind="beat_check",
            prompt_text="prompt",
            raw_text="no footer here",
            status_signal="probably fine",
        )

    note = asyncio.run(go())
    assert note.status_signal is None


def test_save_preserves_raw_text_verbatim_including_emoji_markdown_rtl():
    engine = make_engine()
    gnarly = "\U0001f3c8 **bold** صباح الخير\n" + VALID_BLOCK

    async def go():
        return await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="Test Player",
            kind="beat_check",
            prompt_text="prompt",
            raw_text=gnarly,
        )

    note = asyncio.run(go())
    assert note.raw_text == gnarly


def test_multiple_notes_same_player_all_kept_newest_first_no_dedupe():
    engine = make_engine()

    async def go():
        await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="Repeat Guy",
            kind="beat_check",
            prompt_text="p1",
            raw_text=VALID_BLOCK,
        )
        await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="Repeat Guy",
            kind="beat_check",
            prompt_text="p2",
            raw_text=VALID_BLOCK,
        )
        return await list_player_notes(engine, player_name="Repeat Guy")

    notes = asyncio.run(go())
    assert len(notes) == 2  # no dedupe — research accumulates
    assert notes[0].prompt_text == "p2"  # newest first


def test_delete_note_removes_it():
    engine = make_engine()

    async def go():
        note = await save_player_note(
            engine,
            season=SEASON,
            week=WEEK,
            player_name="Delete Me",
            kind="beat_check",
            prompt_text="p",
            raw_text=VALID_BLOCK,
        )
        await delete_player_note(engine, note)
        return await list_player_notes(engine, player_name="Delete Me")

    assert asyncio.run(go()) == []


def test_preview_without_player_context_skips_official_signal_checks():
    engine = make_engine()
    today = datetime.datetime.now().date().isoformat()
    fresh_block = VALID_BLOCK.replace("NEWEST_SOURCE: 2024-10-07", f"NEWEST_SOURCE: {today}")
    preview = asyncio.run(preview_player_note(engine, fresh_block))
    assert preview["parsed_block"] is True
    assert preview["stale_risk"] is False
    assert preview["conflicts"] == []


def test_preview_with_player_context_runs_full_skepticism():
    engine = make_engine()

    async def go():
        await engine.save(
            InjuryDesignation(
                season=SEASON, week=WEEK, player_name="Test Player", designation="out"
            )
        )
        return await preview_player_note(
            engine, VALID_BLOCK, player_name="Test Player", season=SEASON, week=WEEK
        )

    preview = asyncio.run(go())
    assert any("upgrade" in c for c in preview["conflicts"])


# --- endpoints (spec §6) -------------------------------------------------------


def test_grok_prompt_endpoint_404_unknown_player(client):
    response = client.get("/inseason/grok_prompt?player=Nobody&kind=beat_check")
    assert response.status_code == 404


def test_grok_prompt_endpoint_returns_prompt_for_known_player(client, app_module):
    async def seed():
        await app_module.engine.save(
            FreeAgentSnapshot(
                espn_league_id=1,
                season=2024,
                week=5,
                entries=[
                    FreeAgentEntry(player_id=1, player_name="Known WR", nfl_team="SEA")
                ],
            )
        )

    asyncio.run(seed())
    response = client.get(
        "/inseason/grok_prompt?player=Known+WR&kind=beat_check&season=2024"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["nfl_team"] == "SEA"
    assert "---GROK-NOTE---" in body["prompt_text"]


def test_grok_prompt_endpoint_bad_kind_400(client, app_module):
    async def seed():
        await app_module.engine.save(
            FreeAgentSnapshot(
                espn_league_id=1,
                season=2024,
                week=5,
                entries=[
                    FreeAgentEntry(player_id=1, player_name="Known WR", nfl_team="SEA")
                ],
            )
        )

    asyncio.run(seed())
    response = client.get(
        "/inseason/grok_prompt?player=Known+WR&kind=not_a_kind&season=2024"
    )
    assert response.status_code == 400


def test_player_note_parse_endpoint_previews_without_saving(client, app_module):
    response = client.post(
        "/inseason/player_note/parse", json={"raw_text": VALID_BLOCK}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parsed_block"] is True
    assert body["player"] == "Test Player"

    notes = asyncio.run(list_player_notes(app_module.engine))
    assert notes == []  # preview never saves


def test_player_note_save_endpoint_reparses_server_side(client):
    response = client.post(
        "/inseason/player_note",
        json={
            "player_name": "Test Player",
            "kind": "beat_check",
            "prompt_text": "the generated prompt",
            "raw_text": VALID_BLOCK,
            "season": 2024,
            "week": 5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status_signal"] == "upgrade"
    assert body["verified"] is False
    assert "id" in body


def test_player_note_save_endpoint_422_on_empty_raw_text(client):
    response = client.post(
        "/inseason/player_note",
        json={
            "player_name": "Test Player",
            "kind": "beat_check",
            "prompt_text": "p",
            "raw_text": "   ",
            "season": 2024,
            "week": 5,
        },
    )
    assert response.status_code == 422


def test_player_notes_list_and_delete_endpoints(client):
    saved = client.post(
        "/inseason/player_note",
        json={
            "player_name": "Delete Via Api",
            "kind": "beat_check",
            "prompt_text": "p",
            "raw_text": VALID_BLOCK,
            "season": 2024,
            "week": 5,
        },
    ).json()
    note_id = saved["id"]

    listed = client.get("/inseason/player_notes?player=Delete+Via+Api").json()["notes"]
    assert len(listed) == 1

    assert client.delete(f"/inseason/player_note/{note_id}").status_code == 200
    assert client.delete(f"/inseason/player_note/{note_id}").status_code == 404

    listed_after = client.get(
        "/inseason/player_notes?player=Delete+Via+Api"
    ).json()["notes"]
    assert listed_after == []


# --- quarantine (spec §4.3, the load-bearing check) ---------------------------


def test_no_module_outside_player_notes_and_its_api_imports_playernote():
    """
    Structural enforcement: PlayerNote must be unimportable from anywhere
    except models/player_notes.py itself and inseason_api.py (its API).
    No automated consumer — not a future E1, E4, or C1 — may read it;
    a note renders for a human to read and decide on, nothing more.
    """
    import ast

    backend_dir = Path(__file__).resolve().parent.parent
    allowed = {"models/player_notes.py", "inseason_api.py"}

    candidate_files = list(backend_dir.glob("*.py"))
    candidate_files += list((backend_dir / "models").glob("*.py"))
    candidate_files += list((backend_dir / "data_sources").glob("*.py"))

    for py_file in candidate_files:
        relative = py_file.relative_to(backend_dir).as_posix()
        if relative in allowed:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported_names = []
            if isinstance(node, ast.ImportFrom) and node.module and "player_notes" in node.module:
                imported_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "player_notes" in alias.name:
                        imported_names.append(alias.name)
            assert "PlayerNote" not in imported_names, (
                f"{relative} imports PlayerNote — only player_notes.py and "
                "inseason_api.py may (spec §4.3's quarantine)"
            )
