# D2 — Official Practice-Report Ingestion Strategy (spec)

> **Status:** Design locked (2026-07-11, Fable design pass). This is
> the frontier half of a [SPLIT] task: source selection and parsing
> strategy. The cheap half (a Sonnet session) writes the recurring
> transform in `data_sources/nflverse_injuries.py` against this spec
> verbatim. Fills the `PracticeReport` and `InjuryDesignation` models
> that B2 already settled in `models/inseason.py`.

## 1. Source decision: nflverse `injuries` releases

Candidates considered:

- **NFL.com official injury report pages** (the primary source):
  per-team HTML, no API, markup redesigned mid-season more than once
  historically — the exact “formats change mid-season” failure this
  task was flagged for. Scraping 32 team pages daily is the fragile
  path; rejected.
- **ESPN injuries API**: game-status designations only (questionable/
  out) — no practice participation, which is the entire point (the
  early signal *ahead of* designations). Rejected.
- **nflverse `injuries` data release**
  (`https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv`):
  machine-readable aggregation of the official NFL practice/game
  reports, one HTTPS GET per season file, free, no auth, updated on the
  league’s report cadence (Wed–Sat during the season). **Same source
  family, transport seam, rate-limit story, and failure-mode pattern C4
  already established with `data_sources/nflverse.py`** — the org
  maintains the format-change problem for us, with a public changelog.
  Chosen.

Accepted tradeoff: nflverse lags the primary source by up to a few
hours. For an early signal that beats ESPN designation updates by
~1–2 days, hours are fine; if they ever aren’t, the seam (Transport +
adapter) is where a direct scraper would slot, later, deliberately.

## 2. The columns (and the mid-season-change defense)

Expected schema of `injuries_{season}.csv` (verify against the current
season’s file at implementation time — the mapping table below is the
single place to update):

| CSV column | → model field | notes |
| --- | --- | --- |
| `season`, `week` | `season`, `week` | keep `game_type == "REG"` only |
| `full_name` | `player_name` | |
| `team` | `nfl_team` | normalize via the C4 team-abbrev map (`data_sources/nfl_teams.py`) |
| `position` | `position` | |
| `practice_status` | `participation` | via `PARTICIPATION_MAP` below |
| `report_status` | `designation` (InjuryDesignation) | via `DESIGNATION_MAP` below |
| `practice_primary_injury` / `report_primary_injury` | `note` | body part, e.g. “Hamstring” |
| `date_modified` | `report_date` / `updated_at` | ISO timestamp |

```python
PARTICIPATION_MAP = {
    "full participation in practice": "full",
    "limited participation in practice": "limited",
    "did not participate in practice": "dnp",
}
DESIGNATION_MAP = {
    "questionable": "questionable", "doubtful": "doubtful",
    "out": "out",
}
```

Both maps match on the **lowercased, stripped** CSV value. This
mapping-table-plus-tripwire structure is the format-change design:

1. **Column access is fail-soft per row**: a missing/blank cell skips
   the derived field, not the row; a missing *column* (schema change)
   is caught at header validation — required columns
   (`season, week, full_name, team`) missing → the whole ingest fails
   as one logged `error_kind="parse"` and **last good data stays**
   (B1’s replace-only-after-success contract).
2. **Unknown-value tripwire**: rows whose `practice_status` is
   non-blank but unmapped are counted; if
   `unmapped / non_blank > UNMAPPED_TRIPWIRE (0.2)`, the ingest is
   treated as a parse failure (logged, nothing replaced) and a
   notification `kind="ingest_format_change"`,
   `dedupe_key=f"nflverse_injuries:format:{season}:w{week}"` tells the
   user the wording changed upstream and the map needs one new entry.
   Below the tripwire, unmapped rows are skipped and counted in the
   sync log’s error text — tolerant to one-off junk, loud on drift.
3. Blank `practice_status` with a non-blank `report_status` is normal
   (game-status-only updates late in the week): write the
   `InjuryDesignation`, skip the `PracticeReport`.

## 3. Write semantics

One CSV row is a player-week’s **current** state (nflverse re-states
rows as the week’s reports accumulate: Wed DNP → Thu limited …
`date_modified` moves). Two collections, two write rules:

- **`PracticeReport`** — the day-by-day trail is built by *ingesting
  daily and keying on `(season, week, player_name, report_date)`*:
  upsert per key (the same practice day re-fetched updates in place;
  a new `date_modified` day inserts a new row). Never delete prior
  days — the trail (Wed DNP, Thu limited, Fri full) IS the signal
  shape users know.
- **`InjuryDesignation`** — current-state: replace per
  `(season, week, player_name)` (delete + insert, B1 pattern). ESPN’s
  roster `injury_status` remains a separate signal; consumers (E1/E4)
  already define their precedence (newer wins).

Sync-log sections: log as section `"practice_reports"` with
`espn_league_id=None` (league-independent, like `pro_schedule`).
**Add `"practice_reports"` to `SYNC_SECTIONS`** so `league_freshness()`
surfaces its staleness in every envelope automatically — that is the
only `models/inseason.py` change this task is allowed.

## 4. Downgrade alerts (the C4-style consumer)

After each successful ingest, compare each player’s newest
participation to their previous report **within the same week**:

- Downgrade = `full→limited`, `full→dnp`, `limited→dnp`, or a
  first-report `dnp` (opening the week not practicing is itself news).
- Alert only for players **rostered in at least one synced league**
  (any `TeamWeekRoster`, live week) — same actionability filter as C4;
  free agents’ practice habits are the report view’s job, not push
  material.
- Through `ensure_notification`: `kind="practice_downgrade"`,
  `dedupe_key=f"practice:{season}:w{week}:{player_name}:{participation}"`
  (a player alerts once per severity level per week — Wed dnp and Thu
  dnp don’t double-page; dnp after an earlier limited alert does, being
  a new level).
- Copy is C8-framed and factual:
  “<Name> (<team> <pos>) did not practice Thursday (hamstring) after a
  limited Wednesday — official report, ahead of any ESPN status
  change.” Never speculate about availability; D3 notes and E1 curves
  do the interpreting.
- Upgrades (dnp→limited→full) never notify — good news keeps until the
  user opens the app. (`GET /inseason/practice_reports` shows both.)

## 5. Module & API shape

```python
# backend/data_sources/nflverse_injuries.py
class NflverseInjuriesAdapter:            # styled exactly like NflverseUsageAdapter
    def __init__(self, transport=None, ratelimiter=None): ...
    async def fetch_injuries(self, season) -> (List[PracticeReport], List[InjuryDesignation], stats)

async def ingest_practice_reports(engine, season, week=None) -> dict
    # fetch once; filter to `week` (default: every week present, which
    # in-season means the live week — the file only carries reported
    # weeks); apply write semantics; log one sync-log row; then run the
    # downgrade-alert pass. Never raises.
```

Scheduler: `InSeasonScheduler.run_now` calls it after the league sync,
guarded by `PRACTICE_INGEST_ENABLED` env (default false). Practice
reports only exist Wed–Sat; the ingest itself is cheap and idempotent,
so no weekday gate — an empty diff is a no-op.

Read endpoint (in `inseason_api.py`, both enforcement tests):
`GET /inseason/practice_reports?week=&player=&season=` →
`{week, reports: [PracticeReport dicts newest-first, grouped by
player], designations: [InjuryDesignation dicts]}`. League-independent
(no league in the path), same as `/inseason/usage_shifts`.

Config: `PRACTICE_INGEST_ENABLED=false`, `UNMAPPED_TRIPWIRE=0.2`.

## 6. Edge cases (all must be tested)

- Duplicate rows for one player-week (multiple `date_modified` days) →
  the daily-trail upsert handles it; test Wed/Thu/Fri sequence
  produces three `PracticeReport` rows and one final designation.
- Player on two teams in one season (traded): rows keyed by name +
  report_date; `nfl_team` reflects each row’s value. Name collisions
  (two “Josh Allen”s) are keyed apart by `nfl_team` in the alert
  dedupe? No — accept the name-key collision exactly as
  `PlayerWeekUsage` does (same limitation, same answer; do not invent
  an id-matching layer here).
- Season file missing early (before week 1 reports exist) → HTTP 404
  from GitHub: log `error_kind="http"`, keep going. Not an error state
  in August.
- `week=None` mid-season re-ingest re-processes prior weeks: upserts
  are idempotent, designations replace-per-week — safe by
  construction; test it.
- Downgrade comparison when the previous report is from a prior week →
  not a downgrade (weeks reset; a Wednesday dnp fires the first-report
  rule instead).

## 7. Worked example

Thursday week 6 ingest. CSV rows for K. Walker (SEA RB):
`{week: 6, practice_status: "Did Not Participate in Practice",
report_status: "", practice_primary_injury: "Calf",
date_modified: 2026-10-08T17:40}` and Wednesday’s earlier row
(`limited`, same week) already stored.

- Upsert → new `PracticeReport(week=6, participation="dnp",
  report_date=Oct 8, note="Calf")`; Wednesday’s `limited` row remains.
- No `report_status` → no designation write yet.
- Walker is rostered in league 111 → downgrade `limited→dnp` →
  notification: “Kenneth Walker III (SEA RB) did not practice Thursday
  (calf) after a limited Wednesday — official report, ahead of any
  ESPN status change.”
- Friday’s row says `report_status: "Questionable"` → designation
  row replaces; no new practice alert unless participation changed
  again.

## 8. What the implementing session (Sonnet) must NOT do

- **No scraping NFL.com/ESPN HTML** — the source decision is made; a
  latency complaint is a task-row note, not a rewrite license.
- **No schema changes** to `PracticeReport`/`InjuryDesignation`
  (settled in B2) beyond adding the new entry to `SYNC_SECTIONS`.
- **No hard failures**: every failure mode above degrades to a logged
  sync-log row with last good data intact.
- **No pushes for upgrades or for un-rostered players.**
- **No interpretation in alert copy** (availability predictions are
  E1’s curve; skepticism is D3’s job).
- Tests mirror `test_nflverse.py`: FakeTransport with literal CSV
  text — mapping, tripwire (both sides of 0.2), trail upsert,
  designation replace, downgrade matrix (all four downgrade shapes +
  both non-alerts), rostered-only filter, scheduler guard.
