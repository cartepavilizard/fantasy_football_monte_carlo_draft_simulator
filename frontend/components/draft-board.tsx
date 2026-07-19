"use client";

import * as React from "react";
import clsx from "clsx";

import { League, PickLogEntry } from "@/types";
import { CornerBadge, EmptyStateHawk } from "@/components/mascots";

// HAWK MODE draft board — the centerpiece of the draft room. A BOARD/LIST
// toggle: LIST renders the existing draft-room content unchanged (passed as
// children); BOARD renders the snake grid.
//
// Board geometry:
//   columns = league.teams in array order (team_index ↔ column)
//   rows    = league.round_size (falls back to fitting the on-the-clock pick
//             + every pick_log entry, so old drafts without round_size still
//             render a complete board)
//   snake   = even rows left→right, odd rows right→left (arrows in the gutter)
//   filled cells come from league.pick_log (may be missing/empty on old
//   drafts — those picks render as a dim "picked" cell, never a crash)
//   ON THE CLOCK = the cell for league.current_draft_turn (0-indexed),
//     derived row = floor(turn / N), col = snake
//   my picks = the simulator team's column → green border + hawk corner badge

export interface DraftBoardProps {
  league: League;
  children?: React.ReactNode;
}

type View = "board" | "list";

// position token lookup — fills the cell's left rail + badge background
function positionColor(pos: string): string {
  const p = pos.toUpperCase();
  if (p === "QB") return "var(--pos-qb)";
  if (p === "RB") return "var(--pos-rb)";
  if (p === "WR") return "var(--pos-wr)";
  if (p === "TE") return "var(--pos-te)";
  if (p === "K") return "var(--pos-k)";
  if (p === "DST" || p === "DEF") return "var(--pos-dst)";
  if (p === "FLEX") return "var(--pos-flex)";
  return "var(--grey)";
}

function lastName(fullName: string): string {
  const parts = fullName.trim().split(/\s+/);
  return parts.length > 0 ? parts[parts.length - 1] : fullName;
}

// snake column for a 0-indexed pick number across N teams
function snakeCol(pick: number, n: number): number {
  const row = Math.floor(pick / n);
  const inRow = pick % n;
  return row % 2 === 0 ? inRow : n - 1 - inRow;
}

