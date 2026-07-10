# -*- coding: utf-8 -*-
"""
Phase 5 backend: the scheduled refresh loop (with the draft-day pause
switch) and training the opponent-pick regression from ingested ESPN
history instead of a CSV.
"""
import asyncio

from conftest import sample, upload

from scheduler import RankingsScheduler
from test_owners_flow import make_ingester
from data_sources.espn_history import ingest_league_history


# --- scheduler loop -------------------------------------------------------------


class RefreshRecorder:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    async def __call__(self, engine, season, scoring_format):
        self.calls += 1
        if self.fail:
            raise RuntimeError("sources are down")
        return {"season": season, "scoring_format": scoring_format}


def run_scheduler_for(scheduler, seconds):
    async def go():
        scheduler.start()
        await asyncio.sleep(seconds)
        await scheduler.stop()

    asyncio.run(go())


def test_scheduler_ticks_on_interval_when_enabled():
    recorder = RefreshRecorder()
    scheduler = RankingsScheduler(
        lambda: None, refresh_fn=recorder, enabled=True, interval_hours=0.03 / 3600
    )
    run_scheduler_for(scheduler, 0.2)
    assert recorder.calls >= 3
    status = scheduler.status()
    assert status["last_run"] is not None
    assert status["last_error"] is None
    assert status["last_summary"]["scoring_format"]


def test_disabled_scheduler_never_refreshes_but_keeps_running():
    recorder = RefreshRecorder()
    scheduler = RankingsScheduler(
        lambda: None, refresh_fn=recorder, enabled=False, interval_hours=0.03 / 3600
    )
    run_scheduler_for(scheduler, 0.15)
    assert recorder.calls == 0


def test_draft_day_pause_stops_midstream():
    recorder = RefreshRecorder()
    scheduler = RankingsScheduler(
        lambda: None, refresh_fn=recorder, enabled=True, interval_hours=0.03 / 3600
    )

    async def go():
        scheduler.start()
        await asyncio.sleep(0.1)
        scheduler.configure(enabled=False)  # the draft-day switch
        ticks_when_paused = recorder.calls
        await asyncio.sleep(0.1)
        await scheduler.stop()
        return ticks_when_paused

    ticks_when_paused = asyncio.run(go())
    assert recorder.calls == ticks_when_paused  # no runs after the pause


def test_failed_run_is_recorded_and_loop_survives():
    recorder = RefreshRecorder(fail=True)
    scheduler = RankingsScheduler(
        lambda: None, refresh_fn=recorder, enabled=True, interval_hours=0.03 / 3600
    )
    run_scheduler_for(scheduler, 0.2)
    assert recorder.calls >= 2  # kept ticking after the failure
    assert "sources are down" in scheduler.status()["last_error"]


def test_configure_rejects_bad_interval():
    scheduler = RankingsScheduler(lambda: None, refresh_fn=RefreshRecorder())
    try:
        scheduler.configure(interval_hours=0)
        assert False, "should have raised"
    except ValueError:
        pass


# --- schedule endpoints ----------------------------------------------------------


def test_schedule_endpoints_control_the_running_scheduler(client):
    status = client.get("/rankings/schedule").json()
    assert status["running"] is True  # started with the app
    assert status["enabled"] is False  # env default: off outside compose

    updated = client.post(
        "/rankings/schedule?enabled=true&interval_hours=6"
    ).json()
    assert updated["enabled"] is True
    assert updated["interval_hours"] == 6.0
    assert updated["next_run"] is not None

    paused = client.post("/rankings/schedule?enabled=false").json()
    assert paused["enabled"] is False
    assert paused["next_run"] is None

    assert client.post("/rankings/schedule?interval_hours=0").status_code == 400


# --- historical drafts from ingested ESPN history ---------------------------------


def seed_history(app_module):
    asyncio.run(
        ingest_league_history(app_module.engine, 111, ingester=make_ingester())
    )


def test_historical_draft_sync_replaces_csv(client, app_module, league_id):
    seed_history(app_module)
    response = client.post(
        f"/league/{league_id}/historical_draft/sync?espn_league_id=111"
    )
    assert response.status_code == 200, response.text
    variables = response.json()["logistic_regression_variables"]
    # 8 stored picks: 6 (2024, one keeper, one unmatched position) + 2 (2023)
    # -> keeper and position-less picks excluded
    assert len(variables["x"]) == 6
    assert set(variables["y"]) <= {"qb", "rb", "wr", "te", "dst", "k"}
    # Re-sync replaces rather than 400ing like the CSV path
    assert (
        client.post(
            f"/league/{league_id}/historical_draft/sync?espn_league_id=111"
        ).status_code
        == 200
    )


def test_historical_draft_sync_requires_ingested_history(client, league_id):
    response = client.post(
        f"/league/{league_id}/historical_draft/sync?espn_league_id=42"
    )
    assert response.status_code == 404
    assert "ingest" in response.json()["detail"]


def test_league_is_draft_ready_with_zero_draft_csvs(client, app_module, league_id):
    """Exit criteria: players from the blend, regression from ESPN history —
    only the historical_players CSV (tier distributions) remains manual"""
    from test_rankings_flow import ESPN_RECORDS, StubAdapter

    from data_sources import service

    seed_history(app_module)
    original = service.build_adapters
    service.build_adapters = lambda sources=None: {
        "espn": StubAdapter("espn", ESPN_RECORDS)
    }
    try:
        client.post("/rankings/refresh")
        client.post(f"/league/{league_id}/player/sync")
        client.post(f"/league/{league_id}/historical_draft/sync?espn_league_id=111")
        upload(
            client,
            f"/league/{league_id}/historical_player",
            sample("historical_players.csv"),
        )
        league = client.get(f"/league/{league_id}").json()
        assert league["ready_for_draft"] is True
    finally:
        service.build_adapters = original
