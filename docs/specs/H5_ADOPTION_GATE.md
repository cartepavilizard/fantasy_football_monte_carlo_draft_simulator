# H5 — Adoption Gate: three decisions for the repo owner

**Measured 2026-07-31, branch `h5-adoption-measurement`. Nothing was written
to Mongo. No production code changed. 690 tests passing.**

H5's job was to measure what adoption would change *before* adopting it, so
that a projection change could not reshuffle the board unnoticed before the
draft. The measurement is done and both engineering gates pass. What remains
is three decisions that are not a session's to make.

Reproduce every number below with:

```bash
cd backend && venv312/Scripts/python.exe scripts/h5_adoption_gate.py
```

That script is 21 executed assertions, runs in ~4s, and fails loudly if any
of this stops being true. It is mirrored to the Ringer checks directory as
`behavior_h5_adoption.py`.

---

## What a re-sync would actually adopt: three changes, not one

The live Mahomes board in Mongo was synced from the **2026-07-17** blend.
Since then three independent things have changed, and
`POST /league/{id}/player/sync` adopts all three in one irreversible write.

| # | Change | Projections moved (top-200) | `position_tier` reassigned |
| --- | --- | --- | --- |
| 1 | **`ROSTER_SIZE` 16 → 12** (untracked `.env`) | **0** | **98 / 671** |
| 2 | Data refresh 07-17 → 07-28 batches | 3 / 200 | 1 / 671 |
| 3 | **H2** (scale + sentinel fix) | **200 / 200**, mean \|Δ\| 7.13 | 6 / 649 |
| 4 | *(optional)* **ffanalytics** as a third source | 199 / 200, mean \|Δ\| 3.92 | 19 / 649 |

**The row's central premise was wrong, and that is the headline.** H5 was
written to catch H2 reshuffling tiers. H2 reassigns **6** tier labels.
The 98-label churn is `ROSTER_SIZE`: `models/position.py` derives every
`PositionTiers` cutoff from it (qb1 = top 16 under the old value, top 12
under the new one), so re-tiering the live board with the projections
*frozen* still moves 98 players. Patrick Mahomes goes qb1 → qb2 at an
unchanged 284.18 points.

`position_max_points` vs the live board:

| | qb | rb | wr | te | dst | k |
| --- | --- | --- | --- | --- | --- | --- |
| H2 | **−4.39** | +22.69 | +3.66 | +3.12 | +2.33 | +23.87 |
| H2 + ffanalytics | **−10.68** | +27.07 | +6.81 | −2.66 | −0.87 | +25.18 |

The qb ceiling *falls* while rb and k rise sharply — that asymmetry
re-scales the `randomized_points()` ceiling differently per position, which
is the part that touches every Monte Carlo outcome.

## What the user actually sees barely moves

`value_over_wait` verdicts, seed-paired so RNG noise cannot masquerade as
signal — 3 live Mahomes leagues × 5 seeds at pick 1, and rounds 3/5/7/9/11
× 3 seeds over a scripted identical draft:

**Zero recommendation flips**, at every checkpoint except round 7. Round 7
is a genuine TE/QB near-tie the engine itself labels "either is defensible"
(live: te 11.92 vs qb 8.90; with ffanalytics: 7.98 vs 7.97). That flip
appears under the **plain data refresh too**, so it belongs to the RNG and
the near-tie, not to H2 or ffanalytics.

## Engineering verdicts

**H2 — adopt.** It fixes two measured defects: James Conner 29.90 → 68.15
(an espn `0.0` "no projection" sentinel was halving a real projection), and
the kicker scale disagreement (espn projected 41% more than sleeper on the
same 31 kickers). Cost: 23 more records lose a projection, 672 → 649 — still
four times the 150 picks a 10-team, 15-round draft consumes.

**ffanalytics — adopt.** 1870/1902 rows resolve against the ESPN anchor. It
**adds 61** projected players (649 → 710), more than repaying H2's 23 drops.
Its rescale factors are the best-behaved of any source — qb 0.948, rb 1.007,
wr 0.970, te 0.874, dst 0.854, k 1.007, against sleeper's k 1.412 — so the
blend is not straining to absorb it. And it gives **465 players a third
projection source**, lifting `projection_spread` coverage 478 → 585. A third
source is the precondition both `blend.py`'s docstring and H6's note named
for real outlier handling; it now exists.

