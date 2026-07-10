# Execution Plan — Architecture Audit Fixes

Chunked remediation plan for the Monte Carlo Fantasy Football Draft Simulator
(FastAPI backend in `backend/`, Next.js/NextUI frontend in `frontend/`,
MongoDB via `docker-compose.yml`). Derived from a full architecture audit.

**How to use this file:** Each chunk is fully self-contained — it names the
file, the function, what's broken, why it matters, and the exact change.
Chunks are the smallest independent units of work; do one per commit.
Order within each phase is the recommended order. Dependencies are flagged
explicitly; anything not flagged is independent.

**Token cost scale:** low = single small diff (<1k output tokens),
medium = multi-site or restructured logic (1–4k), high = refactor or
multi-file work (>4k).

**Repo orientation (for a zero-context model):**
- `backend/app.py` — all FastAPI routes + simulation functions (Monte Carlo,
  logistic regression, pick simulation, CSV upload endpoints).
- `backend/models/team.py` — `Team`, `League`, `Draft`, `fill_starters()`.
- `backend/models/player.py` — `Player`, `Players` (position lists + tier
  assignment), randomized point projections.
- `backend/models/position.py` — `PositionSizes`, `PositionTiers`,
  `PositionTierDistributions`, `PositionMaxPoints`.
- `backend/models/config.py` — env-var globals (`DRAFT_YEAR`, `ROUND_SIZE`,
  position sizes, `SNAKE_DRAFT`, `LOCAL`).
- Sample data in `backend/data/*.csv` (Season 2024 players, 2023 historical).
- No tests exist. Line numbers below refer to the audited commit (`2e33e3f`);
  they will drift as chunks land — match on the quoted code, not the number.

> **Read this before starting:** Chunk D1 (Docker env plumbing) is listed in
> Phase 2 per the priority scheme, but it is a *prerequisite for running the
> app at all* in 2025+: `DRAFT_YEAR` defaults to the current calendar year
> while the sample data is Season 2024, and the Dockerfile clobbers `.env`.
> If you intend to verify any chunk end-to-end, do D1 first.

---

## Phase 1 — Statistical / logic bugs

### L1: Injury distributions silently drop 0-point seasons (and can divide by zero) — low

**Broken:** `create_historical_distributions()` in `backend/app.py` (~line 143)
filters historical rows with `if points.actual_points and ...`. A player whose
actual points were `0` — a season-ending injury before week 1, the exact
catastrophe the tool exists to model — is falsy and silently excluded, biasing
every position-tier distribution optimistic. Also `(actual - projected) /
projected` raises `ZeroDivisionError` if a projection is 0.

**Change:** Use an explicit `is not None` check and guard the denominator.

```diff
         for year, points in player.points.items():
-            if points.actual_points and int(year) < int(
-                draft_year
-            ):  # Only use historical data
+            if (
+                points.actual_points is not None
+                and points.projected_points > 0
+                and int(year) < int(draft_year)
+            ):  # Only use historical data; keep 0-point (injury) seasons
                 distributions[player.position_tier].append(
                     (points.actual_points - points.projected_points)
                     / points.projected_points
                 )
```

**Depends on:** nothing.

---

### L2: Position-weight zeroing works only by accident of dict key case — low

**Broken:** `Team.draft_turn_position_weights()` in `backend/models/team.py`
(~lines 119–152). `model.classes_` come from the historical-drafts CSV as
uppercase (`"QB"`), but the loop that zeroes filled positions writes lowercase
keys (`position_weights["qb"] = 0`) — it *adds* duplicate keys instead of
zeroing the real ones. Two consequences: (a) the renormalization at the end
divides by ~1.0 and is a no-op; (b) filled positions only end up at zero
because `simulate_pick()` in `app.py` later lowercases keys and the
later-inserted lowercase zeros happen to win the dict collision. Any change to
CSV case or iteration order silently breaks opponent modeling. There is also
no guard for `total_weight == 0`.

**Change:** Normalize class labels to lowercase when building the dict, and
add a zero-total fallback.

```diff
         position_weights = {}
         probabilities = model.predict_proba([[pick_number]])[0]
         for i, position in enumerate(model.classes_):
-            position_weights[position] = probabilities[i]
+            position_weights[position.lower()] = probabilities[i]
```

```diff
         # Recalculate the total weight and return the position weights
         total_weight = sum(position_weights.values())
+        if total_weight == 0:
+            open_positions = [
+                p
+                for p in starting_positions
+                if len(getattr(self, p)) < ps.model_dump()[p]
+            ]
+            return {p: 1 / len(open_positions) for p in open_positions}
         return {
             position: weight / total_weight
             for position, weight in position_weights.items()
         }
```

Note: after this, the `{k.lower(): v ...}` re-mapping in `simulate_pick()`
(`app.py` ~line 188) becomes a harmless no-op; leave it or remove it.

**Depends on:** nothing. Do before L3 (both touch weight flow; L3 is in
`app.py`, this is in `team.py`, so no textual conflict).

---

### L3: `simulate_pick` corrupts its weight list via remove-by-value; can crash on all-zero weights — low

**Broken:** `simulate_pick()` in `backend/app.py` (~lines 193–208).
`weights.remove(weights[positions.index(selection)])` removes the *first
occurrence of the value*, not the entry at the selection's index. Duplicate
weights are common (multiple zeroed positions), after which `positions` and
`weights` are misaligned and every subsequent draw uses wrong probabilities.
Separately, the all-zero reset only runs *after* a failed exhaustion; if the
weights are all zero on the first draw, `random.choices` raises `ValueError`
→ 500.

