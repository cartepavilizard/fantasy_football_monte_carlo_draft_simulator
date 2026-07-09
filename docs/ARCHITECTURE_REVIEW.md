# Architecture Review: Owner Tendency Profiling & Automated Ranking Aggregation

**Date:** 2026-07-09
**Scope:** Pre-implementation review of the current simulator architecture, focused on
(1) how the simulation loop predicts other owners' picks and player availability, and
(2) how player projections currently enter the system — followed by a phased build plan
for the two proposed capabilities.

---

## Part 1 — Current-State Review

### 1. System overview

The app is three Docker services:

| Service | Stack | Role |
|---|---|---|
| `backend` | FastAPI + ODMantic (async Mongo ODM) + scikit-learn | All simulation logic, CSV ingestion endpoints |
| `frontend` | Next.js 14 (App Router) + NextUI + RTK Query | 6-step setup wizard, draft room UI |
| `mongodb` | MongoDB 7 | Two collections: `league` and `draft` |

Everything about a league — teams, the full player pool, historical distributions, the
regression training data — is **embedded in a single `League` document**
(`backend/models/team.py:248`). A `Draft` is a full deep copy of a `League` plus a
timestamp. There is no separate players collection, no source-data collection, and no
background job infrastructure of any kind. The backend has no outbound HTTP client at
runtime (`httpx` appears only in `requirements-dev.txt` for tests).

### 2. How the simulation loop works

The Monte Carlo entry point is `monte_carlo_draft` (`backend/app.py:288`). Per run it:

1. Trains a fresh `LogisticRegression` from `league.logistic_regression_variables`
   (the stored historical-draft x/y arrays).
2. For ~30 seconds of wall clock, loops over candidate positions (QB/RB/WR/TE, plus
   DST/K after round 7). For each: deep-copies the league, force-drafts the best
   available player at that position to the simulator's team, then simulates **the
   entire rest of the draft** pick by pick, and finally scores the simulator's roster
   with injury/bust randomization (`randomized_starter_points`).
3. Returns average points per candidate position — "what position should I take now."

Opponent picks inside the simulation come from `simulate_pick` (`backend/app.py:199`),
which is a **two-stage position-first process**:

- **Stage 1 — position choice:** `team.draft_turn_position_weights(pick_number, model)`
  (`backend/models/team.py:122`) asks the logistic regression for
  `P(position | overall pick number)`, then zeroes out any position whose starting
  slots are already filled on that team and renormalizes. A position is sampled from
  those weights.
- **Stage 2 — player choice:** the pick is **always the highest-projected undrafted
  player at that position** (`position_players[0]`; the position lists are kept sorted
  by projected points in `Players.assign_players_to_positions`,
  `backend/models/player.py:133`).

#### 2.1 Key finding: the simulator does not actually use ADP

The request framed the current system as using "league-wide ADP to predict player
availability at future picks." **That is not what the code does.** There is no ADP
concept anywhere in the codebase — no ADP column, field, or calculation. Availability
prediction is entirely:

> logistic regression over `(pick number → position)` pairs from your league's own
> historical drafts, then deterministic best-projection-within-position.

Practical consequences that matter for the new work:

- **Player-level availability is deterministic given position flow.** In every
  simulated world, players at a position come off the board in exact projection
  order. Nobody ever "reaches" for a specific player; nobody ever falls. The
  randomness is only in *which position* each team addresses each turn.
- **There is no notion of a specific player being over- or under-valued by the
  room.** "Will Player X be there at my next pick" is answered only implicitly by
  how many picks at his position are simulated to occur before then.
- **Adding ADP-informed, per-owner behavior is therefore not "augmenting ADP" — it
  is introducing player-level ADP modeling for the first time**, layered on top of
  the existing position-level regression. This is genuinely good news: the two-stage
  design gives clean seams for both (see §5).

#### 2.2 Key finding: one generic model for the whole room — and owner identity is discarded

The premise that "every other owner is one generic pool" is confirmed, and it runs
deeper than the model itself:

- One `LogisticRegression` is trained per league and used for **every** team's picks,
  including the simulator's own historical picks in the training data.
- The historical-draft upload endpoint (`backend/app.py:678`) requires only
  `Pick, Pos` columns. Even though the bundled sample
  `frontend/public/historical_drafts.csv` carries `Season, Player, Team` too, the
  endpoint **throws away everything except pick number and position**. Owner identity
  never enters the system's training data at all.
