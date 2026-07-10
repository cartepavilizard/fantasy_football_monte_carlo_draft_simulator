# Feature Brainstorm: Draft-Time Additions & In-Season Management Module

> **Status: Brainstorm / reference material only.** This document captures a
> comprehensive feature brainstorm *before* any architecture or planning work.
> It is **not a build order** and implies no sequencing or commitment.
> Phasing — including how any of this fits relative to the owner-tendency and
> ranking-aggregation work already underway — will be decided separately.

Last updated: 2026-07-10

---

## 1. Draft-Time Tool Additions

Enhancements to the existing Monte Carlo draft simulator.

### 1.1 Positional Scarcity Awareness (Tier-Driven)

Tie scarcity alerts to tier data so the tool actively advises on timing:

- When a tier at a scarce position (e.g., tight end) is nearly depleted,
  nudge the user to **reach early for the last player in that tier**.
- Conversely, when the next tier at that position still has plenty of
  options, explicitly confirm that it's **safe to wait**.
- The point is a directional recommendation ("act now" vs. "you can wait"),
  not just a passive display of tier counts.

### 1.2 Player Tagging System

Let the user flag players ahead of (and during) the draft, with each tag
changing suggestion behavior:

| Tag | Meaning | Effect on suggestions |
| --- | --- | --- |
| **Sleeper** | Late-round upside belief | Boost consideration in late rounds |
| **My guy** | Personal high-conviction target | Prioritize when values are close |
| **Avoid** | Do not want on roster | Filter out of **all** suggestions, regardless of projection |

---

## 2. In-Season Module — Lineup & Strategy

### 2.1 Full Lineup Optimization

- Optimize the **entire lineup**, not just head-to-head player comparisons.
- Refresh **on demand**, plus **scheduled background data pulls**, so
  Thursday-morning decisions are made with the freshest available data.

### 2.2 Matchup Strength Analysis

- Analyze opponent/matchup strength and feed it directly into lineup
  recommendations.

### 2.3 Kicker / Defense Streaming

- Weekly streaming recommendations for kickers and defenses based on
  matchup data.

### 2.4 Snap Count & Target Share Trend Tracking

- Track snap counts and target share week over week.
- Surface **early usage-shift alerts** (e.g., a back-up RB's snap share
  climbing) before the results show up in the box score.

### 2.5 Playoff Schedule Analysis

- Strength-of-schedule analysis for **weeks 14–16** (fantasy playoffs) to
  inform roster construction and trade decisions in advance.

### 2.6 IR Strategy

- Recommend **stashing injured players with known return timelines** rather
  than dropping them.
- Factor stash value into **waiver and trade evaluations** as well — an
  injured player with a return date is an asset, not dead weight.

### 2.7 Lineup-Locking Strategy

- For players with early games (e.g., Thursday night), suggest flex/bench
  placement that **locks early**, preserving Sunday flexibility if something
  goes wrong later in the week.

### 2.8 Handcuff Strategy

- Flag opportunities to roster the **direct backups of the user's own key
  players** as insurance.

### 2.9 Process-Over-Results Framing

- Don't overreact to single-game variance.
- Judge players on **underlying volume and opportunity** (snaps, targets,
  touches), not one week's point total. Bake this framing into how
  recommendations are worded and justified.

### 2.10 Trade Deadline Awareness

- Track each league's trade deadline and flag **buy/sell windows** in the
  weeks leading up to it.

### 2.11 "Homer Check" Guardrail

- Whenever a **Seahawks player** is suggested (draft pick, waiver add,
  trade acceptance), explicitly show a **neutral value comparison against
  alternatives** to counteract personal fan bias.

---

## 3. In-Season Module — Injury & News

### 3.1 Beat Writer Directory

- Maintain a team-to-beat-writer mapping (e.g., Seahawks → Brady Henderson)
  for targeted, more reliable information than national reporters provide.

### 3.2 Official Practice Participation Data

- Ingest official NFL practice participation reports (full / limited /
  did-not-practice) as an **early signal**, ahead of ESPN's injury
  designation updates.

### 3.3 Manual "Grok Bridge" Workflow

- The tool generates a **targeted prompt** — e.g., *"What has [beat writer]
  said about [player] in the last 48 hours?"* — for the user to run
  **manually** through their free xAI account.
- The user pastes the response back into the tool for incorporation.
- Explicitly **no automated or paid API calls** — this is a copy/paste
  bridge by design.

### 3.4 Kickoff Reminders

- Notify before each week's **first lineup lock** — accounting for the
  early-season **Wednesday opener** — and before the **final lock**.

---

## 4. In-Season Module — Trade Management

### 4.1 Trade Grading

- Score both sides of a proposed trade using projections/rankings data.
- Present the **value gap in plain terms**.

### 4.2 Counterproposal Generator

- For a lopsided trade, suggest roster tweaks that would make it fair,
  driven by both rosters' **surplus and need** by position.

### 4.3 Trade-Willingness Owner Profiles

- Build per-owner profiles from **historical trade behavior** to gauge who
  is a live trade partner versus who never deals.

### 4.4 Proactive Opportunity Scanner

- Cross-reference league-wide injury news against **all rosters** to
  surface trade windows — e.g., a rival's starter goes down and the user
  holds surplus at that position.

### 4.5 Blocking Plays

- Flag **handcuffs of rivals' injured stars** worth grabbing purely to deny
  the rival the replacement.

### 4.6 Free Agent Hoarding

- After waivers process, flag **speculative adds/drops worth making before
  Sunday** to keep valuable players off the board.

### 4.7 Data-Driven Trade Messaging

- Generate a **friendly, non-salesy message** framing a proposal or counter
  using actual projection and matchup data, ready to send to the other
  owner.

---

## 5. In-Season Module — Multi-Team View

### 5.1 Team Perspective Switcher

- Dropdown to view **any team in a league** from that team's perspective —
  e.g., the user's brother-in-law's teams across all three leagues.
- **Hard constraint:** switching perspective must use **already-gathered /
  cached data only**. It must **not** trigger new scrapes or manual Grok
  prompt workflows.

---

## 6. General Strategy Awareness

These are strategic concepts the tool should surface **contextually, as
flags — not enforce as hard rules**:

- **Stacking** — QB + pass-catcher correlation (upside pairing).
- **Bye week planning** — avoid clustering byes; anticipate thin weeks.
- **Anti-correlation** — avoid rostering players who compete for the same
  touches (e.g., two RBs in one backfield, outside of handcuff strategy).

---

## Scoping Notes

- Treat everything above as **reference material for scoping**, not a
  committed backlog or build order.
- Phasing relative to the **owner-tendency** and **ranking-aggregation**
  work already underway will be decided in a follow-up discussion.
