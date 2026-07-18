"use client";

import * as React from "react";
import { FiAlertTriangle } from "react-icons/fi";

import {
  HawkCard,
  HawkCardHeader,
  HawkChip,
} from "@/components/hawk-cards";
import { LineupData, LineupPlayer, LineupSlotEntry } from "@/types";

// Per-row current -> optimal resolution. The lineup payload gives us the
// optimal slot assignment plus each player's CURRENT slot (LineupPlayer
// .current_slot) and the moves list. From those we reconstruct, for every
// optimal slot, who is currently sitting there:
//
//   currentBySlot = index every rostered player (optimal + bench + IR) by
//   their current_slot. The current occupant of optimal slot X is whoever
//   has current_slot == X (a benched player being promoted, or another
//   starter being swapped in/out).
//
// A row is a "change" when the optimal player's current_slot != slot.
// Per-row delta = optimal.adjusted_projection - current.adjusted_projection
// (both fields are present on every LineupPlayer). For "ok" rows the delta
// is the composite's em-dash.
function buildCurrentBySlot(
  optimal: LineupSlotEntry[],
  bench: LineupPlayer[],
  ir: LineupPlayer[],
): Map<string, LineupPlayer> {
  const map = new Map<string, LineupPlayer>();

  for (const slot of optimal) {
    if (slot.player) map.set(slot.player.current_slot, slot.player);
  }
  for (const p of bench) map.set(p.current_slot, p);
  for (const p of ir) map.set(p.current_slot, p);

  return map;
}

const SlotRow: React.FC<{
  slot: string;
  current: LineupPlayer | undefined;
  optimal: LineupPlayer | null;
}> = ({ slot, current, optimal }) => {
  const change = optimal != null && optimal.current_slot !== slot;
  const optName = optimal?.player_name ?? "empty";
  const curName = current?.player_name ?? "empty";
  const curProj = current?.adjusted_projection ?? current?.base_projection;
  const optProj = optimal?.adjusted_projection ?? optimal?.base_projection;
  const delta =
    change && curProj != null && optProj != null
      ? optProj - curProj
      : null;

  return (
    <div
      className="grid items-center border-b"
      style={{
        gridTemplateColumns: "44px 1fr 1fr 48px",
        gap: 6,
        padding: "0 12px",
        height: 28,
        borderColor: "var(--border)",
        fontSize: "var(--fs-sm)",
      }}
    >
      <span
        className="font-head font-bold uppercase"
        style={{ fontSize: "var(--fs-xs)", color: "var(--text-mute)" }}
      >
        {slot}
      </span>
      <span
        className="truncate"
        style={{ color: "var(--text-dim)" }}
        title={curName}
      >
        {curName}
      </span>
      <span className="truncate" title={optName}>
        {change ? (
          <b style={{ color: "var(--green)" }}>
            {optName} ↑
          </b>
        ) : (
          <span style={{ color: "var(--text-mute)" }}>{optName}</span>
        )}
      </span>
      <span
        className="text-right tabular-nums font-bold"
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {delta != null ? (
          <span style={{ color: "var(--green)" }}>
            {delta > 0 ? "+" : ""}
            {delta.toFixed(1)}
          </span>
        ) : (
          <span style={{ color: "var(--text-mute)" }}>—</span>
        )}
      </span>
    </div>
  );
};