export function DraftBoard({ league, children }: DraftBoardProps) {
  const [view, setView] = React.useState<View>("board");

  const teams = league.teams ?? [];
  const n = teams.length;
  const pickLog: PickLogEntry[] = league.pick_log ?? [];
  const turn = league.current_draft_turn ?? 0;

  // my (simulator) column — green border + hawk badge on every cell
  const myCol = React.useMemo(
    () => teams.findIndex((t) => t.simulator),
    [teams],
  );

  // resolve round_size: prefer the explicit field, otherwise grow to fit
  // the on-the-clock pick and every logged pick.
  const rows = React.useMemo(() => {
    const explicit = league.round_size ?? 0;
    const fromTurn = n > 0 ? Math.floor(turn / n) + 1 : 0;
    const fromLog =
      n > 0 && pickLog.length > 0
        ? Math.ceil(Math.max(...pickLog.map((p) => p.pick_number)) / n)
        : 0;
    return Math.max(explicit, fromTurn, fromLog, explicit > 0 ? 0 : 1);
  }, [league.round_size, n, turn, pickLog]);

  // index pick_log by pick_number for O(1) cell lookup
  const pickByNumber = React.useMemo(() => {
    const map = new Map<number, PickLogEntry>();
    pickLog.forEach((p) => map.set(p.pick_number, p));
    return map;
  }, [pickLog]);

  const draftComplete = league.draft_order.length === 0 && league.id !== "";

  // No teams → empty state (e.g. a fresh/legacy league with no setup yet)
  if (n === 0) {
    return (
      <div className="flex w-full flex-col items-center gap-3 border p-6 text-center"
        style={{
          borderColor: "var(--border)",
          borderRadius: "var(--radius)",
          background: "var(--surface)",
        }}
      >
        <EmptyStateHawk size={96} />
        <div className="font-head text-base font-bold uppercase tracking-wide">
          Nothing queued
        </div>
        <p className="text-sm text-[color:var(--text-mute)]">
          This league has no teams yet — set it up to see the draft board.
        </p>
        {children}
      </div>
    );
  }

  return (
    <div
      className="flex w-full flex-col gap-2 border p-3"
      style={{
        borderColor: "var(--border)",
        borderRadius: "var(--radius)",
        background: "var(--surface)",
      }}
    >
      {/* Header: title + Board/List toggle + legend */}
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="font-head text-lg font-bold uppercase tracking-[0.06em]">
          Draft Board
        </h3>
        <span
          className="font-head text-xs font-bold uppercase tracking-[0.06em] text-green"
          style={{
            background: "rgba(105,190,40,0.14)",
            border: "1px solid var(--green)",
            borderRadius: 100,
            padding: "2px 9px",
          }}
        >
          Centerpiece
        </span>
        <span className="text-xs text-[color:var(--text-mute)]">
          {n} teams × {rows} rounds · snake
        </span>

        {/* segmented Board / List toggle */}
        <div
          className="flex overflow-hidden"
          style={{
            background: "var(--surface-2)",
            border: "1px solid var(--border-2)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {(["board", "list"] as View[]).map((v) => {
            const active = view === v;
            return (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                className={clsx(
                  "font-head text-xs font-bold uppercase tracking-[0.05em]",
                  active ? "text-[#04240a]" : "text-[color:var(--text-dim)]",
                )}
                style={{
                  background: active ? "var(--green)" : "transparent",
                  border: "none",
                  padding: "5px 12px",
                  cursor: "pointer",
                }}
              >
                {v}
              </button>
            );
          })}
        </div>

        {/* legend */}
        <div className="ml-auto hidden flex-wrap items-center gap-3 text-xs text-[color:var(--text-dim)] md:flex">
          {myCol >= 0 && (
            <span className="inline-flex items-center gap-1">
              <span
                style={{
                  width: 11,
                  height: 11,
                  borderRadius: 2,
                  border: "1.5px solid var(--green)",
                }}
              />
              My picks
            </span>
          )}
          <span className="inline-flex items-center gap-1">
            <span
              style={{
                width: 11,
                height: 11,
                borderRadius: 2,
                background: "rgba(105,190,40,0.4)",
                border: "1px solid var(--green)",
              }}
            />
            On the clock
          </span>
          <span className="inline-flex items-center gap-1">
            <span
              style={{
                width: 11,
                height: 11,
                borderRadius: 2,
                border: "1px dashed var(--border-2)",
              }}
            />
            Open
          </span>
        </div>
      </div>

      {view === "list" ? (
        children
      ) : (
        // Fit-to-width: every team column always visible, no horizontal
        // scroll; 1fr columns shrink and cell text truncates
        <div className="w-full">
          <div style={{ minWidth: 0 }}>
            {/* team header row */}
            <div
              className="grid gap-1"
              style={{
                gridTemplateColumns: `40px repeat(${n}, minmax(0,1fr))`,
                marginBottom: 5,
              }}
            >
              <div className="flex items-center justify-center font-head text-[10px] font-bold uppercase text-[color:var(--text-mute)]">
                RD
              </div>
              {teams.map((team, c) => {
                const mine = c === myCol;
                return (
                  <div
                    key={c}
                    className="relative text-center"
                    style={{
                      padding: "5px 3px",
                      borderRadius: "var(--radius-sm)",
                      background: mine
                        ? "rgba(105,190,40,0.10)"
                        : "var(--surface-2)",
                      border: mine
                        ? "1px solid var(--green)"
                        : "1px solid var(--border)",
                    }}
                  >
                    {mine && (
                      <span className="absolute right-1 top-1">
                        <CornerBadge size={12} />
                      </span>
                    )}
                    <div className="font-head text-[9px] font-bold uppercase tracking-[0.05em] text-[color:var(--text-mute)]">
                      T{c + 1}
                    </div>
                    <div
                      className="font-head text-[11px] font-bold uppercase"
                      style={{
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        color: mine ? "var(--green)" : "var(--text)",
                      }}
                      title={team.name}
                    >
                      {team.name}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* rounds */}
            {Array.from({ length: rows }).map((_, r) => {
              const ltr = r % 2 === 0;
              return (
                <div
                  key={r}
                  className="grid gap-1"
                  style={{
                    gridTemplateColumns: `40px repeat(${n}, minmax(0,1fr))`,
                    marginBottom: 4,
                  }}
                >
                  <div
                    className="flex flex-col items-center justify-center"
                    style={{
                      background: "var(--surface-2)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-dim)",
                    }}
                  >
                    <span className="font-display text-sm leading-none">
                      {r + 1}
                    </span>
                    <span className="text-[11px] leading-none text-green">
                      {ltr ? "→" : "←"}
                    </span>
                  </div>
                  {Array.from({ length: n }).map((_, c) => {
                    const pickNo = ltr
                      ? r * n + c
                      : r * n + (n - 1 - c);
                    // grid pickNo is 0-based (matches current_draft_turn);
                    // pick_log.pick_number is 1-based
                    const logged = pickByNumber.get(pickNo + 1);
                    const isClock = !draftComplete && pickNo === turn;
                    const isPast = pickNo < turn;
                    const mine = c === myCol;
                    const color = logged
                      ? positionColor(logged.position)
                      : "var(--border)";

                    return (
                      <BoardCell
                        key={c}
                        pickNo={pickNo}
                        logged={logged}
                        isClock={isClock}
                        isPast={isPast}
                        mine={mine}
                        color={color}
                      />
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// One board cell — ~84x44px compact. States: filled, on-the-clock (pulsing),
// past-but-unlogged (dim), open (dashed, pick number only).
const BoardCell: React.FC<{
  pickNo: number;
  logged?: PickLogEntry;
  isClock: boolean;
  isPast: boolean;
  mine: boolean;
  color: string;
}> = ({ pickNo, logged, isClock, isPast, mine, color }) => {
  const filled = logged != null;
  // background tinted by position color for filled cells
  const bg = isClock
    ? "rgba(105,190,40,0.12)"
    : filled
      ? "var(--surface-2)"
      : isPast
        ? "var(--surface)"
        : "transparent";
  const border = isClock || mine
    ? "1.5px solid var(--green)"
    : filled
      ? "1px solid var(--border)"
      : "1px dashed var(--border-2)";

  return (
    <div
      className="relative"
      style={{
        minHeight: 44,
        borderRadius: "var(--radius-sm)",
        padding: "4px 5px 4px 8px",
        background: bg,
        border,
        overflow: "visible",
      }}
    >
      {/* left position-color rail */}
      <span
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          bottom: 0,
          width: 3,
          background: color,
          borderRadius: "var(--radius-sm) 0 0 var(--radius-sm)",
        }}
      />
      {mine && (
        <span className="absolute right-1 top-1">
          <CornerBadge size={11} />
        </span>
      )}

      {isClock ? (
        <>
          {/* on-the-clock pulse layer */}
          <span
            className="animate-hawk-pulse"
            style={{
              position: "absolute",
              inset: 0,
              borderRadius: "var(--radius-sm)",
              pointerEvents: "none",
            }}
          />
          <span
            className="animate-glare"
            style={{
              position: "absolute",
              inset: 0,
              background:
                "radial-gradient(circle at 50% 50%, rgba(105,190,40,0.4), transparent 60%)",
              pointerEvents: "none",
            }}
          />
          <div
            className="relative flex h-full flex-col items-center justify-center gap-0.5"
            style={{ minHeight: 36 }}
          >
            <span
              className="animate-eye-scan font-head text-[8px] font-bold uppercase tracking-[0.1em] text-green"
              style={{ letterSpacing: "0.1em" }}
            >
              👁 On Clock
            </span>
            <span className="font-display text-[13px] leading-none text-white">
              P{pickNo + 1}
            </span>
            <span className="text-[8px] uppercase text-grey">You&apos;re up</span>
          </div>
        </>
      ) : filled ? (
        <>
          <div className="flex items-center justify-between">
            <span
              className="font-head text-[9px] font-bold"
              style={{
                color: "#04240a",
                borderRadius: 2,
                padding: "0 4px",
                background: color,
              }}
            >
              {logged!.position.toUpperCase()}
            </span>
            <span className="text-[9px] tabular-nums text-[color:var(--text-mute)]">
              {pickNo + 1}
            </span>
          </div>
          <div
            className="mt-0.5 font-semibold"
            style={{
              fontSize: "var(--fs-xs)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {lastName(logged!.player_name)}
          </div>
          <div className="text-[9px] uppercase tracking-[0.04em] text-[color:var(--text-mute)]">
            {logged!.team_name}
          </div>
        </>
      ) : (
        <div className="flex h-full items-center justify-center text-[9px] tabular-nums text-[color:var(--text-mute)]">
          {pickNo + 1}
        </div>
      )}
    </div>
  );
};