**Change:** Pop by index, and check the zero-total condition at the top of the
loop.

```diff
     position_players = []
     while len(position_players) == 0:
+        # If the total weights are zero, just go random
+        # (this can happen at the end of the draft)
+        if sum(weights) == 0:
+            weights = [1 for _ in positions]
         selection = random.choices(positions, weights=weights)[0]
         position_players = [
             x for x in getattr(players, selection) if x.drafted == False
         ]

         # If there are no players left in that position, remove it from the list
         if len(position_players) == 0:
-            weights.remove(weights[positions.index(selection)])
-            positions.remove(selection)
-
-            # If the total weights are zero, reset them and just go random
-            # (this can happen at the end of the draft)
-            if sum(weights) == 0:
-                weights = [1 for _ in positions]
+            index = positions.index(selection)
+            positions.pop(index)
+            weights.pop(index)
```

**Depends on:** nothing.

---

### L4: DST/K unlock gate conflates rounds with picks-per-round — low

**Broken:** `monte_carlo_draft()` in `backend/app.py` (~line 270) gates DST/K
into the simulation with `league.current_draft_turn > ROUND_SIZE * 7`.
`ROUND_SIZE` is the number of *rounds* (default 14); the number of picks in 7
rounds is `len(league.teams) * 7`. Correct only when team count happens to
equal `ROUND_SIZE` (as in the 14-team sample). A 10-team league would unlock
DST/K at pick 98 = round 10, not round 7.

**Change:**

```diff
-    if league.current_draft_turn > ROUND_SIZE * 7:  # Add DST & K after round 7
+    # Add DST & K after round 7 (turns = teams per round * 7 rounds)
+    if league.current_draft_turn > len(league.teams) * 7:
         results["dst"] = []
         results["k"] = []
```

If `ROUND_SIZE` is then unused in `app.py`, remove it from the import.

**Depends on:** nothing.

---

### L5: Multi-season historical uploads crash with KeyError, and tiers pool across seasons — medium

**Broken:** The `Players` model validator `assign_players_to_positions` in
`backend/models/player.py` (~lines 162–191). Each historical CSV row becomes a
`Player` whose `points` dict has exactly one season key, but the validator
sorts every position list by `x.points[year].projected_points` for *every*
year present in the file. With ≥2 seasons (which the README explicitly tells
users to provide), sorting the list by a year some players lack raises
`KeyError` → 500. Even without the crash, tier assignment ranks all seasons in
one list, so players from different seasons compete for the same tier slots
and tier membership is corrupted — which corrupts the injury distributions
built from those tiers.

**Change:** Rank and tier players *within each season*, then sort each
position list by each player's most recent season (preserves "best available
first" ordering for single-season current-player lists). Replace the block
from the `# For each position, order the players...` comment through the end
of the tier-assignment loop:

```python
        # Rank players within each season separately, so multi-season
        # historical files neither crash nor compete across seasons
        tiers = pt.model_dump()
        for year in data["years"]:
            for position in positions:
                if position not in data:
                    continue
                year_players = sorted(
                    [p for p in data[position] if year in p.points],
                    key=lambda x: x.points[year].projected_points,
                    reverse=True,
                )
                if position not in tiers:  # DST & K do not have tiers
                    for player in year_players:
                        player.position_tier = player.position
                else:
                    tier = tiers[position]
                    for i, player in enumerate(year_players):
                        if i < tier["1"]:
                            player.position_tier = f"{position}1"
                        elif i < tier["2"]:
                            player.position_tier = f"{position}2"
                        else:
                            player.position_tier = f"{position}3"

        # Keep each position list ordered by the player's most recent season
        for position in positions:
            if position in data:
                data[position] = sorted(
                    data[position],
                    key=lambda x: x.points[max(x.points)].projected_points,
                    reverse=True,
                )
```

(`max(x.points)` is a string max over 4-digit years, which sorts correctly.)
Note: `pt` is a module-level `PositionTiers()`; its `model_dump()` includes
`dst`/`k` (and an odmantic `id`), so DST/K currently *do* get tiers like
`dst1` — that interacts with chunk D6; preserve that behavior here.

**Verification:** Duplicate `backend/data/historical_players.csv` rows with a
second season value and POST it to `/league/{id}/historical_player` — must
return 200, and tier counts per season must match single-season counts.

**Depends on:** nothing, but do before D6 (both touch historical-tier flow).

---

### L6: `create_league`'s `snake_draft` parameter is silently ignored — low

**Broken:** In `backend/app.py`, `create_league()` (~line 347) accepts a
`snake_draft` query parameter but constructs the league with the module-level
config constant: `snake_draft=SNAKE_DRAFT`. API callers who set
`snake_draft=false` get a snake draft anyway, with no error.

**Change:**

```diff
     league = League(
         teams=teams,
-        snake_draft=SNAKE_DRAFT,
+        snake_draft=snake_draft,
         name=name,
```

**Depends on:** nothing. Related: S3 removes the duplicate `snake_draft` field
declaration on the `League` model.

---

### L7: Draft order is built from the global `ROUND_SIZE`, not the league's `round_size` — low

**Broken:** The `League` model validator in `backend/models/team.py`
(~line 289) builds the draft order with `for i in range(ROUND_SIZE)` (the
env-var global), ignoring the league's own `round_size` field that the API
accepts and stores. Per-league round counts are cosmetic.

