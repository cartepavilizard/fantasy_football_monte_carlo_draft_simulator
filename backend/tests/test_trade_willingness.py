# -*- coding: utf-8 -*-
"""
E3: trade-willingness owner profiles. Pure-core tests build transaction
lists by hand (test_espn_league_adapter.py's style) and cover every §6
edge case from docs/specs/E3-trade-willingness-features.md; one
endpoint test covers the envelope + sort order through the HTTP client.
"""
import asyncio
import datetime

from models.inseason import (
    InSeasonLeague,
    LeagueTeamInfo,
    LeagueTransaction,
    TransactionItem,
)
from models.trade_willingness import (
    ACTIVE_TRADES_PER_SEASON,
    DEADLINE_FALLBACK_WEEK,
    league_trade_willingness,
    willingness_features,
)

SEASON = 2026
LEAGUE_ID = 111
NOW = datetime.datetime(2026, 10, 15)


def team(team_id, name=None, owner_guid=None, owner_name=None):
    return LeagueTeamInfo(
        espn_team_id=team_id,
        name=name or f"Team {team_id}",
        owner_guid=owner_guid,
        owner_name=owner_name,
    )


def league(teams, trade_deadline=None, latest_scoring_period=9, season=SEASON):
    return InSeasonLeague(
        espn_league_id=LEAGUE_ID,
        season=season,
        name="Test League",
        team_count=len(teams),
        current_matchup_period=latest_scoring_period,
        latest_scoring_period=latest_scoring_period,
        trade_deadline=trade_deadline,
        teams=teams,
    )


def item(player_id, from_team=None, to_team=None, item_type="TRADE"):
    return TransactionItem(
        player_id=player_id,
        player_name=f"Player {player_id}",
        item_type=item_type,
        from_team_id=from_team,
        to_team_id=to_team,
    )


def txn(txn_id, items, kind="TRADE_ACCEPT", status="EXECUTED", team_id=None, week=None, season=SEASON):
    return LeagueTransaction(
        espn_league_id=LEAGUE_ID,
        season=season,
        espn_transaction_id=txn_id,
        type=kind,
        status=status,
        week=week,
        team_id=team_id,
        items=items,
    )


def move(txn_id, team_id, kind="WAIVER", status="EXECUTED", week=5):
    return txn(
        txn_id,
        items=[item(9000 + hash(txn_id) % 1000, to_team=team_id, item_type="ADD")],
        kind=kind,
        status=status,
        team_id=team_id,
        week=week,
    )


def owner(features, team_id):
    return features[str(team_id)]["trade_willingness"]


# --- §6 edge cases -----------------------------------------------------------


def test_zero_transactions_every_owner_unknown_no_division_by_zero():
    lg = league([team(1), team(2)], trade_deadline=datetime.datetime(2026, 11, 18))
    features = willingness_features([], lg, {}, NOW)

    for team_id in (1, 2):
        tw = owner(features, team_id)
        assert tw["willingness"] == "unknown"
        assert tw["n_trades"] == 0
        assert tw["league_mean_trades_per_season"] == 0.0
        assert tw["relative_trade_rate"] is None
        assert tw["activity"]["league_mean_moves_per_season"] == 0.0
        assert tw["veto_context"]["n_vetoed_league"] == 0


def test_owner_without_guid_gets_team_keyed_profile():
    lg = league([team(1, owner_guid=None), team(2, owner_guid="G2")])
    transactions = [
        txn("t1", items=[item(101, from_team=1, to_team=2), item(102, from_team=2, to_team=1)], team_id=1, week=5)
    ]
    features = willingness_features(transactions, lg, {}, NOW)

    assert features["1"]["profile_key"] == f"team:{LEAGUE_ID}:1"
    assert features["2"]["profile_key"] == "G2"
    assert owner(features, 1)["n_trades"] == 1


def test_three_team_trade_counts_once_per_owner_with_every_other_partner():
    lg = league([team(1), team(2), team(3)])
    # a three-way swap: 1->2, 2->3, 3->1
    transactions = [
        txn(
            "t1",
            items=[
                item(101, from_team=1, to_team=2),
                item(102, from_team=2, to_team=3),
                item(103, from_team=3, to_team=1),
            ],
            team_id=1,
            week=5,
        )
    ]
    features = willingness_features(transactions, lg, {}, NOW)

    for team_id in (1, 2, 3):
        assert owner(features, team_id)["n_trades"] == 1
    assert owner(features, 1)["partners"]["n_distinct"] == 2
    assert owner(features, 2)["partners"]["n_distinct"] == 2
    assert owner(features, 3)["partners"]["n_distinct"] == 2


