# -*- coding: utf-8 -*-
"""
F1 (stacking) + F3 (anti-correlation) strategy flags — pure-function
tests. Pins down the spec's fixed rho table verbatim, the extra-weekly-
swing math on the spec's worked examples (±0.05), the F3 handcuff-pair
exclusion, every §5 edge, and the no-mutation invariant.
"""
import copy

import pytest

from models.correlation_flags import (
    FLAG_POSITIONS,
    SIGMA_CV,
    STACK_CORRELATION,
    STACK_GRADE_STRONG_THRESHOLD,
    anticorrelation_flags,
    extra_weekly_swing,
    roster_stack_flags,
    stack_flag,
    stacks_for_roster,
)


# --- the rho table, verbatim from the spec (§2) ------------------------------


def test_rho_table_matches_spec_verbatim():
    assert STACK_CORRELATION == {
        ("QB", "WR"): 0.40,
        ("QB", "TE"): 0.35,
        ("QB", "RB"): 0.10,
        ("WR", "WR"): 0.10,
        ("WR", "TE"): 0.05,
    }


def test_sigma_cv_is_the_spec_midpoint():
    assert SIGMA_CV == 0.45


def test_strong_grade_threshold_is_030():
    assert STACK_GRADE_STRONG_THRESHOLD == 0.30


def test_flag_positions_exclude_dst_and_k():
    assert FLAG_POSITIONS == {"QB", "RB", "WR", "TE"}


# --- the swing math (spec §6 worked examples, ±0.05) -------------------------


def test_extra_swing_qb_wr_worked_example():
    # spec §6: sigma_qb 8.1, sigma_wr 6.3, rho 0.40
    # sigma_pair = sqrt(146.124) ~ 12.088; sigma_indep ~ 10.262; swing ~ 1.83
    swing = extra_weekly_swing(18.0, 14.0, 0.40)
    assert swing == pytest.approx(1.83, abs=0.05)


def test_stack_flag_qb_wr_worked_example_carries_the_swing():
    flag = stack_flag("QB", 18.0, "WR", 14.0, "DK Metcalf")
    assert flag is not None
    assert flag["correlation"] == 0.40
    assert flag["grade"] == "strong"
    assert flag["extra_swing"] == pytest.approx(1.83, abs=0.05)
    assert flag["with"] == "DK Metcalf"
    assert flag["positions"] == ["QB", "WR"]
    assert "weekly swing" in flag["note"]
    assert "0.40" in flag["note"] or "0.4" in flag["note"]


def test_stack_flag_qb_te_worked_example():
    # spec §6: QB 18, TE 9, rho 0.35 -> swing ~1.19, strong by rho
    flag = stack_flag("QB", 18.0, "TE", 9.0, "Noah Fant")
    assert flag["correlation"] == 0.35
    assert flag["grade"] == "strong"
    assert flag["extra_swing"] == pytest.approx(1.19, abs=0.05)


def test_stack_flag_wr_wr_is_mild():
    # spec §6: WR+WR same team, rho 0.10, proj 14/12 -> swing ~0.4, mild
    flag = stack_flag("WR", 14.0, "WR", 12.0, "Tyler Lockett")
    assert flag["correlation"] == 0.10
    assert flag["grade"] == "mild"
    assert flag["extra_swing"] == pytest.approx(0.4, abs=0.05)
    assert "mild" in flag["note"]


def test_stack_flag_qb_rb_is_mild():
    flag = stack_flag("QB", 18.0, "RB", 10.0, "Kenneth Walker")
    assert flag["correlation"] == 0.10
    assert flag["grade"] == "mild"


# --- §5 edges ----------------------------------------------------------------


def test_stack_flag_returns_none_for_dst_and_k_even_if_position_uppercased():
    # DST/K never flag (guard), regardless of the table
    assert stack_flag("DST", 10.0, "QB", 18.0, "Seahawks") is None
    assert stack_flag("K", 8.0, "QB", 18.0, "Myers") is None
    assert stack_flag("QB", 18.0, "K", 8.0, "Myers") is None


def test_stack_flag_returns_none_for_unlisted_pair():
    # RB+RB same team is F3's lens, explicitly out of F1's table
    assert stack_flag("RB", 15.0, "RB", 12.0, "Backup") is None
    # cross-team pairs are never stacks even if positions are flaggable
    assert stack_flag("QB", 18.0, "QB", 17.0, "Other QB") is None


def test_stack_flag_returns_none_when_projection_missing_or_zero():
    assert stack_flag("QB", None, "WR", 14.0, "DK") is None
    assert stack_flag("QB", 18.0, "WR", None, "DK") is None
    assert stack_flag("QB", 0, "WR", 14.0, "DK") is None
    assert stack_flag("QB", 18.0, "WR", 0.0, "DK") is None


def test_stack_flag_returns_none_when_name_missing():
    assert stack_flag("QB", 18.0, "WR", 14.0, None) is None
    assert stack_flag("QB", 18.0, "WR", 14.0, "") is None


