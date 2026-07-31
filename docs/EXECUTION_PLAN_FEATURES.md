# Execution Plan: Draft-Time Additions & In-Season Management Module

> Derived from [`docs/BRAINSTORM.md`](./BRAINSTORM.md). This is the phased
> build plan; the brainstorm remains the feature reference. Each task carries
> an explicit **model-routing recommendation** (see legend) per the scoping
> requirement in the brainstorm.

Last updated: 2026-07-31

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

## Session Protocol

**This section is normative and applies to every phase, not just the one
you are working.** It exists here, in the repo, on purpose: a protocol
that lives only inside a pasted prompt has to survive being copied intact
through every hop forever, and it will not. If a kickoff prompt and this
section disagree, **this section wins** — say so in your closeout.

### Picking the next row

Work the row the plan says is next; do not re-derive the order from
scratch. The rule, in order:

1. **A row already in flight beats everything.** `git branch --list` — an
   unmerged row branch means a previous session stalled there. Resume it.
2. **Honor `Depends` AND any exclusivity gate.** A row is eligible only
   when every dependency is `Done` and no gate in its `Depends` cell is
   unresolved. `H1 + H3-failed` means H6 is ineligible until H3 has
   actually run AND failed — an unrun H3 is an unresolved gate, not a
   green light.
3. **A `[human]` row BLOCKS THE QUEUE — do not route around it.** If the
   next row is `[human]`, that is the answer: hand it back to the repo
   owner with what they need to run it, and stop. Skipping a `[human]`
   row to find the next Claude-actionable one silently pre-empts a
   decision nobody made. This has already gone wrong once: after H1, a
   session skipped the unrun `[human]` H3 and routed to H6 — the fallback
   arm that is only valid *if H3 fails*.
4. **Never reorder without saying why.** "Unblocked" is not "next". State
   which one you mean.

### When the Ringer harness is unreachable

Rows marked Ringer-routed assume WSL and
`/mnt/c/ringer-jobs/ff-finish/checks/`. A session with only Git Bash
cannot reach them. That is a legitimate reason to implement directly —
but it does not lower the evidence bar:

- **Say so explicitly**, in the closeout note and the plan row.
- **Still write an executed behavior check** that proves a real number
  moved, and quote the before/after. Ringer is how the check gets run,
  not why it is required.
- Do not silently downgrade a `research-with-proof` or measurement row
  (H5) to hand-verification. If the measurement cannot be executed,
  stop and hand back rather than self-certifying.

### Authority limits

Without the repo owner asking, in the same session: do **not** `git push`,
do **not** delete a row branch, and do **not** run
`POST /league/{id}/player/sync` against the real draft leagues. Merging a
completed row to `main` locally is fine. Git protects code, not Mongo —
see `CLAUDE.md`.

### Branching and row status

One branch per row, cut from `main` before the work starts and merged back
when the row is done — see the Branching section in `CLAUDE.md` for naming
and for why the Ringer harness needs the branch cut up front. H2 landed on
`main` before this rule was restored; that history stays as-is.

**Before starting any row, run `git branch --list`.** An unmerged row
branch means that row is already in flight and a previous session stalled;
resume the branch rather than re-cutting it. Branch existence — not this
document — is the reliable signal, because a session that crashes never
reaches its closeout to update a status stamp here.

**Row status vocabulary.** `Open` = not started. `In progress (branch
<name>, YYYY-MM-DD)` = a session started it; expect an unmerged branch.
`Done (YYYY-MM-DD)` = merged to `main`. Stamp a row `In progress` with its
branch name when you start it, so the plan and the branch list agree — but
treat a mismatch as "the branch is right, this doc is stale."

### Session closeout — all five steps, every session

1. **Update this plan.** Set the row to `Done (YYYY-MM-DD)` with a
   one-paragraph note in the style of the Phase A–H rows: what shipped,
   the key decision and its rationale, file paths, test count, and any
   spec deviation and why. If you learned something that changes a LATER
   row, edit that row too — a stale row misroutes the next session.
   Distinguish "unblocked" from "next". Update `Last updated:` at the top,
   and the test count in `CLAUDE.md` if it moved.
2. **Save context to Open Brain.** One thought: what shipped, the decision
   and WHY (the rationale is what does not survive in code), measured
   before/after numbers, anything that turned out false or different from
   what the plan assumed, and what the next session needs that it cannot
   get from the repo. Do not re-capture what the code or this doc already
   records. If this session invalidated an earlier note, say so
   explicitly — there are correction notes for the ffanalytics thought and
   for H2; follow that pattern.
3. **Commit on the row branch.** Run the full suite first
   (`cd backend && venv312/Scripts/python.exe -m pytest -q`) and state the
   pass count in the commit body.
4. **Merge** the row branch to `main` locally and report the merge commit.
   Do not delete the branch and do not push unless asked.
5. **Hand off** — print the next session's kickoff prompt, per the
   contract below. This is a deliverable, not a courtesy; a session that
   ends without it has not finished.

**If you cannot finish:** say so plainly, leave the branch in place with
your work committed to it, and state which branch holds the partial work
and what remains. Do not merge a half-done row and do not delete its
branch — its existence is how the next session knows to resume.

### The handoff-prompt contract

Step 5's prompt must stand alone for a session starting **cold**, with no
memory of yours. Required sections — omitting any of them is an incomplete
handoff:

1. **The row**, and the one-line reason it is next (per the selection
   rule above). If the next row is `[human]`, say that instead and stop.
2. **The Claude model** to select in the picker, and **the branch**, each
   called out in one line *before* the code block as well as inside it.
3. **The three-way branch block**, verbatim in shape — clean-tree check →
   `git branch --list <row-branch>` → cut if absent, RESUME if present.
   Never a bare `git checkout -b`.
