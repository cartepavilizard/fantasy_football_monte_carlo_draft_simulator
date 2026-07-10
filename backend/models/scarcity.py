# -*- coding: utf-8 -*-
"""
TIER-DEPLETION SCARCITY MODELS AND DECISION LOGIC (Phase A, task A1)

The scarcity engine answers one question per position at the simulator's
upcoming pick: "reach now, or safely wait until my next pick?" The
directional call must consult Monte Carlo availability predictions —
simulated opponent picks between now and the simulator's next pick —
never raw counts alone. The simulation loop lives in app.py
(scarcity_analysis); everything here is pure and unit-testable.

Spec for consumers (A2's scarcity nudge UI reads GET
/draft/{draft_id}/scarcity, which returns a ScarcityReport):

- positions are qb/rb/wr/te only; DST and K are streamable and carry no
  meaningful tiers, so they never get a scarcity call.
- `call` is one of:
    reach       — the active tier will probably be gone by your next
                  pick (P(survives) < REACH_PROBABILITY); take it now.
    wait        — the active tier will probably still be there
                  (P(survives) >= WAIT_PROBABILITY).
    toss_up     — between the two thresholds; could go either way.
    last_chance — this is the simulator's final pick; waiting is not
                  an option at any position.
    exhausted   — no undrafted players remain at the position.
    no_tiers    — undrafted players exist but none carry tier data.
- `message` is display-ready text; the numbers backing it are also on
  the model so the UI can render badges without parsing strings.
"""
from pydantic import BaseModel
from typing import List, Tuple, Union

# Positions that get scarcity calls (DST & K are streamable)
SCARCITY_POSITIONS = ["qb", "rb", "wr", "te"]

# P(active tier survives to your next pick) below this => reach now
REACH_PROBABILITY = 0.5
# ... at or above this => safe to wait; between the two => toss-up
WAIT_PROBABILITY = 0.8

# How many active-tier players to list with per-player survival odds
AT_RISK_LIMIT = 5


def effective_tier(player) -> Union[int, None]:
    """
    The tier used for scarcity math: the cross-source consensus tier
    when rankings are synced, else the digit of the coarse position_tier
    ("rb2" -> 2) from the CSV path, else None ("dst"/"k"/empty)
    """
    if player.tier is not None:
        return int(player.tier)
    suffix = player.position_tier[len(player.position) :]
    return int(suffix) if suffix.isdigit() else None


def tier_breakdown(available: list) -> Tuple[Union[int, None], list, Union[int, None], list]:
    """
    Split a position's undrafted players into the active tier (the best
    occupied tier number) and the next occupied tier behind it, skipping
    empty tier numbers. Untiered players are left out of tier math.
    Returns (active_tier, active_players, next_tier, next_tier_players)
    """
    tiered = {}
    for player in available:
        tier = effective_tier(player)
        if tier is not None:
            tiered.setdefault(tier, []).append(player)
    if not tiered:
        return None, [], None, []
    tiers = sorted(tiered)
    active = tiers[0]
    next_tier = tiers[1] if len(tiers) > 1 else None
    return active, tiered[active], next_tier, tiered.get(next_tier, [])


def scarcity_call(
    position: str,
    tier: Union[int, None],
    remaining_now: int,
    expected_at_next_pick: float,
    prob_tier_at_next_pick: float,
    next_tier: Union[int, None],
    next_tier_remaining_now: int,
    final_pick: bool,
) -> Tuple[str, str]:
    """
    The directional call for one position, from numbers the Monte Carlo
    availability simulation produced. Returns (call, message).
    """
    pos = position.upper()
    if remaining_now == 0:
        return "exhausted", f"No {pos} remain undrafted."
    if tier is None:
        return "no_tiers", f"No tier data for {pos}."
    if final_pick:
        return "last_chance", f"Final pick — take any {pos} you still want now."

    if prob_tier_at_next_pick < REACH_PROBABILITY:
        if remaining_now == 1:
            lead = f"Reach now — last player in {pos} tier {tier}"
        else:
            lead = f"Reach now — {remaining_now} left in {pos} tier {tier}"
        message = (
            f"{lead}, {prob_tier_at_next_pick:.0%} chance any survive to"
            f" your next pick."
        )
        if next_tier is not None:
            message += f" Tier {next_tier} has {next_tier_remaining_now} options behind them."
        else:
            message += " No tiered players behind them."
        return "reach", message

    call = "wait" if prob_tier_at_next_pick >= WAIT_PROBABILITY else "toss_up"
    lead = "Safe to wait" if call == "wait" else "Toss-up"
    message = (
        f"{lead} — expect {expected_at_next_pick:.1f} of {remaining_now}"
        f" {pos} tier {tier} players at your next pick"
        f" ({prob_tier_at_next_pick:.0%} chance at least one survives)."
    )
    return call, message


class PlayerAvailability(BaseModel):
    """One active-tier player with simulated survival odds"""

    name: str
    tier: Union[int, None] = None
    projected_points: float = 0
    survival_at_pick: float = 0  # P(still available at your upcoming pick)
    survival_at_next_pick: float = 0  # P(still available one pick later)


class PositionScarcity(BaseModel):
    """Depletion state and the directional call for one position"""

    position: str
    call: str  # reach | wait | toss_up | last_chance | exhausted | no_tiers
    message: str
    tier: Union[int, None] = None  # the active (best occupied) tier
    remaining_now: int = 0  # true undrafted count in the active tier
    expected_at_pick: float = 0  # E[active-tier survivors at your upcoming pick]
    expected_at_next_pick: float = 0  # ... one pick later
    prob_tier_at_pick: float = 0  # P(>=1 active-tier player at your upcoming pick)
    prob_tier_at_next_pick: float = 0
    next_tier: Union[int, None] = None
    next_tier_remaining_now: int = 0
    next_tier_expected_at_next_pick: float = 0
    at_risk: List[PlayerAvailability] = []


class ScarcityReport(BaseModel):
    """Scarcity calls for every position at the simulator's upcoming pick"""

    current_pick: int  # overall pick number currently on the clock
    your_pick: int  # the simulator's upcoming pick number
    your_next_pick: Union[int, None] = None  # one wheel later; None on final pick
    on_the_clock: bool = False  # the simulator is picking right now
    final_pick: bool = False
    iterations: int = 0  # Monte Carlo availability runs completed
    elapsed_seconds: float = 0
    positions: List[PositionScarcity] = []
