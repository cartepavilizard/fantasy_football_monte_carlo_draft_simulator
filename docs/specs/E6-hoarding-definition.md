# E6 — Free-Agent Hoarding: “Worth Hoarding” Definition (spec)

> **Status:** Design locked (2026-07-11, Fable design pass). This is
> the frontier half of a [SPLIT] task: it defines the hoard score and
> its inputs. The cheap half (a Sonnet session) implements
> `backend/models/hoarding.py`, the weekly post-waivers report
> scheduling, and the UI against this spec verbatim. Depends on E1
> landed.

## 1. What hoarding is (and is not)

After waivers process (typically Wednesday morning), every remaining
free agent is claimable **first-come-first-served until Sunday**.
Hoarding is spending a bench spot *now* on a player you don’t strictly
need, because the option is about to disappear — either the player
breaks out (speculative upside for you) or a rival needs them this
week (denial). The cost is whoever you drop.

Not hoarding: streaming needs (C3 owns K/DST), handcuffing your own
starters (C7 owns it), and blocking rivals’ *injured-star handcuffs*
specifically (E5 owns that join; E6 must exclude E5’s cases rather
than double-flag them).

## 2. The definition — one inequality

A free agent `f` is **worth hoarding** exactly when:

```
hoard_value(f) − drop_cost > HOARD_MARGIN          (default 3.0 ROS points)
```

with all three quantities in E1 units, computed on one shared
`ValuationContext` per league.

### 2.1 `hoard_value(f)` — the better of two reasons to hold them

```
hoard_value(f) = max( my_gain(f),  DENIAL_WEIGHT × best_rival_gain(f) )
```

- `my_gain(f)` = E1 `fit_delta` of adding `f` to my roster (dropping
  the drop candidate, §2.2) — the speculative-upside lens. Uses the
  full-horizon team DP: a stashed IR player or a rising backup shows
  up here through availability curves and rates.
- `best_rival_gain(f)` = max over rival teams `R` of the fit delta of
  `R` adding `f` (dropping `R`’s min-value player). Restricted to
  rivals for whom `f` fills a *starting* hole — i.e. only count the
  rival’s `starting(w)` improvement, not their bench term (denial only
  matters when they’d actually play them).
- `DENIAL_WEIGHT = 0.5`: denying a rival a point is worth less than
  scoring one yourself — you only face each rival some weeks, and
  denial is probabilistic (they might not have claimed anyway). 0.5 is
  deliberately generous because the alternative reasons (they DO claim
  handcuffs and streamers) are unmodeled; it is env-tunable.
- **max, not sum**: the same roster spot can’t simultaneously realize
  your breakout and their denial at full weight; taking the better
  story avoids double-counting one bench slot into a phantom double
  value.

### 2.2 `drop_cost` — who goes, and what it costs

The drop candidate is my roster’s minimum-`player_value` player,
excluding:

- current starters (anyone in the live week’s optimal lineup, from one
  E1 week-`w0` DP),
- IR-slot players with `player_value > 0` (a valuable stash is not
  droppable fodder),
- active handcuffs of my own rostered starters (C7 map — insurance is
  not fodder),
- K/DST when the league requires starting them and I roster exactly
  one.

```
drop_cost = player_value(drop_candidate)      # context-free, floored at 0
```

Context-free on purpose: the fit-based cost of dropping a bench player
is usually ~0 (they weren’t starting), which would make hoarding
near-free and flag half the FA pool. Market value prices the option
you give up (someone else can claim your drop, too). If no legal drop
candidate exists (all starters/stashes/handcuffs), the roster is full
in the real sense: report `"no droppable player"` and flag nothing.

### 2.3 Candidate pool — keep the scan bounded

Rivals × FAs is the expensive product. Scan only free agents with a
*reason to be scanned*:

- top `HOARD_POOL_TOP_N` (20) FAs by E1 `rate`, plus
- any FA with a C4 rising-usage shift this week, plus
- any FA who is a C7 handcuff of any rostered starter league-wide
  (minus E5’s injured-star cases, excluded per §1).

Dedupe the union; compute `my_gain` for all, `best_rival_gain` only
for those within `HOARD_MARGIN` of clearing on `my_gain` alone
(everything else can only clear via denial — compute rival gains for
the top `HOARD_RIVAL_SCAN_N` (10) by rate). Worst case ≈ 20 + 10×(n_rivals)
team DP evaluations — acceptable weekly; not acceptable per-request,
which is why the report is computed by the scheduler pass and stored
(§3).

## 3. Output — the weekly post-waivers report

New model (replaced per league-week, like every sync scope):

```python
class HoardingReport(Model):
    model_config = {"collection": "hoarding_reports"}
    espn_league_id: int
    season: int
    week: int
    generated_at: datetime
    entries: List[dict] = []      # shape below
    note: Optional[str] = None    # e.g. "no droppable player"
```

Entry shape:

```json
{
  "player_id": ..., "player_name": "...", "position": "RB", "nfl_team": "...",
  "hoard_value": 9.4, "reason": "denial" | "upside",
  "my_gain": 2.1, "best_rival_gain": 14.6, "rival_team_id": 7,
  "drop": {"player_id": ..., "player_name": "...", "value": 1.8},
  "margin": 4.6,
  "sources": ["usage_shift" | "handcuff" | "top_rate"],
  "copy": "…C8-framed one-liner…"
}
```

Sorted by `margin` descending, capped at `HOARD_REPORT_MAX` (5) —
a hoarding report with 15 entries is a report nobody reads.

Copy rules (C8): quote volume/roles and ROS points (“their RB1’s
direct backup”, “22% target share last two weeks”, “9.4 ROS points
of denial value vs your 1.8-point drop”), never last week’s fantasy
points.

**Delivery:** one notification per league-week via
`ensure_notification`, `kind="hoarding_report"`,
`dedupe_key=f"hoard:{espn_league_id}:{season}:w{week}"`, created only
when the report has ≥ 1 entry, body naming the top entry and the
count. Individual entries never push separately — this is a weekly
digest, not an alert stream (E4 owns interrupting).

**Scheduling (cheap half):** runs in `InSeasonScheduler.run_now` after
sync + C4 ingest, guarded by `HOARDING_ENABLED` env (default false),
only on `HOARD_WEEKDAYS` (Wed–Sat: after waivers, before Sunday —
computed post-waiver state is stale Monday/Tuesday and the report
should not exist then; the endpoint serves whatever week’s report
exists with its `generated_at`).

**Endpoint:** `GET /inseason/league/{espn_league_id}/hoarding`
(standard envelope, both enforcement tests) — serves the stored
report; **never computes**. Recompute happens only via the scheduler
pass or `POST /inseason/sync`’s pipeline, keeping the expensive scan
off the read path.

Config: `HOARD_MARGIN=3.0`, `DENIAL_WEIGHT=0.5`, `HOARD_POOL_TOP_N=20`,
`HOARD_RIVAL_SCAN_N=10`, `HOARD_REPORT_MAX=5`, `HOARDING_ENABLED=false`.

## 4. Edge cases (all must be tested)

- **No FreeAgentSnapshot for the week** → no report, note says so.
- **Drop candidate is the FA’s own position and worse**: fine — that’s
  a straight upgrade, `reason: "upside"`; hoarding subsumes obvious
  adds. (C4’s alerts may name the same player; dedupe keys differ by
  design — one is a role alert, one is an action digest.)
- **`ESPN_MY_TEAMS` missing the league** → no “my roster” exists: skip
  the league entirely (hoarding is first-person by definition).
- **Two-entry conflict** (two FAs both “solved” by dropping the same
  fodder player): both entries list the same drop; the note says only
  one is executable. Do not solve the matching problem — the human
  picks.
- **E5 overlap**: an FA who is an *injured* rival starter’s handcuff is
  E5’s flag; exclude here (test the exclusion explicitly).
- **IR-stash FA** (dropped injured star in the pool): availability
  curve prices them; a big `my_gain` from playoff-week returns is
  exactly the brainstorm §2.6 stash-into-waivers case working.

## 5. Worked example

Week 8, Wednesday post-waivers. My drop candidate: bench WR, value 1.8.
FA pool scan:

- **RB “B” — rival’s RB1 backup** (not injured — E5 doesn’t claim it),
  rate 4.0. `my_gain = 0.6` (he never cracks my lineup).
  `best_rival_gain`: team 7 (whose RB1 he backs up) would start him in
  0 weeks *today*… but their RB2 is on bye weeks 10 and 13 →
  fit +3.9; denial-weighted 1.95. `hoard_value = max(0.6, 1.95) = 1.95`;
  `1.95 − 1.8 = 0.15 < 3.0` → **not flagged**. (Correct: healthy-backup
  blocking is almost never worth a roster spot — the margin exists to
  say so.)
- **WR “U” — C4 rising-usage flag** (target share 11% → 21%), rate
  jumped to 9.8. `my_gain = fit_delta = +7.2` (he starts in my flex
  most weeks). `hoard_value = 7.2`; `7.2 − 1.8 = 5.4 > 3.0` →
  **flagged**, `reason: "upside"`, copy: “U’s target share hit 21%
  over the last two weeks and he projects 9.8/week — worth 7.2 ROS
  points to your lineup against a 1.8-point drop. Claimable until
  Sunday.”

Report: 1 entry → one `hoarding_report` notification naming U.

## 6. What the implementing session (Sonnet) must NOT do

- **No redefinition of the inequality or its inputs** — tune via env,
  not by editing formulas.
- **No computing on the GET path** (stored report only) and no fetches
  anywhere.
- **No per-entry pushes**; one digest notification max per league-week.
- **No E5 duplication** — exclusion, with a test.
- **No summing my_gain + denial** (max only, §2.1).
- Tests: pure scoring with hand-built contexts (both `reason` branches,
  margin boundary, exclusions, drop-candidate selection), scheduler
  guard + weekday gate, report replacement idempotence, endpoint
  serves-stored-only.