def test_stack_flag_handles_lowercase_positions():
    # Player.position is stored lowercase ("qb"); the flag must normalize
    flag = stack_flag("qb", 18.0, "wr", 14.0, "DK Metcalf")
    assert flag is not None
    assert flag["positions"] == ["QB", "WR"]


# --- stacks_for_roster (best pair + also_with) -------------------------------


def _roster_entry(name, position, team, proj):
    return {
        "name": name,
        "position": position,
        "nfl_team": team,
        "weekly_projection": proj,
    }


def test_stacks_for_roster_picks_highest_correlation_and_lists_also_with():
    # QB suggested; roster holds his WR (rho 0.40) AND his TE (rho 0.35)
    qb = _roster_entry("Geno Smith", "QB", "SEA", 18.0)
    roster = [
        _roster_entry("DK Metcalf", "WR", "SEA", 14.0),
        _roster_entry("Noah Fant", "TE", "SEA", 9.0),
        _roster_entry("Some RB", "RB", "SF", 12.0),  # different team
    ]
    flag = stacks_for_roster(qb, roster)
    assert flag is not None
    assert flag["with"] == "DK Metcalf"  # higher rho (0.40 > 0.35)
    assert flag["correlation"] == 0.40
    assert flag["also_with"] == ["Noah Fant"]


def test_stacks_for_roster_returns_none_when_no_same_team_teammate():
    qb = _roster_entry("Geno Smith", "QB", "SEA", 18.0)
    roster = [_roster_entry("Some RB", "RB", "SF", 12.0)]
    assert stacks_for_roster(qb, roster) is None


def test_stacks_for_roster_returns_none_when_player_has_no_team():
    qb = _roster_entry("Mystery QB", "QB", None, 18.0)
    roster = [_roster_entry("DK Metcalf", "WR", "SEA", 14.0)]
    assert stacks_for_roster(qb, roster) is None


def test_stacks_for_roster_returns_none_for_dst():
    dst = _roster_entry("Seahawks", "DST", "SEA", 10.0)
    roster = [_roster_entry("DK Metcalf", "WR", "SEA", 14.0)]
    assert stacks_for_roster(dst, roster) is None


def test_stacks_for_roster_returns_none_when_projection_missing():
    qb = _roster_entry("Geno Smith", "QB", "SEA", None)
    roster = [_roster_entry("DK Metcalf", "WR", "SEA", 14.0)]
    assert stacks_for_roster(qb, roster) is None


def test_roster_stack_flags_dedupes_unordered_pairs():
    # Both directions of the same stack appear once in the roster-level view
    qb = _roster_entry("Geno Smith", "QB", "SEA", 18.0)
    wr = _roster_entry("DK Metcalf", "WR", "SEA", 14.0)
    flags = roster_stack_flags([qb, wr])
    assert len(flags) == 1
    assert flags[0]["correlation"] == 0.40


# --- F3: anti-correlation (inverted C7) --------------------------------------


def test_anticorrelation_flags_same_backfield_non_handcuff_pair():
    # Two SEA RBs not in the handcuff table -> competing for touches
    a = _roster_entry("Kenneth Walker III", "RB", "SEA", 15.0)
    a["lineup_slot"] = "RB"
    b = _roster_entry("Zach Charbonnet", "RB", "SEA", 8.0)
    b["lineup_slot"] = "BE"
    # Charbonnet is on the bench here -> not a starter competition
    flags = anticorrelation_flags([a, b])
    assert flags == []

    # Now both are starters -> flagged
    b["lineup_slot"] = "FLEX"
    flags = anticorrelation_flags([a, b])
    assert len(flags) == 1
    flag = flags[0]
    assert set(flag["players"]) == {"Kenneth Walker III", "Zach Charbonnet"}
    assert flag["nfl_team"] == "SEA"
    assert "backfield" in flag["note"]


def test_anticorrelation_excludes_deliberate_handcuff_pair():
    # Walker/Charbonnet IS a curated C7 starter->handcuff pair (see
    # SEED_HANDCUFF_PAIRS). When passed in the exclusion set, the pair
    # is insurance, not competition -> not flagged.
    a = _roster_entry("Kenneth Walker III", "RB", "SEA", 15.0)
    a["lineup_slot"] = "RB"
    b = _roster_entry("Zach Charbonnet", "RB", "SEA", 8.0)
    b["lineup_slot"] = "FLEX"
    exclusion = frozenset({frozenset({"Kenneth Walker III", "Zach Charbonnet"})})
    assert anticorrelation_flags([a, b], exclusion) == []


