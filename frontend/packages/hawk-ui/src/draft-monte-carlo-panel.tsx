"use client";

import * as React from "react";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";

import {
  HomerCheck,
  MonteCarloResults,
  PlayerTag,
} from "./types";
import { HawkCard, HawkCardHeader } from "./hawk-cards";
import { TagBadge } from "./draft-tag-badge";

// HAWK MODE Monte Carlo panel — the existing "Monte Carlo Results" block
// (iterations line, suggested-pick headline, per-position tag-aware
// candidates, homer-check tables), folded under the board in a HawkCard.

const positions = ["qb", "rb", "wr", "te", "dst", "k"];

// A6: neutral value comparison for a homer-team (Seahawks) suggested
// pick vs. the top alternatives at that position. Unchanged from the
// original draft-room page; moved here so the panel owns its own table.
function HomerCheckPanel({ check }: { check: HomerCheck }) {
  const [expanded, setExpanded] = React.useState(false);
  const rows = [check.suggested, ...check.alternatives];

  return (
    <div className="mt-1">
      <button
        className="flex items-center gap-1 text-xs font-bold px-1.5 py-0.5 rounded-full bg-[#69BE28]/15 text-[#69BE28] border border-[#69BE28]/40 w-fit"
        type="button"
        onClick={() => setExpanded(!expanded)}
      >
        Homer Check
      </button>
      {expanded && (
        <div className="mt-2 overflow-x-auto">
          <table className="text-xs w-full text-left border-collapse">
            <thead>
              <tr className="text-default-500">
                <th className="pr-2 py-1 font-normal">Player</th>
                <th className="pr-2 py-1 font-normal">Proj</th>
                <th className="pr-2 py-1 font-normal">Rank</th>
                <th className="pr-2 py-1 font-normal">ADP vs. Pick</th>
                <th className="pr-2 py-1 font-normal">Tier</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name} className="border-t border-default-200">
                  <td className="pr-2 py-1">
                    <span className="flex items-center gap-1 font-bold">
                      <TagBadge tag={row.tag} />
                      {row.name}
                    </span>
                  </td>
                  <td className="pr-2 py-1">{row.projected_points.toFixed(1)}</td>
                  <td className="pr-2 py-1">{row.consensus_rank ?? "—"}</td>
                  <td className="pr-2 py-1">{row.adp_vs_pick ?? "—"}</td>
                  <td className="pr-2 py-1">{row.tier ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-xs italic text-default-500 mt-1">{check.note}</p>
        </div>
      )}
    </div>
  );
}

export interface MonteCarloPanelProps {
  isSimulatorTurn: boolean;
  simulationError: boolean;
  onRetry: () => void;
  monteCarloResults: MonteCarloResults;
  bestPick: string;
  // Cost-of-waiting rule (the new PRIMARY recommendation). All optional;
  // when the payload lacks them the panel renders exactly as it does
  // today (the legacy per-position averages line as its headline).
  // Per-position maps keyed qb/rb/wr/te/dst/k.
  // Field alias cost_of_waiting|costOfWaiting (backend snake_case -> UI camelCase):
  costOfWaiting?: Record<string, number> | null;
  valueNow?: Record<string, number> | null;
  valueAtNextPick?: Record<string, number> | null;
  // The pick number of the user's next turn, for context.
  yourNextPick?: number | null;
  // The position the new rule recommends (highlighted in the table).
  recommendedPosition?: string | null;
  // Plain-terms explanation of the recommended position.
  recommendationReason?: string | null;
  // Additive breakdown of `iterations` by position. The headline
  // `iterations` counts one increment per POSITION in the inner rollout
  // loop, so it is the SUM of these per-position counts -- not "your
  // pick simulated N times". When present the iterations line becomes
  // "19 per position (76 total)"; when absent the panel renders exactly
  // as it does today so older payloads still work. Snake_case backend
  // payloads alias to iterationsPerPosition at the call site.
  iterationsPerPosition?: Record<string, number> | null;
}

