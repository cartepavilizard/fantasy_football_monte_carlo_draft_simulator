"use client";

import { useEffect, useMemo, useState } from "react";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";
import NextLink from "next/link";
import {
  FiAlertTriangle,
  FiClock,
  FiMessageCircle,
  FiRefreshCw,
  FiShield,
  FiTrendingDown,
  FiTrendingUp,
  FiUsers,
} from "react-icons/fi";

import {
  useCreatePlayerNoteMutation,
  useDeleteHandcuffMutation,
  useDeletePlayerNoteMutation,
  useDeleteWriterMutation,
  useGetBlockingQuery,
  useGetByeOutlookQuery,
  useGetDeadlineReportQuery,
  useGetFreeAgentsQuery,
  useGetHandcuffsQuery,
  useGetHoardingQuery,
  useGetLeagueHandcuffsQuery,
  useGetLineupQuery,
  useGetLocksQuery,
  useGetMatchupsQuery,
  useGetOverviewQuery,
  useGetPlayerNotesQuery,
  useGetPlayoffSosQuery,
  useGetRosterQuery,
  useGetStreamingQuery,
  useGetStrategyFlagsQuery,
  useGetTradeOpportunitiesQuery,
  useGetTradeWillingnessQuery,
  useGetTransactionsQuery,
  useGetUsageShiftsQuery,
  useGetWritersQuery,
  useLazyGetGrokPromptQuery,
  useParsePlayerNoteMutation,
  useSeedHandcuffsMutation,
  useSeedWritersMutation,
  useSetHandcuffMutation,
  useSetWriterMutation,
  useSyncLeagueMutation,
} from "@/api/services/inseason";
import { VarianceFlag } from "@/components/variance-flag";
import { EmptyStateHawk } from "@/components/mascots";
import {
  HawkCard,
  HawkCardHeader,
  HawkCardTitle,
  HawkChip,
} from "@/components/hawk-cards";
import {
  HawkStalenessBanner,
  HawkStalenessInline,
} from "@/components/inseason-staleness-banner";
import { HawkTabs } from "@/components/inseason-tabs";
import { InseasonLineupOptimizer } from "@/components/inseason-lineup-optimizer";
import {
  InseasonHandcuffsCard,
  InseasonMatchupsCard,
  InseasonPlayoffSosCard,
  InseasonStreamingCard,
} from "@/components/inseason-side-cards";
import {
  DeadlineReport,
  GrokParsePreview,
  HandcuffFlag,
  HomerCheck,
  InSeasonOverviewEntry,
  MatchupEntry,
  PlayoffSosEntry,
  StrategyFlagsData,
  TradeOpportunityReport,
  TradeWillingnessLabel,
  TradeWillingnessOwner,
  UsageShift,
} from "@/types";

const PLAYOFF_SOS_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"] as const;

// C6: a starter "locks early" when their kickoff is at least this many
// hours before the week's final lock. Mirrors EARLY_LOCK_LEAD_HOURS in
// backend/models/config.py — not exposed over the API, so kept in sync
// here by hand.
const EARLY_LOCK_LEAD_HOURS = 36;

// The select/input base style — surfaced so the curation forms still read
// as hawk surfaces (surface-2 + border-2 + kit radius).
const selectClass =
  "bg-transparent border-medium border-default rounded-medium px-3 py-2 text-sm";