4. **Measured facts not to re-derive**, with numbers. This is what stops
   the next session re-auditing settled work.
5. **What is decided vs. still open**, so it knows which calls are its own.
6. **Ringer harness details** — harness path and arg order, the hard 60s
   `CHECK_TIMEOUT_S` (never bare `pytest` in a check), engine and its
   scoreboard record, and the reference manifest/check paths.
7. **Scope limits** — which rows not to start, and the data-safety line
   about not re-syncing real leagues.
8. **This same five-step closeout**, by reference to this section rather
   than by re-pasting it: *"Follow the Session Protocol → Session closeout
   in docs/EXECUTION_PLAN_FEATURES.md, all five steps."* Pointing here is
   what stops the protocol decaying one copy at a time.

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
| E1 | **Trade grading**: score both sides of a proposed trade from projections/rankings (rest-of-season value, positional need, playoff SOS from C5, IR-stash value); show the value gap in plain terms. | [FRONTIER] → **[Opus 4.8]** | **Done (2026-07-11) — spec: [`docs/specs/E1-trade-valuation.md`](./specs/E1-trade-valuation.md).** `backend/models/trade_valuation.py` implements the two value units verbatim: `player_value` (ROS points above replacement, floored, with a reported-but-never-reweighted `playoff_value` component) and `fit_delta` (roster-context starting-lineup change via C1's `best_assignment` DP + a `BENCH_FACTOR` depth term, never floored). `ValuationContext`/`build_context` are the only async surface (one Mongo read + every rate once); everything below is synchronous and pure so E2 can reuse one context — no awaits in the evaluation path. Availability curve is the IR-stash value (brainstorm §2.6). Reuses `matchup_strength`/`lineup` unchanged. New config in `models/config.py` (`TRADE_HORIZON_FINAL_WEEK` … `FAIR_GAP_FRACTION`, all env-tunable). Endpoints `POST /inseason/league/{id}/trade/evaluate` (proposal body, still pure Mongo reads) and `GET /inseason/league/{id}/player_values`; both added to both cached-only enforcement tests. Tests: `backend/tests/test_trade_valuation.py` (spec §6 edge cases + every Appendix A worked example as assertions, ±0.1). **Two fixture reconciliations, no unit change:** (1) §3.1 writes the rate window as "weeks `<=` w0" but the normative fixtures require strictly `< w0` with w0's own projection as fallback 1 — A.1 gives the *identical* trailing set (11.8/13.0/12.1/12.7 → 12.4) for healthy vs. questionable-at-w0 X, which is only possible if w0 is excluded from the mean, and A.5 explicitly labels the week-1/current projection "fallback 1"; implemented as `< w0`. (2) A.1's parenthetical "healthy X = 29.6 without tilts" is inconsistent with §7's own `12.4 × 8 = 99.2` gross, which yields neutral value **29.0**; tests assert 29.0. E1 lands before any other E task. |
| E2 | **Counterproposal generator**: given a lopsided trade, search both rosters' surplus/need for tweaks that close E1's gap; propose 1–3 fair counters. | [FRONTIER] → **[Opus 4.8]** | **Done (2026-07-14) — spec: [`docs/specs/E2-counterproposal-generator.md`](./specs/E2-counterproposal-generator.md).** `backend/models/counterproposals.py` implements the single-move anchored search verbatim: `generate_counters` is pure and synchronous, reusing E1's `player_value`/`evaluate_trade` on one shared `ValuationContext` (built once by the caller — no new async surface, no new value definitions, E1 untouched). The four-stage funnel is exactly the spec's: Stage 0 caches market values + a one-week (w0) surplus cost per roster player via C1's `best_assignment` (a reader on E1's primitives, never the full horizon); Stage 1 gap-band + untouchables filter (`GAP_SLACK`/`GAP_MIN_FRACTION`/`SURPLUS_COST_CEILING`); Stage 2 ranks by residual gap and keeps ≤ `MAX_FINALISTS`; Stage 3 runs the only full evaluations (≤12, asserted by a call-count test) and drops any counter below `FIT_FLOOR`; Stage 4 maximizes the worse side's fit, deterministic tie-breaks (move type + player ids), dedupes player multisets, returns ≤ `MAX_COUNTERS`. Anchor = most-valuable player received by the disadvantaged side (tie → lower id), never removed/swapped out. No compound moves, no randomness (determinism asserted by running the search twice). Endpoint `POST /inseason/league/{id}/trade/counters` (same body as E1's evaluate; build_context → evaluate_trade → generate_counters, pure Mongo reads) added to both cached-only enforcement tests. Config `MAX_SIDE_PLAYERS … MAX_COUNTERS` in `models/config.py`. Tests: `backend/tests/test_counterproposals.py` (every §5 edge case + the §6 worked example as assertions + endpoint). No trade UI exists yet (E1 shipped none), so this is backend-only per the spec's scope note. **[Correction, 2026-07-31]: a trade UI has since been built** — verified in the tree at `frontend/app/trade-room/`, with `trade-proposal-builder.tsx`, `trade-verdict.tsx`, `trade-counters.tsx`, `trade-header.tsx`, and `trade-message.tsx` in `frontend/packages/hawk-ui/src/`. **One spec reconciliation, no E1 change:** §5 says the evaluate dict "gains a `roster_size_note`" for unequal-count trades, but E1's `evaluate_trade` (as landed) does not emit one; rather than reshape E1's API from a consumer (spec §7 forbids it), E2 annotates the note onto a shallow copy of each evaluate dict itself (`_annotate`/`_roster_size_note`), naming team A's over/under on execution. |
| E3 | **Trade-willingness owner profiles**: extend the existing owner-tendency profiling (`profiling.py`) with historical trade behavior — who trades, how often, what shapes of deals. | [SPLIT] → **[Sonnet]** | **Done (2026-07-14).** Spec: [`docs/specs/E3-trade-willingness-features.md`](./specs/E3-trade-willingness-features.md). `models/trade_willingness.py`: pure `willingness_features()` (n_trades, trades_per_season, relative rate vs. league mean, activity backdrop, deal shapes, position mix best-effort resolved from synced rosters/free agents, timing buckets, partner concentration, inferred initiation rate, league veto climate) plus the async `league_trade_willingness()` loader; owner identity resolves through `profiling.load_alias_map` with a `team:{league}:{team}` fallback for owners with no `owner_guid`. `unknown`-until-deadline rule implemented exactly (falls back to week 11 when a league has no `trade_deadline`). `GET /inseason/league/{id}/trade_willingness` (standard envelope, in both cached-only enforcement tests) + a sorted owners table (most-willing first) in the in-season UI. Computed on read — no storage, no writes to `OwnerProfile`. Tests: `test_trade_willingness.py` (every §6 edge case plus the §7 worked example). |
| E4 | **Proactive opportunity scanner**: cross-reference league-wide injury news (D2/C4 signals) against all rosters (B4's cache) to flag trade windows — e.g., rival's starter goes down, you hold surplus there. | [FRONTIER] → **[Opus 4.8]** | **Done (2026-07-17).** Delivered via verified Ringer run (GLM): `models/opportunity_scanner.py` + `opportunity_api.py`, scan wired into every InSeasonScheduler pass. **Spec'd (2026-07-11, Fable design pass): [`docs/specs/E4-opportunity-scanner.md`](./specs/E4-opportunity-scanner.md).** Five AND-ed trigger conditions (questionable never pushes), push budget of 2/league-week, everything else degrades to an on-demand report endpoint; scan state seeds silently on first pass. Requires E1; reads D2 opportionally. |
| E5 | **Blocking plays**: flag handcuffs (C7's map) of *rivals'* injured stars worth grabbing purely to deny them. | [Sonnet] | **Done (2026-07-17).** `models/blocking.py` (+ report endpoint in `hoarding_api.py`); E5/E6 boundary tested from both sides. Join of C7's handcuff map with D2's injury signals over rivals' rosters. Boundary with E6 is defined in [`docs/specs/E6-hoarding-definition.md`](./specs/E6-hoarding-definition.md) §1 (E5 owns injured-star handcuffs; E6 excludes them). Needs D2 landed. |
| E6 | **Free agent hoarding**: after waivers process each week, flag speculative adds/drops worth making before Sunday to keep players off the board. | [SPLIT] → **[Sonnet]** | **Done (2026-07-17).** `models/hoarding.py` + `hoarding_api.py`; weekly scan wired into the scheduler pass. **Frontier half done — "worth hoarding" spec'd (2026-07-11, Fable design pass): [`docs/specs/E6-hoarding-definition.md`](./specs/E6-hoarding-definition.md).** `max(my_gain, 0.5 × best_rival_gain) − drop_cost > 3.0` in E1 units; bounded candidate pool; stored weekly report + one digest notification; E5's injured-star-handcuff cases excluded. Sonnet implements against the spec after E1 lands. |
| E7 | **Trade messaging generator**: friendly, non-salesy message framing a proposal/counter with actual projection and matchup numbers from E1. | [Sonnet] | **Done (2026-07-17).** `models/trade_messaging.py` + preview endpoint in `trade_comms_api.py`; deterministic templating, willingness informs tone only (leak-tested). Templating over E1's `evaluate_trade` output (quote per-week numbers per E1 spec §4.3's copy rules; E3's willingness informs tone only, never quoted). Needs E1 landed. |
| E8 | **Trade deadline awareness**: per-league deadline tracking; buy/sell window flags in the weeks before it (contender vs. rebuilder lens per team record). | [Sonnet] | **Done (2026-07-17).** `models/deadline_awareness.py` + report endpoint in `trade_comms_api.py`; deadline check wired into the scheduler pass. Date math (`InSeasonLeague.trade_deadline`) + record check (`LeagueTeamInfo` wins/losses) feeding B5 notifications; quotes E1's `playoff_value` component for the buy/sell lens when E1 is available. No E1 hard dependency. |

---

## Phase F — Strategy Awareness Flags (Oct, opportunistic)

Contextual flags, **not hard rules** — surfaced inline where relevant.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| F1 | **Stacking awareness**: flag QB + pass-catcher correlation opportunities in draft suggestions and trade evaluations. | [SPLIT] → **[Sonnet]** | **Done (2026-07-17, flags served via API).** `models/correlation_flags.py` + `flags_api.py`; rho table verbatim. Remaining polish: decorating the draft `suggested` map and E1 trade reports inline (the two spec call sites) — flags available via `GET /inseason/league/{id}/strategy_flags` meanwhile. **Frontier half done — correlation weights spec'd (2026-07-11, Fable design pass): [`docs/specs/F1-stacking-correlation.md`](./specs/F1-stacking-correlation.md).** Fixed ρ table (QB+WR 0.40, QB+TE 0.35, mild rows for honesty), σ ≈ 0.45 × weekly projection, flag quotes "extra weekly swing" points; two call sites (draft `suggested` map, E1 trade report decoration); provably zero effect on any ranking or verdict. Sonnet adds the flags verbatim (trade call site after E1 lands). |
| F2 | **Bye week planning**: warn on bye clustering at draft time; preview thin weeks in-season. | [Sonnet] | **Done (2026-07-17).** `models/bye_planning.py` + bye_outlook endpoint in `flags_api.py`; graceful no-schedule degradation. Schedule joins over data B1 already has. |
| F3 | **Anti-correlation awareness**: flag rostering players who compete for the same touches (same-backfield RBs outside the C7 handcuff case). | [Sonnet] | **Done (2026-07-17).** In `models/correlation_flags.py` (inverted C7, handcuff pairs excluded), served with F1 flags. Reuses C7's depth relationships with an inverted lens. Same flag-only discipline as F1 (see that spec's must-nots — zero effect on rankings/verdicts). |

---

## Phase G — Engine Quality & Data Integrity (Jul 18–31)

Not a feature phase. This workstream came out of measuring whether the
draft engine's recommendations were actually trustworthy, and it
repeatedly found that a number everyone believed was measuring one thing
was measuring something else. G1–G5 have landed; G6–G8 are open.

| # | Task | Routing | Notes |
| --- | --- | --- | --- |
| G1 | **Cost-of-waiting recommendation rule.** | [Opus 5] | **Done.** Replaced `monte_carlo_draft`'s four-branch full-draft EV comparison with `backend/models/value_over_wait.py`: cost of waiting = best available now minus expected best available at your next turn, per position. The old rule was measured to be both too noisy and pointed the wrong way — three identical UI runs on one board gave three different answers (TE, QB, RB), and with scoring randomness switched off entirely the TE branch beat the WR branch, i.e. more samples would have converged on confidently recommending a 20-pick reach. Root cause: the simulator's own 14 remaining picks are made by the same semi-random opponent model in every branch, so the single pick under test washes out. The replacement is deterministic in projections; simulation is used only for the fast-converging question of who survives. `TIE_MARGIN_POINTS = 5.0` makes near-ties say so. Old EV numbers retained but demoted and labelled. |
| G2 | **Round-aware reach SD.** | [Opus 5] | **Done.** `reach_sd_for` returned one number per owner for the whole draft, but measured reach spread is strongly round-dependent (rounds 1-2: 5.13, 3-5: 18.86, 6-9: 23.24, 10+: 28.88). The single lifetime figure saturated the temperature ceiling, so round 1 was simulated with late-round randomness — best available taken only ~34% of the time. Now per-bucket, with a fallback order that prefers the **league's** spread for that round over the owner's round-blind lifetime average (the round effect is ~7x, the between-owner effect within a round only ~2x). Round-1 concentration rose to 0.68–0.88. |
| G3 | **Draft-order verification gating.** | [Opus 5] | **Done.** `HistoricalPick.draft_order_verified` (default **False** — "assume fabricated until proven"), set at ingest from `ESPN_VERIFIED_DRAFT_ORDER_LEAGUE_IDS`. Only one of the three ESPN leagues drafted online; the other two were drafted offline and the commissioner typed the rosters in afterwards, so their `overall_pick` is data-entry order (proven against a real 2025 draft board: 0 of 173 picks agreed on round+slot). Order-dependent metrics in `backend/profiling.py` now use verified league-seasons only. **The trap worth remembering:** this shipped once with the default inverted to True and was completely inert — every pre-existing Mongo row lacks the key and reads back as the default. Greps and unit tests both missed it; what caught it was a before/after profile rebuild showing identical numbers. |
| G4 | **Per-league owner profile scoping.** | [Opus 5] | **Done.** Commits `1bf85d6` and `fff82e7`. `OwnerProfile.metrics["by_league"][str(espn_league_id)]` holds a full metric block per league; `build_team_tendencies` and `build_generic_tendencies` take a **required positional** `espn_league_id` with no default, so a missed call site is a TypeError rather than a silent cross-league blend. `League` gained `espn_league_id`, stamped by `POST /league/{id}/historical_draft/sync` and overridable on `/owners/map`, which returns 400 rather than guess. Motivation: round buckets aren't comparable across league sizes — round 3 is picks 25-36 in a 12-team league but 21-30 in a 10-team one. |
| G5 | **Ship-gate order gating.** | [Opus 5] | **Done.** Commit `dedddc4`. `backend/backtest.py` trained and predicted on `overall_pick` with no verification gating at all, so **5,246 of the 5,919 picks it scored (88.6%) carried fabricated order**. `evaluate()` gained `verified_order_only=True` (default True) and `evaluate_leagues=None`, plus an `order_verification` provenance block; `POST /owners/backtest` exposes both as query params. |
| G6 | **Ship calibration metrics into the gate.** | [Sonnet] | **Open.** `backtest.py` reports argmax position hit rate only, but the engine **samples** from the blended distribution rather than taking argmax, so hit rate is the wrong metric for how the output is consumed. Add log-loss and Brier to both arms alongside hit rate. These were measured out-of-band on 2026-07-31 but never shipped into the file. Reference values to reproduce, on the verified-order league only (n=673): generic hit 0.3536 / log-loss 1.4842 / Brier 0.7347; profile hit 0.3210 / log-loss 1.5111 / Brier 0.7339. |
| G7 | **Decide the fate of the stage-1 position blend.** | [Opus 5] | **Open. Do not action this yet — it is gated on G8.** Measured on the only league with real draft order (Mahomes `61119864`, 673 picks, seasons 2020-2024), `blend_position_weights` makes prediction *worse* than the plain logistic model, and the harm is dose-dependent in `MAX_PROFILE_WEIGHT`: 0.0 → 0.3536 (reproduces generic exactly, a harness control), 0.175 → 0.3536, 0.35 → 0.3447, 0.525 → 0.3254, 0.7 (shipped) → 0.3210. Mechanism split: frequency blend −0.0208, post-miss shift −0.0044, both −0.0326. The calibration defence was tested and failed (see G6's numbers — log-loss worse, Brier a tie). **But the signal is real, not noise:** split-half reliability of owner tendencies is r = 0.426 (rounds 3-5), 0.493 (rounds 6-9), 0.334 (10+), and 0.094 in rounds 1-2 where only 3 owners had enough picks to test. So the conclusion is "this blend is the wrong way to spend a real signal," not "profiles don't work." Existing levers: `USE_OWNER_PROFILES` (on/off) and `MAX_PROFILE_WEIGHT` (the dial). **Scope limit: this concerns stage 1 (position choice) only. Stage 2 — reach SD driving `candidate_weights` for player selection — is a separate mechanism validated independently by G2 and is not implicated.** |
| G8 | **Import the real draft boards.** | [Sonnet] | **Open. Blocked on the repo owner obtaining spreadsheets** for the Skunkweed and Danger Zone leagues (2025 first, then 2024, then 2023). The importer is already built and verified at 100% on the 2025 Skunkweed file but has **never been run with `--apply`**. This is the highest-value open item: 673 real-order picks is the binding constraint on every conclusion in G5–G7, and this work converts 6,660 fabricated-order picks into real ones. |

The analysis scripts backing G5–G7 live outside this repo, at
`C:\ringer-jobs\ff-finish\analysis\`: `diagnose.py` (dose-response and
mechanism split), `diagnose2.py` (per-bucket breakdown), `reliability.py`
(split-half), `calibration.py` (log-loss/Brier). `calibration.py` carries
a self-check that its `n` must come out to 673.

---

## Phase H — External Projection Aggregation (Aug, draft-gated)

Came out of a 2026-07-30 research note proposing **ffanalytics** (R
package) as a multi-source projection/ADP aggregator. The note's premise —
"player pool, projections and ADP are 100% ESPN-sourced, no external
aggregation exists" — was **verified false on 2026-07-31**: the Phase 0
aggregation layer has been live since before this plan started. What the
note actually identified, once cross-referenced against the code, is a
narrower and better-defined defect. Read this preamble before working any
H row.

### What already exists (do not rebuild)

`backend/data_sources/` is a complete multi-source subsystem: five pull
adapters (`sleeper`, `ffc`, `espn_rankings`, `fantasypros`, `yahoo`), one
push/file-drop source (`udk`), per-position z-score blending
(`blend.py`), orchestration with last-known-good fallback (`service.py`),
`RankingsScheduler`, and the `/rankings/*` endpoints. `Player` already
carries `adp` / `consensus_rank` / `tier` / `source_values`.

It is live in Mongo, not merely built. Verified 2026-07-31:

```
blended_rankings: 2026 ppr, sources_used = [sleeper, ffc, espn, fantasypros], 769 records
league "Never Leaving Mahomes 2026": Trey McBride adp 22.81 tier 3
  source_values {sleeper: 2.98, ffc: 1.88, espn: 2.83, fantasypros: 1.94}
```

### The actual defect

Measured field coverage in the 2026-07-28 batches:

| source | records | resolved | **projection** | adp | rank | tier |
| --- | --- | --- | --- | --- | --- | --- |
| sleeper | 3220 | 1020 | **635** | 3220 | 0 | 0 |
| ffc | 233 | 233 | **0** | 233 | 0 | 0 |
| espn | 1026 | 1026 | **567** | 1026 | 1025 | 0 |
| fantasypros | 508 | 499 | **0** | 0 | 508 | 508 |

The ordinal/ADP axis has four sources. **The projection axis has two.**
And `POST /league/{id}/player/sync` (app.py) materializes
`blended_projection` into `PlayerPoints.projected_points` — where
`blended_projection` is a plain unweighted `fmean()` of raw point totals
(`blend.py`), *not* the z-scored `blended_value`.

> **The number the entire simulator runs on — every projection, tier
> assignment, `position_max_points` ceiling and `value_over_wait` verdict
> — is an unweighted raw average of two sources, one of which is ESPN.
> The rigorous four-source consensus is carried as metadata and never
> drives the engine.** `RANKING_BLEND_WEIGHTS` does not reach it. Raw
> point totals from sources with different scoring assumptions are
> averaged without normalization. 97 of 769 blend records are dropped at
> sync for having no projection at all.

That is H2, it is independent of R and of ffanalytics, and it is the
highest-value row in this phase.

**✅ H2 SHIPPED 2026-07-31 — read the H2 row before acting on the
paragraph above, which is preserved as the problem statement.** Two
corrections it got wrong. First, `RANKING_BLEND_WEIGHTS` not reaching
the projection is real but nearly inert: the setting ships as `{}` and
an equal-weighted mean *is* an unweighted mean, so fixing only that
moves zero players. The load-bearing defects were the missing scale
normalization (espn projects 41% more kicker points than sleeper on the
same 31 kickers) and espn's `0.0` "no projection" sentinel silently
halving 23 real projections. Second, the drop count is now 118–120 of
769, not 97 — dropping the sentinels correctly removes ~22 more records
whose only "projection" was a sentinel. **The fix is committed but NOT
adopted:** no re-sync has been run, so the live leagues still carry the
old `projected_points`. Adoption is H5.

### ESPN-only assumptions — the audit the note asked for

| Area | ESPN-only? | Finding |
| --- | --- | --- |
| Player pool / projections / ADP | **No** | Four-source blend, live |
| Value model, tiers, scarcity, `value_over_wait` | **No** | Read `Player.points/adp/tier`; source-agnostic by construction |
| `HistoricalPick` / owner profiling | **Yes, deliberately** | "ESPN-exclusive per Addendum A". Correct — it is *your leagues'* draft history. Not a target. |
| In-season injury / roster status | **Mostly** | `espn_league.py` supplies `injury_status`; D2 already added nflverse practice reports as a second signal. The note's proposed "ESPN stays the injury source" split is already reality. |
| Canonical naming | **ESPN-anchored** | `ANCHOR_PRIORITY = ["espn", "sleeper", "ffc"]`. Intentional (drafts are on ESPN) and load-bearing: Sleeper resolves only 1020/3220 because the anchor namespace is ESPN's 1026. |

**The integration risk is calibration, not architecture.** Adding a
projection source changes `projected_points` → re-ranks players →
reassigns `position_tier` → changes `position_max_points` → changes the
`randomized_points()` ceiling → changes every Monte Carlo outcome and
every `value_over_wait` verdict. A re-sync is a global recalibration of
the engine. Per this project's own verification philosophy, that must be
measured before it is adopted (H5), not after.

### The SOS follow-on is already built

The note flags strength-of-schedule + positional defensive rankings as
the most valuable add-on. **C2 (`matchup_strength.py`) and C5
(`playoff_sos.py`) already ship both**, computed from synced data rather
than bought. The only genuine gap is that both are structurally neutral
before week 1 — there is no *draft-time* SOS. That is H8, it is narrow,
and preseason SOS is weak signal. **Blocker if pursued:**
`inseason_api.py` enforces cached-only reads structurally (a test fails
the build if its import closure ever imports `data_sources`), so any
external SOS must land in Mongo via ingestion and be read from there.

**Fantasy Nerds: dropped.** SOS is built, depth-chart inference was
deliberately rejected by C7, DFS values are irrelevant here, and it is
paid.

### Delivery approach

**File-drop, not a new pull adapter.** `udk` already established the
pattern: an offline producer writes a CSV, `POST /rankings/udk` ingests
it, names resolve against the stored anchor, the blend regenerates. That
turns "build an R integration subsystem" into "generalize one push source
and write an R script" — no R runtime inside the backend, no new fetch
surface, no new scheduler failure mode.

### Branching, row status, closeout and handoff

All four now live in one cross-cutting place: **[Session
Protocol](#session-protocol)** near the top of this file. Read it before
starting any H row. H2 landed on `main` before the branch-per-row rule was
restored; that history stays as-is.

### Ringer routing

Ringer has already shipped work on this repo (GLM 5.2 via OpenCode,
direct-repo mode, ownership-gated). Reusable harness:
`/mnt/c/ringer-jobs/ff-finish/checks/check_task_generic.sh`
(ownership → required identifiers → executed `behavior_*.py` → pytest →
optional tsc → auto-commit on pass), invoking `venv312` through
`cmd.exe` from WSL.

**A row is Ringer-ready iff a `behavior_*.py` script can prove it.** The
scoreboard for this user (`ringer.py models`, 70 rows): GLM 5.2 is
**proven** on code-fix (100% first-try, n=7) and code-review (80%, n=5),
**probation** on code-feature (53% first-try / 80% pass, n=30). Design
decisions and numeric judgment stay with the orchestrator; typing and
verification go to workers.

| # | Task | Routing | Ringer | Est. | Depends |
| --- | --- | --- | --- | --- | --- |
| H1 | **Generalize the push path.** `PUSH_SOURCES` is hardcoded `["udk"]` and the ingest endpoint is UDK-specific. Make push sources registrable so `ffanalytics` (and future drops) ride the same ingest/resolve/blend path. Purely enabling. | [Sonnet] | **Done (2026-07-31).** `PUSH_SOURCES` (a plain list) became `PUSH_SOURCE_PARSERS` (`backend/data_sources/service.py`), a `name -> parser` registry seeded with `udk`'s existing `parse_udk_rows` via a new `register_push_source(name, parser)`; `ALL_SOURCES` became `all_sources()` (a function, not a module-level constant) so a source registered after import — the whole point, since a check or a future adapter module registers at call time, not at `service.py`'s load time — is picked up by blend rebuilds and `/rankings/status` without a restart. Endpoint side: `app.py` gained `POST /rankings/push/{source}`, generic over any registered parser (404 on an unregistered name); `POST /rankings/udk` is now a two-line alias calling the same shared `_ingest_push_source` helper, kept for the existing test suite and any external caller rather than as a compatibility shim with a deprecation path — there's no reason to ever remove it. **Implemented directly rather than via Ringer/GLM** — the interface was small enough (one dict-to-registry conversion, one new thin endpoint) that delegating cost more than it saved; no worker swarm was spun up. **The check, not a grep:** `tests/test_phase2_flow.py::test_generic_push_route_carries_a_registered_source_into_the_blend` registers a synthetic third projection source at runtime, uploads a fixture CSV through the generic route, and asserts `blended_projection` for Christian McCaffrey actually moves — baseline (espn 320 + sleeper 330)/2 = 325 vs. post-upload (320+330+310)/3 = 320.0 — not just that `"synthetic"` shows up in `sources_used` vocabulary. A second test (`test_generic_push_route_rejects_unregistered_source`) covers the 404 path. **690 tests passing** (688 + 2 new). No blend methodology touched — this row is purely the registration/ingest plumbing H2 already established the contract for. | 0.5d | — |
| H2 | **Fix `blended_projection`.** | [Opus 5] → [GLM 5.2] | **Done (2026-07-31).** Commits `a18d957` (contract) and `4cc6c00` (implementation, Ringer/GLM 5.2, first-try pass). **688 tests passing** (682 + 6). The contract lives in `backend/data_sources/blend.py`'s docstring; implementation touches `blend.py`, `models/sources.py`, `app.py` (sync now counts and logs the drop) and `tests/test_blend.py`. **The row as written was wrong about where the value was.** "Weighted mean honoring `RANKING_BLEND_WEIGHTS`" is a *no-op*: the setting ships as `{}` and an equal-weighted mean IS an unweighted mean — measured, 0 of the top 200 move. Shipping only that would have been a third inert change in the G3/G5 family. The real defects were two the row never named. (1) **Scale:** sources disagree on what a point is — espn/sleeper per-position ratios k 1.41, rb 1.14, dst 1.04, te 1.03, wr 1.02, qb 0.98; on the same 31 kickers espn projects 41% more points. Fixed by rescaling each source onto an anchor's (espn's) per-position scale before averaging, factor = **median** of per-player ratios (mean ratio is wrecked by near-zero denominators: wr 1.38 by mean vs 1.02 by median), floored at `RESCALE_MIN_PROJECTION` 10.0 with `RESCALE_MIN_OVERLAP` 10. (2) **Sentinels:** espn writes `0.0` for "no projection" (45 records) — James Conner read sleeper 59.80 / espn 0.00 and materialized at **29.90**; non-positive values are now dropped. **Outlier handling: deliberately none.** With two projection sources every robust estimator degenerates (median of two = mean, trimmed mean of two = mean, MAD has no majority), so rejection reduces to "always believe source X" — and per-source accuracy is unbacktestable here because the historical store keeps ESPN's projected/actual pairs only. Disagreement is preserved in the mean and surfaced as a new `projection_spread` field (478 records carry one). **The 97 stay dropped, not reconstructed:** their `blended_value` is a degenerate tie at −0.417 derived from an espn adp of 584.49 — itself an "undrafted" sentinel — so inverting it would fabricate the engine's primary input from "ESPN declined to rank him". Measured effect, all top-200 (200/200) move, mean \|Δ\| 6.44: Bijan Robinson 338.68→361.37, Jahmyr Gibbs 336.50→358.00, Josh Allen 365.38→**360.99 (down)**, Brandon Aubrey 143.78→167.65, James Conner 29.90→68.15. `position_max_points` k +23.87, rb +22.69, wr +3.66, te +3.12, dst +2.33, qb −4.39. **Not adopted yet — no re-sync was run; see H5.** | 1d | — |
| H3 | **R toolchain spike, timeboxed.** Install R + ffanalytics, run one seasonal `scrape_data()`, confirm usable output. **Kill-switch: >1 day → stop, go to H6.** | [human] | **❌ No** — interactive Windows installers and environment mutation on the host. Not a worker task. If it proceeds, install R **into WSL** so H4's check can execute `Rscript` the way the harness executes `venv312`. **⚠️ THIS IS THE CURRENT NEXT ROW AND IT BLOCKS THE QUEUE (as of 2026-07-31, H1 and H2 done).** It is `[human]` — the repo owner runs it, not a Claude session. Its outcome is the gate that decides the whole rest of the phase: **pass → H4, H5** (the ffanalytics arm); **fail or kill-switch → H6** (native adapters). Both arms are ineligible until it resolves, so **do not route around it** to find a Claude-actionable row — that pre-empts a decision nobody has made. If the owner declines to run it or it blows the ~Aug 10 kill-switch, record that here as a failure and H6 opens. | 0.5–1d | — |
| H4 | **ffanalytics producer script.** `backend/scripts/ffanalytics_export.R` → CSV in H1's schema. Scored to *your* league settings, not generic PPR — the one thing no current source does. | [Sonnet] | **✅ Conditional on H3.** Check must actually run `Rscript` and validate the CSV against H1's schema; if R is unreachable from the check, this is unverifiable and must not run under Ringer. **Gated the same way H6 is, in the opposite direction:** eligible only once H3 has run **and passed**. An unrun H3 is an unresolved gate, not a green light. | 1d | **H1 + H3-passed** |
| H5 | **Measure before adopting.** Rebuild the blend with and without the change; diff `projected_points`, tier assignments, `position_max_points`, and `value_over_wait` verdicts on the live Mahomes board. Gate adoption on the result. | [Opus 5] | **✅ Yes** — `research-with-proof` pattern; the behavior script *is* the measurement. Worker produces the numbers, **orchestrator reads them and decides.** Never let the worker that builds the integration also rule on whether it helped. **⚠️ SCOPE GREW ON 2026-07-31 — but this row does NOT move up the queue.** Its dependency on H4 is gone; its *position* is unchanged. Run it after H1/H3/H4 as planned, so it measures the ffanalytics arm and H2 in ONE pass instead of twice. What changed is that it now has a second thing to gate: **H2 already changed every projection in the pool and has NOT been adopted.** The code is committed but no `POST /league/{id}/player/sync` was run, so the live leagues still hold the old `projected_points`. Re-syncing is the adoption step and it is this row's job to gate. Two things make it non-trivial rather than a formality: (a) **tiers genuinely move** — rescaling an *input* before averaging is not a monotone transform of the output (with `old=(e+s)/2`, `new=(e+f·s)/2`, the shift `(f−1)·s/2` grows with s), measured 76/94 rb and 20/31 k keeping their within-position slot, and `position_tier` is assigned by within-position rank; (b) `position_max_points` shifts hard for k (+23.87) and rb (+22.69) while qb *falls* (−4.39), which re-scales the `randomized_points()` ceiling asymmetrically across positions. Also note 22 more records lose their projection (96 dropped → 118) — all backup QBs and non-entities whose only "projection" was an espn sentinel zero. **The failure mode to avoid is this row becoming unreachable.** If H3 misses its ~Aug 10 kill-switch and the ffanalytics arm defers to in-season, H5 must STILL run pre-draft for H2 alone — otherwise H2's fix sits committed-but-unadopted straight through the draft, which is the worst of both worlds: the defect is "fixed" in code while the live leagues keep drafting on the old numbers. So: normally S5, after H4; but if the ffanalytics arm defers, promote it rather than deferring it with them. Still must be sequenced against G7, not run in parallel. | 1d | ~~H2, H4~~ **H2 (done) — H4 optional** |
| H6 | **Fallback: native Python projection adapters.** If H3 fails, skip R entirely — write `BaseSourceAdapter` subclasses for NumberFire / CBS / FFToday. Less coverage than ffanalytics, zero new runtime, perfect fit for the existing framework. | [Sonnet] | **✅ Best fan-out in the phase** — three independent adapters over disjoint files: textbook `fix-swarm` / parallel lanes, one worker each. Good candidate for an exploration lane on one of the three. **Worth more after H2 than the row implies:** a THIRD projection source is what makes real outlier handling possible for the first time. H2's contract deliberately ships no outlier rule because at n=2 every robust estimator degenerates to the mean; at n≥3 median / trimmed mean / MAD rejection all become computable, and the contract says to revisit then. Each new adapter also just works with the rescale — it is anchored per (source, position) against espn, so a new source needs no calibration beyond clearing `RESCALE_MIN_OVERLAP` (10) overlapping players per position. Any adapter that emits a sentinel for "no projection" must emit `None`, not `0.0`. **⚠️ GATED — H1 alone does NOT make this eligible.** H6 is the *fallback arm*, mutually exclusive with H4/H5: it is only correct once H3 has actually run **and failed** (or blown its ~Aug 10 kill-switch). An unrun H3 is an unresolved gate. If the R spike lands, most of this row's 1.5d is wasted — ffanalytics covers ~10 projection sources against these three. A session skipped the unrun `[human]` H3 and routed here on 2026-07-31; see the selection rule in [Session Protocol](#session-protocol). | 1.5d | **H1 + H3-failed** |
| H7 | **FantasyPros expert-disagreement signal.** *Optional.* Extend the existing adapter to capture std-dev across experts → an uncertainty field feeding tier confidence and the near-tie margin. Genuinely new signal. | [Opus 5] | **✅ Yes** — single-adapter extension, `task_type: code-feature`. **Build on `projection_spread`, do not add a parallel field.** H2 shipped the first uncertainty field on `BlendedRankingRecord` (max minus min of the rescaled per-source projections, `None` below two sources; 478 of 769 records carry one, top-200 mean 12.84 / median 9.06 / max 56.48). Nothing consumes it yet — H7 is the row that gives it a consumer. FantasyPros expert std-dev is a *second, better* uncertainty channel (130+ raters vs 2 sources); the design question this row must settle is whether the two combine into one confidence number or stay separate, not whether to invent another field. | 1d | — |
| H8 | **Preseason SOS.** *Optional, defer.* The only gap C2/C5 don't cover. Must land in Mongo via ingestion — see the structural cached-only blocker above. | [Sonnet] | **✅ Yes** — the existing import-graph test is already the guard rail; the behavior script asserts it still holds. | 1d | — |
| ~~H9~~ | ~~Fantasy Nerds integration~~ | — | **Dropped** — redundant and paid. See above. | — | — |

**Sequencing against the calendar.** It is 2026-07-31; drafts are late
August; G6/G7/G8, D4, the C7 review and the mock-draft dry run all
compete. H2 is worth doing pre-draft on its own merits — a real defect in
the engine's primary input, one day, no R. H1 is cheap and unblocks
everything. H4/H5 only ship pre-draft if H3 comes in clean by **~Aug 10**;
otherwise defer the whole ffanalytics arm to in-season, when a projection
refresh is far lower-stakes than a recalibration on draft eve.

**Interaction with G7.** G7 is deciding the fate of the stage-1 position
blend on measured numbers. Changing the projection pool mid-investigation
muddies its before/after comparisons. Sequence H5 and G7; do not run them
in parallel.

**The failure mode to guard against** is not "the integration doesn't
work." It is "the integration works, silently shifts every projection a
few percent, reshuffles tier boundaries, and the draft runs off it
without anyone diffing." That is precisely the class of bug G3 and G5
caught. H5 is not optional polish.

---

## Outstanding operational items (verified 2026-07-31)

Drafts are ~late August and the season opens early September, so items
1–3 and 6 below are date-driven; the rest are not.

1. **D4 season-start configuration** — still open. Verified: none of
   `ESPN_MY_TEAMS`, `INSEASON_SYNC_ENABLED`, `USAGE_INGEST_ENABLED`,
   `PRACTICE_INGEST_ENABLED`, `LINEUP_PULL_ENABLED` appear in
   `backend/.env`. Also needs boot-time services and the local Claude
   Routine for B5 push notifications.
2. **Phase B exit criterion — the live Routine push to the phone** — still
   open. The notifications backbone, panel, and `pending`/`ack` contract
   are all built; what's missing is a scheduled Claude Routine actually
   polling and pushing, plus a live run.
3. **C7 handcuff seed table human review** — still open, and the plan
   calendars it for **August 2026**, which is now.
4. **D1 beat-writer directory human review** — still open. The row
   already notes it was seeded for all 32 teams from model
   training-data knowledge and "needs a human review pass before
   relying on it."
5. **F1 inline decoration** — still open. Flags are served via
   `GET /inseason/league/{id}/strategy_flags`; the two spec'd call
   sites (decorating the draft `suggested` map and E1 trade reports
   inline) are not wired.
6. **Mock-draft dry run** — a full end-to-end rehearsal before the real
   draft.
7. **Cosmetic:** `CornerBadge` is illegible at 11-16px; `CornerBadgeSvg`
   is a drop-in replacement if wanted.

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
