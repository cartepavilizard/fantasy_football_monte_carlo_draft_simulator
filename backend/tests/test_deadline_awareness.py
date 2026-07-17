# -*- coding: utf-8 -*-
"""
E8: trade-deadline awareness. Pure-core tests build leagues by hand
(test_trade_willingness.py's style); one async test drives
run_deadline_check through an in-memory mongomock engine and asserts
notification dedupe on repeat runs.
"""
import asyncio
import datetime

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from models.deadline_awareness import (
    DEADLINE_WINDOW_WEEKS,
    compute_deadline_windows,
    run_deadline_check,
    team_role,
    weeks_until_deadline,
)
from models.inseason import InSeasonLeague, LeagueTeamInfo
from models.notifications import Notification

SEASON = 2024
LEAGUE_ID = 111


def _team(team_id, wins=0, losses=0, ties=0, name=None):
    return LeagueTeamInfo(
        espn_team_id=team_id,
        name=name or f"Team {team_id}",
        wins=wins,
        losses=losses,
        ties=ties,
    )


def _league(teams, trade_deadline, latest_scoring_period=9, league_id=LEAGUE_ID):
    return InSeasonLeague(
        espn_league_id=league_id,
        season=SEASON,
        name="Deadline League",
        team_count=len(teams),
        latest_scoring_period=latest_scoring_period,
        trade_deadline=trade_deadline,
        teams=teams,
    )


NOW = datetime.datetime(2026, 11, 1)
DEADLINE = datetime.datetime(2026, 11, 18)  # ~2.4 weeks from NOW


# --- pure core ---------------------------------------------------------------


def test_weeks_until_deadline_ceil_whole_weeks():
    assert weeks_until_deadline(DEADLINE, now=datetime.datetime(2026, 11, 4)) == 2
    assert weeks_until_deadline(DEADLINE, now=datetime.datetime(2026, 11, 1)) == 3
    # passed
    assert weeks_until_deadline(DEADLINE, now=datetime.datetime(2026, 12, 1)) < 0


def test_weeks_until_deadline_none_when_no_deadline():
    assert weeks_until_deadline(None, now=NOW) is None


def test_contender_role_from_record():
    assert team_role(_team(1, wins=7, losses=1)) == "contender"
    assert team_role(_team(2, wins=1, losses=7)) == "rebuilder"
    assert team_role(_team(3, wins=4, losses=4)) == "neutral"


def test_record_below_min_decided_games_is_neutral():
    # 1-0 in week 1 is not contender evidence (C2/C4/E3 credibility rule)
    assert team_role(_team(1, wins=1, losses=0)) == "neutral"


def test_deadline_windows_for_contender_and_rebuilder():
    lg = _league(
        [
            _team(1, wins=8, losses=1, name="Contender"),
            _team(2, wins=1, losses=8, name="Rebuilder"),
            _team(3, wins=5, losses=4, name="Bubble"),
        ],
        trade_deadline=DEADLINE,
        latest_scoring_period=9,
    )
    report = compute_deadline_windows(lg, now=NOW)
    assert report["in_window"] is True
    assert report["weeks_to_deadline"] == 3
    by_id = {t["espn_team_id"]: t for t in report["teams"]}
    assert by_id[1]["role"] == "contender"
    assert by_id[1]["window"] == "buy"
    assert by_id[2]["role"] == "rebuilder"
    assert by_id[2]["window"] == "sell"
    # bubble team -> neutral, no window flag
    assert by_id[3]["role"] == "neutral"
    assert by_id[3]["window"] is None
    # playoff_value slot exists but is None until E1 enriches (not built here)
    assert by_id[1]["playoff_value"] is None


def test_outside_window_still_reports_but_in_window_false():
    far_deadline = NOW + datetime.timedelta(weeks=10)
    lg = _league([_team(1, wins=8, losses=1)], trade_deadline=far_deadline)
    report = compute_deadline_windows(lg, now=NOW)
    assert report["in_window"] is False
    # teams list still populated (role is computable regardless); windows
    # just aren't "open" — the report is honest about the countdown
    assert report["teams"][0]["role"] == "contender"


def test_no_deadline_league_is_graceful_no_flags_no_crash():
    lg = _league([_team(1, wins=8, losses=1)], trade_deadline=None)
    report = compute_deadline_windows(lg, now=NOW)
    assert report["in_window"] is False
    assert report["trade_deadline"] is None
    assert report["weeks_to_deadline"] is None
    assert report["teams"] == []


# --- async: run_deadline_check + notification dedupe -------------------------


def _engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-deadline")


