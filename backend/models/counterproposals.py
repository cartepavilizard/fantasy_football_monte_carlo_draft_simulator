# -*- coding: utf-8 -*-
"""
COUNTERPROPOSAL GENERATOR (PHASE E, TASK E2)

Given a proposed trade E1 graded outside its fairness bound (or one the
user simply wants alternatives for), produce 1-3 counterproposals that
(a) keep the original deal's intent, (b) close the market gap into E1's
fair bound, and (c) don't wreck either roster's fit. This is a search over
MODIFICATIONS of the proposal — not a find-any-trade engine (that's E4).

Every quantity here is in E1's units and every evaluation goes through
E1's pure functions on a single shared ValuationContext (built once by the
caller). This module adds no async surface and no new value definitions —
it reuses `player_value` and `evaluate_trade` verbatim, plus E1's `lineup`
primitives for the one-week surplus proxy. It does NOT modify E1; where E2
needs a quantity E1's evaluate dict doesn't carry (the roster-size note),
it annotates the dict here as a consumer.

THE SEARCH — one move, anchored (§2 of the spec):

  ADD    — the advantaged side adds one of its players to its outgoing set
           (they're winning the deal; they sweeten).
  REMOVE — the disadvantaged side drops one outgoing player (>= 2 sent).
  SWAP   — one outgoing player on either side is replaced by a different
           player from the same roster.

Anchor rule: the most valuable player (by E1 `player_value`) RECEIVED by
the disadvantaged side is the deal's reason for existing and is never
removed or swapped out — without it the "best" counter is always "cancel
the trade", which is useless output.

THE PRUNING FUNNEL (§3) — how the search stays under a second:

  Stage 0  build once: market values are O(1) lookups on the context; the
           one-week (w0) surplus cost of every roster player is precomputed
           so the untouchables filter is a dict read.
  Stage 1  candidate filter (cheap, market values only): gap-band bounds on
           ADD/SWAP pieces, untouchables excluded.
  Stage 2  rank by residual gap (pure arithmetic on cached values); keep the
           MAX_FINALISTS with the smallest |new_market_gap| that already sit
           inside the counter's own fair bound.
  Stage 3  full E1 evaluation (expensive, <= MAX_FINALISTS times); discard a
           counter that drives either side's fit_delta below FIT_FLOOR.
  Stage 4  select + diversify: maximize the worse side's fit, deterministic
           tie-breaks, drop duplicate player multisets, return top MAX_COUNTERS.

No randomness anywhere — the tie-break chain makes the output a pure
function of the synced data. The full design contract (every constant's
rationale, the edge cases, the worked example) is
docs/specs/E2-counterproposal-generator.md.
"""
from typing import Dict, List, Optional

from .config import (
    FAIR_GAP_FRACTION,
    FAIR_GAP_POINTS,
    FIT_FLOOR,
    GAP_MIN_FRACTION,
    GAP_SLACK,
    MAX_COUNTERS,
    MAX_FINALISTS,
    MAX_SIDE_PLAYERS,
    SURPLUS_COST_CEILING,
)
from .lineup import best_assignment, slot_instances
from .trade_valuation import (
    ValuationContext,
    evaluate_trade,
    expected_points,
    player_value,
)

# Move types, ordered for the deterministic tie-break (ADD < REMOVE < SWAP).
_MOVE_ORDER = {"add": 0, "remove": 1, "swap": 2}


def _po(overrides, player_id):
    """The per-player availability override slice, or None — mirrors E1's
    own accessor so the surplus DP prices players exactly as E1 does."""
    return overrides.get(player_id) if overrides else None


def _week_starting(
    ctx: ValuationContext,
    player_ids: List[int],
    week: int,
    overrides=None,
) -> float:
    """One week's starting-lineup total via C1's exact assignment DP (the
    same primitive E1's team_ros_points uses), for the surplus proxy. This
    is a READER built on E1's pure functions — a single week at w0, never
    the full horizon, precisely so Stage 1 stays cheap (§3 Stage 1)."""
    slots = slot_instances(ctx.league.lineup_slot_counts)
    candidates = [(pid, ctx.players[pid].get("position")) for pid in player_ids]
    weights = {
        pid: expected_points(ctx, pid, week, _po(overrides, pid))
        for pid in player_ids
    }
    _, starting = best_assignment(slots, candidates, weights)
    return starting


