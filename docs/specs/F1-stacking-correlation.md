# F1 — Stacking Awareness: Correlation Weights (spec)

> **Status:** Design locked (2026-07-11, Fable design pass). This is
> the frontier half of a [SPLIT] task: the correlation weights and the
> math that turns them into a displayable number. The cheap half (a
> Sonnet session) adds the flags at the two call sites against this
> spec verbatim. Phase F ground rule applies with full force:
> **contextual flags, never hard rules** — nothing here changes a
> projection, a value, a suggestion ranking, or a verdict.

## 1. What a stack is worth — variance, not mean

A QB and his own pass-catcher score together: the same completed TD is
points in both boxes. Rostering the pair doesn’t raise your **expected**
points (each player’s projection already includes their share of the
connection); it raises the **variance** of their sum — a higher ceiling
in the weeks they connect, a lower floor in the weeks the passing game
stalls. That’s why stacking is upside strategy (brainstorm §6) and why
the correct product here is a *flag quantifying extra swing*, not a
value bonus.

## 2. The weights — decided here, one table

Same-NFL-team weekly fantasy-point correlations, from the public DFS/
season-long research consensus (values cluster tightly across studies;
we take round mid-points — these are display weights, not fitted
parameters):

```python
STACK_CORRELATION = {          # (position_a, position_b), unordered
    ("QB", "WR"): 0.40,
    ("QB", "TE"): 0.35,
    ("QB", "RB"): 0.10,        # pass-catching backs only barely correlate
    ("WR", "WR"): 0.10,        # same passing offense, mild
    ("WR", "TE"): 0.05,
}
```

- Only pairs listed flag; anything else (QB+K, RB+RB, anything cross
  team) is **not** a stack. Same-backfield RB+RB is F3’s
  anti-correlation lens, explicitly out of scope here.
- QB+WR/TE are the headline stacks; the sub-0.2 rows exist so the flag
  can *say* “mild” instead of overclaiming (`stack_grade`: `strong` ≥
  0.3, `mild` < 0.3).
- Weights are module constants with this table’s rationale comment —
  not env vars. They are definitional and there is no data feed to tune
  them against; an env knob would be a dial connected to nothing.

## 3. From correlation to a number a user can read

Per-player weekly standard deviation is approximated from the weekly
projection (no distributional data exists in the app, and fetching some
is out of scope):

```
σ(p) = SIGMA_CV × proj(p)          SIGMA_CV = 0.45
```

(Empirical weekly coefficient of variation for startable skill players
clusters around 0.40–0.50; 0.45 is the midpoint. K/DST never flag, so
their wilder CVs don’t matter.)

For a pair (a, b) with correlation ρ:

```
σ_pair      = sqrt(σa² + σb² + 2·ρ·σa·σb)
σ_indep     = sqrt(σa² + σb²)
extra_swing = σ_pair − σ_indep        # points of added weekly swing
```

`extra_swing` is the flag’s quoted number: “this pairing adds ~X points
of weekly swing — higher ceiling when they connect.” It is displayed,
compared to nothing, and added to nothing.

## 4. Call sites — exactly two

### 4.1 Draft suggestions

Where: the `suggested` map built in `app.py`’s Monte Carlo result
(A4/A6 already decorate it; F1 is one more decoration — follow
`homer_checks`’ wiring pattern).

For each suggested player `s`, look at the user-team roster drafted so
far; if any rostered player `r` shares an NFL team with `s` and
`(pos_s, pos_r)` is in the table, attach:

```json
"stack": {
  "with": "<r.name>", "positions": ["QB", "WR"], "correlation": 0.40,
  "grade": "strong", "extra_swing": 1.8,
  "note": "Pairs with your QB <r.name> — a strong stack (ρ≈0.40) adding ~1.8 pts of weekly swing. Upside play, not a value edge."
}
```

