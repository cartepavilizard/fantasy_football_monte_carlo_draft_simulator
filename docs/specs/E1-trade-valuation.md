# E1 — Trade Valuation Model (spec)

> **Status:** Design locked (2026-07-11, Fable design pass). Implement
> verbatim in `backend/models/trade_valuation.py`. This is the keystone
> of Phase E: E2 (counters), E4 (opportunity scanner), E5 (blocking),
> E6 (hoarding), E7 (messaging), and E8 (deadline lens) all consume the
> value units and functions defined here. If an implementing session
> finds a concrete defect, it documents the deviation in the task row —
> it does not quietly redefine a unit.

---

## 1. The value units — defined once, used everywhere

Every Phase E feature speaks these two quantities and no others:

1. **`player_value` — context-free market value.** Expected
   league-scoring fantasy points a player produces **above replacement**
   over the remaining fantasy-relevant weeks (“ROS points”). Floored at
   zero (you can always drop a player for a free agent, so negative
   trade value does not exist). Unit: ROS points.
2. **`fit_delta` — roster-context value of a change.** The change in a
   specific roster’s expected **starting-lineup** ROS points caused by a
   roster move (trade, add, drop). Signed — a trade can hurt. Unit: ROS
   points. Divide by `weeks_remaining` for “points per week” copy.

`player_value` answers “is this trade lopsided on raw value?” (market
fairness). `fit_delta` answers “does it help *this* roster?” (positional
need — acquiring a third QB in a 1-QB league has near-zero fit even when
its market value is high). A good trade report shows **both**; they are
deliberately not merged into one number.

Everything is in **league-scoring points** because the underlying
projections are ESPN’s per-league weekly numbers (C1’s decided source).
Values are therefore **per-league** — never compare a `player_value`
computed in league 111 with one from league 222.

## 2. Horizon

```
w0      = league.latest_scoring_period          (the live week)
W_final = min(TRADE_HORIZON_FINAL_WEEK, league.final_scoring_period or 17)
horizon = [w0, w0+1, ..., W_final]
H       = len(horizon)                          ("weeks_remaining")
```

`TRADE_HORIZON_FINAL_WEEK` (env, default `max(PLAYOFF_SOS_WEEKS)` = 16):
value stops at the fantasy championship — week 17 production is
worthless in these leagues. The live week is **included** (trades
normally process before games); see edge cases for the partially-locked
case.

## 3. Per-week expected points

For player `p` in week `w`:

```
epts(p, w) = matchup_adjusted(rate(p), mult(pos_p, opp(team_p, w))) * avail(p, w)
epts(p, w) = 0  when opp(team_p, w) is None (bye)
```

- `matchup_adjusted` and the multiplier come from
  `models/matchup_strength.py` **unchanged** (same capped tilt C1 uses;
  same double-count rationale). `opp` is `opponent_map()` from the
  synced `ProGame` schedule.
- `rate(p)` and `avail(p, w)` are defined below.

### 3.1 `rate(p)` — the neutral weekly rate

A single week’s ESPN projection prices *that week’s* matchup and injury
status. The trade horizon needs the player’s **neutral healthy rate**,
so:

```
rate(p) = mean of the most recent (up to TRADE_RATE_WEEKS = 4) qualifying
          weekly ESPN projections for p, from weeks <= w0
```

- Sources for those historical projections, in order: the player’s
  `RosterSlotEntry.projected_points` rows across the league’s stored
  `TeamWeekRoster` documents (sync backfills prior weeks), then
  `FreeAgentEntry.projected_points` from stored `FreeAgentSnapshot`
  weeks (players move between roster and pool; both are the same ESPN
  number).
- A week **qualifies** when the projection is not `None` and
  `>= RATE_MIN_POINTS` (0.5). Zeros/near-zeros encode absence (bye,
  out, not yet rostered), not talent — averaging them in would make an
  injured star look worthless, which is exactly the mistake this model
  exists to avoid.
