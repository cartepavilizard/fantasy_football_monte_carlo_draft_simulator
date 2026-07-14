# -*- coding: utf-8 -*-
"""
D2 (cheap half): nflverse practice-report ingestion. The contract under
test: REG-only filtering, the PARTICIPATION_MAP/DESIGNATION_MAP mapping
(lowercased/stripped match), header validation for a real schema break,
the unmapped-value tripwire (both sides of UNMAPPED_TRIPWIRE), the
daily-trail upsert for PracticeReport vs replace-per-(season, week,
player_name) for InjuryDesignation, per-failure-mode logging that never
raises, and the downgrade-alert pass (all four downgrade shapes plus
both non-alert shapes), rostered-only filter, and week-reset behavior.

MAPPING NOTE: the current-season injuries CSV (verified against
injuries_2025.csv at implementation time) no longer carries a
date_modified column — see data_sources/nflverse_injuries.py's module
docstring. FIELDS below matches that real current-season header;
LEGACY_FIELDS (with date_modified, no season_type) matches the older
format the adapter still parses when the column is present.
"""
import asyncio
import datetime

from mongomock_motor import AsyncMongoMockClient
from odmantic import AIOEngine

from data_sources.nflverse_injuries import (
    HeaderValidationError,
    NflverseInjuriesAdapter,
    ensure_practice_downgrade_notifications,
    ingest_practice_reports,
)
from data_sources.base import SourceFetchError
from models.inseason import (
    InjuryDesignation,
    LeagueSyncLog,
    PracticeReport,
    RosterSlotEntry,
    TeamWeekRoster,
)
from models.notifications import Notification
from tests.conftest import ScriptedTransport

SEASON = 2026
WEEK = 6

FIELDS = [
    "season", "season_type", "game_type", "team", "week", "gsis_id",
    "position", "full_name", "first_name", "last_name",
    "report_primary_injury", "report_secondary_injury", "report_status",
    "practice_primary_injury", "practice_secondary_injury", "practice_status",
]
LEGACY_FIELDS = [
    "season", "game_type", "team", "week", "gsis_id", "position",
    "full_name", "first_name", "last_name", "report_primary_injury",
    "report_secondary_injury", "report_status", "practice_primary_injury",
    "practice_secondary_injury", "practice_status", "date_modified",
]

HEADER = ",".join(FIELDS)
LEGACY_HEADER = ",".join(LEGACY_FIELDS)
NO_TEAM_FIELDS = [f for f in FIELDS if f != "team"]
NO_TEAM_HEADER = ",".join(NO_TEAM_FIELDS)

DEFAULTS = {
    "season": SEASON,
    "season_type": "REG",
    "game_type": "REG",
    "team": "SEA",
    "week": WEEK,
    "gsis_id": "00-1",
    "position": "RB",
    "full_name": "Kenneth Walker III",
    "first_name": "Kenneth",
    "last_name": "Walker",
    "report_primary_injury": "",
    "report_secondary_injury": "",
    "report_status": "",
    "practice_primary_injury": "",
    "practice_secondary_injury": "",
    "practice_status": "",
    "date_modified": "",
}


def make_row(fields, **overrides):
    values = dict(DEFAULTS)
    values.update(overrides)
    return ",".join(str(values[field]) for field in fields)


def row(**overrides) -> str:
    return make_row(FIELDS, **overrides)


def legacy_row(**overrides) -> str:
    return make_row(LEGACY_FIELDS, **overrides)


def no_team_row(**overrides) -> str:
    return make_row(NO_TEAM_FIELDS, **overrides)


def csv_text(header: str, *rows: str) -> str:
    return "\n".join([header, *rows])


def make_engine():
    return AIOEngine(client=AsyncMongoMockClient(), database="test-nflverse-injuries")


def run(coro):
    return asyncio.run(coro)


def transport(text: str, status: int = 200) -> ScriptedTransport:
    return ScriptedTransport([(status, text)])


