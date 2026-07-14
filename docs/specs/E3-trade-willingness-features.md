# E3 — Trade-Willingness Owner Profiles: Feature Definitions (spec)

> **Status:** Design locked (2026-07-11, Fable design pass). This is
> the frontier half of a [SPLIT] task: it defines *which features* and
> *how they’re computed*. The cheap half (a Sonnet session) implements
> the transform in `backend/models/trade_willingness.py`, the endpoint,
> and the UI against this spec verbatim.

## 1. Ground rules — inherited from `profiling.py`

Same philosophy as the draft-tendency profiles, applied to
`LeagueTransaction` data:

- **Frequencies and averages only, no ML.** Every metric carries its
  raw sample size `n` so consumers apply floors instead of trusting
  thin data.
- **Recency-weighted** where seasons mix: weight
  `RECENCY_DECAY ** (current_season − season)` with the same
  `RECENCY_DECAY = 0.9` (import it; don’t redeclare).
- **Inferred metrics are labeled** `"inferred": true` (we observe
  executed transactions, not offers — response behavior is a proxy).
- **Owner identity**: profiles key on the ESPN member GUID
  (`LeagueTeamInfo.owner_guid` joins `team_id` → owner per league), so
  the same brother-in-law across three leagues merges via the existing
  `owner_aliases` mechanism (`load_alias_map`). Teams with no
  `owner_guid` profile under `f"team:{espn_league_id}:{team_id}"` and
  never merge.

**Data horizon caveat (design input, not a fixture):** B1’s
`mTransactions2` sync yields the **current season** per league. Early
season, `n` will be tiny — the features must degrade to explicit
`"unknown"`, never to a confident-looking 0. If prior-season syncs are
ever added, the season field and recency weights are already in place;
do not build multi-season fetching as part of this task.

## 2. Which transactions count

From `LeagueTransaction` (already filtered at sync to real player
movement):

- **Trade events**: `type` containing `TRADE`, `status == "EXECUTED"`.
  One executed trade appears as one transaction with items moving both
  directions; the two involved team ids come from the items’
  `from_team_id`/`to_team_id` (the header `team_id` is only the
  initiator). Pending/vetoed trades are excluded from execution counts
  but counted separately (see `veto_context` below).
- **Activity events**: executed `WAIVER` / `FREEAGENT` adds and drops —
  the general-motion backdrop that separates “never trades because
  inactive” from “active but trade-averse”, which are opposite
  approach targets.

## 3. The features — the `trade_willingness` block

Computed per owner per league (an owner’s appetite can differ by
league), plus a merged all-leagues view for display. Shape:

```json
"trade_willingness": {
  "n_trades": 3,
  "n_seasons_observed": 1,
  "trades_per_season": 3.0,
  "league_mean_trades_per_season": 1.4,
  "relative_trade_rate": 2.14,          // ratio vs league mean; null when league mean is 0
  "activity": {                          // the motion backdrop
    "n_moves": 41, "moves_per_season": 41.0,
    "league_mean_moves_per_season": 22.5
  },
  "deal_shapes": {                       // over this owner's executed trades
    "n": 3,
    "one_for_one": 0.33, "two_for_one": 0.67, "bigger": 0.0,
    "avg_players_sent": 1.7, "avg_players_received": 1.3
  },
  "position_mix": {                      // positions this owner has traded AWAY
    "n_players_sent": 5,
    "shares": {"RB": 0.4, "WR": 0.4, "TE": 0.2}
  },
  "timing": {                            // when in the season they deal
    "n": 3,
    "buckets": {"early(1-5)": 0.33, "mid(6-9)": 0.33, "deadline(10+)": 0.33}
  },
  "partners": {"n_distinct": 2, "concentration": 0.67},  // top-partner share; 1.0 = only ever deals with one owner
  "initiations": {"n": 2, "rate": 0.67, "inferred": true},
      // share of their executed trades where header team_id == their team
      // (proxy: ESPN records the proposer as initiator)
  "veto_context": {"n_vetoed_league": 0},                 // league-level litigation climate
  "willingness": "active" | "open" | "reluctant" | "unknown"
}
```

### The `willingness` label — exact rule

```
if n_trades + n_seasons has < MIN_TRADE_EVIDENCE:      # see below
    "unknown"
elif trades_per_season >= 2 or relative_trade_rate >= 1.5:
    "active"
elif n_trades >= 1:
    "open"
else:                                # 0 trades over enough observed time
    "reluctant"
```

`MIN_TRADE_EVIDENCE`: evidence = a full observed season (or a partial
season past the trade deadline). Before the deadline of the first
observed season with `n_trades == 0`, the answer is `"unknown"` — an
owner who hasn’t traded by week 6 hasn’t revealed anything.
Concretely: `unknown` while `n_trades == 0` and now < the league’s
`trade_deadline`; after the deadline, 0 trades that season count as
reluctant evidence. (This is the September-credibility rule from C2/C4
applied to social data.)

Rationale for thresholds: in a 10–12 team league ~1–2 trades per team
per season is typical; 2+/season or 1.5× your league’s mean is a
genuinely live partner. One executed trade proves the door opens.