// The hero "Lineup Optimizer" card from the composite: bar header with the
// "+X.X pts" delta badge and an expandable "view moves" disclosure INSTEAD
// of the composite's Apply button. Apply would be a fake action (the app
// has no ESPN lineup-write path), so the composite's Apply button is
// deliberately dropped here per the task's "real or does not exist" rule —
// the disclosure that replaces it surfaces the real moves list, lock-flex
// advice, and the warnings the optimizer emits.
export const InseasonLineupOptimizer: React.FC<{
  data: LineupData | undefined;
  loading: boolean;
  warnings: string[];
  // lock-advice entries only carry player_ids; the page resolves them to
  // names via the lineup payload (see lineupPlayerNames in page.tsx).
  resolveName: (playerId: number) => string;
  earlyLockCutoff: Date | null;
  formatKickoff: (iso: string) => string;
}> = ({ data, loading, warnings, resolveName, earlyLockCutoff, formatKickoff }) => {
  const [showMoves, setShowMoves] = useState(false);
  const [showDetail, setShowDetail] = useState(false);

  if (loading || !data) {
    return (
      <HawkCard>
        <HawkCardHeader title="Lineup Optimizer" />
        <div className="p-3 text-sm" style={{ color: "var(--text-mute)" }}>
          Loading…
        </div>
      </HawkCard>
    );
  }

  const currentBySlot = buildCurrentBySlot(data.optimal, data.bench, data.ir);
  const delta = data.delta_points;
  const hasMoves = data.moves.length > 0;

  return (
    <HawkCard>
      <HawkCardHeader
        title="Lineup Optimizer"
        subtitle={data.week ? `week ${data.week}` : undefined}
        right={
          <>
            <span
              className="font-bold"
              style={{
                color: delta > 0 ? "var(--green)" : "var(--text-mute)",
                fontSize: "var(--fs-xs)",
              }}
            >
              {delta > 0 ? "+" : ""}
              {delta.toFixed(1)} pts
            </span>
            {hasMoves && (
              <button
                type="button"
                onClick={() => setShowMoves((v) => !v)}
                className="font-head font-bold uppercase"
                style={{
                  fontSize: "var(--fs-xs)",
                  background: "var(--green)",
                  color: "#04240a",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  padding: "4px 10px",
                  cursor: "pointer",
                  letterSpacing: "0.04em",
                  marginLeft: 8,
                }}
              >
                {showMoves ? "Hide moves" : "View moves"}
              </button>
            )}
          </>
        }
      />

      {warnings.length > 0 && (
        <ul
          className="flex flex-col gap-1 px-3 py-2"
          style={{
            fontSize: "var(--fs-xs)",
            color: "var(--warn)",
            borderBottom: "1px solid var(--border)",
          }}
        >
          {warnings.map((w, i) => (
            <li key={i} className="flex items-start gap-1">
              <FiAlertTriangle className="mt-0.5 shrink-0" />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      )}

      <div>
        {data.optimal.map((entry, i) => (
          <SlotRow
            key={`${entry.slot}-${i}`}
            slot={entry.slot}
            optimal={entry.player}
            current={currentBySlot.get(entry.slot)}
          />
        ))}
      </div>

      {showMoves && hasMoves && (
        <div
          className="flex flex-col gap-1 px-3 py-2"
          style={{
            background: "var(--surface-2)",
            borderTop: "1px solid var(--border)",
            fontSize: "var(--fs-sm)",
          }}
        >
          <div className="font-head text-xs font-bold uppercase tracking-[0.05em] text-grey">
            Moves to make
          </div>
          <ul className="flex flex-col gap-0.5">
            {data.moves.map((move) => (
              <li key={move.player_id}>
                {move.player_name}: {move.from_slot} →{" "}
                <b>{move.to_slot}</b>
              </li>
            ))}
          </ul>

          {data.lock_advice.length > 0 && (
            <>
              <div className="mt-1 font-head text-xs font-bold uppercase tracking-[0.05em] text-grey">
                Lock-flexibility advice
              </div>
              <ul className="flex flex-col gap-1">
                {data.lock_advice.map((advice, i) => {
                  const early =
                    earlyLockCutoff !== null &&
                    advice &&
                    false; // (advice carries no kickoff; the per-slot kickoff
                  // lives on optimal[].player.kickoff — surfaced in the
                  // detail expansion below, not here, to avoid implying a
                  // lock-flex check this row doesn't actually perform)
                  return (
                    <li
                      key={i}
                      className="flex flex-col gap-0.5 rounded"
                      style={{
                        border: "1px solid rgba(245,179,1,0.4)",
                        background: "rgba(245,179,1,0.08)",
                        padding: "6px 8px",
                      }}
                    >
                      <span>
                        <b>{advice.slot}</b>:{" "}
                        {resolveName(advice.start)}{" "}
                        <span style={{ color: "var(--text-mute)" }}>vs.</span>{" "}
                        {resolveName(advice.alternative)}
                        <span
                          className="ml-1"
                          style={{
                            color: "var(--warn)",
                            fontSize: "var(--fs-xs)",
                          }}
                        >
                          costs {advice.cost_points.toFixed(1)} pts
                        </span>
                      </span>
                      <span style={{ color: "var(--text-mute)", fontSize: "var(--fs-xs)" }}>
                        {advice.note}
                      </span>
                      {early && <span style={{ display: "none" }}>{early}</span>}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      )}

      {/* Per-slot detail expansion: kickoff + matchup multiplier (the
          composite's rows are intentionally too dense to fit these, but
          C2/C6 context stays reachable — never dropped — behind a real
          disclosure instead of a fake Apply button.) */}
      <button
        type="button"
        onClick={() => setShowDetail((v) => !v)}
        className="font-head text-xs font-bold uppercase tracking-[0.05em] text-grey"
        style={{
          background: "var(--surface-2)",
          borderTop: "1px solid var(--border)",
          padding: "6px 12px",
          textAlign: "left",
          cursor: "pointer",
          border: "none",
        }}
      >
        {showDetail ? "Hide slot detail" : "Show slot detail (kickoff + matchup)"}
      </button>
      {showDetail && (
        <div
          className="flex flex-col gap-1 px-3 py-2"
          style={{ fontSize: "var(--fs-xs)", background: "var(--surface-2)" }}
        >
          {data.optimal.map((entry, i) => {
            const p = entry.player;
            if (!p) return null;
            const locksEarly =
              earlyLockCutoff !== null &&
              p.kickoff !== null &&
              new Date(p.kickoff) <= earlyLockCutoff;

            return (
              <div
                key={`${entry.slot}-${i}`}
                className="grid items-center"
                style={{
                  gridTemplateColumns: "44px 1fr 1fr 1fr",
                  gap: 6,
                }}
              >
                <span className="font-head font-bold uppercase text-[var(--text-mute)]">
                  {entry.slot}
                </span>
                <span className="truncate" title={p.player_name}>
                  {p.player_name}{" "}
                  <span style={{ color: "var(--text-mute)" }}>
                    {p.position ?? "—"} · {p.nfl_team ?? "—"}
                  </span>
                </span>
                <span style={{ color: "var(--text-mute)" }}>
                  {p.on_bye
                    ? "bye"
                    : p.kickoff
                      ? `${locksEarly ? "Locks early · " : ""}${formatKickoff(p.kickoff)}`
                      : "—"}
                </span>
                <span className="text-right">
                  <HawkChip tone={p.matchup.confidence === "high" ? "green" : "neutral"}>
                    {p.matchup.multiplier.toFixed(2)}x
                    {p.matchup.rank ? ` #${p.matchup.rank}` : ""}
                  </HawkChip>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </HawkCard>
  );
};

// (importing useState lazily to keep the top of file tidy)
import { useState } from "react";