async def _reports(engine):
    return await engine.find(PracticeReport)


async def _designations(engine):
    return await engine.find(InjuryDesignation)


async def _logs(engine):
    return await engine.find(LeagueSyncLog)


async def _notifications(engine):
    return await engine.find(Notification)


async def seed_roster(engine, player_names, season=SEASON, week=WEEK, league_id=111):
    await engine.save(
        TeamWeekRoster(
            espn_league_id=league_id,
            season=season,
            week=week,
            espn_team_id=1,
            entries=[
                RosterSlotEntry(
                    player_id=idx + 1,
                    player_name=name,
                    lineup_slot="BE",
                )
                for idx, name in enumerate(player_names)
            ],
        )
    )


# --- adapter parsing -------------------------------------------------------------


def test_fetch_injuries_maps_fields_and_falls_back_report_date_without_date_modified():
    csv = csv_text(
        HEADER,
        row(
            practice_status="Limited Participation in Practice",
            report_status="Questionable",
            practice_primary_injury="Hamstring",
            report_primary_injury="Hamstring",
        ),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))
    now = datetime.datetime(2026, 10, 8, 17, 40)

    reports, designations, stats = run(adapter.fetch_injuries(SEASON, now=now))

    (report,) = reports
    assert report.player_name == "Kenneth Walker III"
    assert report.week == WEEK
    assert report.nfl_team == "SEA"
    assert report.position == "RB"
    assert report.participation == "limited"
    assert report.note == "Hamstring"
    assert report.report_date == datetime.datetime(2026, 10, 8)  # fallback: day only

    (designation,) = designations
    assert designation.designation == "questionable"
    assert stats == {"practice_non_blank": 1, "practice_unmapped": 0}


def test_fetch_injuries_non_reg_game_type_excluded():
    csv = csv_text(
        HEADER,
        row(game_type="WC", practice_status="Full Participation in Practice"),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))

    reports, designations, stats = run(adapter.fetch_injuries(SEASON))
    assert reports == []
    assert designations == []
    assert stats == {"practice_non_blank": 0, "practice_unmapped": 0}


def test_fetch_injuries_blank_practice_status_with_designation_writes_designation_only():
    """Late-week game-status-only updates are normal: write the
    InjuryDesignation, skip the PracticeReport (spec §2 bullet 3)"""
    csv = csv_text(HEADER, row(practice_status="", report_status="Out"))
    adapter = NflverseInjuriesAdapter(transport=transport(csv))

    reports, designations, stats = run(adapter.fetch_injuries(SEASON))
    assert reports == []
    (designation,) = designations
    assert designation.designation == "out"
    assert stats == {"practice_non_blank": 0, "practice_unmapped": 0}


def test_fetch_injuries_unmapped_practice_status_counted_and_not_written():
    csv = csv_text(HEADER, row(practice_status="Warming Up Only"))
    adapter = NflverseInjuriesAdapter(transport=transport(csv))

    reports, designations, stats = run(adapter.fetch_injuries(SEASON))
    assert reports == []
    assert stats == {"practice_non_blank": 1, "practice_unmapped": 1}


def test_fetch_injuries_missing_required_column_raises_header_validation_error():
    csv = csv_text(
        NO_TEAM_HEADER,
        no_team_row(practice_status="Full Participation in Practice"),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))

    try:
        run(adapter.fetch_injuries(SEASON))
        assert False, "should have raised"
    except HeaderValidationError as exc:
        assert "team" in str(exc)


def test_fetch_injuries_http_failure_raises_source_fetch_error():
    adapter = NflverseInjuriesAdapter(transport=transport("not found", status=404))
    try:
        run(adapter.fetch_injuries(SEASON))
        assert False, "should have raised"
    except SourceFetchError:
        pass