- `Team.owner` exists (`backend/models/team.py:86`) and is populated from the teams
  CSV — this is the natural join key for per-owner profiles, but today it is used
  for nothing except display.

#### 2.3 Other simulation-loop facts that constrain the design

- **Per-team roster-need adjustment already exists.** `draft_turn_position_weights`
  zeroing filled starting positions is already an "owner state" modifier on the
  generic model. Per-owner tendency profiles slot into exactly this function.
- **The hot loop is expensive.** Each Monte Carlo iteration deep-copies the entire
  league (full player pool included) via `league.model_copy(deep=True)` and
  re-validates Pydantic models on every drafted player. A prior E2E report already
  flags ~30s runs with GIL/latency issues (now mitigated by a `ProcessPoolExecutor`).
  Anything added to `simulate_pick` runs once per pick per simulated draft
  (~200 picks × hundreds of drafts), so owner-profile lookups must be precomputed
  O(1) structures — no pandas, no recomputation in the loop.
- **The model is retrained from stored x/y on every simulation request**
  (`fit_logistic_regression_model`, `backend/app.py:180`). Cheap for logistic
  regression; the same "store raw variables, fit on demand" pattern works for
  frequency-table owner profiles too (or profiles can be stored precomputed, which is
  even simpler since they're just counts and means).

### 3. How projections currently enter the system

The flow is entirely manual, one-shot, and name-keyed:

1. The frontend setup wizard (`frontend/app/setup/page.tsx`) walks through 6 steps and
   uploads **four CSVs** via RTK Query to four endpoints:
   - `POST /league` — teams file (`Name, Order, Owner, Simulator`)
   - `POST /league/{id}/historical_draft` — `Pick, Pos` → regression x/y
   - `POST /league/{id}/player` — current projections
     (`Season, Player, Pos, Team, Projected FFP`)
   - `POST /league/{id}/historical_player` — past projected-vs-actual
     (`... Projected FFP, Actual FFP`) → position-tier bust/boom distributions
2. Uploads are **write-once**: each endpoint 400s if data already exists; you must
   `DELETE` and re-upload to refresh. There is no update/merge path.
3. **Player identity is the exact name string.** Duplicate names are rejected at
   upload (`backend/app.py:522`), and every lookup — drafting, position lists,
   roster math — is `player.name == name`. This is the single biggest integration
   constraint for multi-source aggregation: "Kenneth Walker III" vs "Kenneth Walker"
   vs "K. Walker" from five different sources must be resolved *before* data reaches
   this system, or players will silently fail to match.
4. `DRAFT_YEAR` comes from env config (`backend/models/config.py:28`) and everything
   downstream reads `points[str(DRAFT_YEAR)]`; the player upload validates the
   season matches.
5. Projected points are a **single scalar per player per season** (`PlayerPoints`,
   `backend/models/player.py:16`). There are no tiers-from-sources, no rank fields,
   no per-source provenance. A blended projection therefore has an obvious landing
   spot (it *is* the scalar), but if we want to keep per-source values, tiers, and
   ADP for the simulation to use, the `Player` model needs new optional fields.

### 4. Audit: is there an existing ESPN scraper to extend?

**No. There is no scraper of any kind in this repository.** A full-text search for
ESPN/ADP/scraping/HTTP-client references finds only documentation: the README's
suggestion to hand-reformat a Sleeper export, and `E2E_TEST_REPORT.md`, which
describes a past test where real ESPN league data was pulled — but whatever pulled it
was never committed. There is no `espn_api` dependency, no requests/httpx/playwright
at runtime, no scheduler, and no module layout for data acquisition.

**Conclusion:** both capabilities need a new data-acquisition module built from
scratch. The good news is nothing needs to be *extended around* either — we get to
design the module cleanly. Recommended shape: a new `backend/data_sources/` package
(one adapter per source behind a common interface), new Mongo collections for raw
source data (kept out of the already-heavy embedded `League` document), and thin
FastAPI endpoints that materialize normalized data *into* a league using the existing
model structures — so the simulation engine keeps reading exactly what it reads today.

### 5. Integration seams for the two capabilities

The existing architecture offers precise, low-blast-radius seams:

| New capability | Seam | What changes |
|---|---|---|
| Owner profile substitution (position level) | `Team.draft_turn_position_weights` (`team.py:122`) | Blend generic regression probabilities with the owner's round-conditioned position frequencies when `team.owner` has a profile |
| Owner reach/target behavior (player level) | Stage 2 of `simulate_pick` (`app.py:236-238`) | Replace "always best projection" with weighted sampling over candidates, driven by blended value + ADP + the owner's reach distribution |
| Historical owner data | New collection + ingestion endpoint | Replaces nothing; the existing `Pick, Pos` upload keeps working as the generic fallback |
| Blended projections | `POST /league/{id}/player` equivalent that reads from the aggregation store instead of an `UploadFile` | The `League.players` structure, tiering, and max-points logic are reused untouched |
| ADP on players | New optional fields on `Player` (`adp`, `rank`, `tier`, per-source dict) | Backwards compatible; CSV path leaves them `None` |

### 6. Source-by-source access assessment (anti-bot audit)

The request assumed "most will need Playwright scraping rather than clean API access."
**The actual landscape is friendlier than that** — verified July 2026:

| Source | Access path | Auth | Anti-bot risk | Playwright needed? |
|---|---|---|---|---|
| **ESPN (league history + ADP/rankings)** | Unofficial JSON API: `lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{yr}/segments/0/leagues/{id}?view=mDraftDetail` (2018+) and `.../leagueHistory/{id}?seasonId={yr}` (pre-2018). Player ADP/ownership via `kona_player_info` view + `X-Fantasy-Filter` header. | Private leagues need `espn_s2` + `SWID` cookies (copy from a logged-in browser; you have commissioner access, so this is trivial). **As of Aug 2025 ESPN also requires these cookies for historical data that used to be public.** | Low — it's the same JSON API their own frontend uses. Undocumented, so endpoints/shapes can shift (base URL last changed ~April 2024). | **No** |
| **Sleeper (crowd ADP)** | Official, documented, read-only REST API (`api.sleeper.app`), no key required. ADP fields (`adp_*` by format) live on the projections/players endpoints; some ADP-specific routes are undocumented but stable and widely used. | None | Very low. Their docs ask ≤1000 calls/min; the full player dump is ~5MB and should be fetched at most daily. | **No** |
| **Fantasy Football Calculator (crowd ADP)** | Official, documented, free REST API: `fantasyfootballcalculator.com/api/v1/adp/{format}?teams=12&year=YYYY`. Explicitly free for personal use, attribution requested. **Supports past years — this is our best source of *historical* ADP for the reach-frequency features.** | None | Very low | **No** |
| **FantasyPros (expert consensus)** | Two options: (a) official API (`api.fantasypros.com`) — requires requesting a partner/API key; (b) the public rankings pages embed the full consensus payload as a JSON blob (`ecrData`) in the page source, fetchable with plain HTTP + a browser-like User-Agent. | (a) API key; (b) none (premium-only views need a session cookie) | **Moderate — the main one to watch.** They sit behind Cloudflare; plain-`requests` fetches have historically worked with sane headers and low frequency, but this is the source most likely to need Playwright (or `curl_cffi`-style TLS impersonation) if they tighten bot management. Design the adapter so its transport is swappable. | Maybe (fallback) |
| **Yahoo (rankings/ADP)** | Official Fantasy Sports API, OAuth2. Well documented; the hurdle is one-time app registration + token refresh plumbing, not scraping. | OAuth2 app + refresh token | Low via the API. **Do not scrape Yahoo pages** — aggressive bot detection and login walls make it the worst-value scraping target here. | **No** (use OAuth API) |
| **Fantasy Footballers UDK (paid)** | No API. It's a paid web app (and Google Sheet/CSV export in recent seasons) behind your subscriber login. | Your UDK subscription | High if scraped — subscriber login + likely ToS problems with automated pulls of paid content. | See below |

**UDK recommendation:** treat it as a *file-drop source*, not a scraper. Add a watched
ingestion endpoint/directory that accepts the UDK CSV/sheet export whenever you
download it (or reads a Google Sheet you re-export to). You already pay for it and the
export exists; automating the login with Playwright is brittle, breaks every August
redesign, and is the one integration with real ToS exposure. Every *other* source can
be fully automatic, which still eliminates routine manual CSV work — the UDK becomes
"drop the file, everything else is untouched."

**Net:** only FantasyPros has meaningful anti-bot risk, and only the UDK lacks any
sanctioned automated path. Playwright should be a per-adapter fallback, not the
default transport. Plain `httpx` + per-source rate limiting covers 5 of 6 sources.

### 7. Data-availability caveats for owner profiling

- **Depth of ESPN history:** the v3 API reliably serves 2018+; earlier seasons go
  through the `leagueHistory` endpoint, which generally works but with thinner data
  the further back you go. Expect "20+ years" to yield complete pick-by-pick data
  for roughly the last 8–12 seasons and progressively patchier data before that
  (draft detail for very old seasons is sometimes missing entirely). Plan for the
  ingester to record what it could and couldn't fetch per season, not to assume 20
  clean years. Recent seasons should be weighted more heavily anyway — a 2009
  tendency says little about 2026 behavior.
- **Owner identity across seasons:** ESPN league members have stable member GUIDs
  (SWID-style IDs) that persist across seasons within a league. Profile keys should
  be `(espn_league_id, member_guid)` with a display-name mapping — not display
  names, which owners change. A small manual alias table will still be needed to
  merge the same human across your three leagues and across ancient co-owned teams.
- **Historical ADP for "reach" features:** computing "reached N picks ahead of ADP"
  for a 2015 pick requires 2015 ADP. FantasyFootballCalculator's year-parameterized
  API is the plan of record (data back to ~2010, format-specific). Reach features
  simply won't exist for seasons before historical ADP coverage — the feature
  extractor must degrade gracefully to the position-frequency features.
- **"Behavior after missing a target" is a proxy, not an observable.** Draft logs
  don't record intent (ESPN doesn't expose historical pre-draft queues). The honest
  version of this feature: detect events where a player the owner was *likely* to
  take (per their own profile: e.g., they historically reach for the top player at
  their most-drafted position for that round) was taken within the K picks before
  their turn, then measure how their next pick's position/reach distribution shifts
  versus their baseline. That's estimable from pick-by-pick data alone, but it
  should be labeled as inferred, and only trusted where an owner has enough events
  (frequency floors, see Phase 3).
