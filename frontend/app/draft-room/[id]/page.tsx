"use client";

import { useEffect, useMemo, useState } from "react";

import {
  useGetDraftQuery,
  useDraftPlayerMutation,
  useRunMonteCarloMutation,
} from "@/api/services/draft";
import { useGetPlayersQuery } from "@/api/services/league";
import { title, subtitle } from "@/components/primitives";
import { DraftBoard } from "@/components/draft-board";
import { BestAvailable } from "@/components/draft-best-available";
import { OnTheClock } from "@/components/draft-on-clock";
import { Suggested } from "@/components/draft-suggested";
import { MyRoster } from "@/components/draft-my-roster";
import { ScarcityPanel } from "@/components/draft-scarcity-panel";
import { MonteCarloPanel } from "@/components/draft-monte-carlo-panel";
import {
  Draft,
  League,
  MonteCarloResults,
  PlayerTag,
  Players,
  PositionScarcity,
} from "@/types";

const positions = ["qb", "rb", "wr", "te", "dst", "k"];

const emptyLeague: League = {
  id: "",
  name: "",
  created: "",
  teams: [],
  players: {
    qb: [],
    rb: [],
    wr: [],
    te: [],
    dst: [],
    k: [],
  },
  draft_order: [],
  current_draft_turn: 0,
};

const emptyMonteCarloResults: MonteCarloResults = {
  qb: 0,
  rb: 0,
  wr: 0,
  te: 0,
  dst: 0,
  k: 0,
  iterations: 0,
  suggested: {},
  homer_checks: {},
};