- Fallback chain when no week qualifies (early season, deep FA):
  1. current-week projection if present (week 1: it’s all we have);
  2. `FreeAgentEntry.season_projection / 17` if present;
  3. `0.0`, and append warning `"no projection data for <name>"`.

Accepted noise: questionable-week projections are somewhat depressed
and matchup pricing partially survives the average. Both are bounded
(4-week mean, capped tilt) and beat any alternative that needs a new
data source. Do **not** “fix” this with external projections.

### 3.2 `avail(p, w)` — availability curve

Injury status: use the player’s `RosterSlotEntry.injury_status` from
the `w0` roster (primary; always present for rostered players); for
free agents use `FreeAgentEntry.injury_status`; if D2 has landed and a
newer `InjuryDesignation` row exists for `(p, w0)`, it wins. Lowercased
ESPN statuses map to curves (weeks counted from `w0`):

| status | w0 | w0+1 | w0+2 | w0+3.. | rationale |
| --- | --- | --- | --- | --- | --- |
| active / None | 1.0 | 1.0 | 1.0 | 1.0 | |
| questionable | `QUESTIONABLE_PLAY_PROB` (0.75) | 1.0 | 1.0 | 1.0 | historical Q play rate ≈ 75% |
| doubtful | `DOUBTFUL_PLAY_PROB` (0.25) | 1.0 | 1.0 | 1.0 | historical D play rate ≈ 25% |
| out | 0.0 | 0.75 | 1.0 | 1.0 | next week usually questionable |
| injury_reserve | 0.0 for `IR_RETURN_WEEKS` (3) weeks | ← | ← | `IR_RETURN_DISCOUNT` (0.8) | IR minimum stint; discount = setback/ramp-up risk |
| suspension | same curve as injury_reserve | | | | duration unknown → conservative |

**This table IS the IR-stash value** (brainstorm §2.6): an IR player’s
value is whatever survives the curve — zero for the stash weeks,
discounted production after. It is an input here, not its own task;
E6’s drop decisions and waiver views inherit it by calling
`player_value`.

`evaluate_trade` accepts `availability_overrides: {player_id: {week:
prob}}` for user-known timelines (e.g. a D3 note saying “targeting week
12”). Overrides are always explicit request input — never auto-derived
from D3 notes (those are unverified by design).

### 3.3 `replacement(pos)` — the zero line

```
rr(pos) = rate() of the REPLACEMENT_RANK-th (3rd) best free agent at pos,
          ranked by rate(), from the league's latest FreeAgentSnapshot
```

3rd-best, not 1st: the top FA is often mispriced, stale, or gone by the
time you need them; 3rd-best approximates what is *reliably* attainable
all season. Replacement production is flat across the horizon
(`rr(pos) * H`) — streaming means replacement level never takes a bye.

Fallback: fewer than `REPLACEMENT_RANK` FAs at a position (or no
snapshot) → use the last one available; none at all → `rr = 0` plus
warning `"no free agents at <pos> — values are raw points, inflated"`.

## 4. The two headline computations

### 4.1 `player_value(p)` — market value

```
gross(p)        = Σ_w epts(p, w)                          over horizon
playoff_gross(p)= Σ_w epts(p, w)   for w in horizon ∩ PLAYOFF_SOS_WEEKS
player_value(p) = max(gross(p) - rr(pos_p) * H, 0)
playoff_value(p)= max(playoff_gross(p) - rr(pos_p) * |horizon ∩ PLAYOFF_SOS_WEEKS|, 0)
```

`playoff_value` is a **reported component, not a re-weighting**: the
headline unit stays unweighted ROS points so consumers all mean the
same thing. Verdict copy quotes the playoff component when relevant
(contender lens — E8’s job); it never multiplies into the value. Note
this is C5’s signal at week granularity — the same C2 multipliers over
the same playoff window — so E1 consumes C5 by construction without
importing its report shape.

### 4.2 `team_ros_points(roster)` — roster context

For each week `w` in the horizon, run C1’s exact assignment DP
(`models.lineup.best_assignment`, reused as-is) over the team’s players
with weights `epts(·, w)`, using `slot_instances(league.lineup_slot_counts)`:

```
starting(w)  = best_assignment(slots, candidates, {p: epts(p, w)}).total
bench(w)     = Σ_{p not started in w} max(epts(p, w) - rr(pos_p), 0)
team_ros_points = Σ_w [ starting(w) + BENCH_FACTOR * bench(w) ]
```

- Candidates include **every** player on the roster, including current
  IR-slot occupants (their availability curve already zeroes the stash
  weeks; in return weeks they compete for slots — that’s the stash
  value realizing itself). This deliberately differs from C1’s live
  optimizer, which excludes IR because a same-week start is impossible.
- `BENCH_FACTOR` (0.15): bench players convert to starting points via
  injuries/byes at roughly the league’s starter-miss rate. Without this
  term, depth is worthless and every 2-for-1 consolidation grades as
  free; with a full-weight term, hoarding grades as free. 0.15 ≈
  weekly probability some starter misses.
- `fit_delta` for a trade = `team_ros_points(after) − team_ros_points(before)`,
  where “after” swaps the traded player sets. Never floored.

Cost note: one team eval = `H` (≈ 9–14) DP runs over ≤ 18 candidates —
fine for interactive grading. E2’s search must NOT brute-force this;
see E2’s pruning spec.

### 4.3 Trade evaluation

Proposal: team A sends set `S_A`, team B sends set `S_B`.

```
value_sent_A = Σ player_value(p in S_A)      (likewise B)
market_gap   = value_sent_A - value_sent_B   (positive = A gives more)
fair_bound   = max(FAIR_GAP_POINTS, FAIR_GAP_FRACTION * max(value_sent_A, value_sent_B))
verdict      = "fair"      if |market_gap| <= fair_bound
             = "favors_B"  if market_gap > fair_bound    (A gives more → B wins)
             = "favors_A"  otherwise
fit_delta_A  = team_ros_points(A after) - team_ros_points(A before)
fit_delta_B  = likewise
```

`FAIR_GAP_POINTS` = 10 ROS points (≈ 1 pt/week over 10 weeks — inside
projection noise); `FAIR_GAP_FRACTION` = 0.15 (a 100-point blockbuster
can be fair at a 12-point gap; a 20-point dart throw cannot). The max
of the two keeps small trades from failing fairness on trivial absolute
gaps and big trades from failing on trivial relative ones.

**Plain-terms copy** (the brainstorm requirement): the response includes
a `summary` string built from both lenses, quoting per-week numbers:

> “You send 18.4 more ROS points of market value (about 2.0/week), but
> the deal fills your RB2 hole: your starting lineup projects +2.3
> points/week while theirs gains +0.4. Verdict: favors them on value,
> works for both rosters.”

Copy rules: quote ROS points and per-week points; name the position
need it fills or creates; C8 framing (volume/projection language, never
last week’s score); when any input carried a warning (no projections,
no FA baseline, neutral matchups), the summary says so.

## 5. Module & API shape

New module `backend/models/trade_valuation.py`. Mongo-only — it joins
the cached-only club (no `data_sources` import; add its routes to both
enforcement tests in `test_inseason_api.py`).

```python
class ValuationContext:            # plain dataclass, built once per request
    league, season, w0, horizon, opponents,           # opponent_map()
    strength,                       # defense_position_strength() table
    rates: Dict[int, float],        # player_id -> rate()
    players: Dict[int, dict],       # player_id -> {name, position, nfl_team, injury_status, espn_team_id|None}
    replacement: Dict[str, float],  # position -> rr
    warnings: List[str]

async def build_context(engine, league, week=None) -> ValuationContext
    # loads rosters (trailing TRADE_RATE_WEEKS + current), latest FA
    # snapshot, schedule, strength table; computes every rate once

def availability_curve(status, horizon, overrides=None) -> Dict[int, float]
def expected_points(ctx, player_id, week, overrides=None) -> float
def player_value(ctx, player_id, overrides=None) -> dict
    # {player_id, name, position, nfl_team, injury_status, rate,
    #  gross, value, playoff_value, per_week, stash_note|None, warnings}
def team_ros_points(ctx, player_ids: List[int], overrides=None) -> float
def evaluate_trade(ctx, team_a, team_b, sends_a, sends_b, overrides=None) -> dict
```