def test_run_deadline_check_creates_deduped_notifications_and_is_idempotent():
    engine = _engine()

    async def go():
        await engine.save(
            _league(
                [
                    _team(1, wins=8, losses=1, name="Contender"),
                    _team(2, wins=1, losses=8, name="Rebuilder"),
                    _team(3, wins=5, losses=4, name="Bubble"),
                ],
                trade_deadline=DEADLINE,
                latest_scoring_period=9,
            )
        )
        # first run: one notification per windowed team (contender + rebuilder)
        reports = await run_deadline_check(engine, SEASON, now=NOW)
        assert len(reports) == 1
        report = reports[0]
        assert report["in_window"] is True
        windowed = [t for t in report["teams"] if t["window"]]
        assert {t["espn_team_id"] for t in windowed} == {1, 2}
        created = await engine.find(Notification)
        assert len(created) == 2
        kinds = {n.kind for n in created}
        assert kinds == {"deadline_window"}
        # dedupe keys are per league-season-week-team-window
        keys = sorted(n.dedupe_key for n in created)
        assert all(str(LEAGUE_ID) in k for k in keys)

        # second run inside the same scoring period: no duplicates
        await run_deadline_check(engine, SEASON, now=NOW)
        still = await engine.find(Notification)
        assert len(still) == 2  # idempotent

    asyncio.run(go())


def test_run_deadline_check_skips_league_with_no_deadline():
    engine = _engine()

    async def go():
        await engine.save(
            _league(
                [_team(1, wins=8, losses=1)],
                trade_deadline=None,
            )
        )
        reports = await run_deadline_check(engine, SEASON, now=NOW)
        assert len(reports) == 1
        assert reports[0]["in_window"] is False
        assert reports[0]["teams"] == []
        # no notifications written for a deadline-less league
        created = await engine.find(Notification)
        assert created == []

    asyncio.run(go())


def test_run_deadline_check_skips_league_outside_window():
    engine = _engine()

    async def go():
        far = NOW + datetime.timedelta(weeks=DEADLINE_WINDOW_WEEKS + 5)
        await engine.save(
            _league([_team(1, wins=8, losses=1)], trade_deadline=far)
        )
        reports = await run_deadline_check(engine, SEASON, now=NOW)
        assert reports[0]["in_window"] is False
        # outside the window -> no notifications
        created = await engine.find(Notification)
        assert created == []

    asyncio.run(go())


def test_run_deadline_check_attaches_playoff_value_when_e1_is_available():
    """When E1 context can be built (rosters + FA snapshot present), the
    contender's playoff_value is quoted; the rebuilder's is computed too.
    This is the E1-optional enrichment path."""
    from models.inseason import (
        FreeAgentEntry,
        FreeAgentSnapshot,
        RosterSlotEntry,
        TeamWeekRoster,
    )

    engine = _engine()

    async def go():
        await engine.save(
            _league(
                [
                    _team(1, wins=8, losses=1, name="Contender"),
                    _team(2, wins=1, losses=8, name="Rebuilder"),
                ],
                trade_deadline=DEADLINE,
                latest_scoring_period=9,
            )
        )
        # seed rosters + a FA snapshot so build_context succeeds. Use week 9
        # rosters so w0=9; trailing window is empty -> rate fallback to the
        # current-week projection (E1 fallback 1).
        for team_id, pid, pos, proj in [
            (1, 100, "WR", 14.0),
            (1, 101, "RB", 12.0),
            (2, 200, "WR", 10.0),
            (2, 201, "RB", 9.0),
        ]:
            await engine.save(
                TeamWeekRoster(
                    espn_league_id=LEAGUE_ID, season=SEASON, week=9,
                    espn_team_id=team_id,
                    entries=[
                        RosterSlotEntry(
                            player_id=pid, player_name=f"P{pid}", position=pos,
                            lineup_slot=pos, projected_points=proj,
                        )
                    ],
                )
            )
        await engine.save(
            FreeAgentSnapshot(
                espn_league_id=LEAGUE_ID, season=SEASON, week=9,
                entries=[
                    FreeAgentEntry(player_id=301, player_name="FA1",
                                   position="WR", projected_points=7.5),
                    FreeAgentEntry(player_id=302, player_name="FA2",
                                   position="WR", projected_points=7.0),
                    FreeAgentEntry(player_id=303, player_name="FA3",
                                   position="WR", projected_points=6.5),
                    FreeAgentEntry(player_id=304, player_name="FA1R",
                                   position="RB", projected_points=6.0),
                    FreeAgentEntry(player_id=305, player_name="FA2R",
                                   position="RB", projected_points=5.5),
                    FreeAgentEntry(player_id=306, player_name="FA3R",
                                   position="RB", projected_points=5.0),
                ],
            )
        )
        reports = await run_deadline_check(engine, SEASON, now=NOW)
        report = reports[0]
        by_id = {t["espn_team_id"]: t for t in report["teams"]}
        # E1 enrichment filled playoff_value on every windowed team
        assert by_id[1]["playoff_value"] is not None
        assert by_id[2]["playoff_value"] is not None

    asyncio.run(go())
