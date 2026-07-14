# Execution Plan: Draft-Time Additions & In-Season Management Module

> Derived from [`docs/BRAINSTORM.md`](./BRAINSTORM.md). This is the phased
> build plan; the brainstorm remains the feature reference. Each task carries
> an explicit **model-routing recommendation** (see legend) per the scoping
> requirement in the brainstorm.

Last updated: 2026-07-11

---

## Locked Decisions

| Decision | Choice |
| --- | --- |
| League platform | **All three leagues on ESPN** — single integration path; private-league auth via `espn_s2`/`SWID` cookies. Builds on the existing `data_sources/espn_*` adapters. |
| Build priority | **Draft-time features first** (drafts ~late August), in-season core ready by NFL week 1 (early September). |
| Alert delivery | **Claude Routines + push notifications to the Claude mobile app (Android)**, with an in-app notifications panel as the durable record. No email infrastructure built into the app. |

## Calendar Anchors (2026 season)

- **Now:** July 10 — dev window open.
- **Drafts:** ~late August → Phase A must be done by **Aug 15** (buffer for mock-draft testing).
- **Season opener:** early September (Wednesday opener — first lineup lock is *before* the usual Thursday). Phases B & C core by **week 1**.
- **Trade deadlines:** typically mid-November → Phase E fully live by **early October**.
- **Fantasy playoffs:** weeks 14–16 → playoff SOS tooling useful from **early November**.

## Model-Routing Legend

Per-task recommendation for who builds it:

- **[FRONTIER]** — needs full capability: architectural decisions, tricky
  algorithmic logic, ambiguous requirements, cross-cutting integration.
- **[CHEAP]** — grunt work a cheaper/faster model handles as well:
  boilerplate CRUD, simple data transforms, repetitive adapter code,
  straightforward test scaffolding, UI wiring to existing patterns.
- **[SPLIT]** — frontier designs the interface/algorithm, cheaper model
  fills in the repetitive implementation and tests.

Rule of thumb applied throughout: anything touching the simulation engine's
weighting, a new external data source's *strategy*, or a scoring/valuation
algorithm is [FRONTIER]; anything that is "one more endpoint / one more
table / one more panel shaped like an existing one" is [CHEAP].

**Model picker mapping** (what to select in the Claude Code model picker
when starting a session for a task):

- **[FRONTIER]** → the most capable model available: **Fable 5** if the
  picker offers it, otherwise **Opus**.
- **[CHEAP]** → **Sonnet**. (Haiku only for throwaway scripts, not code
  that ships.)
- **[SPLIT]** → a Fable 5/Opus session designs the core and leaves a spec;
  a follow-up Sonnet session executes the repetitive remainder against
  that spec — or the frontier session just finishes it if the remainder
  is small.

**Post-Fable update (2026-07-11):** Fable 5 access ends 2026-07-12. The
last Fable budget was spent on a design-only pass over every remaining
frontier-grade decision (Phase D/E/F specs — see those rows). From here:
**[FRONTIER] → Opus 4.8**, implementing against the Fable-authored specs
where they exist and designing fresh where they don't; [CHEAP]/[SPLIT]
remainders → Sonnet, unchanged. The specs are the contract: an Opus
session should treat a spec'd methodology as settled unless it finds a
concrete defect, in which case it documents the deviation in the task row.

Do not switch models mid-task; one task, one session, one model. And never
start a [CHEAP] task before the [FRONTIER]/[SPLIT] task it depends on has
landed its interface.

---

## Phase A — Draft-Time Additions (now → Aug 15)