Everything below `build_context` is **synchronous and pure** — this is
load-bearing for E2, which builds one context and evaluates hundreds of
candidate trades against it. Do not put awaits inside the evaluation
path.

`evaluate_trade` returns:

```json
{
  "week": 8, "weeks_remaining": 9,
  "teams": {"a": {"espn_team_id": 3, "name": "..."}, "b": {...}},
  "sends_a": [<player_value dict>, ...],
  "sends_b": [...],
  "value_sent_a": 41.2, "value_sent_b": 22.8,
  "market_gap": 18.4, "fair_bound": 10.0, "verdict": "favors_b",
  "fit_delta_a": 20.7, "fit_delta_b": 3.6,
  "fit_per_week_a": 2.3, "fit_per_week_b": 0.4,
  "summary": "...", "warnings": [...]
}
```

Endpoints (in `inseason_api.py`, standard `_envelope`):

- `POST /inseason/league/{espn_league_id}/trade/evaluate` — body
  `{team_a, team_b, sends_a: [player_id], sends_b: [player_id],
  season?, week?, availability_overrides?}`. POST because it takes a
  proposal body, **not** because it fetches — it must remain pure
  Mongo reads and be covered by the rigged-transport enforcement test.
- `GET /inseason/league/{espn_league_id}/player_values?espn_team_id=&position=`
  — `player_value` for one team’s roster (or, with `position`, the top
  `limit` free agents too). The UI’s value browser; also a cheap sanity
  surface for tuning.

New config in `models/config.py` (env-overridable, defaults as spec’d):
`TRADE_HORIZON_FINAL_WEEK` (default `max(PLAYOFF_SOS_WEEKS)`),
`TRADE_RATE_WEEKS=4`, `RATE_MIN_POINTS=0.5`, `REPLACEMENT_RANK=3`,
`QUESTIONABLE_PLAY_PROB=0.75`, `DOUBTFUL_PLAY_PROB=0.25`,
`IR_RETURN_WEEKS=3`, `IR_RETURN_DISCOUNT=0.8`, `BENCH_FACTOR=0.15`,
`FAIR_GAP_POINTS=10.0`, `FAIR_GAP_FRACTION=0.15`.

## 6. Edge cases (all must be tested)

- **Player in neither `sends` roster** → HTTP 422 naming the player and
  team (“player 12345 is not on team 3’s current roster”). Validate
  against the `w0` `TeamWeekRoster`.
- **Same player on both sides / duplicate ids** → 422.
- **Empty `sends` on one side** (a gift) → allowed and graded; verdict
  will be lopsided, that’s correct.
- **Live week partially played** (Thursday player already locked):
  ignored — values use full-week epts. Document in the endpoint
  docstring; a mid-week trade slightly overcounts the live week for
  both sides symmetrically.
- **Week-1 grading**: no completed weeks → rates fall back to the
  single current-week projection, all multipliers neutral (C2), curve
  from draft-day injury statuses. Works, with warnings — do not block.
- **No FreeAgentSnapshot** → `rr=0` + warning (see 3.3).
- **Player on bye in `w0`**: their rate comes from earlier qualifying
  weeks (the bye week’s 0-projection is filtered by `RATE_MIN_POINTS`);
  epts(w0)=0 via the bye rule, not via the rate.
- **DST/K**: valued like any position (rates and replacement work);
  their `player_value` will hover near zero because replacement level
  is close — correct, that’s why nobody trades kickers.
- **Missing `nfl_team`** → no opponent lookups; every week neutral
  multiplier, no bye zeroing; add warning.

## 7. Worked example (realistic numbers)

League: 10-team, week `w0=8`, `final_scoring_period=17`,
`TRADE_HORIZON_FINAL_WEEK=16` → horizon = weeks 8–16, `H=9`.
Playoff window 14–16 (3 weeks in horizon).

