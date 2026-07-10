"use client";

import { createContext, useEffect, useMemo, useState } from "react";
import { button as buttonStyles } from "@nextui-org/theme";
import { Button } from "@nextui-org/button";
import { Input } from "@nextui-org/input";
import { Spinner } from "@nextui-org/spinner";
import { useTheme } from "next-themes";
import {
  FiAlertTriangle,
  FiClock,
  FiHelpCircle,
  FiMoon,
  FiSlash,
  FiStar,
  FiX,
  FiXCircle,
  FiZap,
} from "react-icons/fi";

import {
  useGetDraftQuery,
  useGetDraftResultsQuery,
  useDraftPlayerMutation,
  useRunMonteCarloMutation,
} from "@/api/services/draft";
import {
  useGetPlayersQuery,
  useTagPlayerMutation,
  useUntagPlayerMutation,
} from "@/api/services/league";
import { useLazyGetScarcityQuery } from "@/api/services/scarcity";
import { title, subtitle } from "@/components/primitives";
import {
  Draft,
  HomerCheck,
  League,
  MonteCarloResults,
  Player,
  PlayerTag,
  Players,
  PositionScarcity,
  ScarcityCall,
} from "@/types";

const positions = ["qb", "rb", "wr", "te", "dst", "k"];

// One consistent icon+color per tag, reused wherever a tagged player's
// name appears: player rows, tag controls, scarcity at-risk lists, and
// the Monte Carlo suggestion panel
const tagMeta: Record<
  PlayerTag,
  { Icon: typeof FiMoon; className: string; label: string }
> = {
  sleeper: { Icon: FiMoon, className: "text-purple-500", label: "Sleeper" },
  my_guy: { Icon: FiStar, className: "text-yellow-500", label: "My Guy" },
  avoid: { Icon: FiSlash, className: "text-danger", label: "Avoid" },
};

// Small icon marker for a tagged player; renders nothing if untagged
function TagBadge({ tag }: { tag: PlayerTag | null | undefined }) {
  if (!tag) return null;
  const { Icon, className, label } = tagMeta[tag];

  return <Icon aria-label={label} className={className} title={label} />;
}

// Per-row tag/untag controls: clicking the active tag clears it,
// clicking another tag replaces it (one tag at a time)
function TagControls({
  leagueId,
  player,
}: {
  leagueId: string;
  player: Player;
}) {
  const [tagPlayer] = useTagPlayerMutation();
  const [untagPlayer] = useUntagPlayerMutation();

  const handleClick = (tag: PlayerTag) => {
    if (player.tag === tag) {
      untagPlayer({ id: leagueId, name: player.name });
    } else {
      tagPlayer({ id: leagueId, name: player.name, tag });
    }
  };

  return (
    <div className="flex items-center justify-center gap-2 text-sm">
      {(Object.keys(tagMeta) as PlayerTag[]).map((tag) => {
        const { Icon, className, label } = tagMeta[tag];
        const active = player.tag === tag;

        return (
          <button
            key={tag}
            aria-label={`Tag ${player.name} as ${label}`}
            className={active ? className : "text-default-400"}
            title={label}
            type="button"
            onClick={() => handleClick(tag)}
          >
            <Icon />
          </button>
        );
      })}
      {player.tag && (
        <button
          aria-label={`Clear ${player.name}'s tag`}
          className="text-default-400"
          title="Clear tag"
          type="button"
          onClick={() => untagPlayer({ id: leagueId, name: player.name })}
        >
          <FiX />
        </button>
      )}
    </div>
  );
}

type Position = (typeof positions)[number];
type PositionColorMap = {
  [key in Position]:
    "primary" | "success" | "warning" | "danger" | "secondary" | "default";
};

const positionColors: PositionColorMap = {
  qb: "danger",
  rb: "primary",
  wr: "success",
  te: "warning",
  dst: "default",
  k: "secondary",
};

// Styling per scarcity call: reach/last_chance urgent, wait calm,
// toss_up neutral, exhausted/no_tiers muted
const scarcityCallStyles: Record<
  ScarcityCall,
  {
    label: string;
    border: string;
    badge: string;
    Icon: typeof FiZap;
  }
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