Extends the existing draft simulator. No new external data required.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| A1 | **Tier-depletion scarcity engine**: given tier data per position, compute depletion state and produce a directional call — "reach now for last player in tier N at TE" vs. "safe to wait, tier N+1 has X options." Must consult Monte Carlo availability predictions, not just raw counts. | [FRONTIER] | **Done (2026-07-10).** `GET /draft/{draft_id}/scarcity` → `ScarcityReport`; consumer spec for A2 in `backend/models/scarcity.py`, engine in `scarcity_analysis` (app.py), tests in `backend/tests/test_scarcity.py`. |
| A2 | **Scarcity nudge UI**: surface A1's output in the draft view (banner/badge per position, reach-vs-wait indicator). | [CHEAP] | **Done (2026-07-10).** Draft room fetches `GET /draft/{id}/scarcity` via a lazy RTK Query hook (explicit refresh, never on render); one card per position with a call badge, tier/remaining counts, message, and an expandable at-risk player list with survival odds. |
| A3 | **Player tag data model + CRUD**: `sleeper` / `my_guy` / `avoid` tags on players; endpoints to set/clear/list; persistence in Mongo. | [CHEAP] | **Done (2026-07-10).** `Player.tag` (single optional tag, `backend/models/player.py`) plus `POST`/`DELETE /league/{id}/player/{name}/tag` and a `tag` filter on `GET /league/{id}/player`; tests in `backend/tests/test_player_tags.py`. |
| A4 | **Tag effects in the suggestion engine**: `avoid` filters out of *all* suggestions regardless of projection; `my_guy` wins ties when values are close (define "close"); `sleeper` boosts late-round consideration (define boost curve). | [FRONTIER] | **Done (2026-07-10).** Semantics + spec in `backend/models/suggestions.py`: close = max(3% of best, 5 pts); sleeper boost ramps 0 → +15% over the draft's back half, selection-only (simulation scoring stays projection-pure). Monte Carlo result gains a `suggested` map (name/tag/reason per position) for A5's UI; avoid also excluded from scarcity option counts. Tests in `backend/tests/test_tag_effects.py`. |
| A5 | **Tag UI**: tag/untag from player tables, tag filter chips, visual markers in suggestion lists. | [CHEAP] | **Done (2026-07-10).** `frontend/api/services/league.ts` adds `getPlayers` (with a `tag` filter), `tagPlayer`, and `untagPlayer`; the draft room (`frontend/app/draft-room/[id]/page.tsx`) gets per-row tag/untag icon controls, All/Sleepers/My Guys/Avoids filter chips backed by `?tag=`, a shared `TagBadge` marker reused in player rows, scarcity at-risk lists, and the Monte Carlo `suggested` panel (name, tag, and reason string per position). |
| A6 | **Homer check (draft scope)**: when a suggested pick is a Seahawks player, render a neutral side-by-side value comparison vs. the top non-Seahawks alternatives at that pick. | [SPLIT] | **Done (2026-07-10).** Methodology in `backend/models/homer.py` (`homer_check` is the single function C9 reuses); draft scope rides on `MonteCarloSimulationResult.homer_checks`. Display: `frontend/app/draft-room/[id]/page.tsx` (`HomerCheckPanel`) renders a subtle green badge on a homer-team suggestion that expands into one comparison table (projection / consensus rank / ADP vs. pick / tier, tag markers on names) with the backend's `note` as caption, verbatim, equal visual weight on every row. Tag-blind by design; no recommendation field. Tests in `backend/tests/test_homer_check.py`. |

**Phase A exit criteria:** full mock draft on sample data with tags and
scarcity nudges active; existing 18+ test suite still green; new tests for
A1/A3/A4.

**✅ Phase A complete (2026-07-10).** Exit criteria verified by
`backend/tests/test_phase_a_exit.py`: a full 196-pick mock draft on the
shipped sample CSVs, driven through the real pick endpoint with tags set,
checkpointing scarcity nudges (on-the-clock and final-pick), tag-aware
suggestions, and homer checks along the way. Suite fully green (175
tests), including a fix for HTTPException failing to pickle out of the
process pool.

---

## Phase B — In-Season Foundations: ESPN League Sync (Aug → Sep 1)