**Player X — WR, healthy, team with week-11 bye.** Trailing ESPN weekly
projections: 11.8, 13.0, 12.1, 12.7 → `rate = 12.4`. Matchup tilts over
weeks 8–16 net ≈ +0.9 total; bye week 11 = 0.

```
gross(X)  = 12.4 × 8 played weeks + 0.9 tilt = 100.1
rr(WR)    = 7.8  (3rd-best FA WR rate)
value(X)  = 100.1 − 7.8 × 9 = 29.9   →  ≈ 3.3 pts/week above replacement
playoff:  weeks 14–16 all played, tilt +0.4 → 37.6 gross
playoff_value(X) = 37.6 − 7.8×3 = 14.2
```

**Player Y — RB on IR (knee), healthy rate from pre-injury weeks:
14.0.** Curve: weeks 8–10 → 0.0; weeks 11–16 → 0.8. Bye week 13 → 0.

```
gross(Y)  = 14.0 × 0.8 × 5 played return weeks (11,12,14,15,16) = 56.0 (± tilt ≈ 56.4)
rr(RB)    = 6.9
value(Y)  = 56.4 − 6.9 × 9 = −5.7 → floored… WAIT: replacement charges
            all 9 weeks but Y only "occupies" value in return weeks.
```

**This is intentional, not a bug**: holding an IR player costs a roster
spot for all 9 weeks — the replacement term is the price of the stash.
A healthy-rate-14 RB back for the playoffs still clears zero in most
leagues (here he doesn’t quite, because rr×9 = 62.1 > 56.4 → value 0.0
with `stash_note: "on IR, projected back ~week 11; 56 raw pts incl.
33.8 in the playoff window — stash value only if you can afford the
spot"`). In a 12-team league (rr(RB)≈5.5) the same player grades
`56.4 − 49.5 = 6.9` — scarcity is exactly why the stash plays deeper
leagues better. The fit computation refines this further: on a roster
with a healthy bench surplus the DP shows the true opportunity cost.

**Trade: A sends X (29.9) for B’s Y (0.0 market).**
`market_gap = +29.9`, `fair_bound = max(10, 0.15×29.9)=10` → verdict
`favors_b`. But suppose A is 7-1 (locked playoff seed) with a WR
surplus and an RB hole in weeks 14–16: `fit_delta_A` may still come out
positive (+4.1: Y starts in A’s playoff-week lineups where A’s current
RB2 is below replacement), while `fit_delta_B = +18.7`. Summary copy
must present exactly this tension — market-lopsided, mutually
positive — and let the human decide. That’s the model working as
designed, not a contradiction to smooth over.

## 8. What the implementing session must NOT do

- **No external fetches.** Mongo only; the module and its imports stay
  out of `data_sources/`; extend both cached-only enforcement tests.
- **No new projection source and no re-projection.** ESPN weekly
  numbers via the trailing-mean rate, period. The seam for a future
  blend is `ValuationContext.rates` — swap what fills it, later,
  deliberately.
- **No changes to `matchup_strength.py` or `lineup.py`** beyond
  importing them. `best_assignment` and `matchup_adjusted` are reused
  as-is; if the DP needs a performance tweak for E2, that lands in E2’s
  task, not here.
- **Do not merge market value and fit into one score.** Two numbers,
  one summary string. Downstream consumers pick the lens they need.
- **Do not re-weight playoff weeks into the headline unit.** Report
  `playoff_value` alongside; never multiply.
- **Do not floor `fit_delta`** (trades can hurt) and **do not un-floor
  `player_value`** (drops are free).
- **No ML, no regression fitting, no historical trade calibration** —
  every constant is an env-tunable with the rationale documented above.
- **No async in the per-evaluation path** (E2 depends on it).
- Tests: unit-test the pure functions with hand-built contexts (no
  Mongo), plus endpoint tests through the in-memory engine per
  `test_inseason_api.py` patterns. Worked-example numbers above should
  appear as test assertions (tolerances ±0.1).
