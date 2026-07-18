"use client";

import * as React from "react";
import { Button } from "@nextui-org/button";

import {
  useLazyGetScarcityQuery,
} from "@/api/services/scarcity";
import {
  PositionScarcity,
  PlayerTag,
  ScarcityCall,
} from "@/types";
import { HawkCard, HawkCardHeader } from "@/components/hawk-cards";
import { TagBadge } from "@/components/draft-tag-badge";
import {
  FiAlertTriangle,
  FiClock,
  FiHelpCircle,
  FiSlash,
  FiXCircle,
  FiZap,
} from "react-icons/fi";

// HAWK MODE Scarcity Check — the existing tier-depletion flow, folded
// under the draft board using the hawk-cards shell. Same lazy query,
// same per-position reach/wait badge + expandable at-risk list, just
// hosted in a HawkCard instead of the original ad-hoc bordered div.

const scarcityCallStyles: Record<
  ScarcityCall,
  { label: string; border: string; badge: string; Icon: typeof FiZap }
> = {
  reach: {
    label: "Reach Now",
    border: "border-danger",
    badge: "bg-danger-100 text-danger",
    Icon: FiZap,
  },
  last_chance: {
    label: "Last Chance",
    border: "border-danger",
    badge: "bg-danger text-danger-foreground animate-pulse",
    Icon: FiAlertTriangle,
  },
  wait: {
    label: "Safe to Wait",
    border: "border-success",
    badge: "bg-success/15 text-success",
    Icon: FiClock,
  },
  toss_up: {
    label: "Toss-Up",
    border: "border-default",
    badge: "bg-default-100 text-default-700",
    Icon: FiHelpCircle,
  },
  exhausted: {
    label: "Exhausted",
    border: "border-default",
    badge: "bg-default-100 text-default-500",
    Icon: FiXCircle,
  },
  no_tiers: {
    label: "No Tier Data",
    border: "border-default",
    badge: "bg-default-100 text-default-500",
    Icon: FiSlash,
  },
};

function ScarcityPositionCard({
  scarcity,
  playerTagByName,
}: {
  scarcity: PositionScarcity;
  playerTagByName: Record<string, PlayerTag | null | undefined>;
}) {
  const [expanded, setExpanded] = React.useState(false);
  const { label, border, badge, Icon } = scarcityCallStyles[scarcity.call];

  return (
    <div
      className={`flex flex-col gap-2 border-medium rounded-large p-3 text-left ${border}`}
    >
      <div className="flex items-center justify-between gap-2 w-full">
        <h4 className="text-lg font-bold">
          {scarcity.position.toLocaleUpperCase()}
        </h4>
        <span
          className={`flex items-center gap-1 rounded-full px-2 py-1 text-xs font-bold ${badge}`}
        >
          <Icon />
          {label}
        </span>
      </div>
      {scarcity.tier != null && (
        <p className="text-sm font-bold">
          Tier {scarcity.tier} · {scarcity.remaining_now} left
        </p>
      )}
      <p className="text-sm text-default-500">{scarcity.message}</p>
      {scarcity.at_risk.length > 0 && (
        <>
          <button
            className="text-xs text-default-500 underline text-left w-fit"
            type="button"
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "Hide at-risk players" : "Show at-risk players"}
          </button>
          {expanded && (
            <ul className="flex flex-col gap-1">
              {scarcity.at_risk.map((player) => (
                <li
                  key={player.name}
                  className="flex items-center justify-between gap-2 text-xs"
                >
                  <span className="flex items-center gap-1 font-bold">
                    <TagBadge tag={playerTagByName[player.name]} />
                    {player.name}
                  </span>
                  <span
                    className="text-default-500"
                    title="Chance the player survives to your pick / your next pick"
                  >
                    {Math.round(player.survival_at_pick * 100)}% /{" "}
                    {Math.round(player.survival_at_next_pick * 100)}%
                  </span>
                </li>
              ))}
              <li className="text-xs italic text-default-400">
                Survival odds at your pick / your next pick
              </li>
            </ul>
          )}
        </>
      )}
    </div>
  );
}

export interface ScarcityPanelProps {
  draftId: string;
  draftComplete: boolean;
  playerTagByName: Record<string, PlayerTag | null | undefined>;
}

export function ScarcityPanel({
  draftId,
  draftComplete,
  playerTagByName,
}: ScarcityPanelProps) {
  const [fetchScarcity, {
    data: scarcityReport,
    isFetching: scarcityFetching,
    isError: scarcityError,
  }] = useLazyGetScarcityQuery();

  return (
    <HawkCard>
      <HawkCardHeader
        title="Scarcity Check"
        right={
          <div className="flex items-center gap-3">
            {scarcityReport && (
              <span className="text-xs text-default-500">
                Pick {scarcityReport.your_pick}
                {scarcityReport.your_next_pick != null &&
                  ` → ${scarcityReport.your_next_pick}`}{" "}
                · {scarcityReport.iterations} sims
              </span>
            )}
            <Button
              color="primary"
              isDisabled={draftComplete || scarcityFetching}
              isLoading={scarcityFetching}
              size="sm"
              variant="flat"
              onClick={() => fetchScarcity({ id: draftId, seconds: 10 })}
            >
              {scarcityReport ? "Refresh" : "Check Scarcity"}
            </Button>
          </div>
        }
      />
      <div className="p-3">
        {scarcityError && (
          <p className="text-sm text-danger">
            Failed to load the scarcity report. Please try again.
          </p>
        )}
        {scarcityReport && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 w-full">
            {scarcityReport.positions.map((positionScarcity) => (
              <ScarcityPositionCard
                key={positionScarcity.position}
                playerTagByName={playerTagByName}
                scarcity={positionScarcity}
              />
            ))}
          </div>
        )}
        {!scarcityReport && !scarcityError && (
          <p className="text-sm text-default-500">
            Run a tier-depletion sim at your upcoming pick to see reach/wait
            calls per position.
          </p>
        )}
      </div>
    </HawkCard>
  );
}