### Feature rationales (why these and not others)

- `relative_trade_rate` — raw counts mislead across leagues with
  different cultures; the within-league ratio is the comparable signal.
- `activity` — separates the two kinds of “never trades” (E7’s
  messaging should not pitch a manager who hasn’t logged in since the
  draft; that’s a different problem than trade-aversion).
- `deal_shapes` and `position_mix` — E2’s counters should prefer
  shapes/positions an owner has actually accepted before (consumption
  note below).
- `timing` — E8’s deadline lens wants to know who wakes up in
  November.
- `partners.concentration` — a high concentration owner deals with a
  friend, not a market; temper expectations.
- Explicitly **rejected**: bid-amount aggression (auction noise, not
  trade appetite), win-loss reactive features (record is visible
  directly; inferring “panic” from it is over-fitting on n≈1), and
  message/scoreboard scraping (no such data source exists — do not
  invent one).

## 4. Storage, module & API shape

**Computed on read, not stored.** The full computation is one pass
over one league’s transactions (hundreds of rows) — there is nothing
to precompute or invalidate. No new collection; `OwnerProfile.metrics`
is not touched (it’s rebuilt wholesale from draft picks by
`build_owner_profiles` — writing in-season data there would be
clobbered).

```python
# backend/models/trade_willingness.py
def willingness_features(transactions, league, alias_map, now) -> dict
    # PURE: per-owner feature dicts for one league (the testable core)
async def league_trade_willingness(engine, espn_league_id, season) -> dict
    # loads transactions + league + alias map, calls the pure core
```

Endpoint: `GET /inseason/league/{espn_league_id}/trade_willingness`
(standard envelope, added to both cached-only enforcement tests).
Response: `{week, owners: [{team_id, team_name, owner_name,
profile_key, trade_willingness: {...}}]}` sorted most-willing first
(`active` > `open` > `unknown` > `reluctant`, then
`trades_per_season`).

Config: none beyond reusing `RECENCY_DECAY`. Thresholds (2.0
trades/season, 1.5 relative rate) are module constants with the
rationale comment — they are definitional, not tunable knobs.

## 5. Consumption notes (for E2/E4/E7 — normative)

- E4’s report (not its trigger conditions) annotates each rival with
  the willingness label — a `reluctant` rival demotes nothing but the
  copy says “historically doesn’t trade”.
- E2 does not filter by willingness (fairness is fairness) but MAY use
  `deal_shapes` to order equally-scored counters toward shapes the
  partner has accepted before.
- E7’s messaging quotes it never (“I know you love 2-for-1s” is
  creepy); it only informs tone selection.
- The label never gates any computation — it is context, not a rule
  (BRAINSTORM §6 spirit).

## 6. Edge cases (all must be tested)

- Zero transactions synced → every owner `"unknown"`, league means 0,
  no division-by-zero.
- Owner with trades but no `owner_guid` → `team:`-keyed profile,
  present in output, excluded from cross-league merge.
- Co-owned teams (two GUIDs, one team): ESPN reports one primary GUID
  in `LeagueTeamInfo`; accept it, note nothing.
- A 3-team trade (ESPN supports them): items fan out to >2 teams —
  count it once per involved owner, partners = every other involved
  owner.
- Vetoed/pending trades → excluded from all owner features; vetoed
  count surfaces only in `veto_context`.
- Trade before `trade_deadline == None` (league without one) → the
  `unknown`-until-deadline rule falls back to week
  `>= DEADLINE_FALLBACK_WEEK = 11`.
- Mid-season team abandonment (owner leaves): their profile freezes;
  no special handling.

## 7. Worked example

League 111, week 9, 2026, deadline Nov 18 (not yet passed). Synced
transactions: 41 moves total. Owner G (team 7): 2 executed trades
(week 3: sent 2 RBs for 1 WR to team 2; week 8: 1-for-1 with team 2),
17 waiver adds. League totals: 5 executed trades / 10 teams.

- `n_trades=2`, `trades_per_season=2.0`, league mean `0.5` →
  `relative_trade_rate=4.0` → **active**.
- `deal_shapes`: n=2, one_for_one 0.5, two_for_one 0.5,
  avg_sent 1.5, avg_received 1.0.
- `position_mix`: sent RB×2, RB×1... shares {"RB": 1.0} on
  n_players_sent=3.
- `timing`: early 0.5, mid 0.5. `partners`: n_distinct=1,
  concentration=1.0 — every deal with team 2; the UI shows “only
  trades with Team Dos so far”.

Owner H (team 4): 0 trades, 2 adds, deadline not passed →
`willingness: "unknown"` (not `reluctant` — the season hasn’t asked
the question yet).

## 8. What the implementing session (Sonnet) must NOT do

- No new collection, no writes to `OwnerProfile`, no storage at all.
- No fetches — synced `LeagueTransaction` rows only.
- No multi-season ESPN backfill (see horizon caveat).
- No blending willingness into E1 values or E2 scoring — context only.
- No sentiment/messaging data sources.
- Keep `willingness_features` pure (transactions in, dicts out) —
  tests build transaction lists by hand exactly like
  `test_espn_league_adapter.py` builds views.
