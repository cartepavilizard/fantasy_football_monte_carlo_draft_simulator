"use client";

import { useEffect, useState } from "react";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";
import {
  FiAlertTriangle,
  FiCheckCircle,
  FiClock,
  FiRefreshCw,
} from "react-icons/fi";

import {
  useGetFreeAgentsQuery,
  useGetLocksQuery,
  useGetMatchupsQuery,
  useGetOverviewQuery,
  useGetRosterQuery,
  useGetTransactionsQuery,
  useSyncLeagueMutation,
} from "@/api/services/inseason";
import { title, subtitle } from "@/components/primitives";
import { InSeasonOverviewEntry } from "@/types";

const cardClass =
  "flex flex-col gap-2 w-full border-medium rounded-large p-4 border-default";
const selectClass =
  "bg-transparent border-medium border-default rounded-medium px-3 py-2 text-sm";

function age(seconds: number | null): string {
  if (seconds === null) return "never";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;

  return `${Math.round(seconds / 86400)}d ago`;
}

// Rendered inside every view that reads an /inseason/* envelope. Cached
// data is always shown (B4's hard constraint) — this is what keeps
// stale or auth-expired cache from silently looking fresh.
function StalenessBanner({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) {
    return (
      <p className="flex items-center gap-1 text-xs text-default-500">
        <FiCheckCircle className="text-success" />
        No staleness warnings
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1 w-full rounded-medium border border-warning bg-warning-50 dark:bg-warning-950/20 p-2">
      {warnings.map((warning, i) => (
        <p
          key={i}
          className="flex items-start gap-2 text-sm text-warning-700 dark:text-warning-400"
        >
          <FiAlertTriangle className="mt-0.5 shrink-0" />
          <span>{warning}</span>
        </p>
      ))}
    </div>
  );
}

export default function InSeasonPage() {
  const { data: overview, isLoading: overviewLoading } = useGetOverviewQuery();

  const [leagueId, setLeagueId] = useState<number | null>(null);
  const [teamId, setTeamId] = useState<number | null>(null);
  const [syncLeague, { isLoading: isSyncing }] = useSyncLeagueMutation();
  const [syncMessage, setSyncMessage] = useState<string>("");

  const leagues = overview?.leagues ?? [];

  // Default to the first synced league/team once the overview loads
  useEffect(() => {
    if (leagueId === null && leagues.length > 0) {
      setLeagueId(leagues[0].league.espn_league_id);
    }
  }, [leagues, leagueId]);

  const selectedEntry: InSeasonOverviewEntry | undefined = leagues.find(
    (entry) => entry.league.espn_league_id === leagueId,
  );

  useEffect(() => {
    if (selectedEntry && selectedEntry.league.teams.length > 0) {
      const stillValid = selectedEntry.league.teams.some(
        (team) => team.espn_team_id === teamId,
      );

      if (!stillValid) {
        setTeamId(selectedEntry.league.teams[0].espn_team_id);
      }
    }
  }, [selectedEntry, teamId]);

  const teamName = (id: number | null) =>
    selectedEntry?.league.teams.find((team) => team.espn_team_id === id)
      ?.name ?? `Team ${id}`;

  const rosterQuery = useGetRosterQuery(
    { leagueId: leagueId ?? 0, teamId: teamId ?? 0 },
    { skip: leagueId === null || teamId === null },
  );
  const matchupsQuery = useGetMatchupsQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  const transactionsQuery = useGetTransactionsQuery(
    { leagueId: leagueId ?? 0, limit: 15 },
    { skip: leagueId === null },
  );
  const freeAgentsQuery = useGetFreeAgentsQuery(
    { leagueId: leagueId ?? 0, limit: 15 },
    { skip: leagueId === null },
  );
  const locksQuery = useGetLocksQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );

  const handleSync = async () => {
    setSyncMessage("");
    try {
      const summary = await syncLeague({ leagueId: leagueId ?? undefined }).unwrap();
      const failures = Object.entries(summary.leagues).flatMap(
        ([id, leagueSummary]) =>
          Object.entries(leagueSummary.sections)
            .filter(([, section]) => !section.success)
            .map(([section]) => `league ${id} ${section}`),
      );

      setSyncMessage(
        failures.length === 0
          ? "Sync complete — all sections refreshed."
          : `Sync completed with failures: ${failures.join(", ")}.`,
      );
    } catch {
      setSyncMessage("Sync failed. Is the backend reachable?");
    }
  };

  return (
    <section className="flex flex-col items-center justify-center gap-8">
      <div className="max-w-lg text-center">
        <h1 className={title()}>In-season.</h1>
        <h2 className={subtitle()}>
          Every view below is served from cache — switching leagues or teams
          never talks to ESPN. Use &quot;Sync now&quot; to pull fresh data.
        </h2>
      </div>

      {/* League + team-perspective switcher — cached-only, never syncs */}
      <div className={cardClass}>
        <h3 className="text-xl">League &amp; perspective</h3>
        {overviewLoading ? (
          <Spinner />
        ) : leagues.length === 0 ? (
          <p className="text-sm text-default-500">
            No leagues synced yet. Use &quot;Sync now&quot; below to pull your
            configured ESPN leagues.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-4">
            <label className="flex flex-col gap-1 text-sm">
              League
              <select
                className={selectClass}
                value={leagueId ?? ""}
                onChange={(e) => {
                  const nextLeagueId = Number(e.target.value);
                  const nextEntry = leagues.find(
                    (entry) => entry.league.espn_league_id === nextLeagueId,
                  );

                  setLeagueId(nextLeagueId);
                  setTeamId(nextEntry?.league.teams[0]?.espn_team_id ?? null);
                }}
              >
                {leagues.map((entry) => (
                  <option
                    key={entry.league.espn_league_id}
                    value={entry.league.espn_league_id}
                  >
                    {entry.league.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              Team perspective
              <select
                className={selectClass}
                value={teamId ?? ""}
                onChange={(e) => setTeamId(Number(e.target.value))}
              >
                {selectedEntry?.league.teams.map((team) => (
                  <option key={team.espn_team_id} value={team.espn_team_id}>
                    {team.name} ({team.wins}-{team.losses}
                    {team.ties ? `-${team.ties}` : ""})
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        {selectedEntry && (
          <StalenessBanner warnings={selectedEntry.warnings} />
        )}
      </div>

      {/* Sync now — visually separated: the only control on this page
          that touches ESPN */}
      <div className={`${cardClass} border-warning`}>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h3 className="text-xl">ESPN sync</h3>
            <p className="text-sm text-default-500">
              {leagueId
                ? "Pulls fresh data for the selected league only."
                : "No league selected — pulls every configured league."}
            </p>
          </div>
          <Button
            color="warning"
            disabled={isSyncing}
            startContent={!isSyncing && <FiRefreshCw />}
            onClick={handleSync}
          >
            {isSyncing ? (
              <span className="flex items-center gap-2">
                <Spinner color="white" size="sm" />
                Syncing…
              </span>
            ) : (
              "Sync now"
            )}
          </Button>
        </div>
        {syncMessage && <p className="text-sm">{syncMessage}</p>}
      </div>

      {leagueId !== null && teamId !== null && (
        <>
          {/* Roster */}
          <div className={cardClass}>
            <h3 className="text-xl">{teamName(teamId)}&apos;s roster</h3>
            {rosterQuery.isLoading || !rosterQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={rosterQuery.data.warnings} />
                {rosterQuery.data.data ? (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-default-500">
                        <th className="pb-1">Slot</th>
                        <th className="pb-1">Player</th>
                        <th className="pb-1">Pos</th>
                        <th className="pb-1">Team</th>
                        <th className="pb-1">Status</th>
                        <th className="pb-1 text-right">Proj</th>
                        <th className="pb-1 text-right">Actual</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rosterQuery.data.data.entries.map((entry) => (
                        <tr
                          key={entry.player_id}
                          className="border-t border-default-100"
                        >
                          <td className="py-1">{entry.lineup_slot}</td>
                          <td className="py-1">{entry.player_name}</td>
                          <td className="py-1">{entry.position ?? "—"}</td>
                          <td className="py-1">{entry.nfl_team ?? "—"}</td>
                          <td className="py-1">
                            {entry.injury_status ?? "—"}
                          </td>
                          <td className="py-1 text-right">
                            {entry.projected_points?.toFixed(1) ?? "—"}
                          </td>
                          <td className="py-1 text-right">
                            {entry.actual_points?.toFixed(1) ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="text-sm text-default-500">
                    No cached roster for this week yet.
                  </p>
                )}
              </>
            )}
          </div>

          {/* Matchups */}
          <div className={cardClass}>
            <h3 className="text-xl">
              Matchups
              {matchupsQuery.data ? ` — week ${matchupsQuery.data.data.week}` : ""}
            </h3>
            {matchupsQuery.isLoading || !matchupsQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={matchupsQuery.data.warnings} />
                {matchupsQuery.data.data.matchups.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No cached matchups for this week.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {matchupsQuery.data.data.matchups.map((matchup, i) => {
                      const involvesSelected =
                        matchup.home_team_id === teamId ||
                        matchup.away_team_id === teamId;

                      return (
                        <li
                          key={i}
                          className={`flex items-center justify-between text-sm border-b border-default-100 pb-2 ${
                            involvesSelected ? "font-bold" : ""
                          }`}
                        >
                          <span>
                            {teamName(matchup.away_team_id)}{" "}
                            {matchup.away_points.toFixed(1)}
                          </span>
                          <span className="text-default-400">@</span>
                          <span>
                            {teamName(matchup.home_team_id)}{" "}
                            {matchup.home_points.toFixed(1)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
          </div>

          {/* Transactions */}
          <div className={cardClass}>
            <h3 className="text-xl">Recent transactions</h3>
            {transactionsQuery.isLoading || !transactionsQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={transactionsQuery.data.warnings} />
                {transactionsQuery.data.data.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No cached transactions yet.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {transactionsQuery.data.data.map((transaction) => (
                      <li
                        key={transaction.espn_transaction_id}
                        className="text-sm border-b border-default-100 pb-2"
                      >
                        <span className="font-bold">{transaction.type}</span>
                        {" · "}
                        {transaction.items
                          .map(
                            (item) =>
                              `${item.item_type} ${item.player_name ?? item.player_id}`,
                          )
                          .join(", ")}
                        {transaction.processed_at && (
                          <span className="text-default-400">
                            {" — "}
                            {new Date(
                              transaction.processed_at,
                            ).toLocaleString()}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>

          {/* Free agents */}
          <div className={cardClass}>
            <h3 className="text-xl">Top free agents</h3>
            {freeAgentsQuery.isLoading || !freeAgentsQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={freeAgentsQuery.data.warnings} />
                {freeAgentsQuery.data.data.free_agents.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No cached free-agent pool yet.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1">
                    {freeAgentsQuery.data.data.free_agents.map((agent) => (
                      <li
                        key={agent.player_id}
                        className="flex items-center justify-between text-sm border-b border-default-100 pb-1"
                      >
                        <span>
                          {agent.player_name}{" "}
                          <span className="text-default-400">
                            {agent.position ?? "—"} · {agent.nfl_team ?? "—"}
                          </span>
                        </span>
                        <span>
                          {agent.projected_points?.toFixed(1) ?? "—"} proj
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>

          {/* Lineup locks */}
          <div className={cardClass}>
            <h3 className="text-xl">
              <FiClock className="inline mb-1 mr-1" />
              Lineup locks
              {locksQuery.data ? ` — week ${locksQuery.data.data.week}` : ""}
            </h3>
            {locksQuery.isLoading || !locksQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={locksQuery.data.warnings} />
                {locksQuery.data.data.locks ? (
                  <p className="text-sm">
                    First lock ({locksQuery.data.data.locks.first_game}):{" "}
                    <span className="font-bold">
                      {new Date(
                        locksQuery.data.data.locks.first_lock,
                      ).toLocaleString()}
                    </span>
                    {" · "}
                    Final lock:{" "}
                    <span className="font-bold">
                      {new Date(
                        locksQuery.data.data.locks.final_lock,
                      ).toLocaleString()}
                    </span>
                  </p>
                ) : (
                  <p className="text-sm text-default-500">
                    No cached schedule for this week yet.
                  </p>
                )}
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}