def test_fetch_injuries_normalizes_la_to_rams():
    csv = csv_text(
        HEADER,
        row(team="LA", practice_status="Full Participation in Practice"),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))

    reports, _designations, _stats = run(adapter.fetch_injuries(SEASON))
    (report,) = reports
    assert report.nfl_team == "LAR"


def test_fetch_injuries_legacy_date_modified_parsed_when_present():
    csv = csv_text(
        LEGACY_HEADER,
        legacy_row(
            practice_status="Did Not Participate in Practice",
            date_modified="2026-10-09T08:15:00",
        ),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))
    # now is a totally different day; the legacy column must win over the fallback
    now = datetime.datetime(2026, 11, 1)

    reports, _designations, _stats = run(adapter.fetch_injuries(SEASON, now=now))
    (report,) = reports
    assert report.report_date == datetime.datetime(2026, 10, 9, 8, 15, 0)


# --- ingest_practice_reports: write semantics -------------------------------------


def test_ingest_daily_trail_upsert_wed_thu_fri_then_one_designation():
    engine = make_engine()
    days = [
        (datetime.datetime(2026, 10, 7), "Did Not Participate in Practice", ""),
        (datetime.datetime(2026, 10, 8), "Limited Participation in Practice", ""),
        (datetime.datetime(2026, 10, 9), "Full Participation in Practice", "Questionable"),
    ]
    for now, practice_status, report_status in days:
        csv = csv_text(
            HEADER,
            row(practice_status=practice_status, report_status=report_status),
        )
        adapter = NflverseInjuriesAdapter(transport=transport(csv))
        run(ingest_practice_reports(engine, SEASON, week=WEEK, adapter=adapter, now=now))

    reports = sorted(run(_reports(engine)), key=lambda r: r.report_date)
    assert len(reports) == 3
    assert [r.participation for r in reports] == ["dnp", "limited", "full"]

    (designation,) = run(_designations(engine))
    assert designation.designation == "questionable"


def test_ingest_same_day_rerun_updates_in_place_not_duplicate():
    engine = make_engine()
    now = datetime.datetime(2026, 10, 8, 9, 0)

    for practice_status in [
        "Did Not Participate in Practice",
        "Limited Participation in Practice",
    ]:
        csv = csv_text(HEADER, row(practice_status=practice_status))
        adapter = NflverseInjuriesAdapter(transport=transport(csv))
        run(ingest_practice_reports(engine, SEASON, week=WEEK, adapter=adapter, now=now))

    (report,) = run(_reports(engine))
    assert report.participation == "limited"  # the later same-day value wins


def test_ingest_designation_replace_per_player_week():
    engine = make_engine()
    day1 = datetime.datetime(2026, 10, 7)
    day2 = datetime.datetime(2026, 10, 9)

    csv1 = csv_text(HEADER, row(report_status="Questionable"))
    run(
        ingest_practice_reports(
            engine, SEASON, week=WEEK,
            adapter=NflverseInjuriesAdapter(transport=transport(csv1)), now=day1,
        )
    )
    csv2 = csv_text(HEADER, row(report_status="Doubtful"))
    run(
        ingest_practice_reports(
            engine, SEASON, week=WEEK,
            adapter=NflverseInjuriesAdapter(transport=transport(csv2)), now=day2,
        )
    )

    (designation,) = run(_designations(engine))
    assert designation.designation == "doubtful"


def test_ingest_name_collision_accepted_not_id_matched():
    """Two real players sharing a name collapse to one row keyed by
    (season, week, player_name, report_date) — the accepted limitation
    from PlayerWeekUsage, no id-matching layer invented here"""
    engine = make_engine()
    now = datetime.datetime(2026, 10, 7)
    csv = csv_text(
        HEADER,
        row(
            full_name="Josh Allen", team="BUF", position="QB",
            practice_status="Full Participation in Practice",
        ),
        row(
            full_name="Josh Allen", team="JAX", position="DE",
            practice_status="Did Not Participate in Practice",
        ),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))
    run(ingest_practice_reports(engine, SEASON, week=WEEK, adapter=adapter, now=now))

    reports = run(_reports(engine))
    assert len(reports) == 1  # collision accepted, not disambiguated