def test_vetoed_and_pending_trades_excluded_but_veto_counted():
    lg = league([team(1), team(2)])
    transactions = [
        txn("vetoed", items=[item(1, from_team=1, to_team=2)], status="VETOED", team_id=1, week=5),
        txn("pending", items=[item(2, from_team=1, to_team=2)], status="PENDING", team_id=1, week=6),
    ]
    features = willingness_features(transactions, lg, {}, NOW)

    assert owner(features, 1)["n_trades"] == 0
    assert owner(features, 2)["n_trades"] == 0
    assert owner(features, 1)["veto_context"]["n_vetoed_league"] == 1
    assert owner(features, 2)["veto_context"]["n_vetoed_league"] == 1


def test_no_deadline_falls_back_to_week_11():
    early = league([team(1)], trade_deadline=None, latest_scoring_period=DEADLINE_FALLBACK_WEEK - 1)
    late = league([team(1)], trade_deadline=None, latest_scoring_period=DEADLINE_FALLBACK_WEEK)

    assert owner(willingness_features([], early, {}, NOW), 1)["willingness"] == "unknown"
    assert owner(willingness_features([], late, {}, NOW), 1)["willingness"] == "reluctant"


def test_zero_trades_before_deadline_is_unknown_not_reluctant():
    lg = league([team(1)], trade_deadline=datetime.datetime(2026, 11, 18), latest_scoring_period=9)
    features = willingness_features([], lg, {}, NOW)  # NOW is before the deadline

    assert owner(features, 1)["willingness"] == "unknown"


# --- §7 worked example ---------------------------------------------------------


def test_worked_example_matches_spec_section_7():
    teams = [
        team(7, owner_guid="G"),
        team(4, owner_guid="H"),
        team(2, owner_guid="P2"),
        team(10, owner_guid="F1"),
        team(11, owner_guid="F2"),
        team(12, owner_guid="F3"),
        team(13, owner_guid="F4"),
        team(14, owner_guid="F5"),
        team(15, owner_guid="F6"),
        team(16, owner_guid="F7"),
    ]
    lg = league(teams, trade_deadline=datetime.datetime(2026, 11, 18), latest_scoring_period=9)

    transactions = [
        # owner G (team 7): week 3, sent 2 RBs for 1 WR to team 2
        txn(
            "g1",
            items=[
                item(701, from_team=7, to_team=2),
                item(702, from_team=7, to_team=2),
                item(703, from_team=2, to_team=7),
            ],
            team_id=7,
            week=3,
        ),
        # owner G: week 8, 1-for-1 with team 2
        txn(
            "g2",
            items=[item(704, from_team=7, to_team=2), item(705, from_team=2, to_team=7)],
            team_id=7,
            week=8,
        ),
        # three more trades elsewhere in the league (5 executed trades total)
        txn("x1", items=[item(801, from_team=10, to_team=11), item(802, from_team=11, to_team=10)], team_id=10, week=4),
        txn("x2", items=[item(803, from_team=12, to_team=13), item(804, from_team=13, to_team=12)], team_id=12, week=6),
        txn("x3", items=[item(805, from_team=14, to_team=15), item(806, from_team=15, to_team=14)], team_id=14, week=7),
    ]
    # owner G: 17 waiver adds; owner H: 2 adds; team 2: 22 (41 moves total)
    transactions += [move(f"g-add-{i}", 7) for i in range(17)]
    transactions += [move(f"h-add-{i}", 4) for i in range(2)]
    transactions += [move(f"p2-add-{i}", 2) for i in range(22)]

    player_positions = {701: "RB", 702: "RB", 703: "WR", 704: "RB", 705: "WR"}
    features = willingness_features(
        transactions, lg, {}, datetime.datetime(2026, 10, 20), player_positions=player_positions
    )

    g = owner(features, 7)
    assert g["n_trades"] == 2
    assert g["trades_per_season"] == 2.0
    assert g["league_mean_trades_per_season"] == 0.5  # 5 trades / 10 teams
    assert g["relative_trade_rate"] == 4.0
    assert g["willingness"] == "active"
    assert g["activity"]["n_moves"] == 17
    assert g["activity"]["league_mean_moves_per_season"] == 4.1  # 41 moves / 10 teams

    assert g["deal_shapes"]["n"] == 2
    assert g["deal_shapes"]["one_for_one"] == 0.5
    assert g["deal_shapes"]["two_for_one"] == 0.5
    assert g["deal_shapes"]["avg_players_sent"] == 1.5
    assert g["deal_shapes"]["avg_players_received"] == 1.0

    assert g["position_mix"]["n_players_sent"] == 3
    assert g["position_mix"]["shares"] == {"RB": 1.0}

    assert g["timing"]["n"] == 2
    assert g["timing"]["buckets"] == {"early(1-5)": 0.5, "mid(6-9)": 0.5}

    assert g["partners"]["n_distinct"] == 1
    assert g["partners"]["concentration"] == 1.0

    h = owner(features, 4)
    assert h["n_trades"] == 0
    assert h["activity"]["n_moves"] == 2
    assert h["willingness"] == "unknown"  # deadline not passed yet


