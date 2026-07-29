"use client";

import * as React from "react";

import { MonteCarloResults, PositionScarcity, SuggestedPick } from "./types";
import { HawkCard, HawkCardTitle } from "./hawk-cards";
import { TagBadge } from "./draft-tag-badge";
import { VictoryBadge } from "./mascots";

// HAWK MODE Suggested — the right rail's middle card. Per the composite
// it's a green-tinted surface with "Suggested", the player name + a
// position badge, a one-line reason, and a full-width "Draft <Name>"
// button. The composite shows a fabricated reason; we surface the
// engine's real `suggested[...].reason` and the active tier-depletion
// `message` from the scarcity report when loaded as context.

function positionBadgeColor(position: string): { fg: string; bg: string } {
  const p = position.toUpperCase();
  if (p === "QB") return { fg: "#04240a", bg: "var(--pos-qb)" };
  if (p === "RB") return { fg: "#04240a", bg: "var(--pos-rb)" };
  if (p === "WR") return { fg: "#04240a", bg: "var(--pos-wr)" };
  if (p === "TE") return { fg: "#04240a", bg: "var(--pos-te)" };
  if (p === "K") return { fg: "#04240a", bg: "var(--pos-k)" };
  if (p === "DST" || p === "DEF") return { fg: "#04240a", bg: "var(--pos-dst)" };
  return { fg: "#04240a", bg: "var(--grey)" };
}

function lastName(fullName: string): string {
  const parts = fullName.trim().split(/\s+/);
  return parts.length > 0 ? parts[parts.length - 1] : fullName;
}

export interface SuggestedProps {
  // The headline pick from the Monte Carlo engine: either the legacy
  // "<name> (<POS>)" bestPick string or a structured SuggestedPick.
  bestPick: string;
  suggested: SuggestedPick | null;
  // The position the bestPick came from, so we can pair it with the
  // scarcity call's message for context.
  bestPosition: string | null;
  // Active scarcity call for the same position, when a scarcity report
  // has been loaded. Null when no report exists yet.
  scarcity: PositionScarcity | null;
  // Disable the Draft button when it's not the simulator's turn, the
  // sim is still running, or no suggestion has resolved.
  canDraft: boolean;
  onDraft: (name: string) => void;
  // Cost-of-waiting rule: the backend's explanation for WHY this
  // position was recommended (e.g. "waiting costs 69.3 pts at RB vs
  // 63.2 pts at WR"). About the POSITION — distinct from `suggested`
  // .reason which is about the PLAYER. Optional; older payloads lack it.
  recommendationReason?: string;
  // The cost-of-waiting value for this card's position, surfaced
  // compactly as a chip. Null/undefined when not applicable.
  costOfWaiting?: number | null;
}