# --- ingest_practice_reports: unmapped tripwire -----------------------------------


def test_ingest_tripwire_below_threshold_skips_unmapped_row_and_succeeds():
    rows = [
        row(gsis_id=f"00-{i}", full_name=f"Player {i}", practice_status=status)
        for i, status in enumerate(
            [
                "Full Participation in Practice",
                "Limited Participation in Practice",
                "Did Not Participate in Practice",
                "Full Participation in Practice",
                "Full Participation in Practice",
            ],
            start=1,
        )
    ]
    rows.append(row(gsis_id="00-99", full_name="Odd Wording", practice_status="Jogged Around"))
    engine = make_engine()
    adapter = NflverseInjuriesAdapter(transport=transport(csv_text(HEADER, *rows)))

    summary = run(ingest_practice_reports(engine, SEASON, week=WEEK, adapter=adapter))

    assert summary["success"] is True
    assert summary["reports_written"] == 5  # the unmapped row is skipped, not written
    (log,) = run(_logs(engine))
    assert log.success is True
    assert run(_notifications(engine)) == []


def test_ingest_tripwire_above_threshold_fails_writes_nothing_and_notifies():
    rows = [
        row(gsis_id="00-1", full_name="Mapped One", practice_status="Full Participation in Practice"),
        row(gsis_id="00-2", full_name="Mapped Two", practice_status="Limited Participation in Practice"),
        row(gsis_id="00-3", full_name="Unmapped One", practice_status="Jogged Around"),
        row(gsis_id="00-4", full_name="Unmapped Two", practice_status="Warming Up"),
        row(gsis_id="00-5", full_name="Unmapped Three", practice_status="Rehab Circuit"),
    ]
    engine = make_engine()
    adapter = NflverseInjuriesAdapter(transport=transport(csv_text(HEADER, *rows)))

    summary = run(ingest_practice_reports(engine, SEASON, week=WEEK, adapter=adapter))

    assert summary["success"] is False
    assert run(_reports(engine)) == []  # nothing written on a tripped ingest
    (log,) = run(_logs(engine))
    assert log.success is False
    assert log.error_kind == "parse"

    (notification,) = run(_notifications(engine))
    assert notification.kind == "ingest_format_change"
    assert notification.dedupe_key == f"nflverse_injuries:format:{SEASON}:w{WEEK}"


# --- ingest_practice_reports: failure modes never raise ---------------------------


def test_ingest_missing_required_column_logs_parse_and_keeps_last_good_data():
    engine = make_engine()
    good_csv = csv_text(HEADER, row(practice_status="Full Participation in Practice"))
    run(
        ingest_practice_reports(
            engine, SEASON, week=WEEK,
            adapter=NflverseInjuriesAdapter(transport=transport(good_csv)),
            now=datetime.datetime(2026, 10, 7),
        )
    )
    assert len(run(_reports(engine))) == 1

    broken_csv = csv_text(NO_TEAM_HEADER, no_team_row(practice_status="Full Participation in Practice"))
    summary = run(
        ingest_practice_reports(
            engine, SEASON, week=WEEK,
            adapter=NflverseInjuriesAdapter(transport=transport(broken_csv)),
            now=datetime.datetime(2026, 10, 8),
        )
    )

    assert summary["success"] is False
    logs = run(_logs(engine))
    failed_logs = [log for log in logs if not log.success]
    assert len(failed_logs) == 1
    assert failed_logs[0].error_kind == "parse"
    assert len(run(_reports(engine))) == 1  # last good data untouched