// One position's scarcity nudge: the reach-vs-wait badge, tier depletion
// numbers, and an expandable list of at-risk players with survival odds
function ScarcityPositionCard({
  scarcity,
  playerTagByName,
}: {
  scarcity: PositionScarcity;
  playerTagByName: Record<string, PlayerTag | null | undefined>;
}) {
  const [expanded, setExpanded] = useState(false);
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

// A6: neutral value comparison for a homer-team (Seahawks) suggested
// pick vs. the top alternatives at that position. A subtle badge that
// expands into one table; deliberately no red/green weighting on the
// gaps and no recommendation copy beyond the backend's factual `note`.
function HomerCheckPanel({ check }: { check: HomerCheck }) {
  const [expanded, setExpanded] = useState(false);
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

type DraftIdContextType = {
  draft: Draft;
  theme: string | undefined;
  monteCarloResults: MonteCarloResults;
  bestPick: string;
  searchFilter: string;
  setSearchFilter: (value: string) => void;
};

const DraftIdContext = createContext<DraftIdContextType>({
  draft: { league: emptyLeague, id: "", created: "" },
  theme: undefined,
  monteCarloResults: emptyMonteCarloResults,
  bestPick: "",
  searchFilter: "",
  setSearchFilter: () => {},
});

export default function DraftIdPage({ params }: { params: { id: string } }) {
  const { theme } = useTheme();
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
  const [tagFilter, setTagFilter] = useState<PlayerTag | undefined>(undefined);
  const [simulationError, setSimulationError] = useState(false);

  const leagueId = draft.league.id;

  // Unfiltered draftable players, used to look up any player's tag
  // (e.g. for markers in the scarcity at-risk lists) regardless of
  // which tag filter chip is currently selected
  const { data: allPlayers, refetch: refetchAllPlayers } = useGetPlayersQuery(
    { id: leagueId },
    { skip: !leagueId },
  );

  // The player list actually rendered in the table below, filtered
  // server-side via the ?tag= query param when a chip is selected
  const {
    data: filteredPlayers,
    refetch: refetchFilteredPlayers,
  } = useGetPlayersQuery({ id: leagueId, tag: tagFilter }, { skip: !leagueId });

  const playerTagByName = useMemo(() => {
    const map: Record<string, PlayerTag | null | undefined> = {};

    positions.forEach((position) => {
      (allPlayers?.[position as keyof Players] ?? []).forEach((player) => {
        map[player.name] = player.tag;
      });
    });

    return map;
  }, [allPlayers]);
  const [
    fetchScarcity,
    {
      data: scarcityReport,
      isFetching: scarcityFetching,
      isError: scarcityError,
    },
  ] = useLazyGetScarcityQuery();

  // The scarcity endpoint 400s once the draft is over
  const draftComplete =
    draft.id !== "" && draft.league.draft_order.length === 0;

  // Draft a player with a POST request to '/draft/:id/pick'
  const handleDraftPlayer = async (name: string) => {
    await draftPlayer({ id: draft.id, name });
    setSearchFilter("");
    refetchAllPlayers();
    refetchFilteredPlayers();
  };

  // When the team drafting is the simulator, set the Monte Carlo results
  useEffect(() => {
    if (
      draft.league.draft_order.length > 0 &&
      draft.league.teams[draft.league.draft_order[0]].simulator
    ) {
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
  }, [draft.league, monteCarloResults, simulationError]);

  // Return all data in the DraftIdContext.Provider
  return (
    <section className="flex flex-col items-center justify-center gap-8">
      <DraftIdContext.Provider
        value={{
          draft,
          theme,
          monteCarloResults,
          bestPick,
          searchFilter,
          setSearchFilter,
        }}
      >
        <div className="inline-block text-center justify-center">
          <h1 className={title()}>
            Run{" "}
            <span className={title({ color: "green" })}>
              {`${draft.league.name}'s`}
            </span>{" "}
            draft.
          </h1>
          <h2 className={subtitle()}>
            For each round, select the players chosen by you and your opponents.
            When {`it's`} your turn to pick, a Monte Carlo simulation will help
            you make the best choice.
          </h2>
        </div>

        {/* Drafting team and Monte Carlo results */}
        {draft.league.draft_order.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 w-full">
            <div className="flex flex-col justify-center gap-2 border-medium rounded-large p-3 border-default">
              <h3 className="w-full text-xl">{`On the Clock - Pick ${draft.league.current_draft_turn + 1}`}</h3>
              <p className="font-bold w-full">
                {draft.league.teams[draft.league.draft_order[0]].name}
              </p>
              <p className="italic text-sm text-default-500">
                Owner: {draft.league.teams[draft.league.draft_order[0]].owner}
              </p>
            </div>
            <div className="flex flex-col justify-center gap-2 border-medium rounded-large p-3 border-default">
              <h3 className="w-full text-xl">Monte Carlo Results</h3>
              {draft.league.teams[draft.league.draft_order[0]].simulator &&
              simulationError ? (
                <div className="flex items-center justify-between w-full">
                  <p className="font-bold text-danger">
                    Simulation failed. Please try again.
                  </p>
                  <Button
                    color="danger"
                    size="sm"
                    variant="flat"
                    onClick={() => setSimulationError(false)}
                  >
                    Retry
                  </Button>
                </div>
              ) : draft.league.teams[draft.league.draft_order[0]].simulator &&
                monteCarloResults.iterations === 0 ? (
                <p className="font-bold w-full">
                  <span className="flex items-center">
                    <Spinner size="sm" />
                    <span className="ml-2">Simulating...</span>
                  </span>
                </p>
              ) : monteCarloResults.iterations > 0 ? (
                <div className="flex justify-between">
                  <p>Best Pick: {bestPick}</p>
                  <p>{`${monteCarloResults.iterations} Iterations Performed`}</p>
                </div>
              ) : (
                <p className="font-bold w-full">Not Simulating...</p>
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
              {/* A4: the tag-aware candidate the engine would take at each position */}
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
          </div>
        ) : null}

        {/* Scarcity nudges: reach-vs-wait calls per position from the tier-depletion engine */}
        {draft.league.draft_order.length > 0 && (
          <div className="flex flex-col gap-2 border-medium rounded-large p-3 border-default w-full">
            <div className="flex items-center justify-between gap-2 w-full flex-wrap">
              <h3 className="text-xl">Scarcity Check</h3>
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
                  onClick={() => fetchScarcity({ id: draft.id, seconds: 10 })}
                >
                  {scarcityReport ? "Refresh" : "Check Scarcity"}
                </Button>
              </div>
            </div>
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
          </div>
        )}

        {/* Input for filtering the players for search */}
        <div className="flex space-between gap-8 w-full">
          <Input
            fullWidth
            isClearable
            placeholder="Filter"
            size="lg"
            value={searchFilter}
            variant="bordered"
            onChange={(e) => setSearchFilter(e.target.value)}
            onClear={() => setSearchFilter("")}
          />
        </div>

        {/* Tag filter chips: All / Sleepers / My Guys / Avoids, backed
            by the ?tag= query param on GET /league/:id/player */}
        <div className="flex items-center gap-2 w-full flex-wrap">
          {(
            [
              { label: "All", tag: undefined },
              { label: "Sleepers", tag: "sleeper" },
              { label: "My Guys", tag: "my_guy" },
              { label: "Avoids", tag: "avoid" },
            ] as { label: string; tag: PlayerTag | undefined }[]
          ).map(({ label, tag }) => (
            <button
              key={label}
              className={`px-3 py-1 rounded-full text-sm border transition-colors ${
                tagFilter === tag
                  ? "bg-primary text-white border-primary"
                  : "border-default-300 text-default-600"
              }`}
              type="button"
              onClick={() => setTagFilter(tag)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Use a flex box to display columns of the six positions */}
        <div className="text-center grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2 w-full">
          {positions.map((position) => (
            <div
              key={position}
              className="col-span-1 flex flex-col items-center gap-4"
            >
              <h3 className="text-lg font-bold mt-0 w-full">
                {position.toLocaleUpperCase()}
              </h3>
              <ul className="flex flex-col gap-4 w-full">
                {(filteredPlayers ?? draft.league.players)[
                  position as keyof Players
                ].map((player, i) => {
                  if (
                    searchFilter.length > 0 &&
                    !player.name
                      .toLowerCase()
                      .includes(searchFilter.toLowerCase())
                  ) {
                    return null;
                  }
                  if (player.drafted === false) {
                    return (
                      <li
                        key={i}
                        className={`flex flex-col gap-1 ${
                          player.tag === "avoid" ? "opacity-50" : ""
                        }`}
                      >
                        <Button
                          className={
                            buttonStyles({
                              size: "lg",
                              // fullWidth: true,
                              variant: "solid",
                              color:
                                positionColors[
                                  position as keyof PositionColorMap
                                ],
                            }) +
                            ` h-fit w-full flex flex-col gap-1 py-4 ${
                              theme === "dark" ? " text-white " : " text-black "
                            } `
                          }
                          disabled={
                            draft.league.draft_order.length > 0 &&
                            draft.league.teams[draft.league.draft_order[0]]
                              .simulator &&
                            monteCarloResults.iterations === 0 &&
                            !simulationError
                          }
                          onClick={() => handleDraftPlayer(player.name)}
                        >
                          <p className="font-bold flex items-center justify-center gap-1">
                            <TagBadge tag={player.tag} />
                            <span
                              className={
                                player.tag === "avoid" ? "line-through" : ""
                              }
                            >
                              {player.name}
                            </span>
                          </p>
                          <p>
                            {player.nfl_team} |{" "}
                            {player.position_tier.toLocaleUpperCase()}
                          </p>
                          {(player.adp != null ||
                            player.consensus_rank != null ||
                            player.tier != null) && (
                            <p className="text-xs opacity-80">
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
                            </p>
                          )}
                        </Button>
                        <TagControls leagueId={leagueId} player={player} />
                      </li>
                    );
                  }
                })}
              </ul>
            </div>
          ))}
        </div>
      </DraftIdContext.Provider>
    </section>
  );
}