class _Search:
    """One counter search over a fixed proposal and context. Holds the
    Stage-0 caches (market values, surplus costs) so every later stage is a
    dict read. Instantiated per call; carries no module state — determinism
    and purity both depend on that."""

    def __init__(self, ctx, team_a, team_b, sends_a, sends_b, overrides):
        self.ctx = ctx
        self.team_a = team_a
        self.team_b = team_b
        self.sends_a = list(sends_a)
        self.sends_b = list(sends_b)
        self.overrides = overrides
        self._value_cache: Dict[int, dict] = {}
        self._surplus_cache: Dict[int, float] = {}

        # Orient the deal: the disadvantaged side sends more market value.
        # market_gap = value_sent_a - value_sent_b (E1 §4.3); ties (incl. a
        # perfectly even fair deal) put A on the disadvantaged side so the
        # anchor/anchor-free choice is deterministic.
        self.value_a = sum(self.pv(pid)["value"] for pid in self.sends_a)
        self.value_b = sum(self.pv(pid)["value"] for pid in self.sends_b)
        self.gap = self.value_a - self.value_b
        if self.gap >= 0:
            self.dis_side, self.adv_side = "a", "b"
            self.dis_team, self.adv_team = team_a, team_b
            self.dis_sends, self.adv_sends = self.sends_a, self.sends_b
        else:
            self.dis_side, self.adv_side = "b", "a"
            self.dis_team, self.adv_team = team_b, team_a
            self.dis_sends, self.adv_sends = self.sends_b, self.sends_a
        self.abs_gap = abs(self.gap)

        # Anchor: the most valuable player the disadvantaged side receives
        # (i.e. the advantaged side's outgoing set). Tie -> higher value,
        # then lower player_id (§5 anchor ambiguity). None only when the
        # disadvantaged side receives nothing (a gift): no reason-to-exist
        # player to protect, so ADD/REMOVE still run with nothing pinned.
        if self.adv_sends:
            self.anchor = max(
                self.adv_sends, key=lambda pid: (self.pv(pid)["value"], -pid)
            )
        else:
            self.anchor = None

    # --- Stage 0 caches ------------------------------------------------------

    def pv(self, player_id: int) -> dict:
        cached = self._value_cache.get(player_id)
        if cached is None:
            cached = player_value(self.ctx, player_id, self.overrides)
            self._value_cache[player_id] = cached
        return cached

    def is_untouchable(self, team: int, player_id: int) -> bool:
        """A player is untouchable when dropping them costs their roster more
        than SURPLUS_COST_CEILING starting points at w0 — the owning side
        clearly shouldn't move them, so they never get added or swapped in
        (§3 Stage 1). One-week proxy, computed once per player, cached."""
        cost = self._surplus_cache.get(player_id)
        if cost is None:
            roster = self.ctx.rosters.get(team, [])
            w0 = self.ctx.w0
            full = _week_starting(self.ctx, roster, w0, self.overrides)
            without = _week_starting(
                self.ctx, [p for p in roster if p != player_id], w0, self.overrides
            )
            cost = full - without
            self._surplus_cache[player_id] = cost
        return cost > SURPLUS_COST_CEILING

    # --- Stage 1: candidate generation --------------------------------------

    def _candidate(self, move, new_dis, new_adv):
        """Assemble a candidate from modified outgoing sets, mapping the
        disadvantaged/advantaged split back to A/B and precomputing the pure
        residual-gap arithmetic Stage 2 ranks on."""
        if self.dis_side == "a":
            new_a, new_b = list(new_dis), list(new_adv)
        else:
            new_a, new_b = list(new_adv), list(new_dis)
        va = sum(self.pv(pid)["value"] for pid in new_a)
        vb = sum(self.pv(pid)["value"] for pid in new_b)
        return {
            "move": move,
            "sends_a": new_a,
            "sends_b": new_b,
            "value_a": va,
            "value_b": vb,
            "new_gap": va - vb,
        }

    def _in_gap_band(self, magnitude: float) -> bool:
        """A Stage-1 piece must close a real chunk of the gap without
        overshooting into unfairness the other way: |value| in
        [GAP_MIN_FRACTION, 1+GAP_SLACK] x |market_gap| (§3 Stage 1)."""
        return (
            self.abs_gap * GAP_MIN_FRACTION
            <= magnitude
            <= self.abs_gap * (1 + GAP_SLACK)
        )

    def _add_candidates(self):
        """ADD: the advantaged side sweetens with one of its own players.
        Only players inside the gap band and not untouchable; skips when the
        side already sends MAX_SIDE_PLAYERS."""
        if len(self.adv_sends) >= MAX_SIDE_PLAYERS:
            return
        roster = self.ctx.rosters.get(self.adv_team, [])
        for pid in roster:
            if pid in self.adv_sends:
                continue
            if self.is_untouchable(self.adv_team, pid):
                continue
            if not self._in_gap_band(self.pv(pid)["value"]):
                continue
            move = {
                "type": "add",
                "team": self.adv_side,
                "player_id": pid,
                "player_name": self.pv(pid)["name"],
            }
            yield self._candidate(move, self.dis_sends, self.adv_sends + [pid])

    def _remove_candidates(self):
        """REMOVE: the disadvantaged side drops one outgoing player. Only
        legal when it sends >= 2 (never empty a side); the anchor lives on
        the OTHER side's outgoing set, so nothing here can touch it."""
        if len(self.dis_sends) < 2:
            return
        for pid in self.dis_sends:
            if pid == self.anchor:  # defensive: anchor is never on this side
                continue
            move = {
                "type": "remove",
                "team": self.dis_side,
                "player_id": pid,
                "player_name": self.pv(pid)["name"],
            }
            new_dis = [p for p in self.dis_sends if p != pid]
            yield self._candidate(move, new_dis, self.adv_sends)

    def _swap_candidates(self):
        """SWAP: replace a non-anchor outgoing player q with a different
        player r from the same roster, in the gap band, moving the gap toward
        fair. On the disadvantaged side that means a cheaper r (send less);
        on the advantaged side a pricier r (give more). r must not be
        untouchable (it's a swap-IN, same bar as an ADD)."""
        for side, side_label, team, sends in (
            (self.dis_sends, self.dis_side, self.dis_team, self.dis_sends),
            (self.adv_sends, self.adv_side, self.adv_team, self.adv_sends),
        ):
            roster = self.ctx.rosters.get(team, [])
            for q in sends:
                if q == self.anchor:
                    continue
                q_value = self.pv(q)["value"]
                for r in roster:
                    if r == q or r in sends:
                        continue
                    if self.is_untouchable(team, r):
                        continue
                    delta = self.pv(r)["value"] - q_value
                    # sign that closes the gap
                    if side_label == self.dis_side and delta >= 0:
                        continue
                    if side_label == self.adv_side and delta <= 0:
                        continue
                    if not self._in_gap_band(abs(delta)):
                        continue
                    move = {
                        "type": "swap",
                        "team": side_label,
                        "player_id": r,
                        "player_name": self.pv(r)["name"],
                        "player_out_id": q,
                        "player_out_name": self.pv(q)["name"],
                    }
                    new_sends = [r if p == q else p for p in sends]
                    if side_label == self.dis_side:
                        yield self._candidate(move, new_sends, self.adv_sends)
                    else:
                        yield self._candidate(move, self.dis_sends, new_sends)

    def candidates(self):
        # Generated add-before-remove-before-swap so the deterministic
        # tie-break order is already the natural iteration order.
        return (
            list(self._add_candidates())
            + list(self._remove_candidates())
            + list(self._swap_candidates())
        )

    # --- Stage 2: residual-gap ranking --------------------------------------

    @staticmethod
    def _fair_bound(va: float, vb: float) -> float:
        return max(FAIR_GAP_POINTS, FAIR_GAP_FRACTION * max(va, vb))

    def _move_key(self, move):
        """Deterministic component of a sort key: move type, then the player
        ids the move touches (§4 Stage 4)."""
        if move["type"] == "swap":
            ids = (move["player_out_id"], move["player_id"])
        else:
            ids = (move["player_id"],)
        return (_MOVE_ORDER[move["type"]], ids)

    def finalists(self):
        """Keep the MAX_FINALISTS candidates with the smallest |new_market_gap|
        that already sit inside their own fair bound (recomputed for the
        counter's own totals, E1 §4.3). Pure arithmetic — no DP yet."""
        scored = []
        for cand in self.candidates():
            bound = self._fair_bound(cand["value_a"], cand["value_b"])
            if abs(cand["new_gap"]) <= bound:
                scored.append(cand)
        scored.sort(key=lambda c: (abs(c["new_gap"]), self._move_key(c["move"])))
        return scored[:MAX_FINALISTS]


