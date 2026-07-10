# -*- coding: utf-8 -*-
"""
Player tag CRUD (A3): set/clear a player's tag, filter the player list by tag
"""
from urllib.parse import quote

from conftest import sample, upload


def _with_players(client, league_id):
    """league_id, with players.csv loaded so there's something to tag"""
    response = upload(client, f"/league/{league_id}/player", sample("players.csv"))
    assert response.status_code == 200, response.text
    return league_id


def test_set_and_get_player_tag(client, league_id):
    _with_players(client, league_id)
    name = quote("Josh Allen")

    response = client.post(f"/league/{league_id}/player/{name}/tag?tag=sleeper")
    assert response.status_code == 200, response.text
    assert response.json()["tag"] == "sleeper"

    response = client.get(f"/league/{league_id}/player/{name}")
    assert response.status_code == 200
    assert response.json()["tag"] == "sleeper"


def test_set_tag_replaces_existing_tag(client, league_id):
    _with_players(client, league_id)
    name = quote("Josh Allen")

    client.post(f"/league/{league_id}/player/{name}/tag?tag=sleeper")
    response = client.post(f"/league/{league_id}/player/{name}/tag?tag=avoid")
    assert response.status_code == 200
    assert response.json()["tag"] == "avoid"


def test_clear_player_tag(client, league_id):
    _with_players(client, league_id)
    name = quote("Josh Allen")

    client.post(f"/league/{league_id}/player/{name}/tag?tag=my_guy")
    response = client.delete(f"/league/{league_id}/player/{name}/tag")
    assert response.status_code == 200
    assert response.json()["tag"] is None

    response = client.get(f"/league/{league_id}/player/{name}")
    assert response.json()["tag"] is None


def test_invalid_tag_value_rejected(client, league_id):
    _with_players(client, league_id)
    name = quote("Josh Allen")

    response = client.post(f"/league/{league_id}/player/{name}/tag?tag=bogus")
    assert response.status_code == 422


def test_tag_unknown_player_404(client, league_id):
    _with_players(client, league_id)

    response = client.post(
        f"/league/{league_id}/player/{quote('Nobody Real')}/tag?tag=sleeper"
    )
    assert response.status_code == 404


def test_untag_unknown_player_404(client, league_id):
    _with_players(client, league_id)

    response = client.delete(f"/league/{league_id}/player/{quote('Nobody Real')}/tag")
    assert response.status_code == 404


def test_list_players_filtered_by_tag(client, league_id):
    _with_players(client, league_id)

    client.post(f"/league/{league_id}/player/{quote('Josh Allen')}/tag?tag=sleeper")
    client.post(f"/league/{league_id}/player/{quote('Jalen Hurts')}/tag?tag=avoid")

    response = client.get(
        f"/league/{league_id}/player?tag=sleeper&draftable_only=false"
    )
    assert response.status_code == 200
    names = {p["name"] for p in response.json()["players"]}
    assert names == {"Josh Allen"}


def test_tag_stays_in_sync_with_position_list(client, league_id):
    """The flat players list and the per-position list must both update"""
    _with_players(client, league_id)
    client.post(f"/league/{league_id}/player/{quote('Josh Allen')}/tag?tag=sleeper")

    response = client.get(f"/league/{league_id}/player?draftable_only=false")
    qb_list = response.json()["qb"]
    josh = next(p for p in qb_list if p["name"] == "Josh Allen")
    assert josh["tag"] == "sleeper"