The load-bearing phase. Everything in C–F reads from what B provides.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| B1 | **ESPN league adapter (in-season)**: authenticated reads of rosters, matchups/scores, transactions, free agents, and lineup-lock times for all three leagues (`espn_s2`/`SWID`). Extends `data_sources/` patterns (transport, ratelimit, cache). | [FRONTIER] | **Done (2026-07-10).** `data_sources/espn_league.py`: direct lm-api-reads views (mTeam/mSettings, mRoster, mMatchup, mTransactions2, kona_player_info, proTeamSchedules_wl) over the shared Transport/RateLimiter seams; cookies from env only. Failure modes are the design: 401/403 → `EspnAuthError` logged as `error_kind='auth'`; sections fetch/persist/log independently; a Mongo scope is replaced only after a successful fetch, so cookie expiry degrades to clearly-stale cached data with visible warnings (`league_freshness`), never a crash or stale-as-fresh. On-demand refresh: `POST /inseason/sync` (the ONLY in-season route that touches ESPN). Tests: `tests/test_espn_league_adapter.py`. |
| B2 | **In-season data models**: leagues, rosters, weekly matchups, transactions, player-week stats, snap counts, target shares, practice reports, injury designations. | [SPLIT] | **Done (2026-07-10) — frontier half covered the whole task.** Schema in `models/inseason.py` with per-consumer design notes (C1–C6, D2, E1–E8, F2 mapped in the module docstring): `InSeasonLeague`, `TeamWeekRoster`, `WeeklyMatchup`, `LeagueTransaction`, `FreeAgentSnapshot`, `PlayerWeekUsage` (league-independent; C4's ingestion fills it), `PracticeReport` + `InjuryDesignation` (D2 fills them), `ProGame` + `week_lock_times()`, `LeagueSyncLog` + `league_freshness()`. No migrations needed (new collections). No Sonnet remainder — C4/D2 write into the settled schema when they land. |
| B3 | **Background pull scheduling**: extend the existing `scheduler.py` refresh loop to in-season cadence (e.g., daily baseline; tighter Wed–Sun). On-demand refresh endpoint per league. | [CHEAP] | **Done (2026-07-10).** `InSeasonScheduler` in `scheduler.py`, structured line-for-line like `RankingsScheduler` (sleep-first loop, failures recorded in `last_error` and never raised, `configure()`/`status()`/`run_now()` surface). Each pass calls `sync_all_leagues(engine, DRAFT_YEAR)` then `ensure_lock_reminders` for every league with a known current week. Cadence re-evaluated every wake-up (`current_interval_hours()`): gameday interval (`INSEASON_SYNC_GAMEDAY_INTERVAL_HOURS`, default 6h) Wed–Sun, baseline (`INSEASON_SYNC_INTERVAL_HOURS`, default 24h) otherwise; `INSEASON_SYNC_ENABLED` defaults false so dev/test never fetches. Wired in `app.py` exactly like `rankings_scheduler` (startup/shutdown handlers, `GET`/`POST /inseason/schedule`). Tests: `tests/test_inseason_scheduler.py`. |
| B4 | **Multi-league + team perspective switcher**: league selector and team-perspective dropdown (any team in any league, e.g., brother-in-law's). **Hard constraint: cached data only — switching perspective never triggers scrapes or Grok prompts.** | [SPLIT] | **Done (2026-07-10).** Backend core: `inseason_api.py` — every read under `GET /inseason/*` (overview, roster-by-perspective, matchups, transactions, free_agents, locks) is Mongo-only and carries a `freshness` + `warnings` envelope. The constraint is enforced structurally, not by convention: the module (and its whole import closure) contains no `data_sources` import — `tests/test_inseason_api.py` fails the build if that changes and also drives every GET with the HTTP transport rigged to raise. Refresh exists only as an explicit POST in `app.py`. Frontend (cheap half): `frontend/api/services/inseason.ts` (RTK Query service for every `/inseason/*` GET plus the one `syncLeague` mutation) and `frontend/app/inseason/` (league + team-perspective switcher driven by `/inseason/overview`, roster/matchups/transactions/free-agents/locks views, a `StalenessBanner` rendered from each response's `warnings` on every card, and a visually separate "Sync now" button hitting `POST /inseason/sync`). Verified live: backend suite green (220 passed) untouched; frontend builds cleanly; browser-driven check against a seeded two-league backend confirmed every league/team switch issues only `GET /inseason/*` (network tab captured), the stale second league surfaced its banner correctly, and clicking "Sync now" made a real `POST /inseason/sync` that hit live ESPN (confirmed by real NFL schedule data coming back) while leaving the cached-only reads unaffected. Real ESPN sync against the three configured leagues (`ESPN_LEAGUE_IDS`) was not exercised end-to-end in this dev environment because no local MongoDB is running (`LOCAL=true` hardcodes `mongodb://localhost:27017` in `app.py`, no `mongod`/Docker available here) — that's an environment gap, not a code issue. |
| B5 | **Notifications backbone**: in-app notifications collection + panel (the durable record) and the Claude Routine templates that read app state and push to the Android Claude app (first-lock reminder incl. Wednesday opener, final-lock reminder). | [SPLIT] | **Core + cheap half done (2026-07-10).** `models/notifications.py`: durable `notifications` collection, `ensure_notification()` dedupe every future producer (C4/D2/E4/E8) inherits, and `ensure_lock_reminders()` — first lock is the week's earliest kickoff, so the Wednesday opener needs no special case; runs on every sync and is idempotent. App↔Routine contract (documented in the module): Routine polls `GET /notifications/pending?channel=push`, pushes, then `POST /notifications/{id}/ack` — at-least-once delivery, idempotent ack. Panel CRUD (`notifications_api.py`): `GET /notifications` (newest first, `unread_only` + `kind` filters), `POST /{id}/read`, `POST /read_all`, `DELETE /{id}` — all independent of the ack/pending contract. Tests: `tests/test_notifications.py` (27 cases). Frontend: `frontend/api/services/notifications.ts` (RTK Query service, wired into `api/store.ts`) and `frontend/components/notifications-panel.tsx` — a navbar bell (`components/navbar.tsx`) with unread badge, dropdown panel listing notifications newest-first with kind-filter chips, per-item mark-read/delete, mark-all-read, and a distinct "pushed to phone" marker (📱) so acked (delivered) and read (seen in-app) stay visually separate even after both are true. Polls every 60s; refetches on open. Verified live: backend suite green (227 passed); frontend builds cleanly; browser-driven check against a seeded mock-engine backend (real MongoDB still unreachable in this dev environment) confirmed unread badge count, kind filtering, mark-read, delete, and mark-all-read all round-trip correctly and the pushed/read states render independently. Remaining for exit: the actual Claude Routine (scheduled, hitting `pending`/`ack`) and a live run against real ESPN-synced leagues. |

**Phase B exit criteria:** all three leagues syncing on schedule; perspective
switcher works offline from cache; a test Routine delivers a push to the
phone.

**Phase B core status (2026-07-10):** B1 done; B2 done; B3 done; B4 fully
done (backend core + frontend switcher); B5 backbone + panel CRUD + frontend
done (see task notes). Suite at 227 passed / 0 failed. Remaining for exit:
B5's live Claude Routine, then a live sync test against the three real
leagues (`ESPN_S2`/`ESPN_SWID`/`ESPN_LEAGUE_IDS` are already set) once run
against an environment with a reachable MongoDB.

**Phase B exit review (2026-07-10):** **Live sync verified against all three
real leagues** with the env cookies (real ESPN network, in-memory Mongo):
every section OK — 10/12/12-team leagues, 70/84/84 matchups, 300 free
agents each, plus the 272-game 2026 pro schedule for lock times; freshness
clean on all sections; `auth_expired=false`. That closes exit criteria 1–2
(three leagues syncing; cached-only perspective reads are enforced
structurally and test-guarded).

**Hosting resolved (2026-07-10):** MongoDB **7.0.28** now runs as an
auto-start Windows service on the dev laptop (8.x does not support
Windows 10 — that was the failed-install mystery; do not upgrade past 7.0
on this machine). Docker stays unnecessary: the stack runs natively
(Mongo service + uvicorn + Next). All three leagues are synced into the
real `fantasy-football` database, and `ensure_lock_reminders` was
exercised against it (0 created — September kickoffs are outside July
lead windows, as designed). The one open exit criterion remains the live
Routine push to the phone, now unblocked: it needs (a) the backend
running on a schedule or at boot, and (b) a scheduled Claude task that
polls `/notifications/pending` and pushes — set both up closer to the
season alongside enabling `INSEASON_SYNC_ENABLED`.

---

## Phase C — Lineup & Strategy (Sep, core by week 1)

Week-1-critical tasks first (C1–C4, C6); the rest can land during September.

**Phase C frontier session (2026-07-11):** C1, C2, C6 fully done; C4 and
C7 cores done with the cheap halves spec-ed in their modules (see task
notes). Suite at 280 passed / 0 failed at session end.

**✅ Phase C complete (2026-07-11).** The Sonnet follow-up sessions
delivered C3, C5, C8, C9, C4's nflverse ingestion, C6's UI wiring, and
C7's flagging + UI against the frontier specs. Suite at 322 passed /
0 failed; frontend builds clean. Remaining operational items (not code):
the August 2026 human review of the handcuff seed table, setting
`ESPN_MY_TEAMS`, and enabling `USAGE_INGEST_ENABLED` +
`LINEUP_PULL_ENABLED` alongside the other schedulers at season start.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| C1 | **Full lineup optimizer**: best legal lineup per league from projections + matchup adjustments; on-demand refresh plus scheduled Thursday-morning pull so decisions use fresh data. | [FRONTIER] | **Done (2026-07-11).** Projection source decided (contract in `models/lineup.py`): ESPN weekly projections as synced into roster entries — league-scoring-correct per league, week-aware, zero new fetch surface; season blend rejected (no weekly decomposition). Swappable behind the `weekly_projections()` seam / `optimize_lineup(projections=...)` override. Exact DP assignment over slot instances (no greedy flex-overlap bugs), C2 tilt applied, output = optimal lineup + moves + delta + per-player matchup context + warnings. `GET /inseason/league/{id}/lineup` (cached-only, enforcement-tested). `LineupPullScheduler` (Thu 7am default, `GET/POST /inseason/lineup_schedule`, `LINEUP_PULL_*` env) syncs then leaves a deduped `lineup_review` notification per league, quoting the delta for teams in `ESPN_MY_TEAMS`. Tests: `test_lineup_optimizer.py`, `test_lineup_scheduler.py`. |
| C2 | **Matchup strength analysis**: opponent-vs-position strength feeding C1's adjustments and shown as context on lineup calls. | [FRONTIER] | **Done (2026-07-11).** Methodology contract in `models/matchup_strength.py`: fantasy points allowed per defense/position from synced roster actuals + pro schedule (no new source); ratios normalized per league-week sample; leagues average within a week (coverage ≠ evidence); shrinkage to neutral with a 4-week prior so **week 1 is exactly neutral** and September tilts stay gentle; confidence reported. Applied as a capped tilt (`alpha` 0.5, max ±10%) since ESPN weekly projections partially price matchups. `GET /inseason/matchup_strength`. Sync now backfills prior-week rosters so completed weeks include Mon-night finals. Tests: `test_matchup_strength.py`. |
| C3 | **K/DST streaming recommendations**: weekly rank of available kickers/defenses by matchup, from C2's data + B1's free-agent list. | [CHEAP] | **Done (2026-07-11).** `models/streaming.py` (`streaming_recommendations`): latest `FreeAgentSnapshot` filtered to K/DST, joined to `defense_position_strength()` via each player's week opponent, ranked by `matchup_adjusted` projection with multiplier tie-break; matchup context (multiplier/rank/confidence) on every row. C9 rides here (see C9 row). `GET /inseason/league/{id}/streaming` (in both cached-only enforcement tests) + streaming panel in the in-season UI. Tests: `test_streaming.py`. |
| C4 | **Snap count & target share trends + usage-shift alerts**: ingest weekly usage data; detect meaningful shifts (rising backup, shrinking role) and raise alerts through B5. Process-over-results: alerts framed on volume/opportunity, never one-week points. | [SPLIT] | **Core done (2026-07-11).** Source decided: **nflverse CSV releases** (snap counts + weekly player stats incl. `target_share`). Signal contract in `models/usage_shifts.py`: current week vs mean of last 2–4 prior weeks; thresholds 0.12 snap / 0.07 target share; floors 0.15/0.10 drop roster churn; min 2 prior weeks → first alert week 3; both directions. Alerts dedupe via `ensure_notification`, restricted to rostered/free-agent players, copy volume-only. `GET /inseason/usage_shifts` serves the trends read. **Cheap half done (2026-07-11):** `data_sources/nflverse.py` (`NflverseUsageAdapter` + `ingest_usage`, snap-counts CSV as spine merged with player-stats CSV, replace-per-week, per-source failure logging, team-abbrev normalization) wired into `InSeasonScheduler.run_now` behind `USAGE_INGEST_ENABLED` (default false) — ingests + alerts for the most recently *completed* week only. Usage-trends view in the in-season UI. Tests: `test_nflverse.py`, `test_inseason_scheduler.py`. |
| C5 | **Playoff schedule analysis**: weeks 14–16 strength of schedule per position, per team; feeds roster and trade decisions. Needed by early Nov, can land later in Sep. | [CHEAP] | **Done (2026-07-11).** `models/playoff_sos.py`: per NFL team, `PLAYOFF_SOS_WEEKS` (env, default 14-16) opponents from `ProGame`, scored per position by averaging `defense_position_strength()` multipliers; rank per position; per-fantasy-roster view joins current rosters (`playoff_sos_for_league`). Confidence propagates as the *weakest* sampled week and the early-season all-neutral case says so explicitly. `GET /inseason/playoff_sos` (optionally `?espn_league_id=`) + report view in the UI. Tests: `test_playoff_sos.py`. |
| C6 | **Lineup-locking strategy**: for early-game players (Thu/Wed opener), suggest flex/bench placement that locks early and preserves Sunday flexibility. | [SPLIT] | **Core done (2026-07-11) — both rules defined and implemented in `models/lineup.py`.** Rule 1 (free, always applied): among equal-total lineups, a second DP puts early-locking players in restrictive slots and late-locking players in flex-type slots, so flexible slots stay unlocked longest; Wednesday opener needs no special case. Rule 2 (advice only): any starter locking ≥36h before final lock with a later-kicking bench alternative within 1.0 pt gets a `lock_advice` entry quoting the exact cost (margin ≈ option value). **Cheap half done (2026-07-11):** lineup optimizer view renders optimal-vs-current with moves/delta, kickoff badges on early-locking starters, matchup-context chips with confidence caveats, and `lock_advice` as advice cards quoting `cost_points` and the note verbatim (never auto-applied). Tests: `test_lineup_locking.py`. |
| C7 | **Handcuff strategy**: maintain a starter→direct-backup map; flag when a key starter's handcuff is available and worth rostering. | [SPLIT] | **Core done (2026-07-11).** Sourcing decided: **curated seed table** over depth-chart inference (inference is weakest in Sept when handcuff value peaks; committees encoded by omission; rationale in `models/handcuffs.py`). ~24 RB pairs seeded from end-of-2025 depth charts — **needs a human review pass in Aug 2026**. Additive-only seeding, manual-marked edits, soft deletes (re-seed can't resurrect a committee call). CRUD live: `GET/POST /inseason/handcuffs`, `POST /inseason/handcuffs/seed`, `DELETE /inseason/handcuffs/{starter}`. **Cheap half done (2026-07-11):** `available_handcuff_flags()` joins the map against rosters + free agents (priority=high on questionable/doubtful/out starters), `ensure_handcuff_notifications()` alerts only on high priority (deduped per league-week-starter, insurance framing), `GET /inseason/league/{id}/handcuffs`, roster-view chips + handcuff management panel over the CRUD. SEA handcuffs carry C9's homer check. Tests: `test_handcuffs.py`. |
| C8 | **Process-over-results framing**: recommendation copy across the module cites volume/opportunity, flags single-game variance explicitly ("1 catch on 9 targets" ≠ "bad game"). | [CHEAP] | **Done (2026-07-11).** Convention was already followed at every notification producer (`_shift_copy`/C4, `_handcuff_notification_copy`/C7, `ensure_lineup_review`/C1, `lock_advice`/C6 all quote projections or volume, never actual points) and every ranking (C3's streaming list, C5's playoff SOS) sorts by matchup-adjusted projection, not results — audited, no changes needed there. New: `variance_note()` in `models/usage_shifts.py` is the single-game variance flag itself — targets ≥ `USAGE_VARIANCE_TARGET_FLOOR` (6) with a catch rate ≤ `USAGE_VARIANCE_CATCH_RATE_CEILING` (0.35), receptions derived from `touches - carries` since `PlayerWeekUsage` has no reception field; attached to every `detect_usage_shifts()` row as `"variance"`. Framing copy lives in the frontend's `VarianceFlag` component (`frontend/components/variance-flag.tsx`) — shared and importable by any future PlayerWeekUsage-driven view, not just the usage trends table it's wired into now. Tests in `test_usage_shifts.py`. |
| C9 | **Homer check (in-season scope)**: reuse A6's neutral comparison whenever a Seahawks player is suggested as a waiver add or trade piece. | [CHEAP] | **Done (2026-07-11).** `homer_check()` reused unmodified (one methodology, new call sites): C3's streaming rows and C7's handcuff flags attach the neutral comparison whenever the player is HOMER_TEAM, via a small `FreeAgentEntry` adapter in `models/streaming.py` (`pick_number=None`); rendered inline (`HomerCheckNote`) on streaming and handcuff rows with the backend's factual `note` verbatim. Covered in `test_streaming.py` and `test_inseason_api.py`. |