def test_ingest_season_file_404_logs_http_and_keeps_going():
    engine = make_engine()
    adapter = NflverseInjuriesAdapter(transport=transport("not found", status=404))

    summary = run(ingest_practice_reports(engine, SEASON, week=1, adapter=adapter))

    assert summary["success"] is False
    (log,) = run(_logs(engine))
    assert log.error_kind == "http"
    assert log.espn_league_id is None
    assert run(_reports(engine)) == []


def test_ingest_week_none_reingest_is_idempotent_across_weeks():
    engine = make_engine()

    def build_csv():
        return csv_text(
            HEADER,
            row(week=5, full_name="Week Five Guy", practice_status="Full Participation in Practice"),
            row(week=6, full_name="Week Six Guy", practice_status="Did Not Participate in Practice"),
        )

    now = datetime.datetime(2026, 10, 8)
    run(
        ingest_practice_reports(
            engine, SEASON, week=None,
            adapter=NflverseInjuriesAdapter(transport=transport(build_csv())), now=now,
        )
    )
    first_count = len(run(_reports(engine)))
    run(
        ingest_practice_reports(
            engine, SEASON, week=None,
            adapter=NflverseInjuriesAdapter(transport=transport(build_csv())), now=now,
        )
    )
    second_count = len(run(_reports(engine)))

    assert first_count == 2
    assert second_count == 2  # re-ingest is idempotent, not duplicating


# --- downgrade-alert pass: all four downgrade shapes + both non-alerts ------------


def _seed_trail(engine, player_reports, roster_players, season=SEASON, week=WEEK):
    async def go():
        for report in player_reports:
            await engine.save(report)
        await seed_roster(engine, roster_players, season=season, week=week)

    run(go())


def _report(player_name, day, participation, note=None, team="SEA", position="RB", week=WEEK):
    return PracticeReport(
        season=SEASON,
        week=week,
        player_name=player_name,
        nfl_team=team,
        position=position,
        report_date=day,
        participation=participation,
        note=note,
    )


WED, THU, FRI = [datetime.datetime(2026, 10, d) for d in (7, 8, 9)]


def test_downgrade_full_to_limited_alerts():
    engine = make_engine()
    _seed_trail(
        engine,
        [_report("Player A", WED, "full"), _report("Player A", THU, "limited")],
        ["Player A"],
    )
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    (notification,) = created
    assert notification.kind == "practice_downgrade"
    assert notification.dedupe_key == f"practice:{SEASON}:w{WEEK}:Player A:limited"


def test_downgrade_full_to_dnp_alerts():
    engine = make_engine()
    _seed_trail(
        engine,
        [_report("Player B", WED, "full"), _report("Player B", THU, "dnp")],
        ["Player B"],
    )
    (notification,) = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    assert notification.dedupe_key == f"practice:{SEASON}:w{WEEK}:Player B:dnp"


def test_downgrade_limited_to_dnp_alerts():
    engine = make_engine()
    _seed_trail(
        engine,
        [_report("Player C", WED, "limited"), _report("Player C", THU, "dnp")],
        ["Player C"],
    )
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    # limited is a first report (not itself alertable) -> only the dnp fires
    assert len(created) == 1
    assert created[0].dedupe_key == f"practice:{SEASON}:w{WEEK}:Player C:dnp"


def test_downgrade_first_report_dnp_alerts():
    engine = make_engine()
    _seed_trail(engine, [_report("Player D", WED, "dnp")], ["Player D"])
    (notification,) = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    assert notification.dedupe_key == f"practice:{SEASON}:w{WEEK}:Player D:dnp"


def test_upgrade_dnp_to_limited_never_alerts():
    engine = make_engine()
    _seed_trail(
        engine,
        [_report("Player E", WED, "dnp"), _report("Player E", THU, "limited")],
        ["Player E"],
    )
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    # Wed's dnp is a first-report alert; Thu's upgrade to limited fires nothing
    assert [c.dedupe_key for c in created] == [f"practice:{SEASON}:w{WEEK}:Player E:dnp"]