export function Suggested({
  bestPick,
  suggested,
  bestPosition,
  scarcity,
  canDraft,
  onDraft,
  recommendationReason,
  costOfWaiting,
}: SuggestedProps) {
  const hasPick = bestPick && bestPick !== "Simulation Error";

  // Resolve the structured pick (name + position + reason) from either
  // the structured SuggestedPick (preferred) or the legacy bestPick
  // string ("<name> (<POS>)").
  const resolved: {
    name: string;
    position: string;
    reason: string | null;
  } | null = React.useMemo(() => {
    if (!hasPick) return null;
    if (suggested) {
      return {
        name: suggested.name,
        position: bestPosition ?? "",
        reason: suggested.reason || null,
      };
    }
    const match = bestPick.match(/^(.*)\s\(([A-Z]+)\)$/);
    if (match) {
      return {
        name: match[1],
        position: match[2].toLowerCase(),
        reason: null,
      };
    }
    return { name: bestPick, position: "", reason: null };
  }, [hasPick, suggested, bestPick, bestPosition]);

  if (!resolved) {
    return (
      <HawkCard variant="green" padded>
        <HawkCardTitle tone="green">Suggested</HawkCardTitle>
        <p
          className="text-xs"
          style={{ color: "var(--text-mute)" }}
        >
          No suggestion yet. Monte Carlo fires when it&apos;s your turn.
        </p>
      </HawkCard>
    );
  }

  const badge = positionBadgeColor(resolved.position);

  return (
    <HawkCard variant="green" padded>
      <HawkCardTitle tone="green">Suggested</HawkCardTitle>
      {/* The victory art lives HERE rather than in the Monte Carlo panel
          below the board: this card is in the right rail, above the fold,
          so it's visible the moment the sim resolves on your turn. Sized
          to the rail's width (~220px usable). */}
      <VictoryBadge
        size={210}
        style={{ marginBottom: "var(--sp-2)", maxWidth: "100%" }}
      />
      <div className="flex items-center gap-2" style={{ margin: "3px 0" }}>
        <span
          className="font-head text-[var(--fs-md)] font-bold uppercase truncate"
          style={{ color: "var(--text)" }}
          title={resolved.name}
        >
          {resolved.name}
        </span>
        {resolved.position && (
          <span
            className="font-head text-[9px] font-bold uppercase"
            style={{
              color: badge.fg,
              background: badge.bg,
              borderRadius: 2,
              padding: "1px 5px",
            }}
          >
            {resolved.position.toUpperCase()}
          </span>
        )}
      </div>
      {/* Cost-of-waiting rule: WHY THIS POSITION. The single most useful
          thing on the card and the app previously showed nothing like it.
          Visually distinct from the player reason below — green + bold +
          labelled, so the two never read as the same line. */}
      {recommendationReason && (
        <p
          className="text-xs font-bold"
          style={{
            margin: "0 0 var(--sp-2)",
            color: "var(--green)",
            borderTop: "1px solid var(--border)",
            paddingTop: "var(--sp-2)",
          }}
        >
          <span
            className="font-head uppercase"
            style={{ opacity: 0.7, marginRight: 4 }}
          >
            Why {bestPosition ? bestPosition.toUpperCase() : "this pos"}:
          </span>
          {recommendationReason}
        </p>
      )}
      {typeof costOfWaiting === "number" && (
        <span
          className="font-head text-[9px] font-bold uppercase"
          style={{
            display: "inline-block",
            color: "var(--text-mute)",
            background: "var(--surface-3)",
            border: "1px solid var(--border)",
            borderRadius: 2,
            padding: "1px 5px",
            margin: "0 0 var(--sp-2)",
          }}
        >
          waiting costs {Math.round(costOfWaiting)} pts
        </span>
      )}
      {/* The PLAYER reason — why THIS PLAYER at this position (sleeper
          boosts, my_guy tie-breaks, avoid filtering). Distinct from the
          position-level recommendationReason above. */}
      <p
        className="text-xs"
        style={{
          margin: "0 0 var(--sp-2)",
          color: "var(--text-dim)",
        }}
      >
        {resolved.reason ?? "Top value on the board at your pick."}
      </p>
      {scarcity && (
        <p
          className="text-xs italic"
          style={{
            margin: "0 0 var(--sp-2)",
            color: "var(--text-mute)",
            borderTop: "1px solid var(--border)",
            paddingTop: "var(--sp-2)",
          }}
          title={`${scarcity.position.toUpperCase()} scarcity call: ${scarcity.call}`}
        >
          {scarcity.message}
        </p>
      )}
      <button
        type="button"
        disabled={!canDraft}
        onClick={() => onDraft(resolved.name)}
        className="w-full font-head text-sm font-bold uppercase"
        style={{
          background: canDraft ? "var(--green)" : "var(--surface-3)",
          color: canDraft ? "#04240a" : "var(--text-mute)",
          border: "none",
          borderRadius: "var(--radius-sm)",
          padding: "7px",
          cursor: canDraft ? "pointer" : "not-allowed",
        }}
      >
        Draft {lastName(resolved.name)}
      </button>
    </HawkCard>
  );
}

// Suppress unused-import warning for TagBadge in some build paths
void TagBadge;