def _roster_size_note(ctx, team_a, team_b, sends_a, sends_b):
    """Informational note (§5): an unequal-count trade leaves team A over or
    under a roster spot on execution. E1's evaluate dict does not carry this
    — E2 annotates it here as a consumer rather than reshaping E1's API."""
    diff = len(sends_b) - len(sends_a)  # net change to team A's roster size
    if diff == 0:
        return None
    n = abs(diff)
    plural = "" if n == 1 else "s"
    if diff > 0:
        over_under, action = "over", "drop"
    else:
        over_under, action = "under", "add"
    label = ctx.team_names.get(team_a) or f"team {team_a}"
    return (
        f"{label} ends this trade {n} player{plural} {over_under} — "
        f"a {action} is required on execution"
    )


def _annotate(evaluation, ctx, team_a, team_b, sends_a, sends_b):
    """Attach the roster-size note to an E1 evaluate_trade dict (a shallow
    copy — E2 never mutates E1's output in place)."""
    note = _roster_size_note(ctx, team_a, team_b, sends_a, sends_b)
    annotated = dict(evaluation)
    annotated["roster_size_note"] = note
    return annotated


def _rationale(search, move, evaluation):
    """Plain-terms C8 copy: quote the ROS/per-week numbers and the gap close,
    never last week's box score (§4). Team A is 'you', B is 'them'."""
    poss = "your" if move["team"] == "a" else "their"
    new_gap = abs(evaluation["market_gap"])
    orig_gap = search.abs_gap
    fit_a = evaluation["fit_per_week_a"]
    fit_b = evaluation["fit_per_week_b"]

    if move["type"] == "add":
        value = search.pv(move["player_id"])["value"]
        pos = search.pv(move["player_id"])["position"] or "player"
        head = (
            f"Adding {pos} {move['player_name']} ({value:.1f} ROS pts) to "
            f"{poss} side"
        )
    elif move["type"] == "remove":
        value = search.pv(move["player_id"])["value"]
        pos = search.pv(move["player_id"])["position"] or "player"
        head = (
            f"Dropping {pos} {move['player_name']} ({value:.1f} ROS pts) from "
            f"{poss} side"
        )
    else:  # swap
        value = search.pv(move["player_id"])["value"]
        pos = search.pv(move["player_id"])["position"] or "player"
        head = (
            f"Swapping {poss} {move['player_out_name']} for {pos} "
            f"{move['player_name']} ({value:.1f} ROS pts)"
        )

    return (
        f"{head} closes the {orig_gap:.1f}-point market gap to {new_gap:.1f} "
        f"— inside the fair range. On roster fit your lineup projects "
        f"{fit_a:+.1f} pts/week and theirs {fit_b:+.1f}."
    )


