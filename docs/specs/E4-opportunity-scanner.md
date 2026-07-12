# E4 — Proactive Trade Opportunity Scanner (spec)

> **Status:** Design locked (2026-07-11, Fable design pass). Implement
> verbatim in `backend/models/trade_opportunities.py`. Depends on E1
> landed (values/fit come from `ValuationContext`); reads D2’s
> `InjuryDesignation` when present but must work from ESPN roster
> `injury_status` alone (D2 may land later).

## 1. Design center: the cost of a false positive

This feature **interrupts the user via push**. A scanner that pings
“trade window!” on every questionable tag trains the user to ignore
it within two weeks, at which point the feature is worth less than not
existing. Every threshold below is therefore set to miss marginal
opportunities on purpose. The release valve for the marginal cases is
the **report endpoint** (§5): everything the scanner sees, at any
severity, is readable on demand — only the hard triggers page the
phone.

## 2. The trigger — all five conditions, AND-ed

The scanner runs per league after each sync pass. For the user’s team
`M` (`ESPN_MY_TEAMS[league]`; leagues not in the map are scanned for
the report but never push), evaluate every **rival** team `R` and every
player `p` on `R`’s current roster:

1. **A real injury event.** `p`’s effective status (D2
   `InjuryDesignation` for the live week if newer, else ESPN roster
   `injury_status`) is `out`, `injury_reserve`, or `doubtful` — AND
   this status is **new**: it differs from the status recorded at the
   previous scan (see §4 state). `questionable` NEVER triggers — it is
   one-week noise by definition and half the league is questionable by
   Friday.
2. **The player mattered to them.** `p` was in `R`’s starting (non
   BE/IR) lineup in the most recent completed week, OR E1
   `rate(p) >= STARTER_RATE_FLOOR` (8.0 pts/week). Backup injuries
   are not trade windows.
3. **The rival is actually hurt by it.** `R`’s best same-position
   alternative (their own roster, not currently starting at that
   position) has `rate` at least `RIVAL_GAP_POINTS` (3.0) per week
   below `rate(p)`. If they roster the handcuff or a same-caliber
   backup, there is no window.
4. **I have real surplus there.** Some player `s` at the same position
   on `M`’s roster satisfies BOTH: removing `s` costs `M`’s live-week
   starting lineup < `SURPLUS_COST_CEILING` (1.5) points (E2’s Stage-0
   one-week DP proxy, same constant), AND `player_value(s) >=
   SURPLUS_VALUE_FLOOR` (10.0 ROS points). A tradable piece must be
   both spare *and* attractive — spare bench fodder isn’t an offer,
   and an attractive starter isn’t spare.
5. **The market lens agrees.** A hypothetical 1-for-1 probe — `M` sends
   the *cheapest* qualifying surplus piece `s`, `R` sends their most
   movable piece at a position of `M`’s need (highest
   `player_value` among `R`’s players whose one-week removal cost to
   `R`’s post-injury lineup is < `SURPLUS_COST_CEILING`) — evaluates in
   E1 with `fit_delta_M > 0`. The probe is a feasibility check, not a
   recommendation: the notification names the window, not the trade
   (E2 generates actual proposals when the user engages).

Conditions 1–4 are cheap (values and one-week DPs from one shared
context per league); condition 5 runs only for candidates that survive
1–4 — typically zero or one per sync.

## 3. Severity and delivery

- **Hard trigger (push):** all five conditions AND status is
  `out`/`injury_reserve` (multi-week horizon — a real window). Creates
  a notification through `ensure_notification`:
  - `kind="trade_window"`,
  - `dedupe_key=f"tradewin:{espn_league_id}:{season}:{rival_team_id}:{injured_player_id}"`
    — note **no week**: one push per injury event per rival, ever;
    re-aggravations don’t re-page,
  - title `“Trade window: <rival team name> just lost <player> (<status>)”`,
  - body in C8 framing: the rival’s weekly gap at the position, my
    surplus piece by name and per-week value above replacement, and the
    playoff-window angle when `playoff_value` of my piece is a selling
    point. Never “they’re desperate” language — this text may end up
    quoted to a human via E7.
- **Soft rows (report only, never push):** candidates passing
  conditions 1–3 but failing 4 or 5, and `doubtful` cases passing all
  five. These appear in the report endpoint with
  `severity: "watch"` and the failed condition named
  (`"you have no spare piece at RB"`), because *why* it isn’t a window
  is the useful information.
- **Rate limit:** at most `TRADE_WINDOW_PUSHES_PER_WEEK` (2) hard
  triggers per league-week actually create notifications; further ones
  degrade to `severity: "watch"` rows with
  `"suppressed: weekly push budget reached"`. Counted per
  (league, season, week) from existing `trade_window` notifications.

## 4. Scan state — how “new status” is known

New model, one row per rostered player per league, replaced per scan:

```python
class InjuryScanState(Model):
    model_config = {"collection": "injury_scan_state"}
    espn_league_id: int
    season: int
    player_id: int
    status: Optional[str]      # effective status at last scan
    scanned_at: datetime
```

First scan of a season seeds state without triggering (everything is
“new” on day one — that’s bootstrap, not news). A player appearing on
a roster for the first time mid-season likewise seeds silently. This
state is scanner-internal — nothing else reads it.

## 5. Module & API shape