def test_active_threshold_on_trades_per_season():
    lg = league([team(1), team(2)])
    transactions = [
        txn(f"t{i}", items=[item(i, from_team=1, to_team=2), item(1000 + i, from_team=2, to_team=1)], team_id=1, week=i)
        for i in range(1, int(ACTIVE_TRADES_PER_SEASON) + 1)
    ]
    features = willingness_features(transactions, lg, {}, NOW)
    assert owner(features, 1)["willingness"] == "active"


def test_open_when_below_active_thresholds():
    # team 1 makes exactly one trade (trades_per_season 1.0, below the 2.0
    # absolute threshold); the rest of the league trades enough that the
    # league mean (0.8) keeps team 1's relative rate (1.25) under 1.5 too
    # — one executed trade with neither threshold cleared lands "open".
    lg = league([team(1), team(2), team(3), team(4), team(5)])
    transactions = [
        txn("t1", items=[item(1, from_team=1, to_team=2), item(2, from_team=2, to_team=1)], team_id=1, week=5),
        txn("t2", items=[item(3, from_team=3, to_team=4), item(4, from_team=4, to_team=3)], team_id=3, week=5),
        txn("t3", items=[item(5, from_team=4, to_team=5), item(6, from_team=5, to_team=4)], team_id=4, week=6),
        txn("t4", items=[item(7, from_team=5, to_team=3), item(8, from_team=3, to_team=5)], team_id=5, week=6),
    ]
    features = willingness_features(transactions, lg, {}, NOW)
    tw = owner(features, 1)
    assert tw["n_trades"] == 1
    assert tw["trades_per_season"] == 1.0
    assert tw["league_mean_trades_per_season"] == 0.8  # 4 trades / 5 teams
    assert tw["relative_trade_rate"] == 1.25
    assert tw["willingness"] == "open"


# --- endpoint -------------------------------------------------------------------


def test_endpoint_envelope_and_sort_order(client, app_module):
    engine = app_module.engine

    async def seed():
        await engine.save(
            InSeasonLeague(
                espn_league_id=LEAGUE_ID,
                season=SEASON,
                name="Trade League",
                team_count=2,
                current_matchup_period=9,
                latest_scoring_period=9,
                trade_deadline=datetime.datetime(2026, 11, 18),
                teams=[
                    LeagueTeamInfo(espn_team_id=1, name="Active Traders", owner_guid="A"),
                    LeagueTeamInfo(espn_team_id=2, name="Quiet Owner", owner_guid="B"),
                    LeagueTeamInfo(espn_team_id=3, name="Trade Partner", owner_guid="C"),
                ],
            )
        )
        # team 1 trades three times with team 3; team 2 never trades
        for i in range(3):
            await engine.save(
                LeagueTransaction(
                    espn_league_id=LEAGUE_ID,
                    season=SEASON,
                    espn_transaction_id=f"trade-{i}",
                    type="TRADE_ACCEPT",
                    status="EXECUTED",
                    week=3 + i,
                    team_id=1,
                    items=[
                        TransactionItem(player_id=i, item_type="TRADE", from_team_id=1, to_team_id=3),
                        TransactionItem(player_id=100 + i, item_type="TRADE", from_team_id=3, to_team_id=1),
                    ],
                )
            )

    asyncio.run(seed())

    payload = client.get(f"/inseason/league/{LEAGUE_ID}/trade_willingness?season={SEASON}").json()
    assert payload["data"]["week"] == 9
    owners = payload["data"]["owners"]
    # both traders rank "active" ahead of the untraded team; "unknown"
    # (team 2, never traded, deadline not yet passed) sorts last
    assert [o["team_id"] for o in owners] == [1, 3, 2]
    assert owners[0]["trade_willingness"]["willingness"] == "active"
    assert owners[1]["trade_willingness"]["willingness"] == "active"
    assert owners[2]["trade_willingness"]["willingness"] == "unknown"
    assert owners[0]["profile_key"] == "A"
