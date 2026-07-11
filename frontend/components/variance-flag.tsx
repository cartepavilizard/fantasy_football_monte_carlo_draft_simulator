"use client";

import { FiAlertCircle } from "react-icons/fi";

import { UsageVariance } from "@/types";

// C8 (process-over-results, BRAINSTORM §2.9): a shared badge for "real
// opportunity that didn't show up in the box score" — high targets, low
// catches in a single game. Any view built on PlayerWeekUsage-derived
// data renders this the same way instead of re-deriving its own copy,
// so the framing never drifts per view. Backend (variance_note() in
// backend/models/usage_shifts.py) only decides whether a game clears
// the target-floor/catch-rate-ceiling bar; all the wording lives here.
export function VarianceFlag({
  variance,
}: {
  variance: UsageVariance | null;
}) {
  if (!variance) return null;

  const pct = (variance.catch_rate * 100).toFixed(0);
  const catchWord = variance.receptions === 1 ? "catch" : "catches";

  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-bold px-1.5 py-0.5 rounded-full bg-default-100 text-default-600 border border-default-300 dark:bg-default-800/40 w-fit"
      title={
        `${variance.receptions} ${catchWord} on ${variance.targets} targets ` +
        `(${pct}%) this game — real opportunity, quiet box score. One ` +
        "game isn't the role."
      }
    >
      <FiAlertCircle />
      {variance.receptions}/{variance.targets} targets
    </span>
  );
}