function confidenceClass(confidence: string): string {
  if (confidence === "high") return "text-success";
  if (confidence === "medium") return "text-warning";

  return "text-default-400";
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

// E4: one trade-opportunity row. severity "window" is the hard five-condition
// trigger; "watch" is the release valve — marginal cases that didn't clear
// the push bar. The probe is E1's 1-for-1 evaluation (fit_delta > 0 means the
// deal grades fit-positive for your roster); null when no probe ran.
function OpportunityRow({ opp }: { opp: TradeOpportunityReport["opportunities"][number] }) {
  const isWindow = opp.severity === "window";

  return (
    <li className="flex flex-col gap-1 border-b border-default-100 pb-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex text-xs font-bold px-1.5 py-0.5 rounded-full border ${
            isWindow
              ? "bg-success-100 text-success-700 border-success-300 dark:bg-success-950/40 dark:text-success-400"
              : "bg-default-100 text-default-600 border-default-300 dark:bg-default-800/40"
          }`}
        >
          {opp.severity}
        </span>
        <span className="font-bold">{opp.rival_team_name}</span>
        <span className="text-default-400">
          {opp.injured.name} ({opp.injured.position ?? "—"}, {opp.injured.status}) —
          ~{opp.rival_gap_per_week.toFixed(1)} pts/wk gap
        </span>
      </div>
      {opp.my_surplus.length > 0 ? (
        <p className="text-default-500">
          Your spare:{" "}
          {opp.my_surplus
            .map((s) => `${s.name} (${s.value.toFixed(1)} ROS)`)
            .join(", ")}
        </p>
      ) : (
        <p className="text-default-400">No spare piece above the offer floor.</p>
      )}
      {opp.probe && (
        <p className="text-default-500">
          Probe fit: you{" "}
          <span
            className={`font-bold ${
              opp.probe.fit_delta_a >= 0 ? "text-success" : "text-danger"
            }`}
          >
            {opp.probe.fit_delta_a > 0 ? "+" : ""}
            {opp.probe.fit_delta_a.toFixed(1)} ROS
          </span>
          , them {opp.probe.fit_delta_b > 0 ? "+" : ""}
          {opp.probe.fit_delta_b.toFixed(1)}.
        </p>
      )}
      {opp.note && <p className="text-xs text-default-400">{opp.note}</p>}
    </li>
  );
}

// E8: one deadline-window team row, with a role badge (contender/rebuilder)
// and a buy/sell window flag. Neutral teams show no window — the model only
// calls when there's a clear strategic reason to act before the deadline.
function roleBadgeClass(role: DeadlineReport["teams"][number]["role"]): string {
  switch (role) {
    case "contender":
      return "bg-success-100 text-success-700 border-success-300 dark:bg-success-950/40 dark:text-success-400";
    case "rebuilder":
      return "bg-danger-100 text-danger-700 border-danger-300 dark:bg-danger-950/40 dark:text-danger-400";
    default:
      return "bg-default-100 text-default-600 border-default-300 dark:bg-default-800/40";
  }
}

function DeadlineTeamRow({ team }: { team: DeadlineReport["teams"][number] }) {
  return (
    <tr className="border-t border-default-100 align-top">
      <td className="py-1">
        {team.name}
        <span className="text-default-400">
          {" "}
          ({team.wins}-{team.losses}
          {team.ties ? `-${team.ties}` : ""})
        </span>
      </td>
      <td className="py-1">
        <span
          className={`inline-flex text-xs font-bold px-1.5 py-0.5 rounded-full border ${roleBadgeClass(
            team.role,
          )}`}
        >
          {team.role}
        </span>
      </td>
      <td className="py-1">
        {team.window ? (
          <span className="font-bold capitalize">{team.window}</span>
        ) : (
          <span className="text-default-400">—</span>
        )}
      </td>
      <td className="py-1 text-right">
        {team.playoff_value != null
          ? team.playoff_value.toFixed(1)
          : "—"}
      </td>
    </tr>
  );
}

// F1: one stack flag (best same-NFL-team QB/pass-catcher pairing). grade
// "strong" is rho>=0.30. Informational styling — never alarming.
function StackFlagRow({ flag }: { flag: StrategyFlagsData["rosters"][number]["stacks"][number] }) {
  return (
    <li className="text-sm border-b border-default-100 pb-1">
      <span className="font-bold">{flag.positions.join(" + ")}</span>{" "}
      stack with {flag.with}
      {flag.also_with && flag.also_with.length > 0 && (
        <span className="text-default-400"> (also: {flag.also_with.join(", ")})</span>
      )}
      <span className="text-default-400">
        {" "}
        — ρ={flag.correlation.toFixed(2)}, +{flag.extra_swing.toFixed(2)} swing
        ({flag.grade})
      </span>
      <p className="text-xs text-default-400">{flag.note}</p>
    </li>
  );
}

// F3: one anti-correlation flag (same-backfield RBs competing for touches,
// excluding C7's deliberate handcuff pairs). Committee competition, not a
// value call.
function AntiCorrelationRow({
  flag,
}: {
  flag: StrategyFlagsData["rosters"][number]["anti_correlation"][number];
}) {
  return (
    <li className="text-sm border-b border-default-100 pb-1">
      <span className="font-bold">{flag.players.join(" & ")}</span>{" "}
      <span className="text-default-400">({flag.nfl_team} backfield)</span>
      <p className="text-xs text-default-400">{flag.note}</p>
    </li>
  );
}

// SectionCard — the bar-header card body the rest of the page uses. Wraps
// the composite's compact card form: HawkCardHeader on top, padded body
// below, optional right-side header node (a chip/stat/button).
function SectionCard({
  title,
  subtitle,
  right,
  children,
  bodyClass = "p-3 flex flex-col gap-3",
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  right?: React.ReactNode;
  children: React.ReactNode;
  bodyClass?: string;
}) {
  return (
    <HawkCard>
      <HawkCardHeader title={title} subtitle={subtitle} right={right} />
      <div className={bodyClass}>{children}</div>
    </HawkCard>
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

  const teamInfo = (id: number | null) =>
    selectedEntry?.league.teams.find((team) => team.espn_team_id === id);
  const teamName = (id: number | null) => teamInfo(id)?.name ?? `Team ${id}`;

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

  // E4/F trade + strategy surfaces — all read-only GETs over the standard
  // cached envelope, each rendered with a StalenessBanner and a graceful
  // empty state (never a crash when the data isn't there yet).
  const tradeOpportunitiesQuery = useGetTradeOpportunitiesQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  const hoardingQuery = useGetHoardingQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  const blockingQuery = useGetBlockingQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  const deadlineReportQuery = useGetDeadlineReportQuery(
    { leagueId: leagueId ?? 0 },
    { skip: leagueId === null },
  );
  // F1/F3 are per-roster when a team is selected, else league-wide.
  const strategyFlagsQuery = useGetStrategyFlagsQuery(
    { leagueId: leagueId ?? 0, teamId: teamId ?? undefined },
    { skip: leagueId === null },
  );
  const byeOutlookQuery = useGetByeOutlookQuery(
    { leagueId: leagueId ?? 0, teamId: teamId ?? undefined },
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

  // D1 writer-panel CRUD: create/repoint (marked manual, survives
  // re-seeds), seed missing teams, delete (soft — a re-seed won't
  // resurrect it). Same three endpoints the panel's table drives.
  const writersQuery = useGetWritersQuery();
  const [setWriter, { isLoading: isSavingWriter }] = useSetWriterMutation();
  const [seedWriters, { isLoading: isSeedingWriters }] = useSeedWritersMutation();
  const [deleteWriter] = useDeleteWriterMutation();
  const [writerForm, setWriterForm] = useState({
    nflTeam: "",
    writerName: "",
    outlet: "",
    note: "",
  });
  const [writerMessage, setWriterMessage] = useState("");

  const handleSaveWriter = async () => {
    if (
      !writerForm.nflTeam.trim() ||
      !writerForm.writerName.trim() ||
      !writerForm.outlet.trim()
    )
      return;
    try {
      await setWriter({
        nflTeam: writerForm.nflTeam.trim(),
        writerName: writerForm.writerName.trim(),
        outlet: writerForm.outlet.trim(),
        note: writerForm.note.trim() || undefined,
      }).unwrap();
      setWriterForm({ nflTeam: "", writerName: "", outlet: "", note: "" });
      setWriterMessage("");
    } catch {
      setWriterMessage("Failed to save that writer.");
    }
  };

  const handleSeedWriters = async () => {
    try {
      const result = await seedWriters().unwrap();

      setWriterMessage(
        `Seeded ${result.created} new writer${result.created === 1 ? "" : "s"} (${result.skipped} already known).`,
      );
    } catch {
      setWriterMessage("Seeding failed.");
    }
  };

  const handleDeleteWriter = async (nflTeam: string) => {
    try {
      await deleteWriter({ nflTeam }).unwrap();
    } catch {
      setWriterMessage(`Failed to delete ${nflTeam}.`);
    }
  };

  // D3 manual Grok bridge: generate a prompt for one player, paste
  // Grok's answer back, preview the parse + skepticism badges, then
  // save. No LLM/xAI call happens anywhere in this codebase — the
  // parser is deterministic block extraction of what the user pastes.
  const [grokPlayer, setGrokPlayer] = useState("");
  const [grokKind, setGrokKind] = useState<
    "beat_check" | "injury_timeline" | "usage_context"
  >("beat_check");
  const [fetchGrokPrompt, { data: grokPromptData, isFetching: isGeneratingPrompt }] =
    useLazyGetGrokPromptQuery();
  const [grokMessage, setGrokMessage] = useState("");
  const [grokRawText, setGrokRawText] = useState("");
  const [parsePlayerNote, { isLoading: isParsingNote }] = useParsePlayerNoteMutation();
  const [grokPreview, setGrokPreview] = useState<GrokParsePreview | null>(null);
  const [manualStatusSignal, setManualStatusSignal] = useState("");
  const [manualSummary, setManualSummary] = useState("");
  const [createPlayerNote, { isLoading: isSavingNote }] = useCreatePlayerNoteMutation();
  const notesQuery = useGetPlayerNotesQuery(
    grokPlayer.trim() ? { player: grokPlayer.trim() } : undefined,
  );
  const [deletePlayerNote] = useDeletePlayerNoteMutation();

  const handleGenerateGrokPrompt = async () => {
    if (!grokPlayer.trim()) return;
    setGrokMessage("");
    setGrokPreview(null);
    try {
      await fetchGrokPrompt({ player: grokPlayer.trim(), kind: grokKind }).unwrap();
    } catch {
      setGrokMessage(
        `No cached data for "${grokPlayer.trim()}" — sync a league first.`,
      );
    }
  };

  const handlePreviewGrokPaste = async () => {
    if (!grokRawText.trim()) return;
    try {
      const preview = await parsePlayerNote({
        rawText: grokRawText,
        playerName: grokPlayer.trim() || undefined,
        week: usageWeek,
        season: overview?.season,
      }).unwrap();

      setGrokPreview(preview);
      setManualStatusSignal(preview.status_signal ?? "");
      setManualSummary(preview.summary ?? "");
    } catch {
      setGrokMessage("Failed to parse that paste.");
    }
  };

  const handleSaveGrokNote = async () => {
    if (!grokPlayer.trim() || !grokRawText.trim()) return;
    try {
      await createPlayerNote({
        playerName: grokPlayer.trim(),
        kind: grokKind,
        // prompt_text is required by the model for provenance, but a
        // note is still savable when generating it failed/was skipped
        // (e.g. the player isn't in cached roster/FA data yet) — the
        // backend doesn't require a resolvable player to save a note.
        promptText: grokPromptData?.prompt_text ?? "",
        rawText: grokRawText,
        season: overview?.season ?? new Date().getFullYear(),
        week: usageWeek,
        summary: manualSummary.trim() || undefined,
        statusSignal: manualStatusSignal || undefined,
      }).unwrap();
      setGrokRawText("");
      setGrokPreview(null);
      setGrokMessage("Saved.");
    } catch {
      setGrokMessage("Failed to save that note.");
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

  // The composite's tab grouping — every existing section stays
  // reachable; nothing is dropped, just grouped into the dashboard's
  // card-grid language.
  const [tab, setTab] = useState<string>("roster");
  const tabs = [
    { key: "roster", label: "Roster & matchups" },
    { key: "playoff", label: "Playoff" },
    { key: "trades", label: "Trades" },
    { key: "curation", label: "Curation" },
  ];

  const selectedTeam = teamInfo(teamId);
  const weekLabel = selectedEntry?.league.latest_scoring_period;

  return (
    <section className="flex flex-col gap-4">
      {/* ---- Header strip: league + team selectors, week badge, record + PF ---- */}
      <HawkCard>
        <div
          className="flex flex-wrap items-center gap-3 p-3"
          style={{ background: "var(--surface-2)" }}
        >
          {overviewLoading ? (
            <Spinner />
          ) : leagues.length === 0 ? (
            <div className="flex flex-col items-center gap-2 text-center py-2 w-full">
              <EmptyStateHawk size={64} />
              <p className="text-sm" style={{ color: "var(--text-dim)" }}>
                No leagues synced yet — hit Refresh to pull your configured
                ESPN leagues.
              </p>
            </div>
          ) : (
            <>
              <label className="flex flex-col gap-1" style={{ fontSize: "var(--fs-sm)" }}>
                <span className="font-head font-bold uppercase text-[var(--text-mute)]" style={{ fontSize: "var(--fs-xs)" }}>
                  League
                </span>
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
              <label className="flex flex-col gap-1" style={{ fontSize: "var(--fs-sm)" }}>
                <span className="font-head font-bold uppercase text-[var(--text-mute)]" style={{ fontSize: "var(--fs-xs)" }}>
                  Perspective
                </span>
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

              {/* League name (display font, large) + week badge + record + PF —
                  the composite's header strip. */}
              <div className="flex items-center gap-2 ml-auto flex-wrap">
                <span
                  className="font-display uppercase"
                  style={{
                    fontSize: "var(--fs-xl)",
                    color: "#fff",
                    lineHeight: 1,
                  }}
                >
                  {selectedEntry?.league.name ?? "—"}
                </span>
                {weekLabel != null && (
                  <HawkChip tone="green">Week {weekLabel}</HawkChip>
                )}
                {selectedTeam && (
                  <span style={{ fontSize: "var(--fs-sm)", color: "var(--text-dim)" }}>
                    Record{" "}
                    <b style={{ color: "var(--text)" }}>
                      {selectedTeam.wins}-{selectedTeam.losses}
                      {selectedTeam.ties ? `-${selectedTeam.ties}` : ""}
                    </b>{" "}
                    · PF{" "}
                    <b style={{ color: "var(--green)" }}>
                      {selectedTeam.points_for.toFixed(0)}
                    </b>
                  </span>
                )}
                {leagueId !== null && (
                  <NextLink
                    className="font-head font-bold uppercase"
                    style={{
                      fontSize: "var(--fs-xs)",
                      color: "var(--green)",
                      letterSpacing: "0.05em",
                    }}
                    href={`/trade-room/${leagueId}`}
                  >
                    Trade room →
                  </NextLink>
                )}
              </div>
            </>
          )}
        </div>
      </HawkCard>

      {/* ---- Staleness banner (composite's amber strip; Refresh = sync) ---- */}
      {leagues.length > 0 && selectedEntry && (
        <HawkStalenessBanner
          warnings={selectedEntry.warnings}
          onRefresh={handleSync}
          refreshing={isSyncing}
          message={syncMessage}
        />
      )}
      {leagues.length === 0 && (
        <HawkStalenessBanner
          warnings={["No leagues synced — Refresh to pull configured ESPN leagues."]}
          onRefresh={handleSync}
          refreshing={isSyncing}
          message={syncMessage}
        />
      )}

      {/* ---- Top dashboard grid (composite's 1.4fr 1fr 1fr) ---- */}
      {leagueId !== null && teamId !== null && (
        <div
          className="grid"
          style={{
            gridTemplateColumns: "repeat(1, 1fr)",
            gap: "var(--sp-3)",
            alignItems: "start",
          }}
        >
          <div
            className="grid"
            style={{
              gridTemplateColumns: "repeat(1, 1fr)",
              gap: "var(--sp-3)",
              alignItems: "start",
            }}
          >
            {/* Hero: Lineup Optimizer */}
            <InseasonLineupOptimizer
              data={lineupQuery.data?.data ?? undefined}
              loading={lineupQuery.isLoading || !lineupQuery.data}
              warnings={lineupQuery.data?.warnings ?? []}
              resolveName={(id) =>
                lineupPlayerNames.get(id) ?? `Player ${id}`
              }
              earlyLockCutoff={earlyLockCutoff}
              formatKickoff={formatKickoff}
            />

            {/* Side cards: Matchups + Playoff SOS | Streaming + Handcuffs */}
            <div
              className="grid"
              style={{
                gridTemplateColumns: "repeat(1, 1fr)",
                gap: "var(--sp-3)",
                alignItems: "start",
              }}
            >
              <InseasonMatchupsCard
                data={matchupsQuery.data?.data}
                loading={matchupsQuery.isLoading || !matchupsQuery.data}
                teamId={teamId}
                teamName={teamName}
                warnings={matchupsQuery.data?.warnings ?? []}
              />
              <InseasonPlayoffSosCard
                data={playoffSosQuery.data}
                loading={playoffSosQuery.isLoading || !playoffSosQuery.data}
                position={playoffPosition}
                onPositionChange={setPlayoffPosition}
                positions={PLAYOFF_SOS_POSITIONS}
              />
              <InseasonStreamingCard
                data={streamingQuery.data?.data}
                loading={streamingQuery.isLoading || !streamingQuery.data}
                warnings={streamingQuery.data?.warnings ?? []}
              />
              <InseasonHandcuffsCard
                flags={handcuffFlagsQuery.data?.data.handcuffs}
                loading={handcuffFlagsQuery.isLoading || !handcuffFlagsQuery.data}
              />
            </div>
          </div>
        </div>
      )}

      {/* ---- Tabs: the rest of the dashboard ---- */}
      <HawkTabs tabs={tabs} active={tab} onChange={setTab} />

      {tab === "roster" && (
        <div className="flex flex-col gap-4">
          {/* Roster */}
          <SectionCard
            title={`${teamName(teamId)}'s roster`}
            right={rosterQuery.data?.warnings?.length ? <HawkChip tone="warn">stale</HawkChip> : undefined}
          >
            {rosterQuery.isLoading || !rosterQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={rosterQuery.data.warnings} />
                {rosterQuery.data.data ? (
                  <div className="hawk-scroll overflow-x-auto">
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
                  </div>
                ) : (
                  <p className="text-sm text-default-500">
                    No cached roster for this week yet.
                  </p>
                )}
              </>
            )}
          </SectionCard>

          {/* Recent transactions */}
          <SectionCard title="Recent transactions">
            {transactionsQuery.isLoading || !transactionsQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={transactionsQuery.data.warnings} />
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
          </SectionCard>

          {/* Top free agents */}
          <SectionCard title="Top free agents">
            {freeAgentsQuery.isLoading || !freeAgentsQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={freeAgentsQuery.data.warnings} />
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
          </SectionCard>

          {/* Lineup locks */}
          <SectionCard
            title={
              <>
                <FiClock className="inline mb-1 mr-1" />
                Lineup locks
              </>
            }
            subtitle={locksQuery.data ? `week ${locksQuery.data.data.week}` : undefined}
          >
            {locksQuery.isLoading || !locksQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={locksQuery.data.warnings} />
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
          </SectionCard>

          {/* Usage trends */}
          <SectionCard
            title="Usage trends"
            right={
              <label className="flex items-center gap-1" style={{ fontSize: "var(--fs-sm)" }}>
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
            }
          >
            <p className="text-sm text-default-500">
              Snap and target share vs. each player&apos;s trailing
              baseline — never fantasy points. A target-count badge means
              real opportunity that didn&apos;t show up in the box score
              this game, not a lost role.
            </p>
            {usageShiftsQuery.isLoading || !usageShiftsQuery.data ? (
              <Spinner />
            ) : usageShiftsQuery.data.shifts.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-2 text-center">
                <EmptyStateHawk size={64} />
                <p className="text-sm text-default-500">
                  No meaningful usage shifts for week {usageWeek} yet — either
                  usage data hasn&apos;t synced, or no role changed enough to
                  clear the noise floor.
                </p>
              </div>
            ) : (
              <div className="hawk-scroll overflow-x-auto">
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
              </div>
            )}
          </SectionCard>
        </div>
      )}

      {tab === "playoff" && (
        <div className="flex flex-col gap-4">
          {/* Playoff SOS (full) */}
          <SectionCard
            title={
              playoffSosQuery.data
                ? `Playoff SOS — weeks ${playoffSosQuery.data.weeks.join("-")}`
                : "Playoff SOS"
            }
            right={
              <label className="flex flex-col gap-1" style={{ fontSize: "var(--fs-sm)" }}>
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
            }
          >
            <p className="text-sm text-default-500">
              Sum of C2&apos;s matchup multipliers across each team&apos;s
              playoff-window opponents. A bye counts as zero, not an
              average — a low score next to a bye badge means fewer games,
              not necessarily a soft schedule.
            </p>
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
                <div className="hawk-scroll overflow-x-auto">
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
                </div>
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
                      <div className="hawk-scroll overflow-x-auto">
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
                      </div>
                    )}
                  </>
                )}
              </>
            )}
          </SectionCard>

          {/* Bye outlook (F2) */}
          <SectionCard
            title={
              byeOutlookQuery.data
                ? `Bye outlook — week ${byeOutlookQuery.data.data.week}`
                : "Bye outlook"
            }
          >
            <p className="text-sm text-default-500">
              Bye-week clustering (league-wide) and each roster&apos;s thinnest
              future week. Awareness flags, not lineup commands.
            </p>
            {byeOutlookQuery.isLoading || !byeOutlookQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={byeOutlookQuery.data.warnings} />
                {byeOutlookQuery.data.data.cluster.status ===
                "no_schedule_data" ? (
                  <p className="text-sm text-default-500">
                    {byeOutlookQuery.data.data.cluster.note ??
                      "No NFL schedule data available — sync the league to load the pro schedule."}
                  </p>
                ) : (
                  <>
                    {byeOutlookQuery.data.data.cluster.warning ? (
                      <p className="flex items-start gap-2 text-sm text-warning-700 dark:text-warning-400">
                        <FiAlertTriangle className="mt-0.5 shrink-0" />
                        <span>{byeOutlookQuery.data.data.cluster.warning}</span>
                      </p>
                    ) : (
                      <p className="text-sm text-default-500">
                        {byeOutlookQuery.data.data.cluster.note ??
                          `No bye week shared by ${byeOutlookQuery.data.data.threshold}+ likely starters.`}
                      </p>
                    )}
                    {byeOutlookQuery.data.data.cluster.clusters.length > 0 && (
                      <ul className="flex flex-col gap-1 text-sm">
                        {byeOutlookQuery.data.data.cluster.clusters.map(
                          (cluster) => (
                            <li
                              key={cluster.week}
                              className="border-b border-default-100 pb-1"
                            >
                              Week {cluster.week} — {cluster.count} starter(s):{" "}
                              {cluster.players
                                .map(
                                  (p) =>
                                    `${p.name ?? "—"} (${p.nfl_team})`,
                                )
                                .join(", ")}
                            </li>
                          ),
                        )}
                      </ul>
                    )}

                    <h4 className="text-sm font-bold text-default-500 mt-2">
                      Thin-week preview
                    </h4>
                    <ul className="flex flex-col gap-2 text-sm">
                      {byeOutlookQuery.data.data.thin_weeks.map((tw) => (
                        <li
                          key={tw.espn_team_id}
                          className="border-b border-default-100 pb-1"
                        >
                          <span className="font-bold">
                            {teamName(tw.espn_team_id)}
                          </span>
                          {tw.preview.status === "no_schedule_data" ? (
                            <span className="text-default-400">
                              {" "}
                              — no schedule data
                            </span>
                          ) : tw.preview.thinnest_week == null ? (
                            <span className="text-default-400">
                              {" "}
                              — {tw.preview.note ?? "No future bye weeks affect this roster."}
                            </span>
                          ) : (
                            <span>
                              {" "}
                              — thinnest in week{" "}
                              {tw.preview.thinnest_week} (
                              {tw.preview.count} on bye):{" "}
                              {tw.preview.affected
                                .map(
                                  (a) =>
                                    `${a.name ?? "—"} (${a.nfl_team})`,
                                )
                                .join(", ")}
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </>
            )}
          </SectionCard>

          {/* Strategy flags (F1 stacks + F3 anti-correlation) */}
          <SectionCard
            title={
              strategyFlagsQuery.data
                ? `Strategy flags — week ${strategyFlagsQuery.data.data.week}`
                : "Strategy flags"
            }
          >
            <p className="text-sm text-default-500">
              Awareness flags only — F1 stacks (same-NFL-team QB/pass-catcher
              pairings) and F3 anti-correlation (same-backfield RBs competing
              for touches). Informational, never a value call.
            </p>
            {strategyFlagsQuery.isLoading || !strategyFlagsQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={strategyFlagsQuery.data.warnings} />
                {strategyFlagsQuery.data.data.rosters.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No cached rosters for this week yet.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-3">
                    {strategyFlagsQuery.data.data.rosters.map((roster) => (
                      <li
                        key={roster.espn_team_id}
                        className="flex flex-col gap-1 border-t border-default-100 pt-2 text-sm"
                      >
                        <h4 className="text-sm font-bold text-default-500">
                          {teamName(roster.espn_team_id)}
                        </h4>
                        {roster.stacks.length === 0 &&
                        roster.anti_correlation.length === 0 ? (
                          <p className="text-default-400">
                            No stack or committee flags on this roster.
                          </p>
                        ) : (
                          <>
                            {roster.stacks.length > 0 && (
                              <div>
                                <p className="text-xs font-bold text-default-500">
                                  Stacks
                                </p>
                                <ul>
                                  {roster.stacks.map((flag, i) => (
                                    <StackFlagRow key={i} flag={flag} />
                                  ))}
                                </ul>
                              </div>
                            )}
                            {roster.anti_correlation.length > 0 && (
                              <div>
                                <p className="text-xs font-bold text-default-500">
                                  Anti-correlation
                                </p>
                                <ul>
                                  {roster.anti_correlation.map((flag, i) => (
                                    <AntiCorrelationRow key={i} flag={flag} />
                                  ))}
                                </ul>
                              </div>
                            )}
                          </>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </SectionCard>
        </div>
      )}

      {tab === "trades" && (
        <div className="flex flex-col gap-4">
          {/* Trade willingness (E3) */}
          <SectionCard title="Trade willingness">
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
                <HawkStalenessInline warnings={tradeWillingnessQuery.data.warnings} />
                {tradeWillingnessQuery.data.data.owners.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No cached transactions yet.
                  </p>
                ) : (
                  <div className="hawk-scroll overflow-x-auto">
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
                  </div>
                )}
              </>
            )}
          </SectionCard>

          {/* Trade opportunities (E4) */}
          <SectionCard title="Trade opportunities">
            <p className="text-sm text-default-500">
              Every rival injury window the scanner sees right now. A
              &quot;window&quot; row cleared all five conditions; a
              &quot;watch&quot; row is the release valve — real, but below
              the push bar.
            </p>
            {tradeOpportunitiesQuery.isLoading || !tradeOpportunitiesQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={tradeOpportunitiesQuery.data.warnings} />
                {tradeOpportunitiesQuery.data.data.error ? (
                  <p className="text-sm text-default-500">
                    {tradeOpportunitiesQuery.data.data.error}
                  </p>
                ) : tradeOpportunitiesQuery.data.data.opportunities.length === 0 ? (
                  <p className="text-sm text-default-500">
                    No active injury windows right now. The scanner re-runs
                    against synced data on every refresh.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {tradeOpportunitiesQuery.data.data.opportunities.map(
                      (opp) => (
                        <OpportunityRow
                          key={`${opp.rival_team_id}-${opp.injured.player_id}`}
                          opp={opp}
                        />
                      ),
                    )}
                  </ul>
                )}
              </>
            )}
          </SectionCard>

          {/* Blocking (E5) */}
          <SectionCard
            title={
              <>
                <FiShield className="inline mb-1 mr-1" />
                Blocking plays
              </>
            }
          >
            <p className="text-sm text-default-500">
              Rivals&apos; injured-star handcuffs sitting on waivers — claim
              purely to deny the rival the insurance. Denial, not points.
            </p>
            {blockingQuery.isLoading || !blockingQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={blockingQuery.data.warnings} />
                {blockingQuery.data.data.entries.length === 0 ? (
                  <p className="text-sm text-default-500">
                    {blockingQuery.data.data.note ??
                      "No rival injured-star handcuffs available right now."}
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {blockingQuery.data.data.entries.map((entry) => (
                      <li
                        key={entry.handcuff_player_id}
                        className="text-sm border-b border-default-100 pb-2"
                      >
                        <span className="font-bold">{entry.handcuff_name}</span>
                        <span className="text-default-400">
                          {" "}
                          ({entry.position ?? "—"} · {entry.nfl_team ?? "—"})
                        </span>
                        <p className="text-default-500">{entry.copy}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </SectionCard>

          {/* Hoarding (E6) */}
          <SectionCard title="Hoarding report">
            <p className="text-sm text-default-500">
              The stored weekly post-waivers worth-hoarding scan. Generated by
              the scheduler (Mon/Tue post-waivers), not on demand — empty
              until one exists for this week.
            </p>
            {hoardingQuery.isLoading || !hoardingQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={hoardingQuery.data.warnings} />
                {!hoardingQuery.data.data ? (
                  <p className="text-sm text-default-500">
                    No hoarding report generated for this week yet.
                  </p>
                ) : (
                  <>
                    <p className="text-xs text-default-400">
                      Generated{" "}
                      {new Date(
                        hoardingQuery.data.data.generated_at,
                      ).toLocaleString()}
                      {hoardingQuery.data.data.note && (
                        <> — {hoardingQuery.data.data.note}</>
                      )}
                    </p>
                    {hoardingQuery.data.data.entries.length === 0 ? (
                      <p className="text-sm text-default-500">
                        No hoard targets cleared the margin this week.
                      </p>
                    ) : (
                      <ul className="flex flex-col gap-2">
                        {hoardingQuery.data.data.entries.map((entry) => (
                          <li
                            key={entry.player_id}
                            className="text-sm border-b border-default-100 pb-2"
                          >
                            <span className="font-bold">
                              {entry.player_name}
                            </span>
                            <span className="text-default-400">
                              {" "}
                              ({entry.position ?? "—"} · {entry.nfl_team ?? "—"})
                            </span>
                            <span className="font-bold">
                              {" "}
                              — {entry.hoard_value.toFixed(1)} hoard value
                            </span>
                            <span className="text-default-400">
                              {" "}
                              ({entry.reason}, margin {entry.margin.toFixed(1)},
                              drop {entry.drop.player_name}{" "}
                              {entry.drop.value.toFixed(1)})
                            </span>
                            <p className="text-default-500">{entry.copy}</p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </>
            )}
          </SectionCard>

          {/* Deadline report (E8) */}
          <SectionCard title="Trade deadline report">
            <p className="text-sm text-default-500">
              Per-team buy/sell windows in the weeks before the trade
              deadline. Contenders buy (chase playoff value); rebuilder sell
              (move expiring assets). Neutral teams show no window.
            </p>
            {deadlineReportQuery.isLoading || !deadlineReportQuery.data ? (
              <Spinner />
            ) : (
              <>
                <HawkStalenessInline warnings={deadlineReportQuery.data.warnings} />
                {!deadlineReportQuery.data.data.in_window ? (
                  <p className="text-sm text-default-500">
                    {deadlineReportQuery.data.data.trade_deadline
                      ? `Outside the pre-deadline window (deadline ${new Date(
                          deadlineReportQuery.data.data.trade_deadline,
                        ).toLocaleDateString()}, ${deadlineReportQuery.data.data.weeks_to_deadline}w away).`
                      : "This league has no configured trade deadline."}
                  </p>
                ) : (
                  <>
                    <p className="text-sm">
                      <span className="font-bold">
                        {deadlineReportQuery.data.data.weeks_to_deadline}
                      </span>{" "}
                      weeks to deadline ({new Date(
                        deadlineReportQuery.data.data.trade_deadline!,
                      ).toLocaleDateString()}).
                    </p>
                    <div className="hawk-scroll overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-default-500">
                            <th className="pb-1">Team</th>
                            <th className="pb-1">Role</th>
                            <th className="pb-1">Window</th>
                            <th className="pb-1 text-right">Playoff value</th>
                          </tr>
                        </thead>
                        <tbody>
                          {deadlineReportQuery.data.data.teams.map((team) => (
                            <DeadlineTeamRow
                              key={team.espn_team_id}
                              team={team}
                            />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </>
            )}
          </SectionCard>
        </div>
      )}

      {tab === "curation" && (
        <div className="flex flex-col gap-4">
          {/* Handcuff map (C7) — the curated starter -> direct-backup table */}
          <SectionCard
            title={
              <>
                <FiShield className="inline mb-1 mr-1" />
                Handcuffs
              </>
            }
            right={
              <Button
                disabled={isSeedingHandcuffs}
                size="sm"
                onClick={handleSeedHandcuffs}
              >
                {isSeedingHandcuffs ? <Spinner size="sm" /> : "Seed missing pairs"}
              </Button>
            }
          >
            <p className="text-sm text-default-500">
              Curated starter → direct-backup map. Rows this user deletes or
              repoints stay that way — re-seeding only fills in what&apos;s
              missing.
            </p>

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
              <h4 className="text-sm font-bold text-default-500">
                Curated map
              </h4>

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
                <div className="hawk-scroll overflow-x-auto">
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
                              <FiRefreshCw className="hidden" />
                              ✕
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </SectionCard>

          {/* Beat-writer directory (D1) */}
          <SectionCard
            title={
              <>
                <FiUsers className="inline mb-1 mr-1" />
                Beat Writers
              </>
            }
            right={
              <Button
                disabled={isSeedingWriters}
                size="sm"
                onClick={handleSeedWriters}
              >
                {isSeedingWriters ? <Spinner size="sm" /> : "Seed missing teams"}
              </Button>
            }
          >
            <p className="text-sm text-default-500">
              Curated team → beat-writer directory. Rows this user deletes or
              repoints stay that way — re-seeding only fills in what&apos;s
              missing.
            </p>

            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs">
                Team
                <input
                  className={`${selectClass} w-16`}
                  placeholder="SEA"
                  value={writerForm.nflTeam}
                  onChange={(e) =>
                    setWriterForm({ ...writerForm, nflTeam: e.target.value })
                  }
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                Writer
                <input
                  className={`${selectClass} w-40`}
                  placeholder="Writer name"
                  value={writerForm.writerName}
                  onChange={(e) =>
                    setWriterForm({ ...writerForm, writerName: e.target.value })
                  }
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                Outlet
                <input
                  className={`${selectClass} w-40`}
                  placeholder="Outlet"
                  value={writerForm.outlet}
                  onChange={(e) =>
                    setWriterForm({ ...writerForm, outlet: e.target.value })
                  }
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                Note
                <input
                  className={`${selectClass} w-40`}
                  placeholder="optional"
                  value={writerForm.note}
                  onChange={(e) =>
                    setWriterForm({ ...writerForm, note: e.target.value })
                  }
                />
              </label>
              <Button
                color="primary"
                disabled={
                  isSavingWriter ||
                  !writerForm.nflTeam.trim() ||
                  !writerForm.writerName.trim() ||
                  !writerForm.outlet.trim()
                }
                size="sm"
                onClick={handleSaveWriter}
              >
                {isSavingWriter ? <Spinner size="sm" /> : "Save"}
              </Button>
            </div>
            {writerMessage && (
              <p className="text-sm text-default-500">{writerMessage}</p>
            )}

            {writersQuery.isLoading || !writersQuery.data ? (
              <Spinner />
            ) : (
              <div className="hawk-scroll overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-default-500">
                      <th className="pb-1">Team</th>
                      <th className="pb-1">Writer</th>
                      <th className="pb-1">Outlet</th>
                      <th className="pb-1">Note</th>
                      <th className="pb-1">Source</th>
                      <th className="pb-1" />
                    </tr>
                  </thead>
                  <tbody>
                    {writersQuery.data.writers.map((writer) => (
                      <tr
                        key={writer.nfl_team}
                        className="border-t border-default-100"
                      >
                        <td className="py-1">{writer.nfl_team}</td>
                        <td className="py-1">{writer.writer_name}</td>
                        <td className="py-1">{writer.outlet}</td>
                        <td className="py-1 text-default-400">
                          {writer.note ?? "—"}
                        </td>
                        <td className="py-1 text-default-400">{writer.source}</td>
                        <td className="py-1 text-right">
                          <button
                            className="text-danger-500 hover:text-danger-700"
                            title={`Delete ${writer.nfl_team}`}
                            type="button"
                            onClick={() => handleDeleteWriter(writer.nfl_team)}
                          >
                            ✕
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </SectionCard>

          {/* Manual Grok bridge (D3) */}
          <SectionCard
            title={
              <>
                <FiMessageCircle className="inline mb-1 mr-1" />
                Grok Bridge (manual research)
              </>
            }
          >
            <p className="text-sm text-default-500">
              Generate a prompt, run it yourself in a free xAI account, paste
              the answer back. Nothing here calls an LLM, and nothing saved
              here is ever auto-trusted.
            </p>

            <div className="flex flex-wrap items-end gap-2">
              <label className="flex flex-col gap-1 text-xs">
                Player
                <input
                  className={`${selectClass} w-40`}
                  placeholder="Player name"
                  value={grokPlayer}
                  onChange={(e) => setGrokPlayer(e.target.value)}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs">
                Kind
                <select
                  className={selectClass}
                  value={grokKind}
                  onChange={(e) =>
                    setGrokKind(e.target.value as typeof grokKind)
                  }
                >
                  <option value="beat_check">Beat check (last 48h)</option>
                  <option value="injury_timeline">Injury timeline</option>
                  <option value="usage_context">Usage / role context</option>
                </select>
              </label>
              <Button
                disabled={isGeneratingPrompt || !grokPlayer.trim()}
                size="sm"
                onClick={handleGenerateGrokPrompt}
              >
                {isGeneratingPrompt ? <Spinner size="sm" /> : "Generate prompt"}
              </Button>
            </div>
            {grokMessage && (
              <p className="text-sm text-default-500">{grokMessage}</p>
            )}

            {grokPromptData && (
              <div className="flex flex-col gap-1">
                <h4 className="text-sm font-bold text-default-500">
                  Prompt (copy into your xAI account)
                  {grokPromptData.nfl_team && ` — ${grokPromptData.nfl_team}`}
                </h4>
                <textarea
                  readOnly
                  className={`${selectClass} w-full h-40 font-mono text-xs`}
                  value={grokPromptData.prompt_text}
                />
              </div>
            )}

            <div className="flex flex-col gap-1">
              <h4 className="text-sm font-bold text-default-500">
                Paste Grok&apos;s answer
              </h4>
              <textarea
                className={`${selectClass} w-full h-32 font-mono text-xs`}
                placeholder="Paste the full answer, including the ---GROK-NOTE--- block"
                value={grokRawText}
                onChange={(e) => {
                  setGrokRawText(e.target.value);
                  setGrokPreview(null);
                }}
              />
              <div className="flex gap-2">
                <Button
                  disabled={isParsingNote || !grokRawText.trim()}
                  size="sm"
                  onClick={handlePreviewGrokPaste}
                >
                  {isParsingNote ? <Spinner size="sm" /> : "Preview parse"}
                </Button>
              </div>
            </div>

            {grokPreview && (
              <div className="flex flex-col gap-2 rounded-medium border border-default-200 p-3">
                <div className="flex flex-wrap gap-2 text-xs">
                  <span
                    className={`rounded-full px-2 py-0.5 ${
                      grokPreview.parsed_block
                        ? "bg-success-100 text-success-700"
                        : "bg-warning-100 text-warning-700"
                    }`}
                  >
                    {grokPreview.parsed_block
                      ? "block parsed"
                      : "no block found — fill in manually"}
                  </span>
                  {grokPreview.stale_risk && (
                    <span className="rounded-full bg-warning-100 px-2 py-0.5 text-warning-700">
                      undated or stale sources
                    </span>
                  )}
                  {grokPreview.conflicts.map((conflict, i) => (
                    <span
                      key={i}
                      className="rounded-full bg-danger-100 px-2 py-0.5 text-danger-700"
                    >
                      {conflict}
                    </span>
                  ))}
                  <span className="rounded-full bg-default-100 px-2 py-0.5 text-default-500">
                    manual Grok research — unverified
                  </span>
                </div>

                <div className="flex flex-wrap items-end gap-2">
                  <label className="flex flex-col gap-1 text-xs">
                    Status signal
                    <select
                      className={selectClass}
                      value={manualStatusSignal}
                      onChange={(e) => setManualStatusSignal(e.target.value)}
                    >
                      <option value="">— choose —</option>
                      <option value="upgrade">Upgrade</option>
                      <option value="downgrade">Downgrade</option>
                      <option value="unchanged">Unchanged</option>
                      <option value="unclear">Unclear</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-xs">
                    Summary
                    <input
                      className={`${selectClass} w-64`}
                      value={manualSummary}
                      onChange={(e) => setManualSummary(e.target.value)}
                    />
                  </label>
                  <Button
                    color="primary"
                    disabled={isSavingNote || !grokPlayer.trim() || !grokRawText.trim()}
                    size="sm"
                    onClick={handleSaveGrokNote}
                  >
                    {isSavingNote ? <Spinner size="sm" /> : "Save note"}
                  </Button>
                </div>
              </div>
            )}

            {grokPlayer.trim() && (
              <div className="flex flex-col gap-1">
                <h4 className="text-sm font-bold text-default-500">
                  Saved notes for {grokPlayer.trim()}
                </h4>
                {notesQuery.isLoading || !notesQuery.data ? (
                  <Spinner />
                ) : notesQuery.data.notes.length === 0 ? (
                  <p className="text-sm text-default-500">No notes saved yet.</p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {notesQuery.data.notes.map((note) => (
                      <li
                        key={note.id}
                        className="flex flex-col gap-1 border-t border-default-100 pt-2 text-sm"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-bold">
                            {note.kind} — week {note.week}
                            {note.status_signal && ` — ${note.status_signal}`}
                          </span>
                          <button
                            className="text-danger-500 hover:text-danger-700"
                            title="Delete note"
                            type="button"
                            onClick={() => deletePlayerNote({ noteId: note.id })}
                          >
                            ✕
                          </button>
                        </div>
                        {note.summary && <p>{note.summary}</p>}
                        <div className="flex flex-wrap gap-2 text-xs text-default-500">
                          {note.stale_risk && (
                            <span className="text-warning-600">stale risk</span>
                          )}
                          {note.conflicts.map((conflict, i) => (
                            <span key={i} className="text-danger-600">
                              {conflict}
                            </span>
                          ))}
                          <span>unverified — manual research</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </SectionCard>
        </div>
      )}
    </section>
  );
}