---

## Phase D — Injury & News (Sep)

> **Design pass (2026-07-11, Fable):** every frontier-grade decision in
> Phases D/E/F is now spec'd in [`docs/specs/`](./specs/). Implementation
> routing below is post-Fable: **[Opus 4.8]** for the intricate builds,
> **[Sonnet]** for everything with a settled interface. Recommended
> session order at the bottom of this document.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| D1 | **Beat writer directory**: team→writer mapping (Seahawks → Brady Henderson, etc.), editable, seeded for all 32 teams. | [Sonnet] | **Done (2026-07-14).** `models/beat_writers.py` (`BeatWriter` + `seed_beat_writers`/`list_beat_writers`/`upsert_beat_writer`/`delete_beat_writer`/`get_beat_writer`), following C7's handcuff-table pattern verbatim (insert-missing-only seed, manual-source marking, soft delete survives re-seed). Seeded for all 32 teams from this codebase's training-data knowledge — **needs a human review pass** before relying on it, same caveat as the handcuff seed table. CRUD under `/inseason/writers` in `inseason_api.py`; small management panel on the in-season page (seed button + table + add/delete form). D3's `beat_check` prompt template joins on it by `nfl_team`. 6 new tests in `test_beat_writers.py`. |
| D2 | **Official practice participation ingestion**: full/limited/DNP from official NFL injury reports as an early signal ahead of ESPN designation updates; attach to player records; feed C4-style alerts on downgrades. | [SPLIT] → **[Sonnet]** | **Done (2026-07-14).** `data_sources/nflverse_injuries.py` (`NflverseInjuriesAdapter` + `ingest_practice_reports` + `ensure_practice_downgrade_notifications`) per [`docs/specs/D2-practice-report-ingestion.md`](./specs/D2-practice-report-ingestion.md); `GET /inseason/practice_reports`; scheduler wiring behind `PRACTICE_INGEST_ENABLED` (default false, live-week not completed-week). **Mapping-table update found at implementation time:** the current-season `injuries_{season}.csv` (verified against `injuries_2025.csv`) no longer carries `date_modified` — nflverse added `season_type` and dropped it. `report_date` falls back to the ingest run's own day when the column is absent (same-day reruns upsert, a new day starts a new trail row); the column is still parsed when present, so historical files with it keep working. Also generalized `league_freshness()`'s single `pro_schedule` league-independent check into a small set so `practice_reports`' staleness surfaces correctly (the one necessary addition beyond the SYNC_SECTIONS list-add). 36 new tests (29 in `test_nflverse_injuries.py`, 5 scheduler-wiring, 2 API); full suite green (358 passed). |
| D3 | **Manual Grok bridge**: generate a targeted prompt (e.g., "What has [beat writer from D1] said about [player] in the last 48 hours?") for the user to run in their free xAI account; paste-back box ingests the response and attaches it to the player as a sourced note. **No automated or paid API calls — by design.** | [SPLIT] → **[Sonnet]** | **Done (2026-07-14).** Implements [`docs/specs/D3-grok-bridge-parsing.md`](./specs/D3-grok-bridge-parsing.md) verbatim: `models/player_notes.py` (`PlayerNote` + `parse_grok_paste` — deterministic last-block extraction, never raises; `compute_skepticism` — staleness off `NEWEST_SOURCE`, conflicts vs D2's official designation/practice participation and Grok's own speculation label, requested-vs-answered player mismatch; `build_grok_prompt` — the three templates, joining D1's writer directory by `nfl_team`, 404 on an unresolvable player). `verified` is always `False`; enforced by a structural import-graph test (no module outside `player_notes.py`/`inseason_api.py` may import `PlayerNote`). Five endpoints under `/inseason/` (`grok_prompt`, `player_note/parse`, `player_note` POST/GET, `player_note/{id}` DELETE) — the save endpoint always re-derives from `raw_text` server-side, never trusts the client's preview round-trip. Paste-back UI: generate → paste → preview (parsed/stale/conflict badges + manual status/summary fallback) → save, plus a per-player saved-notes list. One real implementation choice beyond the spec's literal model: `newest_source_date` is stored as `datetime.datetime` at midnight, not `datetime.date` — BSON has no date-only type (same convention as every other date-like field in this codebase); the parser's own contract still returns a plain `date`. 45 new tests in `test_player_notes.py` covering every §7 edge case, the skepticism matrix, endpoint round-trips, and the quarantine test; full suite green (409 passed). |
| D4 | **Kickoff reminders (live)**: turn on the B5 Routines for the real season — pre-first-lock (Wednesday opener aware, from B1's lock times) and pre-final-lock pushes, including "you have an injured/BYE player starting" checks. | [Sonnet] | Configuration of B5's backbone, not new machinery. Do at season start alongside enabling `INSEASON_SYNC_ENABLED` / `USAGE_INGEST_ENABLED` / `LINEUP_PULL_ENABLED`. |

---

## Phase E — Trade Management (Sep → early Oct)

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| E1 | **Trade grading**: score both sides of a proposed trade from projections/rankings (rest-of-season value, positional need, playoff SOS from C5, IR-stash value); show the value gap in plain terms. | [FRONTIER] → **[Opus 4.8]** | **Spec'd (2026-07-11, Fable design pass): [`docs/specs/E1-trade-valuation.md`](./specs/E1-trade-valuation.md).** Defines the Phase-E value units (ROS points above replacement + roster-context fit delta) that E2/E4/E5/E6/E7/E8 all consume; IR-stash value is baked into the availability curve (brainstorm §2.6). Implement the spec verbatim — E1 must land before any other E task starts. |
| E2 | **Counterproposal generator**: given a lopsided trade, search both rosters' surplus/need for tweaks that close E1's gap; propose 1–3 fair counters. | [FRONTIER] → **[Opus 4.8]** | **Spec'd (2026-07-11, Fable design pass): [`docs/specs/E2-counterproposal-generator.md`](./specs/E2-counterproposal-generator.md).** Single-move anchored search (ADD/REMOVE/SWAP) with a four-stage pruning funnel over E1's pure evaluation functions; deterministic output. Requires E1 landed first. |
| E3 | **Trade-willingness owner profiles**: extend the existing owner-tendency profiling (`profiling.py`) with historical trade behavior — who trades, how often, what shapes of deals. | [SPLIT] → **[Sonnet]** | **Frontier half done — features spec'd (2026-07-11, Fable design pass): [`docs/specs/E3-trade-willingness-features.md`](./specs/E3-trade-willingness-features.md).** Computed-on-read (no storage), profiling.py ground rules (n-counts, recency weights, inferred labels), `unknown`-until-deadline rule for zero-trade owners. Sonnet implements the transform + endpoint + UI verbatim. |
| E4 | **Proactive opportunity scanner**: cross-reference league-wide injury news (D2/C4 signals) against all rosters (B4's cache) to flag trade windows — e.g., rival's starter goes down, you hold surplus there. | [FRONTIER] → **[Opus 4.8]** | **Spec'd (2026-07-11, Fable design pass): [`docs/specs/E4-opportunity-scanner.md`](./specs/E4-opportunity-scanner.md).** Five AND-ed trigger conditions (questionable never pushes), push budget of 2/league-week, everything else degrades to an on-demand report endpoint; scan state seeds silently on first pass. Requires E1; reads D2 opportionally. |
| E5 | **Blocking plays**: flag handcuffs (C7's map) of *rivals'* injured stars worth grabbing purely to deny them. | [Sonnet] | Join of C7's handcuff map with D2's injury signals over rivals' rosters. Boundary with E6 is defined in [`docs/specs/E6-hoarding-definition.md`](./specs/E6-hoarding-definition.md) §1 (E5 owns injured-star handcuffs; E6 excludes them). Needs D2 landed. |
| E6 | **Free agent hoarding**: after waivers process each week, flag speculative adds/drops worth making before Sunday to keep players off the board. | [SPLIT] → **[Sonnet]** | **Frontier half done — "worth hoarding" spec'd (2026-07-11, Fable design pass): [`docs/specs/E6-hoarding-definition.md`](./specs/E6-hoarding-definition.md).** `max(my_gain, 0.5 × best_rival_gain) − drop_cost > 3.0` in E1 units; bounded candidate pool; stored weekly report + one digest notification; E5's injured-star-handcuff cases excluded. Sonnet implements against the spec after E1 lands. |
| E7 | **Trade messaging generator**: friendly, non-salesy message framing a proposal/counter with actual projection and matchup numbers from E1. | [Sonnet] | Templating over E1's `evaluate_trade` output (quote per-week numbers per E1 spec §4.3's copy rules; E3's willingness informs tone only, never quoted). Needs E1 landed. |
| E8 | **Trade deadline awareness**: per-league deadline tracking; buy/sell window flags in the weeks before it (contender vs. rebuilder lens per team record). | [Sonnet] | Date math (`InSeasonLeague.trade_deadline`) + record check (`LeagueTeamInfo` wins/losses) feeding B5 notifications; quotes E1's `playoff_value` component for the buy/sell lens when E1 is available. No E1 hard dependency. |

---

## Phase F — Strategy Awareness Flags (Oct, opportunistic)

Contextual flags, **not hard rules** — surfaced inline where relevant.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| F1 | **Stacking awareness**: flag QB + pass-catcher correlation opportunities in draft suggestions and trade evaluations. | [SPLIT] → **[Sonnet]** | **Frontier half done — correlation weights spec'd (2026-07-11, Fable design pass): [`docs/specs/F1-stacking-correlation.md`](./specs/F1-stacking-correlation.md).** Fixed ρ table (QB+WR 0.40, QB+TE 0.35, mild rows for honesty), σ ≈ 0.45 × weekly projection, flag quotes "extra weekly swing" points; two call sites (draft `suggested` map, E1 trade report decoration); provably zero effect on any ranking or verdict. Sonnet adds the flags verbatim (trade call site after E1 lands). |
| F2 | **Bye week planning**: warn on bye clustering at draft time; preview thin weeks in-season. | [Sonnet] | Schedule joins over data B1 already has. |
| F3 | **Anti-correlation awareness**: flag rostering players who compete for the same touches (same-backfield RBs outside the C7 handcuff case). | [Sonnet] | Reuses C7's depth relationships with an inverted lens. Same flag-only discipline as F1 (see that spec's must-nots — zero effect on rankings/verdicts). |

---

## Cross-Cutting Notes

- **IR strategy** (brainstorm §2.6) is intentionally not one task: IR-stash
  value is an *input* to E1 (trade grading), E6 (hoarding drop decisions),
  and waiver suggestions. Frontier bakes it into E1's valuation; the rest
  inherit it.
- **Homer check** is one methodology (A6) with three call sites (draft,
  waivers, trades) — build once, reuse.
- **The perspective switcher's cached-only constraint** (B4) is enforced at
  the API layer, not the UI, so no future feature can accidentally violate it.
- **Routing summary:** of 31 tasks — 8 [FRONTIER], 12 [CHEAP], 11 [SPLIT].
  The [CHEAP] and the cheap halves of [SPLIT] tasks are safe to hand to a
  faster/cheaper model *after* their frontier-designed interfaces exist;
  don't reorder a [CHEAP] task ahead of the [FRONTIER] task it consumes.

## Suggested Session Cadence

1. One frontier session per phase to design the [FRONTIER]/[SPLIT] cores and
   leave precise specs behind in the code and this doc.
2. Cheaper-model sessions execute the [CHEAP] backlog against those specs.
3. A short frontier review pass at each phase's exit criteria before moving on.

## Phase D/E/F Implementation Session Order (post-design-pass, 2026-07-11)

Every remaining decision is spec'd in [`docs/specs/`](./specs/); this is
the order the implementing sessions should run, honoring dependencies
and the calendar (Phase D live in Sep; E fully live by early Oct):

1. **E1** [Opus 4.8] — [`E1-trade-valuation.md`](./specs/E1-trade-valuation.md).
   The long pole; six tasks consume its units. Start first.
2. **D2** [Sonnet] — [`D2-practice-report-ingestion.md`](./specs/D2-practice-report-ingestion.md).
   No dependencies; September-critical. Can run in parallel with E1.
3. **D1 + D3** [Sonnet, one session] — directory, then
   [`D3-grok-bridge-parsing.md`](./specs/D3-grok-bridge-parsing.md) on top of it.
4. **E3** [Sonnet] — [`E3-trade-willingness-features.md`](./specs/E3-trade-willingness-features.md).
   Independent of E1 (reads only LeagueTransaction); lands the
   willingness labels E4's report annotates with.
5. **E2** [Opus 4.8] — [`E2-counterproposal-generator.md`](./specs/E2-counterproposal-generator.md).
   Needs E1's pure evaluation path.
6. **E4** [Opus 4.8] — [`E4-opportunity-scanner.md`](./specs/E4-opportunity-scanner.md).
   Needs E1; reads D2 and E3 opportunistically (both in by now).
7. **E6 + E5** [Sonnet, one session] — [`E6-hoarding-definition.md`](./specs/E6-hoarding-definition.md)
   plus the E5 join, so the E5/E6 exclusion boundary is built and
   tested together. Needs E1 + D2.
8. **E7 + E8** [Sonnet, one session] — messaging templates over E1's
   output; deadline flags.
9. **F1 + F2 + F3** [Sonnet, one session] —
   [`F1-stacking-correlation.md`](./specs/F1-stacking-correlation.md)
   (both call sites — E1 exists by now), byes, anti-correlation.
10. **D4** [Sonnet, short] — season-start configuration: live Routines
    + enabling the scheduler env flags, alongside B5's outstanding live
    push test and the C7 handcuff-table human review (Aug).

A short Opus review pass at Phase E exit (after step 8) is worth its
cost: it's the phase where spec drift would compound.
