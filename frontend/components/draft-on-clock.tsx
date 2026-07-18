"use client";

import * as React from "react";

import { League, Team } from "@/types";
import { HawkCard } from "@/components/hawk-cards";

// HAWK MODE On The Clock — the right rail's top card. Per the composite
// it's a navy/green panel with an "On The Clock" label and a large
// pick number; the composite fakes a 0:47 countdown timer, but the app
// has no draft clock, so we surface the current pick number instead
// and highlight "your turn" when the simulator team is the one drafting.

export interface OnTheClockProps {
  league: League;
  draftComplete: boolean;
}

export function OnTheClock({ league, draftComplete }: OnTheClockProps) {
  if (draftComplete) {
    return (
      <HawkCard padded className="text-center">
        <div
          className="font-head text-xs font-bold uppercase tracking-[0.1em]"
          style={{ color: "var(--text-mute)" }}
        >
          On The Clock
        </div>
        <div
          className="font-display mt-1"
          style={{ fontSize: "var(--fs-xl)", color: "var(--text)" }}
        >
          Draft Complete
        </div>
        <div className="text-xs text-[color:var(--text-mute)]">
          All picks are in.
        </div>
      </HawkCard>
    );
  }

  const turn = league.current_draft_turn ?? 0;
  const n = league.teams.length;
  const round = n > 0 ? Math.floor(turn / n) + 1 : 1;
  const pickNo = turn + 1;
  const currentTeam: Team | undefined =
    league.draft_order.length > 0
      ? league.teams[league.draft_order[0]]
      : undefined;
  const yourTurn = currentTeam?.simulator === true;

  return (
    <div
      className="relative overflow-hidden text-center"
      style={{
        background: "var(--navy)",
        border: "1.5px solid var(--green)",
        borderRadius: "var(--radius)",
        padding: "var(--sp-3)",
        // Composite's hawkPulse glow on the simulator's turn only —
        // no fake countdown, but the pulse keeps the "you're up" cue.
        animation: yourTurn
          ? "hawkPulse 1.6s ease-in-out infinite"
          : undefined,
      }}
    >
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          background:
            "radial-gradient(circle at 50% 40%, rgba(105,190,40,0.28), transparent 65%)",
          animation: yourTurn
            ? "glare 1.8s ease-in-out infinite"
            : undefined,
        }}
      />
      <div className="relative">
        <div
          className="font-head text-xs font-bold uppercase tracking-[0.1em]"
          style={{ color: "var(--green)" }}
        >
          On The Clock
        </div>
        <div
          className="font-display"
          style={{
            fontSize: "var(--fs-2xl)",
            color: "#fff",
            lineHeight: 1,
            margin: "2px 0",
          }}
        >
          P{pickNo}
        </div>
        <div className="text-xs" style={{ color: "var(--grey)" }}>
          Round {round} · Pick {pickNo}
        </div>
        {currentTeam && (
          <div
            className="mt-1 font-head text-sm font-bold uppercase tracking-[0.04em] truncate"
            style={{ color: "#fff" }}
            title={currentTeam.name}
          >
            {currentTeam.name}
          </div>
        )}
        <div
          className="text-xs"
          style={{
            color: yourTurn ? "var(--green)" : "var(--text-mute)",
            fontWeight: yourTurn ? 700 : 400,
          }}
        >
          {yourTurn ? "your turn" : `Owner: ${currentTeam?.owner ?? "—"}`}
        </div>
      </div>
    </div>
  );
}
