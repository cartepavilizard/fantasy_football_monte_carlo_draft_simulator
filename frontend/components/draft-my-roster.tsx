"use client";

import * as React from "react";

import { League, PickLogEntry } from "@/types";
import { HawkCard, HawkCardHeader } from "@/components/hawk-cards";
import { CornerBadge } from "@/components/mascots";

// HAWK MODE My Roster — the right rail's bottom card. Per the composite
// it's a surface card with a hawk-badge header and one dense row per
// drafted player: a position color dot, the name, and the position.
// We source the simulator team's picks from league.pick_log, grouped by
// position so the row order matches the composite's per-position list.

function positionDotColor(pos: string): string {
  const p = pos.toUpperCase();
  if (p === "QB") return "var(--pos-qb)";
  if (p === "RB") return "var(--pos-rb)";
  if (p === "WR") return "var(--pos-wr)";
  if (p === "TE") return "var(--pos-te)";
  if (p === "K") return "var(--pos-k)";
  if (p === "DST" || p === "DEF") return "var(--pos-dst)";
  return "var(--grey)";
}

const positionOrder = ["qb", "rb", "wr", "te", "dst", "k"];

export interface MyRosterProps {
  league: League;
}

export function MyRoster({ league }: MyRosterProps) {
  const teams = league.teams ?? [];
  const myTeamIndex = React.useMemo(
    () => teams.findIndex((t) => t.simulator),
    [teams],
  );

  const myPicks: PickLogEntry[] = React.useMemo(() => {
    const log = league.pick_log ?? [];
    if (myTeamIndex < 0) return [];
    return log.filter((p) => p.team_index === myTeamIndex);
  }, [league.pick_log, myTeamIndex]);

  // Group by position in the composite's canonical order so QBs sit on
  // top, then RB/WR/TE/DST/K.
  const grouped = React.useMemo(() => {
    const map = new Map<string, PickLogEntry[]>();
    positionOrder.forEach((p) => map.set(p, []));
    myPicks.forEach((p) => {
      const key = p.position.toLowerCase();
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(p);
    });
    return map;
  }, [myPicks]);

  const myTeamName =
    myTeamIndex >= 0 ? teams[myTeamIndex]?.name ?? "Your Team" : "Your Team";

  return (
    <HawkCard className="hawk-scroll" style={{ overflowY: "auto" }}>
      <HawkCardHeader
        title={
          <span className="flex items-center gap-2">
            <CornerBadge size={16} />
            <span>My Roster</span>
          </span>
        }
        right={
          <span
            className="text-[10px] uppercase tracking-[0.04em]"
            style={{ color: "var(--text-mute)" }}
          >
            {myTeamName}
          </span>
        }
      />
      {myPicks.length === 0 ? (
        <div
          className="px-3 py-3 text-xs"
          style={{ color: "var(--text-mute)" }}
        >
          No picks yet.
        </div>
      ) : (
        Array.from(grouped.entries()).map(([pos, picks]) =>
          picks.length === 0 ? null : (
            <div key={pos}>
              {picks.map((p) => (
                <div
                  key={`${pos}-${p.pick_number}`}
                  className="flex items-center gap-2 px-3"
                  style={{
                    height: 28,
                    borderBottom: "1px solid var(--border)",
                    fontSize: "var(--fs-sm)",
                  }}
                >
                  <span
                    style={{
                      width: 5,
                      height: 5,
                      borderRadius: "50%",
                      background: positionDotColor(p.position),
                      flexShrink: 0,
                    }}
                  />
                  <span
                    className="flex-1 truncate"
                    title={p.player_name}
                    style={{ color: "var(--text)" }}
                  >
                    {p.player_name}
                  </span>
                  <span
                    className="text-[9px] uppercase"
                    style={{ color: "var(--text-mute)" }}
                  >
                    {p.position.toUpperCase()}
                  </span>
                </div>
              ))}
            </div>
          ),
        )
      )}
    </HawkCard>
  );
}