export function MonteCarloPanel({
  isSimulatorTurn,
  simulationError,
  onRetry,
  monteCarloResults,
  bestPick,
  costOfWaiting,
  valueNow,
  valueAtNextPick,
  yourNextPick,
  recommendedPosition,
  recommendationReason,
  iterationsPerPosition,
}: MonteCarloPanelProps) {
  if (!isSimulatorTurn) {
    return (
      <HawkCard padded>
        <HawkCardHeader
          title="Monte Carlo Results"
          className="border-b-0 pb-0"
        />
        <p className="font-bold w-full mt-2 text-default-500">
          Not simulating — waits for the simulator&apos;s turn.
        </p>
      </HawkCard>
    );
  }

  return (
    <HawkCard>
      <HawkCardHeader title="Monte Carlo Results" />
      <div className="flex flex-col gap-2 w-full p-3">
        {simulationError ? (
          <div className="flex items-center justify-between w-full">
            <p className="font-bold text-danger">
              Simulation failed. Please try again.
            </p>
            <Button
              color="danger"
              size="sm"
              variant="flat"
              onClick={onRetry}
            >
              Retry
            </Button>
          </div>
        ) : monteCarloResults.iterations === 0 ? (
          <p className="font-bold w-full">
            <span className="flex items-center">
              <Spinner size="sm" />
              <span className="ml-2">Simulating...</span>
            </span>
          </p>
        ) : (
          <div className="flex flex-col gap-2 w-full">
            <div className="flex justify-between">
              <p>Best Pick: {bestPick}</p>
              <p
                style={{
                  color: iterationsPerPosition
                    ? "var(--text)"
                    : "var(--text-mute)",
                }}
              >
                {iterationsPerPosition &&
                Object.keys(iterationsPerPosition).length > 0
                  ? (() => {
                      // `iterations` is the SUM of per-position rollout
                      // counts (the loop increments once per position in
                      // its inner loop), so the honest headline is the
                      // per-position count plus the additive total.
                      const counts = Object.values(iterationsPerPosition);
                      const total = monteCarloResults.iterations;
                      const min = Math.min(...counts);
                      const max = Math.max(...counts);
                      const perPosition =
                        min === max
                          ? `${min} per position`
                          : `${min}-${max} per position`;
                      return `${perPosition} (${total} total)`;
                    })()
                  : `${monteCarloResults.iterations} Iterations Performed`}
              </p>
            </div>
            {/* The suggested-pick callout (victory art + headline pick)
                used to live here, below the board and below the fold. It
                duplicated the right rail's Suggested card, so it now
                lives only there — above the fold, visible the moment the
                sim resolves. This panel keeps the raw per-position
                numbers, which is what it's actually for. */}
          </div>
        )}
        {/* Cost-of-waiting rule — the panel's PRIMARY content. Per
            position: best available now vs best expected at your NEXT
            turn, and the difference (the cost of waiting). The position
            the backend recommends is highlighted. Renders ONLY when the
            new payload carries the maps; older payloads fall through to
            the legacy averages line below, unchanged. */}
        {costOfWaiting && Object.keys(costOfWaiting).length > 0 && (
          <div
            className="flex flex-col gap-1 w-full"
            style={{ padding: "4px 0" }}
          >
            <div
              className="flex items-center gap-2"
              style={{ flexWrap: "wrap" }}
            >
              {yourNextPick != null && (
                <span
                  className="font-head text-[10px] font-bold uppercase"
                  style={{
                    color: "var(--text-mute)",
                    background: "var(--surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: 2,
                    padding: "1px 5px",
                  }}
                >
                  your next pick: {yourNextPick}
                </span>
              )}
              {recommendedPosition && (
                <span
                  className="font-head text-[10px] font-bold uppercase"
                  style={{
                    color: "#04240a",
                    background: "var(--green)",
                    borderRadius: 2,
                    padding: "1px 5px",
                  }}
                >
                  rec: {recommendedPosition.toUpperCase()}
                </span>
              )}
            </div>
            {recommendationReason && (
              <p
                className="text-xs font-bold"
                style={{ color: "var(--green)", margin: 0 }}
              >
                {recommendationReason}
              </p>
            )}
            <div className="flex flex-col gap-0.5 w-full">
              {positions
                .filter((p) => costOfWaiting[p] != null)
                .map((p) => {
                  const cost = costOfWaiting[p];
                  const now = valueNow?.[p];
                  const next = valueAtNextPick?.[p];
                  const isRec = recommendedPosition === p;
                  return (
                    <div
                      key={p}
                      className="flex items-center gap-1.5 text-xs"
                      style={{
                        padding: "2px 4px",
                        borderRadius: "var(--radius-sm)",
                        background: isRec ? "var(--surface-3)" : "transparent",
                        border: isRec
                          ? "1px solid var(--border-2)"
                          : "1px solid transparent",
                      }}
                    >
                      <span
                        className="font-head font-bold uppercase"
                        style={{
                          minWidth: 26,
                          color: isRec ? "var(--green)" : "var(--text)",
                        }}
                      >
                        {p.toLocaleUpperCase()}
                      </span>
                      <span style={{ color: "var(--text)" }}>
                        {now != null ? now.toFixed(1) : "—"}
                      </span>
                      <span style={{ color: "var(--text-mute)" }}>now vs</span>
                      <span style={{ color: "var(--text-dim)" }}>
                        {next != null ? next.toFixed(1) : "—"}
                      </span>
                      <span style={{ color: "var(--text-mute)" }}>next</span>
                      <span
                        className="ml-auto font-bold"
                        style={{ color: "var(--green)" }}
                      >
                        {/* A COST, so it reads positive: "waiting costs you
                            59.1 pts here". Rendering it as −59.1 contradicted
                            the reason line directly above, which says
                            "waiting costs 59.1 pts at WR". */}
                        {cost.toFixed(1)}
                      </span>
                    </div>
                  );
                })}
            </div>
          </div>
        )}
        {/* The legacy per-position full-draft averages — the SUPERSEDED
            rule's output. Known to be noisy AND wrong (on the same board
            it gave three different answers across three identical runs,
            and with randomness off it still ranked a TE ADP 22.8 above a
            WR ADP 3.8). Kept for traceability and older payloads; demoted
            visually and labelled honestly when the cost-of-waiting display
            above is present. Renders exactly as before when it is not. */}
        {costOfWaiting && Object.keys(costOfWaiting).length > 0 ? (
          <details
            className="text-xs"
            style={{ color: "var(--text-mute)" }}
          >
            <summary
              className="italic"
              style={{ cursor: "pointer", opacity: 0.7 }}
            >
              older, noisier estimate (full-draft averages)
            </summary>
            <p className="italic" style={{ marginTop: 2 }}>
              {`
                QB: ${Math.round(monteCarloResults.qb).toLocaleString()} |
                RB: ${Math.round(monteCarloResults.rb).toLocaleString()} |
                WR: ${Math.round(monteCarloResults.wr).toLocaleString()} |
                TE: ${Math.round(monteCarloResults.te).toLocaleString()} |
                DST: ${Math.round(monteCarloResults.dst).toLocaleString()} |
                K: ${Math.round(monteCarloResults.k).toLocaleString()}
              `}
            </p>
          </details>
        ) : (
          <p className="italic text-sm text-default-500">
            {`
              QB: ${Math.round(monteCarloResults.qb).toLocaleString()} |
              RB: ${Math.round(monteCarloResults.rb).toLocaleString()} |
              WR: ${Math.round(monteCarloResults.wr).toLocaleString()} |
              TE: ${Math.round(monteCarloResults.te).toLocaleString()} |
              DST: ${Math.round(monteCarloResults.dst).toLocaleString()} |
              K: ${Math.round(monteCarloResults.k).toLocaleString()}
            `}
          </p>
        )}
        {/* A4: the tag-aware candidate the engine would take at each
            position, plus the A6 homer-check table where present. */}
        {Object.keys(monteCarloResults.suggested).length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 w-full mt-1 border-t border-default pt-2">
            {positions
              .filter((position) => monteCarloResults.suggested[position])
              .map((position) => {
                const pick = monteCarloResults.suggested[position];
                return (
                  <div key={position} className="flex flex-col text-left">
                    <span className="flex items-center gap-1 text-sm font-bold">
                      {position.toLocaleUpperCase()}:{" "}
                      <TagBadge tag={pick.tag as PlayerTag | null} />
                      {pick.name}
                    </span>
                    {pick.reason && (
                      <span className="text-xs italic text-default-500">
                        {pick.reason}
                      </span>
                    )}
                    {monteCarloResults.homer_checks[position] && (
                      <HomerCheckPanel
                        check={monteCarloResults.homer_checks[position]}
                      />
                    )}
                  </div>
                );
              })}
          </div>
        )}
      </div>
    </HawkCard>
  );
}