**Change:**

```diff
         # For the number of rounds, create the draft order as a list
         data["draft_order"] = []
         team_indices = [data["teams"].index(team) for team in data["teams"]]
-        for i in range(ROUND_SIZE):
+        rounds = int(data.get("round_size", ROUND_SIZE))
+        for i in range(rounds):
```

**Depends on:** nothing. S4 (validator hardening) touches adjacent lines —
if doing both, do L7 first.

---

### L8: Starter-filling and pick weights use env-var position sizes, not the league's — high

**Broken:** `backend/models/team.py` builds a module-level
`ps = PositionSizes()` from env vars (~line 17) and uses it in
`fill_starters()` (~line 34), `Team.draft_turn_position_weights()` (~line 135),
and `Team.randomized_starter_points()` (via `fill_starters`). The
`league.position_sizes` object that the API accepts (`qb_size`, `rb_size`, …
on `POST /league`) is stored but never consulted. Any league whose lineup
differs from the env defaults gets simulations for the wrong lineup, silently.

**Change (approach — this is a plumbing refactor):**
1. Add a field to `Team`: `position_sizes: PositionSizes = PositionSizes()`.
2. In `create_league()` (`backend/app.py`), build the `PositionSizes` object
   first and pass it into every `Team(...)` constructor as well as the
   `League`.
3. Change `fill_starters(roster)` to `fill_starters(roster, sizes: PositionSizes)`
   and replace every `ps.` / `ps.model_dump()` reference inside it with
   `sizes`.
4. In `Team.autofill_starters` (model validator), call
   `fill_starters(data["roster"], PositionSizes(**data["position_sizes"]) if
   isinstance(data.get("position_sizes"), dict) else data.get("position_sizes")
   or PositionSizes())`.
5. In `draft_turn_position_weights` and `randomized_starter_points`, use
   `self.position_sizes` instead of `ps`.
6. Delete the module-level `ps` once nothing references it.

Note `Team` objects are re-instantiated from `model_dump()` in
`League.add_player_to_current_draft_turn_team` — the new field survives that
automatically. Existing DB documents lack the field and will get the default;
acceptable for a dev tool (or delete stale leagues).

**Verification:** Create a league with `rb_size=3` via the API and confirm a
team's `rb` starters list holds 3 players after enough picks.

**Depends on:** L2 (its zero-total fallback reads `ps.model_dump()` — update
that line to `self.position_sizes` here). Touches the same functions as L2;
do L2 first.

---

### L9a: A player can be drafted twice — low

**Broken:** `draft_player()` in `backend/app.py` (~lines 215–246) never checks
`player.drafted`. `POST /draft/{draft_id}/pick?name=X` with an already-drafted
name puts the same player on two rosters and advances the turn. The simulator
path is unaffected (it only offers undrafted players), but manual picks —
the primary UI flow — are unguarded.

**Change:** After resolving the player by name in `draft_player()`:

```diff
     else:
         player = player[0]
+    if player.drafted:
+        raise HTTPException(status_code=400, detail="Player already drafted")
```

**Depends on:** nothing.

---

### L9b: Picks and simulations after the draft ends crash with IndexError — low

**Broken:** When the draft order is exhausted, `league.draft_order` is `[]`,
and `add_player_to_current_draft_turn_team` (`backend/models/team.py`
~line 333) does `self.draft_order[0]` → `IndexError` → 500. Reachable from
`POST /draft/{draft_id}/pick` and `POST /draft/{draft_id}/monte_carlo` in
`backend/app.py`.

**Change:** In `make_draft_pick()` and `run_monte_carlo_simulation()`
(`backend/app.py`), immediately after fetching the draft:

```diff
     draft = await get_a_draft_by_id(draft_id)
+    if not draft.league.draft_order:
+        raise HTTPException(status_code=400, detail="Draft is complete")
```

**Depends on:** nothing.

---

### L9c: Monte Carlo crashes if no team is flagged as the simulator — low

**Broken:** `monte_carlo_draft()` in `backend/app.py` (~lines 268, 299)
computes `simulator_team = [...]` then indexes `simulator_team[0]` without
checking emptiness. A teams CSV with no `Simulator=True` row yields
`IndexError` → 500 on every simulation.

**Change:**

```diff
     simulator_team = [i for i, team in enumerate(league.teams) if team.simulator]
+    if not simulator_team:
+        raise HTTPException(
+            status_code=400, detail="League has no simulator team"
+        )
```

(Better: reject the teams CSV at upload time — see D3 — but this guard is
still correct defense.)

**Depends on:** nothing.

---

### L10: Exhausted positions poison Monte Carlo averages with 0-sentinels — low

**Broken:** In `monte_carlo_draft()` (`backend/app.py` ~line 290), when a
candidate position has no undrafted players the code appends a literal `0` to
that position's results. The final average then blends real simulated scores
with sentinel zeros, dragging the position's reported value toward 0 rather
than marking it unavailable.

**Change:**

```diff
             if len(possible_players) == 0:
-                results[position].append(0)  # No players left
-                continue
+                continue  # No players left; average the samples we have
```

```diff
     # Turn the arrays into averages
     for position in results.keys():
-        results[position] = round(sum(results[position]) / len(results[position]), 2)
+        samples = results[position]
+        results[position] = (
+            round(sum(samples) / len(samples), 2) if samples else 0.0
+        )
```

**Depends on:** nothing. Do before L11 (same function).

---