def generate_counters(
    ctx: ValuationContext,
    team_a: int,
    team_b: int,
    sends_a: List[int],
    sends_b: List[int],
    overrides: Optional[Dict[int, Dict[int, float]]] = None,
) -> dict:
    """Search modifications of the proposal for 1-MAX_COUNTERS fair counters
    (spec §4). Pure and synchronous — all data comes from `ctx`, which the
    caller built once. Runs the four-stage funnel; the original is always
    evaluated and returned so the UI can show what's being countered.

    Determinism: no randomness, and the tie-break chain (worse-side fit,
    then |market_gap|, then move type + player ids) makes the output a pure
    function of the synced data — running the search twice yields identical
    results.
    """
    search = _Search(ctx, team_a, team_b, sends_a, sends_b, overrides)

    original_eval = evaluate_trade(ctx, team_a, team_b, sends_a, sends_b, overrides)
    original = _annotate(original_eval, ctx, team_a, team_b, sends_a, sends_b)
    verdict = original_eval["verdict"]

    # Stage 3: full E1 evaluation of the finalists; drop any counter that
    # drives either side's fit below FIT_FLOOR (a counter that actively hurts
    # a roster won't be accepted, fairness or not).
    survivors = []
    for cand in search.finalists():
        evaluation = evaluate_trade(
            ctx, team_a, team_b, cand["sends_a"], cand["sends_b"], overrides
        )
        fit_a = evaluation["fit_delta_a"]
        fit_b = evaluation["fit_delta_b"]
        if min(fit_a, fit_b) < FIT_FLOOR:
            continue
        survivors.append((cand, evaluation, min(fit_a, fit_b)))

    # Stage 4: maximize the worse side's fit (the most ACCEPTABLE counter, not
    # the most extractive); tie-break on |market_gap| ascending, then the
    # deterministic move key. Skip duplicate player multisets.
    survivors.sort(
        key=lambda item: (
            -item[2],
            abs(item[1]["market_gap"]),
            search._move_key(item[0]["move"]),
        )
    )

    counters = []
    seen = set()
    for cand, evaluation, _worse in survivors:
        multiset = (tuple(sorted(cand["sends_a"])), tuple(sorted(cand["sends_b"])))
        if multiset in seen:
            continue
        seen.add(multiset)
        annotated = _annotate(
            evaluation, ctx, team_a, team_b, cand["sends_a"], cand["sends_b"]
        )
        counters.append(
            {
                "move": cand["move"],
                "sends_a": cand["sends_a"],
                "sends_b": cand["sends_b"],
                "evaluation": annotated,
                "rationale": _rationale(search, cand["move"], evaluation),
            }
        )
        if len(counters) >= MAX_COUNTERS:
            break

    if verdict == "fair":
        note = "original trade is already fair — counters below are strict upgrades"
    elif not counters:
        note = "no fair counter exists within one move of this proposal"
    else:
        note = None

    return {"original": original, "counters": counters, "note": note}