*Caveat worth carrying forward:* ffanalytics runs systematically low on
rookies and second-year players (Jeremiyah Love −13.4, Jadarian Price −11.7,
Matthew Golden −11.3, KC Concepcion −10.8, Carnell Tate −9.6, Denzel Boston
−9.9). Its largest single mover, Stefon Diggs 174.19 → 141.88, is a
*coverage* gain rather than a scale artifact: Diggs previously had **one**
source (sleeper 170.2, `projection_spread` `None`), and ffanalytics's 113.0
is the first second opinion he has had.

---

## The three questions

### 1. Run the sync?  — *blocked on H11, see question 2*

`POST /league/{id}/player/sync` against the real draft leagues is outside a
session's authority without being asked. If you want it run, say so
explicitly.

**Git does not protect Mongo.** The sync overwrites `projected_points`,
`position_tier` and `position_max_points` in place across 13 league
documents, and no branch checkout rolls that back. `mongodump` the
`fantasy-football` database, or copy the league documents, **first**.

### 2. What should `ROSTER_SIZE` be — and settle it *before* the sync?

The sync bakes whatever value is live into every tier label, so this has to
be decided first or it gets decided by accident.

**The name means two different things in this codebase, and that is the
actual bug.** Verified 2026-07-31:

| symbol | value | what it really means | who reads it |
| --- | --- | --- | --- |
| `config.ROSTER_SIZE` (global) | 12 | **number of teams** | `PositionTiers` — every tier cutoff |
| `League.roster_size` (per league) | 15 | **players per team** | **nobody** — written at creation, never read |

Evidence for the global being team count: `position.py`'s own comment
(`# 14 in a 14-team league`) and the arithmetic — `qb1 = QB_SIZE *
ROSTER_SIZE` is "1 starting QB x N teams", i.e. the number of league-wide
startable QBs, which is the standard definition of a QB1. Evidence for the
per-league field being players-per-team: both live leagues store
`roster_size=15`, exactly equal to their `round_size=15`, while having 12
and 10 teams respectively.

So the answer to "shouldn't that vary per league?" is **yes, and it does —
in a field the tier code never looks at.** Mahomes is 10 teams, Skunkweed is
12; one global cannot be right for both.

This also gives the most likely explanation for the drift itself: `16` is
close to a 15-man roster and nowhere near either league's team count, so
whoever set it was probably reading the name literally. A name that invites
the wrong value is worse than a wrong value.

Options, best first:

1. **Derive the cutoffs from `len(league.teams)` at sync time**, instead of
   from a module-level global read from the environment at import. This is
   the real fix: it is per-league by construction and deletes the ambiguity.
   `PositionTiers` takes a team-count argument rather than closing over
   `ROSTER_SIZE`. Tracked as **H11**, and it **blocks question 1** — run it
   before the sync, or the sync bakes the wrong cutoffs in permanently.
2. Rename the global to `LEAGUE_TEAM_COUNT` (and `League.roster_size` to
   `roster_slots`, or delete it — it is unread) and set it explicitly in
   `backend/.env`. Cheap, keeps the global, still wrong for one of the two
   leagues.
3. Accept 12 and move on. Correct for Skunkweed, wrong for Mahomes, and the
   next person to read the name will re-introduce the drift.

### 3. Wire ffanalytics in permanently?

One line — `register_push_source("ffanalytics", parse_udk_rows)` in
`backend/data_sources/service.py` — plus a round-trip test. H1 built the
registry for exactly this and H4 spelled the CSV headers to match
`udk.py`'s `COLUMN_ALIASES`, so no parser work is needed. Tracked as **H10**;
it is a code change, so it is not H5's to make.

Operational note for whoever runs the producer: it needs
`R_LIBS_USER=~/R/library` under WSL, or `library(ffanalytics)` fails with
"there is no package called 'ffanalytics'". The export is a regenerable data
artifact and is gitignored.
