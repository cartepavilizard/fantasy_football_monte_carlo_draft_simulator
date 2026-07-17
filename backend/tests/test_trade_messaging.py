# -*- coding: utf-8 -*-
"""
E7: trade messaging generator. Pure-function tests over hand-built
evaluate_trade dicts (the A.6 fixture shape from docs/specs/E1-trade-valuation.md
§7/Appendix A — real numbers, not placeholders). The willingness-label
non-leak rule is asserted for every label.
"""
from models.trade_messaging import render_trade_message

# The §7 / A.6 evaluation: A sends X (29.9 market, WR) for B's Y
# (0.0 market, RB on IR with a stash_note). Verdict favors_b; fit +0.5/+2.1
# per week. These are the actual numbers E1 produces; E7 must quote them.
EVALUATION = {
    "week": 8,
    "weeks_remaining": 9,
    "teams": {
        "a": {"espn_team_id": 3, "name": "My Team"},
        "b": {"espn_team_id": 7, "name": "Big Truss"},
    },
    "sends_a": [
        {
            "player_id": 101,
            "name": "X",
            "position": "WR",
            "nfl_team": "DET",
            "injury_status": None,
            "rate": 12.4,
            "gross": 100.1,
            "value": 29.9,
            "playoff_value": 14.2,
            "per_week": 3.3,
            "stash_note": None,
            "warnings": [],
        }
    ],
    "sends_b": [
        {
            "player_id": 202,
            "name": "Y",
            "position": "RB",
            "nfl_team": "SEA",
            "injury_status": "injury_reserve",
            "rate": 14.0,
            "gross": 56.4,
            "value": 0.0,
            "playoff_value": 13.1,
            "per_week": 0.0,
            "stash_note": (
                "on IR, projected back ~week 11; 56.4 raw pts incl. 33.8 in "
                "the playoff window — stash value only if you can afford the spot"
            ),
            "warnings": [],
        }
    ],
    "value_sent_a": 29.9,
    "value_sent_b": 0.0,
    "market_gap": 29.9,
    "fair_bound": 10.0,
    "verdict": "favors_b",
    "fit_delta_a": 4.1,
    "fit_delta_b": 18.7,
    "fit_per_week_a": 0.5,
    "fit_per_week_b": 2.1,
    "summary": "irrelevant to E7 — E7 templates its own message",
    "warnings": [],
}

EVALUATION_WITH_WARNINGS = {
    **EVALUATION,
    "warnings": ["no free agents at RB — values are raw points, inflated"],
}


def test_message_quotes_real_ros_and_per_week_numbers_from_evaluation():
    msg = render_trade_message(EVALUATION)
    # ROS points: both sides quoted (E1 §4.3)
    assert "29.9" in msg  # value_sent_a
    assert "0.0" in msg  # value_sent_b
    # per-week points quoted (weeks_remaining=9 -> 29.9/9 = 3.3)
    assert "3.3" in msg  # per_week_a
    # roster-fit per-week lens quoted, signed
    assert "+0.5" in msg  # fit_per_week_a
    assert "+2.1" in msg  # fit_per_week_b
    # the players are named
    assert "X" in msg and "Y" in msg
    # the recipient team is named in the opener
    assert "Big Truss" in msg


def test_message_names_the_position_need_it_fills_or_creates():
    msg = render_trade_message(EVALUATION)
    # A sends WR, receives RB -> fills RB need, costs WR depth. E1 §4.3
    # requires naming the position need.
    assert "RB" in msg
    assert "WR" in msg


def test_message_quotes_stash_note_when_an_ir_player_is_involved():
    msg = render_trade_message(EVALUATION)
    # the stash_note's playoff-window raw points must surface verbatim
    assert "56.4" in msg
    assert "33.8" in msg
    assert "playoff window" in msg


def test_message_is_deterministic_same_inputs_same_string():
    a = render_trade_message(EVALUATION, willingness_label="open")
    b = render_trade_message(EVALUATION, willingness_label="open")
    assert a == b


def test_willingness_label_never_leaks_into_message_text():
    """E3's labels inform TONE ONLY. The label string and any synonym of
    its level/rank must never appear in the generated message — tested
    for every valid label, including the unknown-default case."""
    forbidden = [
        "active", "open", "unknown", "reluctant",
        "willingness", "label",
    ]
    for label in ("active", "open", "unknown", "reluctant", None, "garbage"):
        msg = render_trade_message(EVALUATION, willingness_label=label).lower()
        for word in forbidden:
            assert word not in msg, (
                f"label={label!r}: message contains forbidden word {word!r}: {msg}"
            )


def test_willingness_label_changes_tone_but_not_the_numbers():
    """Tone variants shift the opener/closer phrasing only; the body
    numbers stay identical across labels (the copy rules are inviolable)."""
    active = render_trade_message(EVALUATION, willingness_label="active")
    reluctant = render_trade_message(EVALUATION, willingness_label="reluctant")
    open_ = render_trade_message(EVALUATION, willingness_label="open")
    # tone differs (opener/closer)
    assert active != reluctant
    assert open_ != reluctant
    # numbers are quoted identically in every tone
    for needle in ("29.9", "3.3", "+0.5", "+2.1", "56.4", "33.8"):
        assert needle in active
        assert needle in reluctant
        assert needle in open_


def test_message_surfaces_warnings_when_inputs_carried_one():
    """E1 §4.3: when any input carried a warning, the message says so."""
    msg = render_trade_message(EVALUATION_WITH_WARNINGS)
    assert "heads up" in msg.lower()
    assert "no free agents at RB" in msg


def test_message_handles_fair_verdict_without_overselling():
    fair = {
        **EVALUATION,
        "value_sent_a": 29.9,
        "value_sent_b": 25.0,
        "market_gap": 4.9,
        "verdict": "fair",
    }
    msg = render_trade_message(fair)
    assert "fair range" in msg.lower()


def test_message_handles_empty_sends_on_one_side_gift():
    gift = {
        **EVALUATION,
        "sends_b": [],
        "value_sent_b": 0.0,
        "verdict": "favors_b",
    }
    msg = render_trade_message(gift)
    # still well-formed, names the one player sent
    assert "X" in msg
    assert "no one" in msg  # the empty side


def test_message_never_quotes_last_weeks_score_c8_framing():
    """C8: projection/volume language, never results. The evaluation
    carries no actual-points field; assert the message doesn't invent one
    or reference last week's score."""
    msg = render_trade_message(EVALUATION).lower()
    assert "last week" not in msg
    assert "scored" not in msg
    assert "actual" not in msg