### L11 (optional): Monte Carlo reports point estimates with no uncertainty — medium

**Broken:** `monte_carlo_draft()` (`backend/app.py`) runs a wall-clock-bounded
loop (30s) with heavy per-iteration deep copies, so it may complete only tens
of full-draft simulations. It returns bare averages; the difference between
the "best" and second-best position is often within Monte Carlo noise, and the
user can't tell.

**Change:** Track per-position sample counts and standard error; extend
`MonteCarloSimulationResult` (`backend/models/team.py` ~line 369) with
additive optional fields, e.g. `qb_se: float = 0`, … or a
`counts: dict = {}` / `standard_errors: dict = {}` pair. Compute
`se = stdev(samples) / sqrt(len(samples))` (guard `len < 2`). Update the
frontend type `MonteCarloResults` in `frontend/types/index.ts` only if you
want to display it — additive fields don't break the existing UI.

**Depends on:** L10 (same function/result shape). Optional; skip under time
pressure.

---

### L12: Bare `except:` hides the real cause of regression-training failures — low

**Broken:** `fit_logistic_regression_model()` in `backend/app.py`
(~lines 161–170) wraps training in `except:` — which swallows everything
(malformed picks, single-class `y`, even `KeyboardInterrupt`) and rebrands it
as a generic 500 with no cause.

**Change:**

```diff
     try:
         draft_pick_model = LogisticRegression(max_iter=1000)
         x = [[int(x)] for x in logistic_regression_variables.x]
         y = logistic_regression_variables.y
         draft_pick_model.fit(x, y)
-    except:
+    except (ValueError, TypeError, KeyError) as exc:
         raise HTTPException(
-            status_code=500, detail="Failed to train logistic regression model"
+            status_code=400,
+            detail=f"Failed to train logistic regression model: {exc}",
         )
     return draft_pick_model
```

**Depends on:** nothing.

---

### L13: Two player GET endpoints iterate the model instead of its list (always 500) — low

**Broken:** In `backend/app.py`, `get_players()` (~line 485) and
`get_player()` (~line 509) iterate `league.players` directly. `Players` is a
Pydantic model, so iteration yields `(field_name, value)` tuples;
`player.drafted` raises `AttributeError` → 500 on every call. The shipped
frontend never calls these, so the break is latent — but they're public API.

**Change:**

```diff
     if draftable_only:
-        return Players(players=[player for player in players if not player.drafted])
+        return Players(
+            players=[p for p in players.players if not p.drafted]
+        )
```

```diff
-    player = [player for player in league.players if player.name == player_name]
+    player = [p for p in league.players.players if p.name == player_name]
```

**Depends on:** nothing.

---

## Phase 2 — Data pipeline

### D1: Docker bakes `LOCAL=false` over any user `.env`; `DRAFT_YEAR` can never be set in Docker — low ⚠️ do first if running the app

**Broken:** `backend/Dockerfile` line 13 runs `RUN echo "LOCAL=false" > .env`,
clobbering the `.env` the README tells users to create. `docker-compose.yml`
passes no `environment:` to the backend. Meanwhile `backend/models/config.py`
defaults `DRAFT_YEAR` to the *current calendar year*, but the shipped
`players.csv` is Season 2024 — every points lookup is keyed by
`str(DRAFT_YEAR)`, so in 2025+ a fresh `docker-compose up` throws
`KeyError: '<current year>'` (500) the moment players are uploaded
(`create_max_points`, `app.py` ~line 119). There is currently no way to
configure the backend in Docker without editing the Dockerfile.

**Change:** Delete the `.env` bake from the Dockerfile and pass env through
compose.

`backend/Dockerfile`:
```diff
 COPY . /backend

-RUN echo "LOCAL=false" > .env
-
 EXPOSE 8000
```

`docker-compose.yml`:
```diff
   fastapi-backend:
     build:
       context: ./backend
       dockerfile: Dockerfile
     container_name: fastapi-backend
     restart: always
+    environment:
+      - LOCAL=false
+      - DRAFT_YEAR=2024  # must match the Season column in your players CSV
     ports:
       - 8000:8000
```

(`load_dotenv()` does not override real environment variables, so compose
values win; a local `.env` still works for non-Docker runs.)

**Depends on:** nothing. C2 (volume mount) depends on this.

---

### D2: Players upload accepts data for the wrong season, deferring the failure — low

**Broken:** `add_players_to_league()` in `backend/app.py` (~lines 432–472)
keys each player's points by the CSV's `Season` column, but everything
downstream reads `points[str(DRAFT_YEAR)]`. If the CSV season ≠ `DRAFT_YEAR`,
the upload half-succeeds and later requests 500 with an opaque `KeyError`.

**Change:** In `add_players_to_league()`, before constructing players:

```python
    seasons = {str(row["Season"]) for row in rows}
    if str(DRAFT_YEAR) not in seasons:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Players file seasons {sorted(seasons)} do not include "
                f"the configured DRAFT_YEAR ({DRAFT_YEAR})"
            ),
        )
```