def test_first_report_limited_never_alerts():
    """A first report that's merely limited (not dnp) isn't news on its own"""
    engine = make_engine()
    _seed_trail(engine, [_report("Player F", WED, "limited")], ["Player F"])
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    assert created == []


def test_downgrade_dedupes_same_level_but_new_level_fires():
    """Wed dnp and Thu dnp don't double-page; a later dnp after an
    earlier limited alert does, being a new level"""
    engine = make_engine()
    _seed_trail(
        engine,
        [
            _report("Player G", WED, "dnp"),
            _report("Player G", THU, "dnp"),
        ],
        ["Player G"],
    )
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    assert len(created) == 1  # same level twice -> one alert


def test_downgrade_alert_only_for_rostered_players():
    engine = make_engine()
    _seed_trail(
        engine,
        [
            _report("Rostered Guy", WED, "full"),
            _report("Rostered Guy", THU, "dnp"),
            _report("Free Agent Guy", WED, "full"),
            _report("Free Agent Guy", THU, "dnp"),
        ],
        ["Rostered Guy"],  # Free Agent Guy is not rostered
    )
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    assert [c.dedupe_key for c in created] == [
        f"practice:{SEASON}:w{WEEK}:Rostered Guy:dnp"
    ]


def test_downgrade_comparison_resets_across_weeks():
    """A prior week ending in dnp doesn't suppress the new week's
    first-report dnp rule — weeks reset (spec §6)"""
    engine = make_engine()

    async def go():
        await engine.save(_report("Player H", FRI, "dnp", week=5))
        await engine.save(_report("Player H", WED, "dnp", week=6))
        await seed_roster(engine, ["Player H"], week=5)
        await seed_roster(engine, ["Player H"], week=6)

    run(go())
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [5, 6]))
    # each week's first dnp report fires its own first-report alert
    assert sorted(c.dedupe_key for c in created) == [
        f"practice:{SEASON}:w5:Player H:dnp",
        f"practice:{SEASON}:w6:Player H:dnp",
    ]


def test_downgrade_copy_matches_worked_example():
    """Spec §7's worked example, verbatim"""
    engine = make_engine()
    _seed_trail(
        engine,
        [
            _report("Kenneth Walker III", WED, "limited", note="Calf", team="SEA", position="RB"),
            _report("Kenneth Walker III", THU, "dnp", note="Calf", team="SEA", position="RB"),
        ],
        ["Kenneth Walker III"],
    )
    created = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    (notification,) = created
    assert notification.body == (
        "Kenneth Walker III (SEA RB) did not practice Thursday (calf) after "
        "a limited Wednesday — official report, ahead of any ESPN status change."
    )


def test_downgrade_notifications_idempotent_on_rerun():
    engine = make_engine()
    _seed_trail(
        engine,
        [_report("Player I", WED, "full"), _report("Player I", THU, "dnp")],
        ["Player I"],
    )
    first = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    second = run(ensure_practice_downgrade_notifications(engine, SEASON, [WEEK]))
    assert len(first) == 1
    assert second == []


# --- end-to-end: ingest triggers the downgrade pass -------------------------------


def test_ingest_triggers_downgrade_alert_for_rostered_player():
    engine = make_engine()

    async def seed():
        await engine.save(_report("Kenneth Walker III", WED, "limited", note="Calf"))
        await seed_roster(engine, ["Kenneth Walker III"])

    run(seed())

    csv = csv_text(
        HEADER,
        row(
            full_name="Kenneth Walker III",
            practice_status="Did Not Participate in Practice",
            practice_primary_injury="Calf",
        ),
    )
    adapter = NflverseInjuriesAdapter(transport=transport(csv))
    summary = run(
        ingest_practice_reports(
            engine, SEASON, week=WEEK, adapter=adapter, now=THU
        )
    )

    assert summary["downgrade_alerts"] == 1
    (notification,) = run(_notifications(engine))
    assert "did not practice Thursday" in notification.body