- **Sample size reality:** ~15 picks/owner/season × even 12 usable seasons is
  ~180 picks — fine for round-conditioned position frequencies (with round bucketing)
  and average reach, far too thin for anything conditional-on-conditional. This is a
  strong argument *for* the requested frequency/average approach and against anything
  fancier, and it means every profile metric needs a minimum-sample floor with
  fallback to the generic league model.

### 8. Risks and pre-existing friction the build must respect

1. **Name matching is the project-wide keystone risk.** Six ranking sources, ESPN
   draft logs, and the existing name-keyed player model all meet at "is this the same
   player?" A canonical-player resolver (normalize suffixes/punctuation, match on
   name+position+team with fuzzy fallback and a persisted manual-override map) must
   be built *first* and used by *both* capabilities.
2. **Write-once endpoints.** Automated refresh requires an idempotent upsert path for
   league players; today's endpoints 400 on re-upload. New sync endpoints should
   replace-in-place (safe: leagues used for live drafting are copies).
3. **Embedded-document weight.** Raw multi-source rankings and 20 years of draft logs
   must NOT go inside `League` (it's deep-copied per Monte Carlo iteration). New
   top-level collections, with only small precomputed profiles/blends materialized
   into the league.
4. **Hot-loop budget.** Owner profiles must be dict lookups; player-level sampling
   in Stage 2 must be O(candidates-considered), e.g., sample among the top ~10 by
   value at the chosen position rather than the full pool.
5. **Season-shape drift.** Three leagues × 20 years means changing team counts,
   snake vs. auction years (auction years must be excluded or handled separately),
   and keeper picks (which look like massive reaches and must be filtered or
   flagged during ingestion — ESPN marks keeper picks in draft detail).
6. **The existing `Pick, Pos` upload and CSV flow must keep working** as the
   fallback for leagues without ESPN ingestion, per "augments, not replaces."

---

## Part 2 — Phased Build Plan

Ordering rationale: the ranking-aggregation core comes **before** owner-tendency
feature extraction because the reach features in Capability 1 depend on historical
ADP, which is fetched by an adapter belonging to Capability 2's module. Building the
shared foundation first avoids doing name-resolution and HTTP plumbing twice.

### Phase 0 — Foundations (shared by both capabilities)

- New `backend/data_sources/` package: `BaseSourceAdapter` interface
  (`fetch() -> list[RawRecord]`, transport pluggable: httpx default, Playwright
  optional), per-source rate limiting, on-disk/Mongo raw-response caching.
- **Canonical player resolver**: normalization rules + `(name, pos, team)` matching
  + fuzzy fallback + persisted manual override collection. Unit-tested against real
  multi-source name lists.
- New Mongo collections (top-level, not embedded): `source_rankings` (raw, per
  source per fetch), `blended_rankings`, `historical_picks`, `owner_profiles`,
  `player_aliases`.
- Config: source credentials (`espn_s2`/`SWID` per league, Yahoo OAuth tokens) via
  env/`.env`, never in Mongo.
- Add runtime `httpx` to `requirements.txt`.

**Exit criteria:** resolver passes tests; collections and adapter skeleton exist;
no user-visible change.

### Phase 1 — Ranking aggregation, API-first sources

- Adapters: **Sleeper**, **FantasyFootballCalculator** (incl. `year=` historical
  ADP), **ESPN** rankings/ADP. All are clean JSON — no scraping risk, fast win.
- Normalization: per source, convert to a common schema
  `(canonical_player, source, rank, position_rank, tier, adp, projection, fetched_at)`;
  blend via configurable weighted average over positional value (z-score within
  position, so a rank-heavy source and a points-heavy source combine sanely).
- New endpoints: `POST /rankings/refresh` (on-demand pull of all configured
  sources), `GET /rankings/blended`, and `POST /league/{id}/player/sync` which
  materializes the blend into `league.players` using the existing `Players`/
  tiering/max-points code path (upsert semantics — replaces the write-once
  restriction for this route only). CSV upload remains as fallback.
- Extend `Player` with optional `adp`, `consensus_rank`, `tier`,
  `source_values: dict` fields (default `None`; zero impact on CSV flow).

**Exit criteria:** a league can be fully populated with blended projections without
touching a CSV; simulation runs unchanged on the result.

### Phase 2 — Remaining sources: FantasyPros, Yahoo, UDK

- **FantasyPros**: adapter tries official API key first (worth requesting one now —
  lead time is the constraint); falls back to `ecrData` page-embed fetch with
  realistic headers; Playwright transport as last resort. Alert-on-failure rather
  than silent staleness.
- **Yahoo**: one-time OAuth2 app registration; token-refresh plumbing; rankings +
  ADP via official API.
- **UDK**: file-drop ingestion endpoint (`POST /rankings/udk` accepting their
  CSV/sheet export) — deliberately not scraped (login-walled paid content, ToS risk,
  annual markup churn). This is the only remaining manual step, by design.
- Blend weights configurable per source; staleness metadata surfaced (`GET
  /rankings/status`: last successful fetch per source).

**Exit criteria:** all six sources feed the blend; each source's failure degrades the
blend rather than breaking it.

### Phase 3 — ESPN historical draft ingestion + owner tendency extraction

- Ingester: for each of the three leagues × available seasons, pull `mDraftDetail`
  (+ `leagueHistory` pre-2018) with commissioner cookies; store normalized
  pick-by-pick rows `(espn_league_id, season, overall_pick, round, member_guid,
  team_id, canonical_player, position, is_keeper)` in `historical_picks`; record
  per-season fetch success/failure; exclude auction seasons and keeper picks from
  tendency math.
- Owner alias table to merge the same human across leagues/seasons.
- Feature extraction into `owner_profiles` (pure counts/averages, recency-weighted,
  each with sample size `n` attached):
  - position frequency by round bucket (e.g., rounds 1–2, 3–5, 6–9, 10+);
  - reach stats vs. historical ADP (mean/SD of `pick − ADP`, reach rate), where
    historical ADP exists (FFC adapter from Phase 1);
  - positional-run participation (do they follow runs?);
  - inferred post-miss shift (per §7): distribution deltas after "likely target
    taken within K prior picks" events;
  - QB/TE timing (first round they historically take each onesie position).
- Endpoints: `POST /owners/ingest/{espn_league_id}`, `GET /owners/{id}/profile`
  (inspectable JSON — you should be able to eyeball "does this match how Dave
  actually drafts" before it ever touches the simulator).

**Exit criteria:** profiles exist for known owners with per-metric sample sizes;
nothing in the simulator consumes them yet.

### Phase 4 — Simulation-engine integration

- Teams gain an optional `owner_profile_key`; league creation/sync maps
  `Team.owner` → profile via the alias table.
- **Stage 1 (position choice)** in `draft_turn_position_weights`: when the team has
  a profile with sufficient `n` for the current round bucket, blend
  `w · owner_frequency + (1 − w) · logistic_regression_prob` before the existing
  roster-need zeroing (which stays — it already encodes "they can't start three
  QBs"). `w` scales with sample size and floors to 0 (pure generic model) for
  unknown owners — the "augments, does not replace" requirement, made literal.
- **Stage 2 (player choice)** in `simulate_pick`: replace deterministic
  best-projection with weighted sampling over the top-K available at the chosen
  position, weights from blended value + ADP proximity, temperature widened by the
  owner's reach SD (a chalky owner ≈ current behavior; a reacher spreads the
  distribution). Teams without profiles get a league-generic reach distribution
  estimated from the same historical picks — so even the "generic pool" gets more
  realistic player-level variance than today's deterministic order.
- Post-miss modifier: when a likely target for the on-the-clock owner was taken in
  the last K picks, apply that owner's inferred post-miss position-shift delta.
- Performance guardrails: profiles precomputed into plain dicts on the league copy
  before the loop starts; benchmark iterations-per-30s before/after with a target
  of ≥80% of current throughput; feature-flag (`USE_OWNER_PROFILES`) so A/B against
  the current engine is one env var.
- Validation (the actually important part): **backtest** — replay each held-out
  historical season, predicting each real pick with (a) current generic engine and
  (b) profile engine; compare position-hit-rate and player-in-top-K rate. The
  feature ships only if (b) measurably beats (a).

**Exit criteria:** measured accuracy improvement on held-out drafts; Monte Carlo
throughput within budget; flag-off path identical to today.

### Phase 5 — Scheduling, refresh automation, frontend

- Scheduler (APScheduler in-process, or a compose-level cron container) for source
  refresh: daily off-season, configurable (e.g., every 6h) in August; on-demand
  refresh already exists from Phase 1.
- Frontend: setup wizard gains "build from sources" path beside CSV upload; a small
  sources-status panel (per-source freshness, blend weights); draft room shows
  blended value + ADP + tier per player, and optionally "profile vs. generic"
  annotation on predicted picks.
- Draft-day switch: rankings refresh is manual-triggered on draft day (no scheduled
  job racing a live draft).

**Exit criteria:** zero routine manual file handling except the deliberate UDK
file-drop; one-click full refresh.

---

### Summary of corrections to the request's premises

1. The simulator **does not use ADP today** — opponent modeling is a league-trained
   logistic regression over pick-number→position, plus deterministic
   best-projection-within-position. The new work introduces ADP, it doesn't augment
   an existing ADP mechanism (the thing it augments is the regression).
2. There is **no existing ESPN or live-draft scraper** to extend — the data
   acquisition module is greenfield (which is cleaner anyway).
3. **Most sources do not need Playwright**: ESPN, Sleeper, FFC are clean JSON; Yahoo
   is a clean OAuth API; only FantasyPros carries real anti-bot (Cloudflare) risk,
   and the UDK is best handled as a file drop rather than scraped at all.
4. One nuance on "no machine learning needed": the current engine already contains a
   (very small) ML model — the scikit-learn logistic regression. The plan keeps it
   as the generic baseline and blends frequency/average profiles on top, per the
   request.

### External references

- ESPN v3 fantasy API endpoints, views, and cookie auth: [Steven Morse — Using ESPN's Fantasy API (v3)](https://stmorse.github.io/journal/espn-fantasy-v3.html), [ffscrapr ESPN endpoint docs](https://ffscrapr.ffverse.com/articles/espn_getendpoint.html), [ESPN draft API walkthrough](https://jman4190.medium.com/how-to-use-python-with-the-espn-fantasy-draft-api-ecde38621b1b), [fflr (2025 cookie-requirement change)](https://k5cents.github.io/fflr/)
- FantasyFootballCalculator ADP REST API: [official docs](https://help.fantasyfootballcalculator.com/article/42-adp-rest-api)
- Sleeper API: [docs.sleeper.com](https://docs.sleeper.com/)