```python
# backend/models/trade_opportunities.py
async def scan_league(engine, espn_league_id, season) -> dict
    # runs the full scan, updates InjuryScanState, creates hard-trigger
    # notifications; returns the report (below). Never raises (B1 rule).
async def trade_opportunity_report(engine, espn_league_id, season) -> dict
    # read-only re-run of conditions WITHOUT state mutation or
    # notifications — what the GET serves
```

Report shape:

```json
{
  "week": 8,
  "my_team_id": 3,
  "opportunities": [
    {
      "severity": "window" | "watch",
      "rival_team_id": 7, "rival_team_name": "...",
      "injured": {"player_id": ..., "name": "...", "position": "RB",
                   "status": "out", "rate": 14.1},
      "rival_gap_per_week": 6.3,
      "my_surplus": [{"player_id": ..., "name": "...", "value": 18.2,
                       "weekly_cost_to_me": 0.4}],
      "probe": <E1 evaluate_trade dict> | null,
      "note": "why this is/isn't a push",
      "detected_at": "..."
    }
  ]
}
```

Endpoint: `GET /inseason/league/{espn_league_id}/trade_opportunities`
(in `inseason_api.py`, standard envelope, added to both cached-only
enforcement tests). The GET calls `trade_opportunity_report` — pure
Mongo reads, no state writes, so refreshing the page never consumes
push budget or mutates scan state.

Scheduler wiring: `InSeasonScheduler.run_now` calls `scan_league` for
each league after the league sync (and after D2’s ingest once that
lands), guarded by `TRADE_SCAN_ENABLED` env (default false), matching
every other scheduled producer.

Config: `STARTER_RATE_FLOOR=8.0`, `RIVAL_GAP_POINTS=3.0`,
`SURPLUS_VALUE_FLOOR=10.0`, `TRADE_WINDOW_PUSHES_PER_WEEK=2`,
`TRADE_SCAN_ENABLED=false` (`SURPLUS_COST_CEILING` is shared with E2 —
define it once in config, both import it).

## 6. Edge cases (all must be tested)

- **My own player gets hurt**: `M` is not a rival of itself; no
  trigger. (C7’s handcuff flags and D2’s downgrade alerts own that.)
- **Rival’s injured player is my trade target, not my surplus seller**:
  out of scope by design — this scanner finds windows to *sell into
  need*. Buy-low scanning is a different signal with a different
  false-positive story; do not bolt it on.
- **Two rivals lose players the same week**: both can push (budget 2).
- **Status oscillation** (out → questionable → out): the dedupe key has
  no week, so the second `out` does not re-push. Correct — same injury.
  A genuinely *new* injury to the same player later in the season is
  accepted collateral (rare; the report still shows it).
- **`ESPN_MY_TEAMS` missing this league**: report works (scan “as
  nobody” — conditions 4–5 skipped, everything caps at `watch`),
  pushes never fire.
- **No FreeAgentSnapshot / week 1 neutral data**: E1 handles the
  warnings; conditions still evaluate (rates fall back per E1 §3.1).
  First-scan seeding means week 1 never pushes anyway.
- **D2 not yet landed**: effective status = ESPN roster status alone.
  The `InjuryDesignation` read must be optional-by-construction.

## 7. Worked example

Week 9 sync, league 111, my team 3. Rival team 7’s RB1 (rate 14.1,
started week 8) goes `out` (was `active` in scan state → condition 1 ✓,
2 ✓). Team 7’s other RBs: rates 6.2 and 4.9 → gap 14.1−6.2 = 7.9 ≥ 3.0
(✓ 3). My roster: three startable RBs; removing RB “S” (value 18.2)
costs my live-week lineup 0.4 pts (< 1.5) and 18.2 ≥ 10 (✓ 4). Probe:
I send S, team 7 sends their WR4 (value 18.9, removal cost to them
0.8): E1 → `fit_delta_M = +5.1 > 0` (✓ 5). Status is `out` → hard
trigger, first push this week → notification:

> **Trade window: Big Truss just lost J. Starter (out)**
> Their RB spot drops ~7.9 points/week with him down, and you can
> spare S (18.2 ROS points above replacement, costs your lineup 0.4
> this week). If you deal him, target their WR depth — open the trade
> panel for counters.

Same scenario but my best spare RB is worth 6.0 ROS points → condition
4 fails → `watch` row: `"note": "window exists but your spare RB (6.0
ROS pts) is below the 10-point offer floor"`. No push.

## 8. What the implementing session must NOT do

- **No push on `questionable`, ever.** Not configurable-on; not a
  severity level. If the user wants noise, the report endpoint has it.
- **No auto-generated proposals in the notification.** The push names
  the window; E2 makes offers when asked.
- **No OR-ing of trigger conditions** and no “score” that trades one
  condition off against another — five ANDs, by design, reviewed only
  as a whole.
- **No new fetch surface.** Scanner runs off synced Mongo state inside
  the scheduler pass; the GET is pure reads.
- **Do not let the GET mutate scan state or create notifications** —
  idempotent reads, or the enforcement tests will be lying.
- **No cross-league aggregation** (a player hurt in league A says
  nothing about league B’s rosters — leagues scan independently).
- Tests: condition-by-condition unit tests with hand-built contexts
  (each condition failing alone), bootstrap-seeding behavior, dedupe
  across re-scans, push budget, and the GET’s purity (call twice,
  assert no notifications and unchanged state).
