import * as React from "react";
import clsx from "clsx";
import { Spinner } from "@nextui-org/spinner";
import { FiAlertTriangle, FiArrowRight, FiCheckCircle, FiClock } from "react-icons/fi";

import { EmptyStateHawk } from "@/components/mascots";
import { HawkChip } from "@/components/hawk-cards";
import {
  LeagueTeamInfo,
  TradeWillingnessLabel,
} from "@/types";

// E3 willingness label → the composite's "Very Active" copy + a tone for the
// chip rendered next to the partner name. "unknown" stays neutral (the season
// hasn't asked the question yet, not a no).
export function willingnessCopy(label: TradeWillingnessLabel): string {
  switch (label) {
    case "active":
      return "Very Active";
    case "open":
      return "Open";
    case "reluctant":
      return "Reluctant";
    default:
      return "Unknown";
  }
}

export function willingnessTone(
  label: TradeWillingnessLabel,
): "green" | "info" | "warn" | "neutral" {
  switch (label) {
    case "active":
      return "green";
    case "open":
      return "info";
    case "reluctant":
      return "warn";
    default:
      return "neutral";
  }
}

const selectClass =
  "bg-[var(--surface-2)] border border-[color:var(--border-2)] rounded-[var(--radius-sm)] px-3 py-2 text-sm text-[color:var(--text)] focus:outline-none focus:border-[color:var(--green)]";

// The standard freshness/warnings banner — identical to the in-season page's
// (kept inline per the codebase convention; every cached view renders it).
function StalenessBanner({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) {
    return (
      <p className="flex items-center gap-1 text-xs text-[color:var(--text-mute)]">
        <FiCheckCircle className="text-[color:var(--green)]" />
        No staleness warnings
      </p>
    );
  }

  return (
    <div
      className="flex flex-col gap-1 w-full rounded-[var(--radius-sm)] p-2"
      style={{
        background: "rgba(245,179,1,0.10)",
        border: "1px solid var(--warn)",
      }}
    >
      {warnings.map((warning, i) => (
        <p
          key={i}
          className="flex items-start gap-2 text-sm text-[color:var(--warn)]"
        >
          <FiAlertTriangle className="mt-0.5 shrink-0" />
          <span>{warning}</span>
        </p>
      ))}
    </div>
  );
}

export interface TradeHeaderProps {
  teams: LeagueTeamInfo[];
  teamAId: number | null;
  teamBId: number | null;
  onTeamAChange: (id: number) => void;
  onTeamBChange: (id: number) => void;
  overviewLoading: boolean;
  // E3 willingness for the partner (team B), null when not yet loaded.
  partnerWillingness: TradeWillingnessLabel | null;
  // E8 deadline window for the partner (team B), null when the league has no
  // deadline or the partner's window is undecided.
  partnerWindow: "buy" | "sell" | null;
  inWindow: boolean;
  weeksToDeadline: number | null;
  warnings: string[];
}

export const TradeHeader: React.FC<TradeHeaderProps> = ({
  teams,
  teamAId,
  teamBId,
  onTeamAChange,
  onTeamBChange,
  overviewLoading,
  partnerWillingness,
  partnerWindow,
  inWindow,
  weeksToDeadline,
  warnings,
}) => {
  if (overviewLoading) {
    return (
      <div className="flex items-center justify-center py-6">
        <Spinner />
      </div>
    );
  }

  if (teams.length < 2) {
    return (
      <div className="flex flex-col items-center gap-3 text-center py-6">
        <EmptyStateHawk size={88} />
        <p className="text-sm text-[color:var(--text-dim)] max-w-md">
          This league needs at least two synced teams to build a trade. Sync
          the league from the in-season page first.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex flex-col gap-1">
          <span className="font-head text-[10px] font-bold uppercase tracking-[0.06em] text-[color:var(--text-mute)]">
            You
          </span>
          <select
            className={selectClass}
            value={teamAId ?? ""}
            onChange={(e) => onTeamAChange(Number(e.target.value))}
          >
            {teams.map((team) => (
              <option key={team.espn_team_id} value={team.espn_team_id}>
                {team.name} ({team.wins}-{team.losses}
                {team.ties ? `-${team.ties}` : ""})
              </option>
            ))}
          </select>
        </label>

        <FiArrowRight className="text-[color:var(--green)] mt-4" />

        <label className="flex flex-col gap-1">
          <span className="font-head text-[10px] font-bold uppercase tracking-[0.06em] text-[color:var(--text-mute)]">
            Partner
          </span>
          <select
            className={selectClass}
            value={teamBId ?? ""}
            onChange={(e) => onTeamBChange(Number(e.target.value))}
          >
            {teams.map((team) => (
              <option key={team.espn_team_id} value={team.espn_team_id}>
                {team.name} ({team.wins}-{team.losses}
                {team.ties ? `-${team.ties}` : ""})
              </option>
            ))}
          </select>
        </label>

        {partnerWillingness && (
          <HawkChip tone={willingnessTone(partnerWillingness)} className="mt-4">
            {willingnessCopy(partnerWillingness)}
          </HawkChip>
        )}

        <div className="ml-auto flex items-center gap-2">
          {partnerWindow ? (
            <HawkChip tone={partnerWindow === "buy" ? "info" : "warn"}>
              <span className="inline-flex items-center gap-1">
                <FiClock />
                {partnerWindow === "buy" ? "Buy Window" : "Sell Window"}
              </span>
            </HawkChip>
          ) : (
            <HawkChip tone="neutral">
              <span className="inline-flex items-center gap-1">
                <FiClock />
                {inWindow
                  ? weeksToDeadline != null
                    ? `${weeksToDeadline}w to deadline`
                    : "Trade Window"
                  : "Past Deadline"}
              </span>
            </HawkChip>
          )}
          <div
            aria-hidden
            className="w-7 h-7 rounded-full"
            style={{
              background: "linear-gradient(135deg, var(--green), var(--navy))",
              border: "1px solid var(--green)",
            }}
          />
        </div>
      </div>

      <StalenessBanner warnings={warnings} />
    </div>
  );
};