Highest-correlation pair wins if several exist (report one, keep the
flag readable). The projections used for σ are the same season-scaled
weekly numbers the draft view already shows — cheap half picks the
existing field, converts season → weekly by `/17` if that’s what’s
available, and says so in the note’s tooltip.

### 4.2 Trade evaluation (E1’s report)

Where: decorating `evaluate_trade`’s `sends_a`/`sends_b` player dicts
(a pure post-pass over the result — E1’s module is not modified;
the decoration lives in F1’s module and is applied by the endpoint).

For each **incoming** player, check stacks against the **receiving**
roster’s post-trade composition (their new teammates), using E1’s
`rate()` numbers for σ. Same flag shape; note phrased for trades:
“Acquiring <name> stacks him with your <QB name> …”. Also flag the
*outgoing* direction when a trade **breaks** a strong stack
(`"breaks_stack": {...}`, same numbers, note “this deal splits your
QB–WR1 stack”) — symmetric information, still zero effect on the
verdict.

Module:

```python
# backend/models/stacking.py
def stack_flag(pos_a, proj_a, pos_b, proj_b, name_b) -> Optional[dict]   # pure
def stacks_for_roster(player, roster_players) -> Optional[dict]          # best pair
```

## 5. Edge cases (all must be tested)

- Player with `nfl_team` None (or projection None/0) → no flag, no σ
  of zero weirdness.
- QB suggested when roster holds two of his receivers → one flag, the
  higher-ρ pairing named, `also_with: ["<other name>"]` listed.
- Trade sending a QB *and* his WR to the same side → the pair travels
  together: flag on both, note adjusted (“arrives as a ready-made
  stack”).
- Bye-week/injured teammate: irrelevant — stacks are season-shaped;
  no availability math in a flag (that’s E1’s job and this is not a
  value).
- DST/K positions → never flag even if someone edits the table
  (guard: positions outside QB/RB/WR/TE return None).

## 6. Worked example

Draft, round 6: engine suggests WR (weekly proj 14.0); user drafted a
QB (weekly proj 18.0) from the same NFL team in round 4.

```
σ_qb  = 0.45 × 18.0 = 8.1
σ_wr  = 0.45 × 14.0 = 6.3
ρ     = 0.40 (QB,WR)
σ_pair  = sqrt(8.1² + 6.3² + 2×0.40×8.1×6.3) = sqrt(146.1) = 12.09
σ_indep = sqrt(105.3) = 10.26
extra_swing = 1.83 → flag: strong stack, "~1.8 pts of weekly swing"
```

Same numbers with a TE (ρ 0.35, proj 9.0): σ_te 4.05,
σ_pair = sqrt(65.6+16.4+2×0.35×8.1×4.05)=sqrt(105.0)=10.25 vs
σ_indep = sqrt(82.0)=9.06 → extra_swing 1.19, still `strong` by ρ.
WR+WR same team (ρ 0.10, projections 14/12): extra_swing ≈ 0.4 →
`mild`, and the note says mild.

## 7. What the implementing session (Sonnet) must NOT do

- **Never touch selection or scoring**: no boost in
  `suggest_candidate`, no term in E1 values or verdicts, no reordering
  of anything by stack status. If a reviewer asks “does any number
  change when the flag is removed?” the answer must be no — test
  exactly that (suggestions and trade verdicts identical with the
  decoration stripped).
- **No new correlation entries** and no env knobs for the weights
  (§2’s rationale).
- **No probability claims in copy** (“ρ≈0.40” and “~1.8 pts of weekly
  swing” are the entire quantitative vocabulary; no win-rate or
  boom-rate percentages — nothing here supports them).
- **No third call site** (waivers/lineup don’t stack-flag in v1; a
  lineup-stack note is a plausible future F-task, not scope creep for
  this one).
- Tests: pure-function math (the worked examples as assertions,
  ±0.05), both call-site decorations, the no-effect-when-stripped
  property, and every §5 edge.
