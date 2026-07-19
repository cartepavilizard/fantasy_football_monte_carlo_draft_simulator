import * as React from "react";
import { FiShuffle } from "react-icons/fi";

import { HawkCard, HawkCardHeader, HawkStatTrio } from "./hawk-cards";
import { TradeCounter, TradeEvaluation } from "./types";

function moveLabel(counter: TradeCounter): string {
  const move = counter.move;
  const side = move.team === "a" ? "your" : "their";
  switch (move.type) {
    case "add":
      return `Add ${move.player_name} to ${side} side`;
    case "remove":
      return `Drop ${move.player_name} from ${side} side`;
    case "swap":
      return `Swap ${move.player_out_name} for ${move.player_name} on ${side} side`;
  }
}

function verdictChip(verdict: TradeEvaluation["verdict"]): {
  text: string;
  fg: string;
  bg: string;
  bd: string;
} {
  switch (verdict) {
    case "fair":
      return {
        text: "Fair",
        fg: "var(--info)",
        bg: "rgba(74,168,255,0.14)",
        bd: "var(--info)",
      };
    case "favors_a":
      return {
        text: "You win",
        fg: "var(--green)",
        bg: "rgba(105,190,40,0.14)",
        bd: "var(--green)",
      };
    case "favors_b":
      return {
        text: "They win",
        fg: "var(--loss)",
        bg: "rgba(255,92,108,0.14)",
        bd: "var(--loss)",
      };
  }
}

function CompactEvaluation({ evaluation }: { evaluation: TradeEvaluation }) {
  const chip = verdictChip(evaluation.verdict);
  return (
    <div
      className="flex flex-col gap-2 rounded-[var(--radius-sm)] p-2"
      style={{ background: "var(--surface-2)", border: "1px solid var(--border)" }}
    >
      <div className="flex items-center gap-2">
        <span
          className="font-head text-[10px] font-bold uppercase tracking-[0.04em]"
          style={{ color: chip.fg, background: chip.bg, border: `1px solid ${chip.bd}`, borderRadius: 100, padding: "2px 8px" }}
        >
          {chip.text}
        </span>
        <span className="text-xs text-[color:var(--text-mute)]">
          gap {evaluation.market_gap > 0 ? "+" : ""}
          {evaluation.market_gap.toFixed(0)} · fair ±
          {evaluation.fair_bound.toFixed(0)}
        </span>
      </div>
      <HawkStatTrio
        send={evaluation.value_sent_a.toFixed(0)}
        get={evaluation.value_sent_b.toFixed(0)}
        gap={
          <span
            style={{
              color: evaluation.market_gap >= 0 ? "var(--green)" : "var(--loss)",
            }}
          >
            {evaluation.market_gap > 0 ? "+" : ""}
            {evaluation.market_gap.toFixed(0)}
          </span>
        }
      />
      <p className="text-xs text-[color:var(--text-dim)] leading-relaxed">
        {evaluation.summary}
      </p>
    </div>
  );
}

export interface TradeCountersCardProps {
  counters: TradeCounter[] | null;
  note: string | null;
}

export const TradeCountersCard: React.FC<TradeCountersCardProps> = ({
  counters,
  note,
}) => {
  if (counters === null) return null;

  return (
    <HawkCard>
      <HawkCardHeader
        title={
          <span className="inline-flex items-center gap-2">
            <FiShuffle className="text-[color:var(--text-mute)]" />
            Counterproposals
          </span>
        }
      />
      <div className="p-3 flex flex-col gap-3">
        {note && <p className="text-sm text-[color:var(--text-mute)]">{note}</p>}
        {counters.length === 0 ? (
          <p className="text-sm text-[color:var(--text-mute)]">
            No fair counter exists within one move of this proposal.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {counters.map((counter, i) => (
              <li
                key={i}
                className="flex flex-col gap-2 rounded-[var(--radius-sm)] p-3"
                style={{
                  background: "var(--surface-2)",
                  border: "1px solid var(--border)",
                }}
              >
                <div className="flex items-center gap-2">
                  <FiShuffle className="text-[color:var(--green)]" />
                  <span className="text-sm font-bold text-[color:var(--text)]">
                    {moveLabel(counter)}
                  </span>
                </div>
                <p className="text-sm text-[color:var(--text-dim)]">
                  {counter.rationale}
                </p>
                <CompactEvaluation evaluation={counter.evaluation} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </HawkCard>
  );
};