(Requires materializing the DictReader into a list first — trivial, or fold
into D3's helper.) Import `DRAFT_YEAR` from `models.config` (already imported).

**Depends on:** cleanest after D3, but can be done standalone.

---

### D3: No CSV validation on any upload — missing columns become opaque 500s — medium

**Broken:** All four upload endpoints in `backend/app.py` (`create_league`,
`add_players_to_league`, `add_historical_player_data_to_league`,
`add_historical_draft_data_to_league`) index rows like `row["Player"]`,
`row["Pick"]` with no header or type checks. A missing/renamed column or
non-numeric value produces a raw `KeyError`/`ValidationError` → 500 with no
actionable message. These CSVs are hand-assembled by users, so this is the
most common failure mode of the whole app.

**Change:** Add one helper and use it in all four endpoints:

```python
def read_csv_upload(content: bytes, required_columns: set) -> list:
    """Parse an uploaded CSV and 422 with a clear message on bad shape"""
    rows = list(csv.DictReader(content.decode("utf-8-sig").splitlines()))
    if not rows:
        raise HTTPException(status_code=422, detail="CSV file is empty")
    missing = required_columns - set(rows[0].keys())
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"CSV missing required columns: {sorted(missing)}",
        )
    return rows
```

Then in each endpoint replace
`data = csv.DictReader((await file.read()).decode("utf-8-sig").splitlines())`
with e.g.
`data = read_csv_upload(await file.read(), {"Season", "Player", "Pos", "Team", "Projected FFP"})`
(columns per endpoint: teams = `{"Name", "Order", "Owner", "Simulator"}`;
historical players adds `"Actual FFP"`; historical drafts =
`{"Pick", "Pos"}`). Optionally wrap numeric coercions
(`Projected FFP`, `Pick`, `Order`) to raise 422 naming the offending row.

**Depends on:** nothing. D2 and D5 are simplest after this lands.

---

### D4: `Simulator == 1` compares a string to an int (dead code) — low

**Broken:** `create_league()` in `backend/app.py` (~line 341):
`row["Simulator"] == 1` — `csv.DictReader` yields strings, so this branch is
never true. Only `"True"` and `"1"` (strings) work; `"true"`, `"TRUE"`,
`" 1"` etc. silently produce non-simulator teams, which later breaks Monte
Carlo (see L9c).

**Change:**

```diff
-                simulator=row["Simulator"] == "True"
-                or row["Simulator"] == 1
-                or row["Simulator"] == "1",
+                simulator=str(row["Simulator"]).strip().lower()
+                in ("true", "1"),
```

**Depends on:** nothing.

---

### D5: Duplicate player names silently collapse into one player — low

**Broken:** Players are identified by name string everywhere
(`draft_player()`, `make_draft_pick()`, both in `backend/app.py`). Two rows
with the same `Player` value (real NFL name collisions happen) become two
`Player` objects, but every lookup takes the first match — the second is
undraftable and picks are ambiguous.

**Change:** In `add_players_to_league()` after parsing rows:

```python
    names = [row["Player"] for row in rows]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise HTTPException(
            status_code=422,
            detail=f"Duplicate player names in CSV: {duplicates} "
            "(disambiguate, e.g. append team abbreviation)",
        )
```

**Depends on:** cleanest after D3.

---

### D6: Historical files containing DST/K rows crash distribution building — low

**Broken:** Tier assignment (`backend/models/player.py`) gives DST/K players
tiers like `dst1`/`k1` (because `PositionTiers` defines dst/k entries), but
`PositionTierDistributions` (`backend/models/position.py`) has no such fields.
`create_historical_distributions()` (`backend/app.py` ~line 152) then calls
`PositionTierDistributions(**distributions)` with unexpected keys. The sample
historical CSV has no DST/K rows, so this is latent — but the README doesn't
forbid them.

**Change:** In `create_historical_distributions()`, filter to known tiers
before constructing:

```diff
-    # Return the position tier distributions
-    return PositionTierDistributions(**distributions)
+    # Return the position tier distributions (drop tiers we don't model, e.g. DST/K)
+    known = set(PositionTierDistributions.model_fields.keys())
+    return PositionTierDistributions(
+        **{k: v for k, v in distributions.items() if k in known}
+    )
```

**Depends on:** do after L5 (same code area).

---

## Phase 3 — Structural

### S1: 30-second synchronous Monte Carlo blocks the entire API — medium

**Broken:** `run_monte_carlo_simulation()` and `get_draft_results()` in
`backend/app.py` (~lines 709–744) are `async def` routes that call heavy
synchronous CPU loops (`monte_carlo_draft` runs ~30s wall-clock;
results runs 1000 randomizations × teams with deep copies) directly on the
event loop. Uvicorn runs a single loop, so *every* other request — including
the frontend's draft polling — freezes until the loop finishes.

**Change:** Run the CPU work in Starlette's threadpool.

```diff
+from starlette.concurrency import run_in_threadpool
```

```diff
     draft = await get_a_draft_by_id(draft_id)
-    return monte_carlo_draft(draft.league)
+    return await run_in_threadpool(monte_carlo_draft, draft.league)
```

For `get_draft_results()`, extract the team loop into a sync helper:

```python
def compute_draft_results(league: League) -> dict:
    results = {}
    for team in league.teams:
        points = [
            team.randomized_starter_points(
                distributions=league.position_tier_distributions,
                max_points=league.position_max_points,
            )
            for _ in range(1000)
        ]
        results[team.name] = round(sum(points) / len(points), 2)
    return results
```

and call `return await run_in_threadpool(compute_draft_results, draft.league)`.

**Depends on:** nothing. (True concurrency-safety of simultaneous picks is
S8, separate.)

---

### S2: `datetime.now()` evaluated once at import — all objects share one timestamp — low

**Broken:** `backend/models/team.py` declares
`created: datetime.datetime = datetime.datetime.now()` on `LeagueSimple`
(~line 228), `League` (~line 240), `DraftSimple` (~line 355), and `Draft`
(~line 366). The default is evaluated at import time, so every league/draft
created by a process shares the same stale "created" time. (The endpoints
happen to pass `created=datetime.now()` explicitly in two places, masking
this partially.)

**Change:** Use `default_factory` on all four:

```diff
-from pydantic import BaseModel, ConfigDict, model_validator
+from pydantic import BaseModel, ConfigDict, Field, model_validator
```

```diff
-    created: datetime.datetime = datetime.datetime.now()
+    created: datetime.datetime = Field(default_factory=datetime.datetime.now)
```

(Pydantic's `Field` works for both the `BaseModel` classes and the odmantic
`Model` classes here; if odmantic complains on `League`/`Draft`, use
`from odmantic import Field as ODField` for those two.)

**Depends on:** nothing.

---

### S3: `League.snake_draft` is declared twice — low

**Broken:** `backend/models/team.py` declares `snake_draft: bool = True`
(~line 245) and again `snake_draft: bool = SNAKE_DRAFT` (~line 251). The
second silently wins; the duplication invites drift.

**Change:** Delete one declaration; keep a single
`snake_draft: bool = SNAKE_DRAFT`.

```diff
     roster_size: int = 14
     position_sizes: PositionSizes = PositionSizes()
     round_size: int = 14
-    snake_draft: bool = True
     ready_for_draft: bool = False
```

**Depends on:** nothing. Related to L6.

---

### S4: `League` validator crashes on missing dict keys — low

**Broken:** The `League` model validator in `backend/models/team.py` assumes
`data["teams"]` (~line 270) and `data["current_draft_turn"]` (~line 299)
exist. Constructing `League(...)` without them raises a bare `KeyError` from
inside the validator (this is why `create_league` must pass
`current_draft_turn=0` explicitly). Also `data["snake_draft"]` (~line 291)
has the same problem.

**Change:** Use `.get()` with defaults at the top of the validator:

```python
        data.setdefault("teams", [])
        data.setdefault("current_draft_turn", 0)
        data.setdefault("snake_draft", SNAKE_DRAFT)
```

and adjust the empty-teams path (`sorted([])` and the draft-order loop are
already safe with empty lists).

**Depends on:** L7 touches adjacent lines — land L7 first.

---

### S5: Throwaway value objects declared as database `Model`s — low

**Broken:** `PlayerPointsRandomized` (`backend/models/player.py` ~line 26) and
`PositionTiers` (`backend/models/position.py` ~line 68) subclass odmantic
`Model` — collection-backed documents that mint ObjectIds — but are used as
in-memory value objects, never saved. Wasteful and misleading; `model_dump()`
of `PositionTiers` even carries a spurious `id` key.

**Change:** In `player.py`, change `PlayerPointsRandomized(Model)` to a plain
Pydantic `BaseModel` (add `from pydantic import BaseModel`); in
`position.py`, change `PositionTiers(Model)` to `BaseModel` and drop the
now-unused `Model` import if nothing else uses it.

**Depends on:** L5 reads `pt.model_dump()` — no conflict, but re-run its
verification after this.

---

### S6: Deleting a league leaves its drafts (and draft-copy leagues) behind — low

**Broken:** `delete_league()` in `backend/app.py` (~lines 389–396) deletes
only the league document. `Draft` documents reference copied leagues; deleting
a referenced league orphans drafts, and `get_draft` on an orphan 500s when
odmantic can't resolve the reference. Draft-copy leagues also accumulate
forever.

**Change:**

```diff
 async def delete_league(league_id: ObjectId):
     league = await get_a_league_by_id(league_id)
+    drafts = await engine.find(Draft, Draft.league == league.id)
+    for draft in drafts:
+        await engine.delete(draft)
     await engine.delete(league)
     return Response(status_code=204)
```

(Full cleanup of copy-leagues when their draft is deleted would need a
delete-draft endpoint, which doesn't exist yet — out of scope.)

**Depends on:** nothing. S7 depends on this.

---

### S7: `get_drafts` is an N+1 query loop — low

**Broken:** `get_drafts()` in `backend/app.py` (~lines 654–663) fetches all
leagues, then queries drafts per league. It exists partly to hide orphaned
drafts; with S6 in place, orphans shouldn't occur.

**Change:**

```diff
 async def get_drafts():
-    leagues = await engine.find(League)
-    drafts = []
-    for league in leagues:
-        drafts += await engine.find(Draft, Draft.league == league.id)
-    return drafts
+    return await engine.find(Draft)
```

**Depends on:** S6 (otherwise pre-existing orphaned drafts will make
`engine.find(Draft)` fail on reference resolution). If the DB may contain
orphans from before S6, wipe the dev DB or keep the loop.

---

### S8 (optional): Concurrent picks race — last write wins — medium

**Broken:** `make_draft_pick()` (`backend/app.py`) loads the draft, mutates it
in memory, and saves the whole document. Two simultaneous picks both read turn
N and both write; one pick is silently lost and the turn advances once.
Low-stakes for a single-user dev tool, real if multiple browsers hit one
draft.

**Change (approach):** Optimistic concurrency — send the expected turn with
the request (`expected_turn: int` query param), and reject if
`draft.league.current_draft_turn != expected_turn` with 409. Requires a
one-line frontend change in `frontend/api/services/draft.ts` to pass the
turn it rendered. Alternatively an `asyncio.Lock` keyed by draft id (single
process only).

**Depends on:** nothing. Optional.

---

### S9 (optional): League document grows O(n²) — full team snapshot stored per pick — high

**Broken:** `add_player_to_current_draft_turn_team()`
(`backend/models/team.py` ~line 342) appends a complete `Team` copy (entire
roster) to `draft_results` on every pick, on top of ~490 embedded players.
The document is rewritten on every pick; a 196-pick draft stores ~196
progressively larger team snapshots.

**Change (approach):** Replace `draft_results: List[Team]` with a lightweight
pick log, e.g. `List[dict]` of `{"turn": int, "team_index": int,
"player_name": str, "position": str}`. **Breaking change for the frontend:**
`frontend/app/draft-room/[id]/page.tsx` and `frontend/types/index.ts` render
`draft_results` — audit their usage and update the rendering to look up teams
by index. Only attempt with time to update and manually test the frontend.

**Depends on:** nothing backend-side; frontend changes are mandatory.
Optional — skip unless doc growth is actually hurting.

---

### S10: No tests at all — high

**Broken:** Nothing exercises the simulation math, validators, or endpoints —
which is how L1, L2, L5, and L13 survived. The repo has no `tests/`, no CI.

**Change (approach):** Add `backend/tests/` with pytest + httpx
(`fastapi.testclient`), using the shipped sample CSVs as fixtures and
`mongomock-motor` (or a real Mongo via compose) for the engine. Minimum
suite:
1. Upload teams/players/historical/drafts happy path → league
   `ready_for_draft == True`.
2. `create_historical_distributions` includes a 0-actual-points row (guards
   L1) and never divides by zero.
3. Multi-season historical upload succeeds and tiers are per-season (guards
   L5).
4. `simulate_pick` with a filled position never selects it (guards L2/L3);
   run with a fixed `random.seed`.
5. Double-pick of the same name returns 400 (guards L9a); pick after draft
   completion returns 400 (guards L9b).
6. Bad CSV (missing column) returns 422 (guards D3).
Add `pytest` + chosen mock lib to `backend/requirements.txt` (dev section or
separate `requirements-dev.txt`).

**Depends on:** best written after Phase 1 + D3 land (tests assert the fixed
behavior). D1 if tests run against Docker.

---

## Phase 4 — Cosmetic / infrastructure

### C1: MongoDB 4.4 is EOL, unauthenticated, and published to the host — low

**Broken:** `docker-compose.yml` uses `mongo:4.4` (EOL Feb 2024), no auth,
and maps `27017:27017` onto the host — exposed to the LAN if the host
firewall allows.

**Change:**

```diff
   mongodb:
-    image: mongo:4.4
+    image: mongo:7
     container_name: mongodb
     restart: always
-    ports:
-      - 27017:27017
+    # Only needed for host-side tooling; keep it loopback-only if so:
+    # ports:
+    #   - 127.0.0.1:27017:27017
```

⚠️ **Data caveat:** an existing `mongodb_data` volume written by 4.4 will
*not* start under 7.x (WiredTiger version gap). For this dev tool, wipe it:
`docker compose down && docker volume rm <project>_mongodb_data`. Note this in
the commit message. The backend connects over the compose network
(`mongodb://mongodb:27017`), so removing the host port mapping doesn't affect
the app; local non-Docker runs (`LOCAL=true`) need the loopback mapping
uncommented.

**Depends on:** nothing.

---

### C2: Backend volume mount points at the wrong path (dead mount) — low

**Broken:** `docker-compose.yml` mounts `./backend:/app`, but the backend
image's code and `WORKDIR` are `/backend` — the mount does nothing (no live
reload; uvicorn has no `--reload` anyway). Worse, if someone "fixes" it to
`/backend` *before* D1 lands, the host dir shadows the image's baked `.env`
(`LOCAL=false`), the app tries `mongodb://localhost:27017` inside the
container, and every request hangs.

**Change:** either delete the mount (simplest):

```diff
     ports:
       - 8000:8000
-    volumes:
-      - ./backend:/app
     depends_on:
       - mongodb
```

or, for a live-reload dev setup, mount to the right path and enable reload:

```diff
     volumes:
-      - ./backend:/app
+      - ./backend:/backend
```
plus in `backend/Dockerfile`:
```diff
-CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
+CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

**Depends on:** D1 **must land first** (removes the baked `.env` that the
corrected mount would shadow, and moves `LOCAL=false` into compose env).

---

### C3: Frontend build is unreproducible — lockfiles disabled, unpinned base image — medium

**Broken:** `frontend/.npmrc` contains `package-lock=false`; there is no
lockfile; `frontend/Dockerfile` uses unpinned `node:alpine` with `^` semver
ranges in `package.json`. Every fresh build resolves a different dependency
tree; a future Node major or NextUI minor can break the build with zero code
changes.

**Change:**
1. Delete `frontend/.npmrc` (or remove the `package-lock=false` line).
2. Run `npm install` in `frontend/` to generate `package-lock.json`; commit it.
3. Pin the base image and use `npm ci`:

```diff
-FROM node:alpine
+FROM node:20-alpine
```
```diff
-RUN npm install
+RUN npm ci
```

**Depends on:** nothing. Do before C4.

---

### C4: Frontend container runs the Next.js dev server — low

**Broken:** `frontend/Dockerfile` `CMD ["npm", "run", "dev"]` — unoptimized
dev server with HMR as the "deployed" frontend; slow, memory-hungry, and dev
error overlays face the user.

**Change:**

```diff
 COPY . /frontend

+RUN npm run build
+
 EXPOSE 3000

-CMD ["npm", "run", "dev"]
+CMD ["npm", "start"]
```

**Depends on:** C3 (reproducible install), C5 if the API URL is made
build-time configurable (Next.js inlines `NEXT_PUBLIC_*` at build).

---

### C5: API base URL hardcoded to `http://localhost:8000` — low

**Broken:** `frontend/api/services/base.ts` hardcodes the backend URL, and
the backend's CORS allowlist (`backend/app.py` ~lines 73–78) only permits
`localhost`/`127.0.0.1` origins. Works only when the browser runs on the
Docker host; any remote or renamed deployment breaks silently.

**Change:** `frontend/api/services/base.ts`:

```diff
 export const baseQuery = {
-  baseUrl: "http://localhost:8000",
+  baseUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
 };
```

and pass `NEXT_PUBLIC_API_URL` via compose (build arg if using C4's
production build, since Next inlines it at build time). Optionally make the
backend CORS origins list extendable via an env var in the same spirit.

**Depends on:** none to land; interacts with C4 (build-time inlining).

---

### C6: Obsolete `version: '3.8'` key in compose file — low

**Broken:** Compose v2 ignores the top-level `version:` key and prints a
warning on every invocation.

**Change:**

```diff
-version: '3.8'
-
 services:
```

**Depends on:** nothing.

---

### C7: Duplicate sample CSVs in `frontend/public/` can drift from `backend/data/` — low

**Broken:** All four sample CSVs exist in both `frontend/public/` (served as
downloadable samples) and `backend/data/`. Two copies of "the same" data with
no sync mechanism; they will drift.

**Change:** First check usage — grep `frontend/` for `players.csv`,
`teams.csv`, etc. If the setup page links them as sample downloads, keep
`frontend/public/` as the single copy and delete `backend/data/` (the backend
never reads its copies at runtime — they're reference samples), noting the
move in the README. If unused, delete the `frontend/public/` copies instead.

**Depends on:** nothing.

---

### C8: No health checks or startup ordering guarantees — low

**Broken:** `docker-compose.yml` `depends_on: [mongodb]` only orders container
*start*, not readiness. Motor connects lazily so this mostly works, but the
first requests after `up` can race Mongo's init, and there's no healthcheck
for any service.

**Change:**

```diff
   mongodb:
     image: mongo:7
     ...
+    healthcheck:
+      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
+      interval: 10s
+      timeout: 5s
+      retries: 5
```
```diff
     depends_on:
-      - mongodb
+      mongodb:
+        condition: service_healthy
```

**Depends on:** C1 (mongosh exists in mongo:5+ images; 4.4 ships `mongo`
instead).

---

## Deferred / design-decision chunks (not scheduled)

- **O1 — Asymmetric clipping bias:** `Player.randomized_points()`
  (`backend/models/player.py` ~lines 99–111) caps upside at
  top-projection × (1 + `MAX_RANDOM_ADJUSTMENT`) but floors downside at 0,
  biasing the randomized mean low. A fix (e.g., resample instead of clip, or
  widen the cap) changes model behavior — decide deliberately, don't "fix"
  casually.
- **O2 — Logistic regression realism:** one feature (raw overall pick) with
  linear multinomial logits partitions the pick axis into contiguous
  per-position regions — it cannot represent two-wave patterns (elite QBs
  early *and* QB2s late). Trained on 192 picks = one season's draft.
  Improvements: add spline/round-bucket features, gather multiple seasons of
  draft history, or replace with empirical per-round position frequencies.
  High token, changes recommendations; needs owner sign-off.
- **O3 — Backup valuation realism:** starter refill compares *season totals*,
  overvaluing rostered backups vs. streaming (real backups replace weeks, not
  seasons; busts get dropped to waivers). Model redesign, not a bug fix.

---

## Handoff summary

This branch (`claude/fantasy-football-audit-m830hx`) contains an architecture
audit and this execution plan for `joewlos/fantasy_football_monte_carlo_draft_simulator`
(fork: `cartepavilizard/...`) — a FastAPI + ODMantic + MongoDB backend
(`backend/app.py` holds all routes and simulation logic; models in
`backend/models/`) with a Next.js frontend and docker-compose. **No code
fixes have been applied yet** — the plan above is the full backlog. Work the
chunks top-to-bottom: Phase 1 items L1–L13 are correctness bugs in the Monte
Carlo/regression/injury logic (mostly small single-file diffs in
`backend/app.py` and `backend/models/team.py`/`player.py`), Phase 2 is CSV
upload validation, Phase 3 structural (start with S1, the event-loop
blocker), Phase 4 Docker/frontend hygiene — but note chunk **D1 first if you
need to run the app**, since fresh environments crash on the `DRAFT_YEAR`
default (sample data is Season 2024, config defaults to the current year, and
the Dockerfile clobbers `.env`). Honor the flagged dependencies (L2→L3→L8,
L7→S4, L5→D6, S6→S7, D1→C2, C1→C8, C3→C4/C5); everything else is
independent and safe to do in any order. Line numbers reference commit
`2e33e3f` — match on quoted code as they drift. There are no tests until S10
lands, so verify Phase 1 chunks by exercising the API with the sample CSVs in
`backend/data/` (upload teams → players → historical players → historical
drafts → create draft → pick/monte-carlo). Commit one chunk per commit with
the chunk ID in the message, and push to this branch only.
