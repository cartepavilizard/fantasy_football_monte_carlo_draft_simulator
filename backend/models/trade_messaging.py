# -*- coding: utf-8 -*-
"""
TRADE MESSAGING GENERATOR (PHASE E, TASK E7)

A single pure function that templates a friendly, non-salesy message framing
a trade proposal, quoting ACTUAL projection and matchup numbers from E1's
`evaluate_trade` output dict. Deterministic templating only — no LLM calls,
no network, no randomness.

COPY RULES (E1 spec §4.3, inherited verbatim):
- quote ROS points and per-week points (never last week's score — C8);
- name the position need the deal fills or creates;
- when any input carried a warning (no projections, no FA baseline, neutral
  matchups), the message says so;
- the two value lenses (market value + roster fit) are both presented, never
  merged into one number — E1's contract, preserved here.

WILLINGNESS (E3) TONE RULE — load-bearing and tested:
`willingness_label` (one of E3's "active" / "open" / "unknown" / "reluctant")
may select among tone variants for the opener and closer phrasing ONLY. The
label's text and its rank/level must NEVER appear in the generated message —
E3's profiles inform tone, they are never quoted to the recipient. A test
asserts this for every label.

This module templates over E1's output; it never recomputes a value, never
re-grades, and never touches Mongo. Pure in, string out.
"""
import os
from typing import List, Optional

# How many decimal places to quote for ROS / per-week points. One decimal
# matches E1's summary copy and is honest about projection noise.
_PTS_FMT = "{:.1f}"

# Tunable: when True, the message names the position need the deal fills
# (incoming minus outgoing positions). Env-overridable so a future copy
# review can A/B it without a code change.
NAME_POSITION_NEED = os.getenv("E7_NAME_POSITION_NEED", "1") == "1"


_VALID_LABELS = {"active", "open", "unknown", "reluctant"}


def render_trade_message(
    evaluation: dict,
    willingness_label: Optional[str] = None,
) -> str:
    """
    Render a friendly, non-salesy message framing a trade proposal, quoting
    real numbers from `evaluation` (E1's `evaluate_trade` output dict).

    `willingness_label` (E3) selects a tone variant for the opener/closer
    phrasing only; its text/level never appears in the output. Unknown
    labels are treated as the default (neutral) tone.

    Deterministic: same inputs -> identical string, always.
    """
    label = (willingness_label or "").strip().lower()
    if label not in _VALID_LABELS:
        label = "unknown"

    teams = evaluation.get("teams", {})
    team_a = teams.get("a", {}) or {}
    team_b = teams.get("b", {}) or {}
    name_a = team_a.get("name") or "your team"
    name_b = team_b.get("name") or "their team"

    sends_a = evaluation.get("sends_a", []) or []
    sends_b = evaluation.get("sends_b", []) or []
    value_a = float(evaluation.get("value_sent_a", 0.0) or 0.0)
    value_b = float(evaluation.get("value_sent_b", 0.0) or 0.0)
    weeks_remaining = int(evaluation.get("weeks_remaining", 0) or 0)
    per_week_a = _per_week(value_a, weeks_remaining)
    per_week_b = _per_week(value_b, weeks_remaining)

    fit_pw_a = float(evaluation.get("fit_per_week_a", 0.0) or 0.0)
    fit_pw_b = float(evaluation.get("fit_per_week_b", 0.0) or 0.0)
    verdict = evaluation.get("verdict", "fair")
    warnings = evaluation.get("warnings", []) or []

    names_a = _join_names(sends_a)
    names_b = _join_names(sends_b)

    opener = _opener(label, name_b)
    proposal = _proposal_line(name_a, name_b, names_a, names_b)
    market = _market_line(
        value_a, per_week_a, value_b, per_week_b, verdict, weeks_remaining
    )
    fit = _fit_line(fit_pw_a, fit_pw_b)
    need = _position_need_line(sends_a, sends_b) if NAME_POSITION_NEED else ""
    stash = _stash_line(sends_a, sends_b)
    warning_line = _warning_line(warnings)
    closer = _closer(label)

    parts: List[str] = [opener, proposal, market, fit]
    if need:
        parts.append(need)
    if stash:
        parts.append(stash)
    if warning_line:
        parts.append(warning_line)
    parts.append(closer)
    return " ".join(part for part in parts if part)


# --- tone variants (label never appears in any of these) ----------------------


