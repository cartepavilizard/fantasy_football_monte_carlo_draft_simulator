# -*- coding: utf-8 -*-
"""
CSV upload endpoints: happy path and validation (guards D2-D5, L6)
"""
from conftest import sample, upload


def test_setup_happy_path(client, ready_league_id):
    """Uploading all four sample files makes the league ready for a draft"""
    league = client.get(f"/league/{ready_league_id}").json()
    assert league["ready_for_draft"] is True
    assert len(league["teams"]) == 14
    assert len(league["players"]["players"]) > 0


def test_simulator_flag_parsed_case_insensitively(client):
    csv_text = b"Name,Owner,Simulator,Order\nT1,Bob,true,1\nT2,Ann,0,2"
    response = upload(client, "/league", csv_text)
    assert response.status_code == 200
    teams = {t["name"]: t["simulator"] for t in response.json()["teams"]}
    assert teams == {"T1": True, "T2": False}


def test_snake_draft_parameter_is_honored(client):
    csv_text = b"Name,Owner,Simulator,Order\nT1,Bob,1,1\nT2,Ann,0,2\nT3,Cy,0,3"
    response = client.post(
        "/league?snake_draft=false",
        files={"file": ("teams.csv", csv_text, "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["draft_order"][:6] == [0, 1, 2, 0, 1, 2]


def test_missing_columns_rejected(client):
    response = upload(client, "/league", b"Name,Owner\nT1,Bob")
    assert response.status_code == 422
    assert "Order" in response.json()["detail"]
    assert "Simulator" in response.json()["detail"]


def test_empty_csv_rejected(client):
    response = upload(client, "/league", b"Name,Owner,Simulator,Order\n")
    assert response.status_code == 422
    assert "empty" in response.json()["detail"]


def test_wrong_season_players_rejected(client, league_id):
    wrong = sample("players.csv").replace(b"2024,", b"2023,")
    response = upload(client, f"/league/{league_id}/player", wrong)
    assert response.status_code == 422
    assert "2023" in response.json()["detail"]
    assert "2024" in response.json()["detail"]


def test_duplicate_player_names_rejected(client, league_id):
    lines = sample("players.csv").splitlines()
    duplicated = b"\n".join(lines + [lines[1]])
    response = upload(client, f"/league/{league_id}/player", duplicated)
    assert response.status_code == 422
    assert "Duplicate player names" in response.json()["detail"]


def test_multi_season_historical_upload_succeeds(client, league_id):
    """Multi-season historical files must not crash tier assignment (L5)"""
    lines = sample("historical_players.csv").splitlines()
    second_season = [
        line.replace(b"2023,", b"2022,", 1) for line in lines[1:]
    ]
    content = b"\n".join(lines + second_season)
    response = upload(
        client, f"/league/{league_id}/historical_player", content
    )
    assert response.status_code == 200, response.text
    distributions = response.json()["position_tier_distributions"]
    assert len(distributions["qb1"]) > 0