export default function DraftIdPage({ params }: { params: { id: string } }) {
  const {
    data: draft = {
      league: emptyLeague,
      id: "",
      created: "",
    },
  } = useGetDraftQuery(params.id);
  const [draftPlayer] = useDraftPlayerMutation();
  const [runMonteCarlo] = useRunMonteCarloMutation();
  const [monteCarloResults, setMonteCarloResults] = useState<MonteCarloResults>(
    emptyMonteCarloResults,
  );
  const [bestPick, setBestPick] = useState("");
  const [searchFilter, setSearchFilter] = useState("");
  const [simulationError, setSimulationError] = useState(false);

  const leagueId = draft.league.id;

  // Unfiltered draftable players, used to look up any player's tag
  // (e.g. for markers in the scarcity at-risk lists) regardless of
  // which tag filter chip is currently selected in the Best Available rail.
  const { data: allPlayers, refetch: refetchAllPlayers } = useGetPlayersQuery(
    { id: leagueId },
    { skip: !leagueId },
  );

  // The filtered player list (the Best Available rail issues its own
  // tagged query via the same hook). We still consume the unfiltered
  // list here for tag lookup and the board's LIST view fallback.
  const { refetch: refetchFilteredPlayers } = useGetPlayersQuery(
    { id: leagueId },
    { skip: !leagueId },
  );

  const playerTagByName = useMemo(() => {
    const map: Record<string, PlayerTag | null | undefined> = {};

    positions.forEach((position) => {
      (allPlayers?.[position as keyof Players] ?? []).forEach((player) => {
        map[player.name] = player.tag;
      });
    });

    return map;
  }, [allPlayers]);

  // The scarcity endpoint 400s once the draft is over
  const draftComplete =
    draft.id !== "" && draft.league.draft_order.length === 0;

  const isSimulatorTurn =
    draft.league.draft_order.length > 0 &&
    draft.league.teams[draft.league.draft_order[0]].simulator;

  // Drafting is paused (per the original page) when the simulator team
  // is on the clock and the Monte Carlo sim is still running and has
  // not errored out.
  const draftPaused =
    isSimulatorTurn &&
    monteCarloResults.iterations === 0 &&
    !simulationError;

  // Draft a player with a POST request to '/draft/:id/pick'
  const handleDraftPlayer = async (name: string) => {
    await draftPlayer({ id: draft.id, name });
    setSearchFilter("");
    refetchAllPlayers();
    refetchFilteredPlayers();
  };

  // When the team drafting is the simulator, set the Monte Carlo results
  const useEffectDeps = [draft.league, monteCarloResults, simulationError];
  useEffect(() => {
    if (isSimulatorTurn) {
      if (monteCarloResults.iterations === 0 && !simulationError) {
        runMonteCarlo({ id: draft.id })
          .unwrap()
          .then((data) => {
            setMonteCarloResults(data);

            // Find the position in the results with the highest value
            // (excluding the non-numeric `suggested` and `homer_checks`
            // maps, added in A4/A6)
            const bestPosition = Object.keys(data)
              .filter((key) => key !== "suggested" && key !== "homer_checks")
              .reduce((a, b) =>
                data[a as keyof MonteCarloResults] >
                data[b as keyof MonteCarloResults]
                  ? a
                  : b,
              );

            if (bestPosition === "iterations") {
              setBestPick("Simulation Error");
            } else {
              const bestPlayer = draft.league.players[
                bestPosition as keyof Players
              ].find((player) => player.drafted === false);

              setBestPick(
                `${bestPlayer?.name} (${bestPosition.toLocaleUpperCase()})`,
              );
            }
          })
          .catch((error) => {
            console.error("Monte Carlo simulation failed:", error);
            setSimulationError(true);
          });
      }
    } else {
      setMonteCarloResults(emptyMonteCarloResults);
      setBestPick("");
      setSimulationError(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, useEffectDeps);

  // Resolve the structured SuggestedPick for the headline position so
  // the Suggested card can show the engine's reason, plus the matching
  // scarcity call (if a report has been loaded) for context.
  const suggestedPick = useMemo(() => {
    if (!bestPick || bestPick === "Simulation Error") return null;
    const match = bestPick.match(/\(([A-Z]+)\)$/);
    if (!match) return null;
    const pos = match[1].toLowerCase();
    return monteCarloResults.suggested[pos] ?? null;
  }, [bestPick, monteCarloResults.suggested]);

  const bestPosition = useMemo(() => {
    if (!bestPick || bestPick === "Simulation Error") return null;
    const match = bestPick.match(/\(([A-Z]+)\)$/);
    return match ? match[1].toLowerCase() : null;
  }, [bestPick]);

  // Scarcity context for the Suggested card: the report is owned by the
  // ScarcityPanel, but the panel writes back via a refetched query; we
  // surface the matching position's call by reading the same lazy hook
  // here would duplicate state. Instead, the Suggested card accepts a
  // null scarcity and shows just the engine reason — the panel below
  // carries the full tier-depletion context. Keeping the data flow single-source.
  const scarcityContext: PositionScarcity | null = null;

  const league = draft.league;
  const n = league.teams.length;
  const turn = league.current_draft_turn ?? 0;
  const round = n > 0 ? Math.floor(turn / n) + 1 : 1;
  const pickNo = turn + 1;

  return (
    <section className="flex flex-col gap-4 w-full">
      {/* Title block — kept from the original page so the league name +
          explainer stay reachable. Sits above the three-zone grid. */}
      <div className="inline-block text-center justify-center">
        <h1 className={title()}>
          Run{" "}
          <span className={title({ color: "green" })}>
            {`${league.name}'s`}
          </span>{" "}
          draft.
        </h1>
        <h2 className={subtitle()}>
          For each round, select the players chosen by you and your opponents.
          When {`it's`} your turn to pick, a Monte Carlo simulation will help
          you make the best choice.
        </h2>
      </div>

      {draft.league.draft_order.length > 0 ? (
        <div
          className="grid gap-3 w-full"
          style={{
            gridTemplateColumns:
              "minmax(0, 236px) minmax(0, 1fr) minmax(0, 244px)",
          }}
        >
          {/* LEFT RAIL — Best Available */}
          <BestAvailable
            leagueId={leagueId}
            draftId={draft.id}
            searchFilter={searchFilter}
            setSearchFilter={setSearchFilter}
            draftPaused={draftPaused}
            onDraft={handleDraftPlayer}
            onRefresh={() => {
              refetchAllPlayers();
              refetchFilteredPlayers();
            }}
          />

          {/* CENTER — board header + DraftBoard (unchanged) + MC/scarcity
              panels folded beneath it. */}
          <div className="flex flex-col gap-3 min-w-0">
            <div
              className="border"
              style={{
                background: "var(--surface)",
                borderColor: "var(--border)",
                borderRadius: "var(--radius)",
                overflow: "hidden",
              }}
            >
              <div
                className="flex items-center gap-2 px-3 py-2"
                style={{
                  background: "var(--surface-2)",
                  borderBottom: "1px solid var(--border)",
                }}
              >
                <span className="font-head text-sm font-bold uppercase tracking-[0.05em]">
                  Draft Board
                </span>
                <span
                  className="text-xs"
                  style={{ color: "var(--text-mute)" }}
                >
                  Round {round} · Pick {pickNo}
                </span>
                <span
                  className="ml-auto text-xs"
                  style={{ color: "var(--green)" }}
                >
                  Snake · {n}-team
                </span>
              </div>
              <div className="p-3">
                <DraftBoard league={league}>
                  {/* LIST view fallback — the original six-position
                      player columns, kept reachable via the DraftBoard
                      toggle. Sources the unfiltered player list so the
                      rail's tag filter never hides players from the
                      board's own list view. */}
                  <div className="text-center grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2 w-full">
                    {positions.map((position) => {
                      const colorMap: Record<
                        string,
                        | "primary"
                        | "success"
                        | "warning"
                        | "danger"
                        | "secondary"
                        | "default"
                      > = {
                        qb: "danger",
                        rb: "primary",
                        wr: "success",
                        te: "warning",
                        dst: "default",
                        k: "secondary",
                      };
                      return (
                        <div
                          key={position}
                          className="col-span-1 flex flex-col items-center gap-4"
                        >
                          <h3 className="text-lg font-bold mt-0 w-full">
                            {position.toLocaleUpperCase()}
                          </h3>
                          <ul className="flex flex-col gap-4 w-full">
                            {(allPlayers ?? league.players)[
                              position as keyof Players
                            ].map((player, i) => {
                              if (player.drafted === false) {
                                return (
                                  <li
                                    key={i}
                                    className={`flex flex-col gap-1 ${
                                      player.tag === "avoid" ? "opacity-50" : ""
                                    }`}
                                  >
                                    <button
                                      type="button"
                                      disabled={draftPaused}
                                      onClick={() =>
                                        handleDraftPlayer(player.name)
                                      }
                                      className="w-full h-fit flex flex-col gap-1 py-4 rounded-large text-white"
                                      style={{
                                        background:
                                          "linear-gradient(135deg, var(--surface-3), var(--surface-2))",
                                        border: "1px solid var(--border-2)",
                                        cursor: draftPaused
                                          ? "not-allowed"
                                          : "pointer",
                                        opacity: draftPaused ? 0.6 : 1,
                                      }}
                                      title={`${player.nfl_team} · ${player.position_tier.toLocaleUpperCase()}`}
                                    >
                                      <span className="font-bold">
                                        {player.name}
                                      </span>
                                      <span className="text-xs">
                                        {player.nfl_team} |{" "}
                                        {player.position_tier.toLocaleUpperCase()}
                                      </span>
                                      {(player.adp != null ||
                                        player.consensus_rank != null ||
                                        player.tier != null) && (
                                        <span className="text-xs opacity-80">
                                          {[
                                            player.adp != null
                                              ? `ADP ${Math.round(player.adp)}`
                                              : null,
                                            player.consensus_rank != null
                                              ? `ECR ${Math.round(player.consensus_rank)}`
                                              : null,
                                            player.tier != null
                                              ? `Tier ${player.tier}`
                                              : null,
                                          ]
                                            .filter(Boolean)
                                            .join(" | ")}
                                        </span>
                                      )}
                                    </button>
                                  </li>
                                );
                              }
                              return null;
                            })}
                          </ul>
                        </div>
                      );
                    })}
                  </div>
                </DraftBoard>
              </div>
            </div>

            <MonteCarloPanel
              isSimulatorTurn={isSimulatorTurn}
              simulationError={simulationError}
              onRetry={() => setSimulationError(false)}
              monteCarloResults={monteCarloResults}
              bestPick={bestPick}
            />

            <ScarcityPanel
              draftId={draft.id}
              draftComplete={draftComplete}
              playerTagByName={playerTagByName}
            />
          </div>

          {/* RIGHT RAIL — On The Clock / Suggested / My Roster stacked. */}
          <div className="flex flex-col gap-3">
            <OnTheClock league={league} draftComplete={draftComplete} />
            <Suggested
              bestPick={bestPick}
              suggested={suggestedPick}
              bestPosition={bestPosition}
              scarcity={scarcityContext}
              canDraft={isSimulatorTurn && !draftPaused && !!bestPick}
              onDraft={handleDraftPlayer}
            />
            <MyRoster league={league} />
          </div>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-4 py-8 text-center">
          <DraftBoard league={league} />
        </div>
      )}
    </section>
  );
}
