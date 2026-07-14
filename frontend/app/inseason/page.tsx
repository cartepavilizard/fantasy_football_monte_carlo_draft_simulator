"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";
import {
  FiAlertTriangle,
  FiCheckCircle,
  FiClock,
  FiRefreshCw,
  FiShield,
  FiTrash2,
  FiTrendingDown,
  FiTrendingUp,
} from "react-icons/fi";

import {
  useDeleteHandcuffMutation,
  useGetFreeAgentsQuery,
  useGetHandcuffsQuery,
  useGetLeagueHandcuffsQuery,
  useGetLineupQuery,
  useGetLocksQuery,
  useGetMatchupsQuery,
  useGetOverviewQuery,
  useGetPlayoffSosQuery,
  useGetRosterQuery,
  useGetStreamingQuery,
  useGetTradeWillingnessQuery,
  useGetTransactionsQuery,
  useGetUsageShiftsQuery,
  useSeedHandcuffsMutation,
  useSetHandcuffMutation,
  useSyncLeagueMutation,
} from "@/api/services/inseason";
import { title, subtitle } from "@/components/primitives";
import { VarianceFlag } from "@/components/variance-flag";
import {
  HandcuffFlag,
  HomerCheck,
  InSeasonOverviewEntry,
  MatchupEntry,
  PlayoffSosEntry,
  TradeWillingnessLabel,
  TradeWillingnessOwner,
  UsageShift,
} from "@/types";

const PLAYOFF_SOS_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"];

// C6: a starter "locks early" when their kickoff is at least this many
// hours before the week's final lock. Mirrors EARLY_LOCK_LEAD_HOURS in
// backend/models/config.py — not exposed over the API, so kept in sync
// here by hand.
const EARLY_LOCK_LEAD_HOURS = 36;

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