def _opener(label: str, name_b: str) -> str:
    """The opener varies by willingness tone; the label is never named."""
    if label == "active":
        return f"Hey {name_b} — wanted to run a trade by you."
    if label == "reluctant":
        return (
            f"Hey {name_b}, no pressure at all on this — wanted to float "
            "a trade idea your way."
        )
    # "open" and "unknown" share the standard friendly opener; the label
    # itself is never named in the phrasing.
    return f"Hey {name_b} — would you be up for a trade?"


def _closer(label: str) -> str:
    """The closer varies by willingness tone; the label is never named."""
    if label == "reluctant":
        return "Totally fine if it's not for you — just wanted to put it out there."
    if label == "active":
        return "Let me know what you think; happy to tweak the pieces."
    return "Let me know if this is in the ballpark, or what you'd change."


# --- body lines (identical across tones; deterministic, honest) ---------------


def _proposal_line(name_a: str, name_b: str, names_a: str, names_b: str) -> str:
    return (
        f"{name_a} would send {names_a} and {name_b} would send {names_b}."
    )


def _market_line(
    value_a: float,
    per_week_a: float,
    value_b: float,
    per_week_b: float,
    verdict: str,
    weeks_remaining: int,
) -> str:
    """Quote ROS points and per-week points (E1 §4.3); name the fairness
    verdict in plain terms without overselling."""
    if weeks_remaining <= 0:
        per_a = per_b = ""
    else:
        per_a = f", about {per_week_a:.1f}/week"
        per_b = f", about {per_week_b:.1f}/week"
    base = (
        f"On market value: your side is {value_a:.1f} ROS points{per_a} "
        f"and theirs is {value_b:.1f}{per_b}."
    )
    if verdict == "fair":
        return f"{base} That's inside the fair range on value."
    if verdict == "favors_a":
        return f"{base} The value leans your way here."
    if verdict == "favors_b":
        return f"{base} The value leans their way here."
    return base


def _fit_line(fit_pw_a: float, fit_pw_b: float) -> str:
    """Roster-fit lens: per-week starting-lineup change for each side.
    Signed, never floored (a trade can hurt) — E1's contract."""
    return (
        f"On roster fit, your starting lineup projects {fit_pw_a:+.1f} "
        f"points/week and theirs {fit_pw_b:+.1f}."
    )


def _position_need_line(sends_a: list, sends_b: list) -> str:
    """Name the position need the deal fills or creates (E1 §4.3). Derived
    from the position multiset each side sends — projection/volume framing,
    never results."""
    outgoing = _position_counts(sends_a)
    incoming = _position_counts(sends_b)
    gained = [pos for pos, n in incoming.items() for _ in range(n)]
    lost = [pos for pos, n in outgoing.items() for _ in range(n)]
    net_gain = _multiset_diff(gained, lost)
    net_loss = _multiset_diff(lost, gained)
    if not net_gain and not net_loss:
        return ""
    fills = "/".join(dict.fromkeys(net_gain)) if net_gain else None
    creates = "/".join(dict.fromkeys(net_loss)) if net_loss else None
    if fills and creates:
        return f"It fills your {fills} need but costs {creates} depth."
    if fills:
        return f"It fills your {fills} need."
    if creates:
        return f"It costs you {creates} depth."
    return ""


def _stash_line(sends_a: list, sends_b: list) -> str:
    """If either side includes an IR/suspension stash, surface E1's
    stash_note verbatim — the playoff-window raw points are exactly the
    number that decides whether the spot is worth it."""
    stash = next(
        (v.get("stash_note") for v in sends_a + sends_b if v.get("stash_note")),
        None,
    )
    if not stash:
        return ""
    return f"Stash context: {stash}."


def _warning_line(warnings: list) -> str:
    """E1 §4.3: when any input carried a warning, the message says so."""
    if not warnings:
        return ""
    return f"Heads up — {warnings[0]}."


# --- helpers ------------------------------------------------------------------


def _join_names(values: list) -> str:
    names = [v.get("name") for v in values if v.get("name")]
    if not names:
        return "no one"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _per_week(value: float, weeks: int) -> float:
    return value / weeks if weeks else 0.0


def _position_counts(values: list) -> dict:
    counts: dict = {}
    for v in values:
        pos = (v.get("position") or "").upper()
        if pos:
            counts[pos] = counts.get(pos, 0) + 1
    return counts


def _multiset_diff(a: list, b: list) -> list:
    """Items in a not covered by b, multiset-wise, preserving a's order."""
    remaining = list(b)
    result = []
    for item in a:
        if item in remaining:
            remaining.remove(item)
        else:
            result.append(item)
    return result
