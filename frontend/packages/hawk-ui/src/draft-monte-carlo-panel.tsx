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
import { VictoryBadge } from "./mascots";

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
}

export function MonteCarloPanel({
  isSimulatorTurn,
  simulationError,
  onRetry,
  monteCarloResults,
  bestPick,
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
              <p>{`${monteCarloResults.iterations} Iterations Performed`}</p>
            </div>
            {/* HAWK MODE suggested-pick panel — victory mascot + the
                engine's headline pick on a green-tinted navy cutout. */}
            {bestPick && bestPick !== "Simulation Error" && (
              <div
                className="relative overflow-hidden flex items-center gap-3"
                style={{
                  background:
                    "linear-gradient(120deg, rgba(105,190,40,0.16), transparent 70%), var(--navy)",
                  border: "1px solid var(--green)",
                  borderRadius: "var(--radius)",
                  padding: "var(--sp-3)",
                }}
              >
                <div className="shrink-0">
                  <VictoryBadge size={48} />
                </div>
                <div className="min-w-0">
                  <div className="font-head text-xs font-bold uppercase tracking-[0.08em] text-green">
                    Suggested Pick
                  </div>
                  <div className="font-head text-lg font-bold uppercase text-white">
                    {bestPick}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
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