// C9: a homer-team (Seahawks) row's neutral comparison note (streaming
// picks and, now, handcuffs). No recommendation, by design — just the
// facts, expandable on demand.
function HomerCheckNote({ check }: { check: HomerCheck | null }) {
  const [expanded, setExpanded] = useState(false);

  if (!check) return null;

  return (
    <div className="mt-1">
      <button
        className="text-xs font-bold px-1.5 py-0.5 rounded-full bg-[#69BE28]/15 text-[#69BE28] border border-[#69BE28]/40 w-fit"
        type="button"
        onClick={() => setExpanded(!expanded)}
      >
        Homer check
      </button>
      {expanded && (
        <div className="mt-1 text-xs text-default-500">
          <p>{check.note}</p>
          <ul className="mt-1 flex flex-col gap-0.5">
            {check.alternatives.map((alt) => (
              <li key={alt.name}>
                {alt.name} ({alt.nfl_team}) — {alt.projected_points.toFixed(1)} proj
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// C7: a rostered starter's flagged handcuff, as a chip on the roster
// row. priority "high" (starter questionable/doubtful/out) is styled
// distinctly — that's the insurance that's about to matter, not a
// healthy starter's spare parts. C9's homer check, when present,
// expands the same way the streaming table's does.
function HandcuffChip({ flag }: { flag: HandcuffFlag }) {
  const [expanded, setExpanded] = useState(false);
  const urgent = flag.priority === "high";
  const owned =
    flag.handcuff_percent_owned != null
      ? `${flag.handcuff_percent_owned.toFixed(0)}% owned`
      : "available";

  return (
    <div className="mt-1">
      <button
        className={`inline-flex items-center gap-1 text-xs font-bold px-1.5 py-0.5 rounded-full border w-fit ${
          urgent
            ? "bg-danger-100 text-danger-700 border-danger-300 dark:bg-danger-950/40 dark:text-danger-400"
            : "bg-default-100 text-default-600 border-default-300 dark:bg-default-800/40"
        }`}
        title={`${flag.handcuff_name} — direct backup, ${owned}`}
        type="button"
        onClick={() => setExpanded(!expanded)}
      >
        <FiShield />
        Handcuff: {flag.handcuff_name}
      </button>
      {expanded && (
        <div className="mt-1 text-xs text-default-500">
          <p>
            {flag.starter_name}
            {flag.starter_injury_status
              ? ` is ${flag.starter_injury_status}`
              : " is healthy"}
            . {flag.handcuff_name} inherits the workload and is sitting on
            waivers ({owned}).
          </p>
          <HomerCheckNote check={flag.homer_check} />
        </div>
      )}
    </div>
  );
}

// C4/C8: one usage-shift row, framed in volume and opportunity —
// current vs. trailing-baseline share, never fantasy points.
function UsageShiftRow({ shift }: { shift: UsageShift }) {
  const rising = shift.direction === "rising";
  const who = [shift.nfl_team, shift.position].filter(Boolean).join(" ");

  return (
    <tr className="border-t border-default-100 align-top">
      <td className="py-1">
        {shift.player_name}
        {who && <span className="text-default-400"> ({who})</span>}
        <div>
          <VarianceFlag variance={shift.variance} />
        </div>
      </td>
      <td className="py-1">{shift.metric_phrase}</td>
      <td className="py-1 text-right">{(shift.current * 100).toFixed(0)}%</td>
      <td className="py-1 text-right text-default-400">
        {(shift.baseline * 100).toFixed(0)}% avg
        <span className="text-default-300">
          {" "}
          (prior {shift.baseline_weeks}w)
        </span>
      </td>
      <td
        className={`py-1 text-right font-bold ${
          rising ? "text-success" : "text-danger"
        }`}
      >
        <span className="inline-flex items-center gap-1">
          {rising ? <FiTrendingUp /> : <FiTrendingDown />}
          {(Math.abs(shift.delta) * 100).toFixed(0)} share pts
        </span>
      </td>
    </tr>
  );
}

// C5: one NFL team's row in the playoff (weeks 14-16) SOS table. score
// is a SUM of C2's multipliers, not an average — a bye contributes
// nothing, so a low score next to a bye badge reads as "fewer games",
// not "soft schedule".
function PlayoffSosRow({
  team,
  entry,
}: {
  team: string;
  entry: PlayoffSosEntry;
}) {
  return (
    <tr className="border-t border-default-100">
      <td className="py-1">{entry.rank}</td>
      <td className="py-1 font-bold">{team}</td>
      <td className={`py-1 text-right ${confidenceClass(entry.confidence)}`}>
        {entry.score.toFixed(2)}
      </td>
      <td className="py-1 text-right text-default-400">
        {entry.games_scheduled}/{entry.games_scheduled + entry.bye_weeks.length}
        {entry.bye_weeks.length > 0 && (
          <span> (bye wk {entry.bye_weeks.join(", ")})</span>
        )}
      </td>
    </tr>
  );
}

function confidenceClass(confidence: string): string {
  if (confidence === "high") return "text-success";
  if (confidence === "medium") return "text-warning";

  return "text-default-400";
}

// E3: the willingness label's badge styling — "unknown" is deliberately
// neutral (not a lesser "reluctant"), since it means the season hasn't
// asked the question yet, not that the owner said no.
function willingnessBadgeClass(label: TradeWillingnessLabel): string {
  switch (label) {
    case "active":
      return "bg-success-100 text-success-700 border-success-300 dark:bg-success-950/40 dark:text-success-400";
    case "open":
      return "bg-primary-100 text-primary-700 border-primary-300 dark:bg-primary-950/40 dark:text-primary-400";
    case "reluctant":
      return "bg-danger-100 text-danger-700 border-danger-300 dark:bg-danger-950/40 dark:text-danger-400";
    default:
      return "bg-default-100 text-default-600 border-default-300 dark:bg-default-800/40";
  }
}

// E3: one owner's row in the trade-willingness table. n-counts stay
// visible next to every rate (profiling.py's ground rule) so a thin
// sample never reads as a confident verdict.
function TradeWillingnessRow({ owner }: { owner: TradeWillingnessOwner }) {
  const tw = owner.trade_willingness;

  return (
    <tr className="border-t border-default-100 align-top">
      <td className="py-1">
        {owner.team_name}
        {owner.owner_name && (
          <span className="text-default-400"> ({owner.owner_name})</span>
        )}
      </td>
      <td className="py-1">
        <span
          className={`inline-flex text-xs font-bold px-1.5 py-0.5 rounded-full border ${willingnessBadgeClass(tw.willingness)}`}
        >
          {tw.willingness}
        </span>
      </td>
      <td className="py-1 text-right">
        {tw.trades_per_season.toFixed(1)}
        <span className="text-default-400"> (n={tw.n_trades})</span>
      </td>
      <td className="py-1 text-right">
        {tw.relative_trade_rate != null ? `${tw.relative_trade_rate.toFixed(2)}x` : "—"}
      </td>
      <td className="py-1 text-right">
        {tw.activity.moves_per_season.toFixed(1)}
        <span className="text-default-400"> (n={tw.activity.n_moves})</span>
      </td>
      <td className="py-1 text-right">
        {tw.partners.n_distinct > 0 ? (
          <>
            {tw.partners.n_distinct}
            {tw.partners.concentration != null && (
              <span className="text-default-400">
                {" "}
                ({(tw.partners.concentration * 100).toFixed(0)}% top)
              </span>
            )}
          </>
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}

function formatKickoff(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

// C6: a starter's kickoff, badged distinctly when it locks early (i.e.
// at or before earlyCutoff) — the players a lineup call gets locked out
// of adjusting first.
function KickoffBadge({
  kickoff,
  earlyCutoff,
}: {
  kickoff: string | null;
  earlyCutoff: Date | null;
}) {
  if (!kickoff) {
    return <span className="text-xs text-default-400">bye</span>;
  }

  const label = formatKickoff(kickoff);
  const locksEarly = earlyCutoff !== null && new Date(kickoff) <= earlyCutoff;

  if (!locksEarly) {
    return <span className="text-xs text-default-400">{label}</span>;
  }

  return (
    <span className="text-xs font-bold px-1.5 py-0.5 rounded-full bg-warning-100 text-warning-700 border border-warning-300 dark:bg-warning-950/40 dark:text-warning-400">
      Locks early · {label}
    </span>
  );
}

// C2: per-player matchup context (multiplier, defensive rank, and
// confidence). The confidence caveat stays visible — not tucked behind
// a hover — whenever confidence is low/none, since that's exactly when
// the multiplier is least trustworthy.
function MatchupChip({ matchup }: { matchup: MatchupEntry }) {
  const lowConfidence = matchup.confidence === "low" || matchup.confidence === "none";

  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <span className={`text-xs font-bold ${confidenceClass(matchup.confidence)}`}>
        {matchup.multiplier.toFixed(2)}x
        {matchup.rank ? ` (#${matchup.rank})` : ""}
      </span>
      {lowConfidence && (
        <span className="text-[10px] text-default-400">
          {matchup.confidence === "none"
            ? "no matchup data yet"
            : "low-confidence matchup"}
        </span>
      )}
    </span>
  );
}

// C1/C6: one advice card, quoting cost_points and note verbatim. Advice
// is surfaced, never applied — there is deliberately no action here.
function LockAdviceCard({
  slot,
  starterName,
  alternativeName,
  costPoints,
  note,
}: {
  slot: string;
  starterName: string;
  alternativeName: string;
  costPoints: number;
  note: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-medium border border-warning-300 bg-warning-50 dark:bg-warning-950/20 p-3">
      <p className="text-sm font-bold">
        {slot}: {starterName}{" "}
        <span className="font-normal text-default-500">vs.</span>{" "}
        {alternativeName}
        <span className="ml-2 text-xs font-normal text-warning-700 dark:text-warning-400">
          costs {costPoints.toFixed(1)} pts
        </span>
      </p>
      <p className="text-xs text-default-500">{note}</p>
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
  const lineupQuery = useGetLineupQuery(
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
  const streamingQuery = useGetStreamingQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  const tradeWillingnessQuery = useGetTradeWillingnessQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );

  // C7: flagged handcuffs for the selected league-week (chips on the
  // roster view), and the curated map itself (the panel's CRUD) —
  // unscoped by league, so it loads regardless of a selection.
  const handcuffFlagsQuery = useGetLeagueHandcuffsQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  const handcuffsQuery = useGetHandcuffsQuery();
  const handcuffFlagByStarter = useMemo(() => {
    const map = new Map<string, HandcuffFlag>();

    for (const flag of handcuffFlagsQuery.data?.data.handcuffs ?? []) {
      map.set(flag.starter_name, flag);
    }

    return map;
  }, [handcuffFlagsQuery.data]);

  // C6: the cutoff before which a kickoff counts as "locks early",
  // derived from this week's final lock (locksQuery, already fetched
  // below) rather than adding a new fetch path.
  const earlyLockCutoff = useMemo(() => {
    const finalLock = locksQuery.data?.data.locks?.final_lock;

    if (!finalLock) return null;

    return new Date(
      new Date(finalLock).getTime() - EARLY_LOCK_LEAD_HOURS * 60 * 60 * 1000,
    );
  }, [locksQuery.data]);

  // lock_advice entries only carry player_ids — resolve them against
  // the same lineup payload's optimal/bench/ir players.
  const lineupPlayerNames = useMemo(() => {
    const names = new Map<number, string>();
    const data = lineupQuery.data?.data;

    if (!data) return names;
    for (const entry of data.optimal) {
      if (entry.player) names.set(entry.player.player_id, entry.player.player_name);
    }
    for (const player of [...data.bench, ...data.ir]) {
      names.set(player.player_id, player.player_name);
    }

    return names;
  }, [lineupQuery.data]);

  // Usage trends (C4) are league-independent — default the week to the
  // selected league's current week (once known), but the view works
  // with no league selected at all.
  const [usageWeek, setUsageWeek] = useState<number>(1);

  useEffect(() => {
    if (selectedEntry) {
      setUsageWeek(selectedEntry.league.latest_scoring_period);
    }
  }, [selectedEntry]);

  const usageShiftsQuery = useGetUsageShiftsQuery({
    week: usageWeek,
    season: overview?.season,
  });

  // Playoff SOS (C5) is league-independent by default; scoping to the
  // selected league additionally joins that league's current starters.
  const [playoffPosition, setPlayoffPosition] = useState<string>("RB");
  const playoffSosQuery = useGetPlayoffSosQuery({
    position: playoffPosition,
    leagueId: leagueId ?? undefined,
    season: overview?.season,
  });
  const playoffSosRoster = playoffSosQuery.data?.rosters?.find(
    (team) => team.espn_team_id === teamId,
  );

  // C7 handcuff-panel CRUD: create/repoint (marked manual, survives
  // re-seeds), seed missing pairs, delete (soft — a re-seed won't
  // resurrect it). Same three endpoints the panel's table drives.
  const [setHandcuff, { isLoading: isSavingHandcuff }] = useSetHandcuffMutation();
  const [seedHandcuffs, { isLoading: isSeedingHandcuffs }] = useSeedHandcuffsMutation();
  const [deleteHandcuff] = useDeleteHandcuffMutation();
  const [handcuffForm, setHandcuffForm] = useState({
    starterName: "",
    handcuffName: "",
    nflTeam: "",
    note: "",
  });
  const [handcuffMessage, setHandcuffMessage] = useState("");

  const handleSaveHandcuff = async () => {
    if (!handcuffForm.starterName.trim() || !handcuffForm.handcuffName.trim()) return;
    try {
      await setHandcuff({
        starterName: handcuffForm.starterName.trim(),
        handcuffName: handcuffForm.handcuffName.trim(),
        nflTeam: handcuffForm.nflTeam.trim() || undefined,
        note: handcuffForm.note.trim() || undefined,
      }).unwrap();
      setHandcuffForm({ starterName: "", handcuffName: "", nflTeam: "", note: "" });
      setHandcuffMessage("");
    } catch {
      setHandcuffMessage("Failed to save that mapping.");
    }
  };

  const handleSeedHandcuffs = async () => {
    try {
      const result = await seedHandcuffs().unwrap();

      setHandcuffMessage(
        `Seeded ${result.created} new mapping${result.created === 1 ? "" : "s"} (${result.skipped} already known).`,
      );
    } catch {
      setHandcuffMessage("Seeding failed.");
    }
  };

  const handleDeleteHandcuff = async (starterName: string) => {
    try {
      await deleteHandcuff({ starterName }).unwrap();
    } catch {
      setHandcuffMessage(`Failed to delete ${starterName}.`);
    }
  };

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

      {/* Usage trends (C4) — league-independent: volume and opportunity,
          never fantasy points. A role change shows up here before it
          shows up in a box score. */}
      <div className={cardClass}>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h3 className="text-xl">Usage trends</h3>
            <p className="text-sm text-default-500">
              Snap and target share vs. each player&apos;s trailing
              baseline — never fantasy points. A target-count badge means
              real opportunity that didn&apos;t show up in the box score
              this game, not a lost role.
            </p>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            Week
            <input
              className={`${selectClass} w-20`}
              min={1}
              type="number"
              value={usageWeek}
              onChange={(e) =>
                setUsageWeek(Math.max(1, Number(e.target.value) || 1))
              }
            />
          </label>
        </div>
        {usageShiftsQuery.isLoading || !usageShiftsQuery.data ? (
          <Spinner />
        ) : usageShiftsQuery.data.shifts.length === 0 ? (
          <p className="text-sm text-default-500">
            No meaningful usage shifts for week {usageWeek} yet — either
            usage data hasn&apos;t synced, or no role changed enough to
            clear the noise floor.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-default-500">
                <th className="pb-1">Player</th>
                <th className="pb-1">Metric</th>
                <th className="pb-1 text-right">This week</th>
                <th className="pb-1 text-right">Baseline</th>
                <th className="pb-1 text-right">Move</th>
              </tr>
            </thead>
            <tbody>
              {usageShiftsQuery.data.shifts.map((shift) => (
                <UsageShiftRow
                  key={`${shift.player_name}-${shift.metric}`}
                  shift={shift}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Playoff strength of schedule (C5) — league-independent by
          default; when a league is selected, "Your starters" below joins
          that team's current lineup against the same table. */}
      <div className={cardClass}>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div>
            <h3 className="text-xl">
              Playoff SOS
              {playoffSosQuery.data
                ? ` — weeks ${playoffSosQuery.data.weeks.join("-")}`
                : ""}
            </h3>
            <p className="text-sm text-default-500">
              Sum of C2&apos;s matchup multipliers across each team&apos;s
              playoff-window opponents. A bye counts as zero, not an
              average — a low score next to a bye badge means fewer games,
              not necessarily a soft schedule.
            </p>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            Position
            <select
              className={selectClass}
              value={playoffPosition}
              onChange={(e) => setPlayoffPosition(e.target.value)}
            >
              {PLAYOFF_SOS_POSITIONS.map((position) => (
                <option key={position} value={position}>
                  {position}
                </option>
              ))}
            </select>
          </label>
        </div>
        {playoffSosQuery.isLoading || !playoffSosQuery.data ? (
          <Spinner />
        ) : (
          <>
            {playoffSosQuery.data.note && (
              <p className="flex items-start gap-2 text-sm text-warning-700 dark:text-warning-400">
                <FiAlertTriangle className="mt-0.5 shrink-0" />
                <span>{playoffSosQuery.data.note}</span>
              </p>
            )}
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-default-500">
                  <th className="pb-1">#</th>
                  <th className="pb-1">Team</th>
                  <th className="pb-1 text-right">SOS</th>
                  <th className="pb-1 text-right">Games</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(
                  playoffSosQuery.data.positions[playoffPosition] ?? {},
                )
                  .sort(([, a], [, b]) => a.rank - b.rank)
                  .map(([team, entry]) => (
                    <PlayoffSosRow key={team} entry={entry} team={team} />
                  ))}
              </tbody>
            </table>
            {leagueId !== null && (
              <>
                <h4 className="text-lg mt-2">
                  {teamName(teamId)}&apos;s starters
                </h4>
                {!playoffSosRoster ? (
                  <p className="text-sm text-default-500">
                    No cached roster to join for this team yet.
                  </p>
                ) : (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-default-500">
                        <th className="pb-1">Player</th>
                        <th className="pb-1">Pos</th>
                        <th className="pb-1">Team</th>
                        <th className="pb-1 text-right">SOS</th>
                        <th className="pb-1 text-right">Rank</th>
                      </tr>
                    </thead>
                    <tbody>
                      {playoffSosRoster.starters.map((starter) => (
                        <tr
                          key={starter.player_name}
                          className="border-t border-default-100"
                        >
                          <td className="py-1">{starter.player_name}</td>
                          <td className="py-1">{starter.position}</td>
                          <td className="py-1">{starter.nfl_team ?? "—"}</td>
                          <td className="py-1 text-right">
                            {starter.playoff_sos?.score.toFixed(2) ?? "—"}
                          </td>
                          <td
                            className={`py-1 text-right ${
                              starter.playoff_sos
                                ? confidenceClass(
                                    starter.playoff_sos.confidence,
                                  )
                                : ""
                            }`}
                          >
                            {starter.playoff_sos
                              ? `#${starter.playoff_sos.rank}`
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </>
        )}
      </div>

      {/* Handcuff map (C7) — the curated starter -> direct-backup table
          (CRUD + seed) plus, once a league is selected, which of those
          backups are available right now. Chips on the roster below
          point back here. */}
      <div className={cardClass}>
        <div>
          <h3 className="text-xl">
            <FiShield className="inline mb-1 mr-1" />
            Handcuffs
          </h3>
          <p className="text-sm text-default-500">
            Curated starter → direct-backup map. Rows this user deletes or
            repoints stay that way — re-seeding only fills in what&apos;s
            missing.
          </p>
        </div>

        {leagueId !== null && (
          <div className="flex flex-col gap-1">
            <h4 className="text-sm font-bold text-default-500">
              Available this week
            </h4>
            {handcuffFlagsQuery.isLoading || !handcuffFlagsQuery.data ? (
              <Spinner />
            ) : handcuffFlagsQuery.data.data.handcuffs.length === 0 ? (
              <p className="text-sm text-default-500">
                No rostered starter&apos;s curated handcuff is currently a
                free agent.
              </p>
            ) : (
              <ul className="flex flex-col gap-1">
                {handcuffFlagsQuery.data.data.handcuffs.map((flag) => (
                  <li
                    key={flag.starter_name}
                    className="flex items-center justify-between text-sm border-b border-default-100 pb-1"
                  >
                    <span>
                      {flag.starter_name}
                      {flag.starter_injury_status && (
                        <span
                          className={
                            flag.priority === "high"
                              ? " text-danger font-bold"
                              : " text-default-400"
                          }
                        >
                          {" "}
                          ({flag.starter_injury_status})
                        </span>
                      )}
                    </span>
                    <HandcuffChip flag={flag} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <h4 className="text-sm font-bold text-default-500">
              Curated map
            </h4>
            <Button
              disabled={isSeedingHandcuffs}
              size="sm"
              onClick={handleSeedHandcuffs}
            >
              {isSeedingHandcuffs ? <Spinner size="sm" /> : "Seed missing pairs"}
            </Button>
          </div>

          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-xs">
              Starter
              <input
                className={`${selectClass} w-36`}
                placeholder="Starter name"
                value={handcuffForm.starterName}
                onChange={(e) =>
                  setHandcuffForm({ ...handcuffForm, starterName: e.target.value })
                }
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              Handcuff
              <input
                className={`${selectClass} w-36`}
                placeholder="Backup name"
                value={handcuffForm.handcuffName}
                onChange={(e) =>
                  setHandcuffForm({ ...handcuffForm, handcuffName: e.target.value })
                }
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              Team
              <input
                className={`${selectClass} w-16`}
                placeholder="SEA"
                value={handcuffForm.nflTeam}
                onChange={(e) =>
                  setHandcuffForm({ ...handcuffForm, nflTeam: e.target.value })
                }
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              Note
              <input
                className={`${selectClass} w-40`}
                placeholder="optional"
                value={handcuffForm.note}
                onChange={(e) =>
                  setHandcuffForm({ ...handcuffForm, note: e.target.value })
                }
              />
            </label>
            <Button
              color="primary"
              disabled={
                isSavingHandcuff ||
                !handcuffForm.starterName.trim() ||
                !handcuffForm.handcuffName.trim()
              }
              size="sm"
              onClick={handleSaveHandcuff}
            >
              {isSavingHandcuff ? <Spinner size="sm" /> : "Save"}
            </Button>
          </div>
          {handcuffMessage && (
            <p className="text-sm text-default-500">{handcuffMessage}</p>
          )}

          {handcuffsQuery.isLoading || !handcuffsQuery.data ? (
            <Spinner />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-default-500">
                  <th className="pb-1">Starter</th>
                  <th className="pb-1">Handcuff</th>
                  <th className="pb-1">Team</th>
                  <th className="pb-1">Note</th>
                  <th className="pb-1">Source</th>
                  <th className="pb-1" />
                </tr>
              </thead>
              <tbody>
                {handcuffsQuery.data.handcuffs.map((pair) => (
                  <tr
                    key={pair.starter_name}
                    className="border-t border-default-100"
                  >
                    <td className="py-1">{pair.starter_name}</td>
                    <td className="py-1">{pair.handcuff_name}</td>
                    <td className="py-1">{pair.nfl_team ?? "—"}</td>
                    <td className="py-1 text-default-400">
                      {pair.note ?? "—"}
                    </td>
                    <td className="py-1 text-default-400">{pair.source}</td>
                    <td className="py-1 text-right">
                      <button
                        className="text-danger-500 hover:text-danger-700"
                        title={`Delete ${pair.starter_name}`}
                        type="button"
                        onClick={() => handleDeleteHandcuff(pair.starter_name)}
                      >
                        <FiTrash2 />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
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
                          <td className="py-1">
                            {entry.player_name}
                            {handcuffFlagByStarter.has(entry.player_name) && (
                              <HandcuffChip
                                flag={handcuffFlagByStarter.get(entry.player_name)!}
                              />
                            )}
                          </td>
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

          {/* Lineup optimizer (C1/C2/C6) */}
          <div className={cardClass}>
            <h3 className="text-xl">
              Lineup optimizer
              {lineupQuery.data?.data ? ` — week ${lineupQuery.data.data.week}` : ""}
            </h3>
            {lineupQuery.isLoading || !lineupQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={lineupQuery.data.warnings} />
                {!lineupQuery.data.data ? (
                  <p className="text-sm text-default-500">
                    No cached roster for this week yet.
                  </p>
                ) : (
                  <>
                    <div className="flex flex-wrap items-baseline gap-4 text-sm">
                      <span>
                        Current:{" "}
                        <span className="font-bold">
                          {lineupQuery.data.data.current_total.toFixed(1)}
                        </span>
                      </span>
                      <span>
                        Optimal:{" "}
                        <span className="font-bold">
                          {lineupQuery.data.data.optimal_total.toFixed(1)}
                        </span>
                      </span>
                      <span
                        className={`font-bold ${
                          lineupQuery.data.data.delta_points > 0
                            ? "text-success"
                            : "text-default-400"
                        }`}
                      >
                        {lineupQuery.data.data.delta_points > 0 ? "+" : ""}
                        {lineupQuery.data.data.delta_points.toFixed(1)} pts
                      </span>
                    </div>

                    {lineupQuery.data.data.warnings.map((warning, i) => (
                      <p
                        key={i}
                        className="flex items-start gap-2 text-sm text-warning-700 dark:text-warning-400"
                      >
                        <FiAlertTriangle className="mt-0.5 shrink-0" />
                        <span>{warning}</span>
                      </p>
                    ))}

                    {lineupQuery.data.data.moves.length === 0 ? (
                      <p className="text-sm text-default-500">
                        Your current lineup is already optimal.
                      </p>
                    ) : (
                      <div>
                        <h4 className="text-sm font-bold text-default-500">
                          Moves to make
                        </h4>
                        <ul className="flex flex-col gap-1 text-sm">
                          {lineupQuery.data.data.moves.map((move) => (
                            <li key={move.player_id}>
                              {move.player_name}: {move.from_slot} →{" "}
                              <span className="font-bold">{move.to_slot}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-default-500">
                          <th className="pb-1">Slot</th>
                          <th className="pb-1">Player</th>
                          <th className="pb-1">Opp</th>
                          <th className="pb-1 text-right">Adj</th>
                          <th className="pb-1 text-right">Kickoff</th>
                          <th className="pb-1 text-right">Matchup</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lineupQuery.data.data.optimal.map((slotEntry, i) => (
                          <tr
                            key={`${slotEntry.slot}-${i}`}
                            className="border-t border-default-100 align-top"
                          >
                            <td className="py-1">{slotEntry.slot}</td>
                            <td className="py-1">
                              {slotEntry.player ? (
                                <>
                                  {slotEntry.player.player_name}
                                  <span className="text-default-400">
                                    {" "}
                                    {slotEntry.player.position ?? "—"} ·{" "}
                                    {slotEntry.player.nfl_team ?? "—"}
                                  </span>
                                </>
                              ) : (
                                <span className="text-default-400">empty</span>
                              )}
                            </td>
                            <td className="py-1">
                              {slotEntry.player?.on_bye
                                ? "bye"
                                : slotEntry.player?.opponent ?? "—"}
                            </td>
                            <td className="py-1 text-right">
                              {slotEntry.player?.adjusted_projection?.toFixed(1) ??
                                "—"}
                            </td>
                            <td className="py-1 text-right">
                              <KickoffBadge
                                earlyCutoff={earlyLockCutoff}
                                kickoff={slotEntry.player?.kickoff ?? null}
                              />
                            </td>
                            <td className="py-1 text-right">
                              {slotEntry.player && (
                                <MatchupChip matchup={slotEntry.player.matchup} />
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>

                    {lineupQuery.data.data.lock_advice.length > 0 && (
                      <div className="flex flex-col gap-2">
                        <h4 className="text-sm font-bold text-default-500">
                          Lock-flexibility advice
                        </h4>
                        {lineupQuery.data.data.lock_advice.map((advice, i) => (
                          <LockAdviceCard
                            key={i}
                            alternativeName={
                              lineupPlayerNames.get(advice.alternative) ??
                              `Player ${advice.alternative}`
                            }
                            costPoints={advice.cost_points}
                            note={advice.note}
                            slot={advice.slot}
                            starterName={
                              lineupPlayerNames.get(advice.start) ??
                              `Player ${advice.start}`
                            }
                          />
                        ))}
                      </div>
                    )}
                  </>
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

          {/* Trade willingness (E3) — owner profiles from executed
              LeagueTransaction history, sorted most-willing first. */}
          <div className={cardClass}>
            <h3 className="text-xl">Trade willingness</h3>
            <p className="text-sm text-default-500">
              Who trades, how often, and with whom — computed from this
              league&apos;s synced transaction history.
              &quot;unknown&quot; means the season hasn&apos;t reached the
              trade deadline with zero trades yet, not that the owner
              said no.
            </p>
            {tradeWillingnessQuery.isLoading || !tradeWillingnessQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={tradeWillingnessQuery.data.warnings} />
                {tradeWillingnessQuery.data.data.owners.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No cached transactions yet.
                  </p>
                ) : (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-default-500">
                        <th className="pb-1">Owner</th>
                        <th className="pb-1">Willingness</th>
                        <th className="pb-1 text-right">Trades/season</th>
                        <th className="pb-1 text-right">Vs. league</th>
                        <th className="pb-1 text-right">Moves/season</th>
                        <th className="pb-1 text-right">Partners</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tradeWillingnessQuery.data.data.owners.map((owner) => (
                        <TradeWillingnessRow key={owner.team_id} owner={owner} />
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>

          {/* K/DST streaming (C3) */}
          <div className={cardClass}>
            <h3 className="text-xl">
              K/DST streaming
              {streamingQuery.data
                ? ` — week ${streamingQuery.data.data.week}`
                : ""}
            </h3>
            {streamingQuery.isLoading || !streamingQuery.data ? (
              <Spinner />
            ) : (
              <>
                <StalenessBanner warnings={streamingQuery.data.warnings} />
                {streamingQuery.data.data.recommendations.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No available kickers or defenses in the cached
                    free-agent pool yet.
                  </p>
                ) : (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-default-500">
                        <th className="pb-1">#</th>
                        <th className="pb-1">Player</th>
                        <th className="pb-1">Pos</th>
                        <th className="pb-1">Opp</th>
                        <th className="pb-1 text-right">Proj</th>
                        <th className="pb-1 text-right">Adj</th>
                        <th className="pb-1 text-right">Matchup</th>
                      </tr>
                    </thead>
                    <tbody>
                      {streamingQuery.data.data.recommendations.map((rec) => (
                        <tr
                          key={rec.player_id}
                          className="border-t border-default-100 align-top"
                        >
                          <td className="py-1">{rec.rank}</td>
                          <td className="py-1">
                            {rec.player_name}
                            <HomerCheckNote check={rec.homer_check} />
                          </td>
                          <td className="py-1">{rec.position}</td>
                          <td className="py-1">
                            {rec.opponent ?? "bye"}
                          </td>
                          <td className="py-1 text-right">
                            {rec.projected_points?.toFixed(1) ?? "—"}
                          </td>
                          <td className="py-1 text-right font-bold">
                            {rec.matchup_adjusted_points?.toFixed(1) ?? "—"}
                          </td>
                          <td
                            className={`py-1 text-right ${confidenceClass(rec.matchup.confidence)}`}
                          >
                            {rec.matchup.multiplier.toFixed(2)}x
                            {rec.matchup.rank ? ` (#${rec.matchup.rank})` : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
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