def test_anticorrelation_does_not_flag_cross_team_or_non_rb():
    rb_sea = _roster_entry("RB A", "RB", "SEA", 12.0)
    rb_sea["lineup_slot"] = "RB"
    rb_sf = _roster_entry("RB B", "RB", "SF", 12.0)
    rb_sf["lineup_slot"] = "RB"
    wr_sea = _roster_entry("WR C", "WR", "SEA", 14.0)
    wr_sea["lineup_slot"] = "WR"
    flags = anticorrelation_flags([rb_sea, rb_sf, wr_sea])
    assert flags == []  # no same-backfield RB pair


def test_anticorrelation_flags_multiple_committees_independently():
    sea1 = _roster_entry("SEA RB1", "RB", "SEA", 12.0)
    sea1["lineup_slot"] = "RB"
    sea2 = _roster_entry("SEA RB2", "RB", "SEA", 10.0)
    sea2["lineup_slot"] = "FLEX"
    sf1 = _roster_entry("SF RB1", "RB", "SF", 12.0)
    sf1["lineup_slot"] = "RB"
    sf2 = _roster_entry("SF RB2", "RB", "SF", 8.0)
    sf2["lineup_slot"] = "RB"
    flags = anticorrelation_flags([sea1, sea2, sf1, sf2])
    assert len(flags) == 2
    teams = {f["nfl_team"] for f in flags}
    assert teams == {"SEA", "SF"}


# --- CRITICAL: the no-mutation invariant -------------------------------------


def test_stack_flag_never_mutates_its_inputs():
    pos_a, pos_b = "QB", "WR"
    proj_a, proj_b = 18.0, 14.0
    name_b = "DK Metcalf"
    # strings/floats are immutable, but assert the call returns a fresh
    # dict that shares no mutable structure with anything passed in
    flag = stack_flag(pos_a, proj_a, pos_b, proj_b, name_b)
    assert flag is not None
    flag["correlation"] = 999.0
    flag["note"] = "tampered"
    # the table is untouched (no in-place edit of the module constant)
    assert STACK_CORRELATION[("QB", "WR")] == 0.40
    # inputs unchanged
    assert (pos_a, pos_b, proj_a, proj_b, name_b) == ("QB", "WR", 18.0, 14.0, "DK Metcalf")


def test_stacks_for_roster_never_mutates_its_inputs():
    qb = _roster_entry("Geno Smith", "QB", "SEA", 18.0)
    wr = _roster_entry("DK Metcalf", "WR", "SEA", 14.0)
    te = _roster_entry("Noah Fant", "TE", "SEA", 9.0)
    roster = [wr, te]
    qb_snapshot = copy.deepcopy(qb)
    roster_snapshot = copy.deepcopy(roster)

    flag = stacks_for_roster(qb, roster)
    assert flag is not None
    # mutate the returned flag — must not bleed into inputs
    flag["correlation"] = -1.0
    flag["also_with"].append("EVIL")

    assert qb == qb_snapshot
    assert roster == roster_snapshot
    # the best-flag dict's "with" still names the original teammate
    # (re-run to confirm idempotent + unchanged)
    flag2 = stacks_for_roster(qb, roster)
    assert flag2["with"] == "DK Metcalf"
    assert flag2["correlation"] == 0.40


def test_anticorrelation_flags_never_mutates_its_inputs():
    a = _roster_entry("Kenneth Walker III", "RB", "SEA", 15.0)
    a["lineup_slot"] = "RB"
    b = _roster_entry("Zach Charbonnet", "RB", "SEA", 8.0)
    b["lineup_slot"] = "FLEX"
    exclusion = frozenset({frozenset({"A", "B"})})
    inputs_snapshot = copy.deepcopy([a, b])
    exclusion_snapshot = set(exclusion)

    flags = anticorrelation_flags([a, b], exclusion)
    assert flags
    flags[0]["players"].append("EVIL")
    flags[0]["note"] = "tampered"

    assert [a, b] == inputs_snapshot
    assert exclusion == exclusion_snapshot


def test_roster_stack_flags_never_mutates_its_inputs():
    qb = _roster_entry("Geno Smith", "QB", "SEA", 18.0)
    wr = _roster_entry("DK Metcalf", "WR", "SEA", 14.0)
    inputs_snapshot = copy.deepcopy([qb, wr])
    flags = roster_stack_flags([qb, wr])
    assert flags
    flags[0]["also_with"] = ["EVIL"] if "also_with" in flags[0] else []
    flags[0]["correlation"] = -1.0
    assert [qb, wr] == inputs_snapshot


# --- also_with is a fresh list, not aliased into the table -------------------


def test_also_with_is_a_fresh_list_per_call():
    qb = _roster_entry("Geno Smith", "QB", "SEA", 18.0)
    wr = _roster_entry("DK Metcalf", "WR", "SEA", 14.0)
    te = _roster_entry("Noah Fant", "TE", "SEA", 9.0)
    flag1 = stacks_for_roster(qb, [wr, te])
    flag2 = stacks_for_roster(qb, [wr, te])
    assert flag1["also_with"] == flag2["also_with"]
    assert flag1["also_with"] is not flag2["also_with"]
