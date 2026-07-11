# Execution Plan: Draft-Time Additions & In-Season Management Module

> Derived from [`docs/BRAINSTORM.md`](./BRAINSTORM.md). This is the phased
> build plan; the brainstorm remains the feature reference. Each task carries
> an explicit **model-routing recommendation** (see legend) per the scoping
> requirement in the brainstorm.

Last updated: 2026-07-10

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

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| C1 | **Full lineup optimizer**: best legal lineup per league from projections + matchup adjustments; on-demand refresh plus scheduled Thursday-morning pull so decisions use fresh data. | [FRONTIER] | Optimization across roster slots with constraints; the projection-adjustment model is the hard part. |
| C2 | **Matchup strength analysis**: opponent-vs-position strength feeding C1's adjustments and shown as context on lineup calls. | [FRONTIER] | Methodology design (which inputs, how much weight, small-sample handling early in season). |
| C3 | **K/DST streaming recommendations**: weekly rank of available kickers/defenses by matchup, from C2's data + B1's free-agent list. | [CHEAP] | Once C2 exists this is a filter-and-sort over available players. |
| C4 | **Snap count & target share trends + usage-shift alerts**: ingest weekly usage data; detect meaningful shifts (rising backup, shrinking role) and raise alerts through B5. Process-over-results: alerts framed on volume/opportunity, never one-week points. | [SPLIT] | Frontier designs the shift-detection signal (what's "meaningful" vs. noise); cheaper model writes the ingestion transform and alert plumbing. |
| C5 | **Playoff schedule analysis**: weeks 14–16 strength of schedule per position, per team; feeds roster and trade decisions. Needed by early Nov, can land later in Sep. | [CHEAP] | A report over C2's matchup data; no new methodology. |
| C6 | **Lineup-locking strategy**: for early-game players (Thu/Wed opener), suggest flex/bench placement that locks early and preserves Sunday flexibility. | [SPLIT] | Frontier defines the placement logic (when locking early is +EV); cheaper model wires it into the optimizer output and UI. |
| C7 | **Handcuff strategy**: maintain a starter→direct-backup map; flag when a key starter's handcuff is available and worth rostering. | [SPLIT] | Frontier decides sourcing for the handcuff map (depth-chart inference vs. curated table) once; cheaper model handles the flagging logic and UI. |
| C8 | **Process-over-results framing**: recommendation copy across the module cites volume/opportunity, flags single-game variance explicitly ("1 catch on 9 targets" ≠ "bad game"). | [CHEAP] | Presentation-layer convention applied consistently; the data comes from C4. |
| C9 | **Homer check (in-season scope)**: reuse A6's neutral comparison whenever a Seahawks player is suggested as a waiver add or trade piece. | [CHEAP] | A6 built the methodology; this is reuse at two more call sites. |

---

## Phase D — Injury & News (Sep)

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| D1 | **Beat writer directory**: team→writer mapping (Seahawks → Brady Henderson, etc.), editable, seeded for all 32 teams. | [CHEAP] | Static reference table + CRUD + small UI. |
| D2 | **Official practice participation ingestion**: full/limited/DNP from official NFL injury reports as an early signal ahead of ESPN designation updates; attach to player records; feed C4-style alerts on downgrades. | [SPLIT] | Frontier picks the source and parsing strategy (official report formats change); cheaper model writes the recurring transform once the strategy is set. |
| D3 | **Manual Grok bridge**: generate a targeted prompt (e.g., "What has [beat writer from D1] said about [player] in the last 48 hours?") for the user to run in their free xAI account; paste-back box ingests the response and attaches it to the player as a sourced note. **No automated or paid API calls — by design.** | [SPLIT] | Frontier designs the paste-back parsing (free-text → structured note, with skepticism about stale/hallucinated info); cheaper model builds the prompt templates and UI. |
| D4 | **Kickoff reminders (live)**: turn on the B5 Routines for the real season — pre-first-lock (Wednesday opener aware, from B1's lock times) and pre-final-lock pushes, including "you have an injured/BYE player starting" checks. | [CHEAP] | Configuration of B5's backbone, not new machinery. |

---

## Phase E — Trade Management (Sep → early Oct)

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| E1 | **Trade grading**: score both sides of a proposed trade from projections/rankings (rest-of-season value, positional need, playoff SOS from C5, IR-stash value); show the value gap in plain terms. | [FRONTIER] | The valuation model is the heart of the whole trade phase; E2–E7 all consume it. |
| E2 | **Counterproposal generator**: given a lopsided trade, search both rosters' surplus/need for tweaks that close E1's gap; propose 1–3 fair counters. | [FRONTIER] | Search over roster-combination space with fairness constraints. |
| E3 | **Trade-willingness owner profiles**: extend the existing owner-tendency profiling (`profiling.py`) with historical trade behavior — who trades, how often, what shapes of deals. | [SPLIT] | Frontier defines the willingness features; cheaper model does the ESPN transaction-history transform (B1 provides the data). |
| E4 | **Proactive opportunity scanner**: cross-reference league-wide injury news (D2/C4 signals) against all rosters (B4's cache) to flag trade windows — e.g., rival's starter goes down, you hold surplus there. | [FRONTIER] | Cross-cutting correlation of injuries × rosters × needs; high false-positive cost (it interrupts you via push). |
| E5 | **Blocking plays**: flag handcuffs (C7's map) of *rivals'* injured stars worth grabbing purely to deny them. | [CHEAP] | Join of C7's handcuff map with D2's injury signals over rivals' rosters. |
| E6 | **Free agent hoarding**: after waivers process each week, flag speculative adds/drops worth making before Sunday to keep players off the board. | [SPLIT] | Frontier defines "worth hoarding" (drop cost vs. denial value); cheaper model schedules and renders the weekly report. |
| E7 | **Trade messaging generator**: friendly, non-salesy message framing a proposal/counter with actual projection and matchup numbers from E1. | [CHEAP] | Templating over E1's output. |
| E8 | **Trade deadline awareness**: per-league deadline tracking; buy/sell window flags in the weeks before it (contender vs. rebuilder lens per team record). | [CHEAP] | Date math + record check feeding B5 notifications. |

---

## Phase F — Strategy Awareness Flags (Oct, opportunistic)

Contextual flags, **not hard rules** — surfaced inline where relevant.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| F1 | **Stacking awareness**: flag QB + pass-catcher correlation opportunities in draft suggestions and trade evaluations. | [SPLIT] | Frontier sets the correlation weighting; cheaper model adds the flags at existing call sites. |
| F2 | **Bye week planning**: warn on bye clustering at draft time; preview thin weeks in-season. | [CHEAP] | Schedule joins over data B1 already has. |
| F3 | **Anti-correlation awareness**: flag rostering players who compete for the same touches (same-backfield RBs outside the C7 handcuff case). | [CHEAP] | Reuses C7's depth relationships with an inverted lens. |

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
