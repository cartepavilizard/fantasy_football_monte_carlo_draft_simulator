"use client";

import { useEffect, useMemo, useState } from "react";
import NextLink from "next/link";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";
import {
  FiAlertTriangle,
  FiArrowRight,
  FiCheckCircle,
  FiCopy,
  FiShuffle,
} from "react-icons/fi";

import {
  useCountersTradeMutation,
  useEvaluateTradeMutation,
  useGetOverviewQuery,
  useGetRosterQuery,
  useLazyGetTradeMessageQuery,
} from "@/api/services/inseason";
import { title, subtitle } from "@/components/primitives";
import { EmptyStateHawk } from "@/components/mascots";
import {
  InSeasonOverviewEntry,
  LeagueTeamInfo,
  RosterSlotEntry,
  TradeCounter,
  TradeEvaluation,
  TradePlayerValue,
  TradeVerdict,
} from "@/types";

const cardClass =
  "flex flex-col gap-2 w-full border-medium rounded-large p-4 border-default";
const selectClass =
  "bg-transparent border-medium border-default rounded-medium px-3 py-2 text-sm";

// The standard freshness/warnings banner — identical to the in-season page's
// (kept inline per the codebase convention; every cached view renders it).
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

function verdictLabel(verdict: TradeVerdict): string {
  switch (verdict) {
    case "fair":
      return "Fair on value";
    case "favors_a":
      return "Favors you (A)";
    case "favors_b":
      return "Favors them (B)";
  }
}

function verdictClass(verdict: TradeVerdict): string {
  switch (verdict) {
    case "fair":
      return "bg-success-100 text-success-700 border-success-300 dark:bg-success-950/40 dark:text-success-400";
    case "favors_a":
      return "bg-primary-100 text-primary-700 border-primary-300 dark:bg-primary-950/40 dark:text-primary-400";
    case "favors_b":
      return "bg-danger-100 text-danger-700 border-danger-300 dark:bg-danger-950/40 dark:text-danger-400";
  }
}

// One side's outgoing pieces with their E1 market value + per-week line.
function SideValueList({
  label,
  values,
}: {
  label: string;
  values: TradePlayerValue[];
}) {
  if (values.length === 0) {
    return (
      <div>
        <h4 className="text-sm font-bold text-default-500">{label}</h4>
        <p className="text-sm text-default-400">— nothing —</p>
      </div>
    );
  }

  return (
    <div>
      <h4 className="text-sm font-bold text-default-500">
        {label} · {values.reduce((sum, v) => sum + v.value, 0).toFixed(1)} ROS pts
      </h4>
      <ul className="flex flex-col gap-1 text-sm">
        {values.map((v) => (
          <li key={v.player_id} className="flex items-center justify-between">
            <span>
              {v.name}
              <span className="text-default-400">
                {" "}
                {v.position ?? "—"} · {v.nfl_team ?? "—"}
              </span>
              {v.injury_status && (
                <span className="text-warning-600"> ({v.injury_status})</span>
              )}
            </span>
            <span className="text-right">
              <span className="font-bold">{v.value.toFixed(1)}</span>
              <span className="text-default-400">
                {" "}
                ({v.per_week.toFixed(1)}/wk)
              </span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Both lenses of one evaluation: market value (fairness) and roster fit
// (does it help each side). The two numbers are presented separately, never
// merged — the summary copy carries the plain-terms read.
function EvaluationPanel({ evaluation }: { evaluation: TradeEvaluation }) {
  return (
    <div className="flex flex-col gap-3 rounded-medium border border-default-200 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex text-xs font-bold px-2 py-0.5 rounded-full border ${verdictClass(
            evaluation.verdict,
          )}`}
        >
          {verdictLabel(evaluation.verdict)}
        </span>
        <span className="text-sm text-default-500">
          Market gap{" "}
          <span className="font-bold">
            {evaluation.market_gap > 0 ? "+" : ""}
            {evaluation.market_gap.toFixed(1)}
          </span>{" "}
          (fair band ±{evaluation.fair_bound.toFixed(1)})
        </span>
        <span className="text-sm text-default-400">
          · week {evaluation.week}, {evaluation.weeks_remaining}w left
        </span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <SideValueList
          label={`${evaluation.teams.a.name ?? "Team A"} sends`}
          values={evaluation.sends_a}
        />
        <SideValueList
          label={`${evaluation.teams.b.name ?? "Team B"} sends`}
          values={evaluation.sends_b}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
        <div>
          <h4 className="text-sm font-bold text-default-500">Roster fit</h4>
          <p>
            {evaluation.teams.a.name ?? "A"}:{" "}
            <span
              className={`font-bold ${
                evaluation.fit_delta_a >= 0 ? "text-success" : "text-danger"
              }`}
            >
              {evaluation.fit_delta_a > 0 ? "+" : ""}
              {evaluation.fit_delta_a.toFixed(1)} ROS
            </span>{" "}
            <span className="text-default-400">
              ({evaluation.fit_per_week_a > 0 ? "+" : ""}
              {evaluation.fit_per_week_a.toFixed(1)}/wk)
            </span>
          </p>
          <p>
            {evaluation.teams.b.name ?? "B"}:{" "}
            <span
              className={`font-bold ${
                evaluation.fit_delta_b >= 0 ? "text-success" : "text-danger"
              }`}
            >
              {evaluation.fit_delta_b > 0 ? "+" : ""}
              {evaluation.fit_delta_b.toFixed(1)} ROS
            </span>{" "}
            <span className="text-default-400">
              ({evaluation.fit_per_week_b > 0 ? "+" : ""}
              {evaluation.fit_per_week_b.toFixed(1)}/wk)
            </span>
          </p>
        </div>
        {evaluation.roster_size_note && (
          <div>
            <h4 className="text-sm font-bold text-default-500">
              Roster size
            </h4>
            <p className="text-xs text-default-500">
              {evaluation.roster_size_note}
            </p>
          </div>
        )}
      </div>

      <p className="text-sm">{evaluation.summary}</p>

      {evaluation.warnings.length > 0 && (
        <div className="flex flex-col gap-1">
          {evaluation.warnings.map((warning, i) => (
            <p
              key={i}
              className="flex items-start gap-2 text-xs text-warning-700 dark:text-warning-400"
            >
              <FiAlertTriangle className="mt-0.5 shrink-0" />
              <span>{warning}</span>
            </p>
          ))}
        </div>
      )}

      {/* stack_flags is an optional F1 decoration the backend may attach in
          the future; rendered defensively when present, never crashes. */}
      {evaluation.stack_flags != null && (
        <p className="text-xs text-default-400">
          Stack context attached to this evaluation.
        </p>
      )}
    </div>
  );
}

function CounterRow({ counter }: { counter: TradeCounter }) {
  const move = counter.move;
  const moveLabel =
    move.type === "add"
      ? `Add ${move.player_name} to ${move.team === "a" ? "your" : "their"} side`
      : move.type === "remove"
        ? `Drop ${move.player_name} from ${move.team === "a" ? "your" : "their"} side`
        : `Swap ${move.player_out_name} for ${move.player_name} on ${move.team === "a" ? "your" : "their"} side`;

  return (
    <li className="flex flex-col gap-2 rounded-medium border border-default-200 p-3">
      <div className="flex items-center gap-2">
        <FiShuffle className="text-default-400" />
        <span className="text-sm font-bold">{moveLabel}</span>
      </div>
      <p className="text-sm text-default-500">{counter.rationale}</p>
      <EvaluationPanel evaluation={counter.evaluation} />
    </li>
  );
}

// A multi-select roster list — one checkbox per player. The roster endpoint
// already gives us player_ids and names; we select on player_id (the unit
// the trade proposal body carries).
function RosterPicker({
  entries,
  selected,
  onToggle,
}: {
  entries: RosterSlotEntry[];
  selected: Set<number>;
  onToggle: (playerId: number) => void;
}) {
  if (entries.length === 0) {
    return <p className="text-sm text-default-500">No cached roster yet.</p>;
  }

  return (
    <ul className="flex flex-col gap-1 max-h-72 overflow-auto">
      {entries.map((entry) => {
        const checked = selected.has(entry.player_id);

        return (
          <li key={entry.player_id}>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                checked={checked}
                type="checkbox"
                onChange={() => onToggle(entry.player_id)}
              />
              <span className={checked ? "font-bold" : ""}>
                {entry.player_name}
              </span>
              <span className="text-default-400">
                {entry.position ?? "—"} · {entry.nfl_team ?? "—"} ·{" "}
                {entry.lineup_slot}
              </span>
              {entry.injury_status && (
                <span className="text-warning-600">
                  ({entry.injury_status})
                </span>
              )}
            </label>
          </li>
        );
      })}
    </ul>
  );
}

export default function TradeRoomPage({
  params,
}: {
  params: { leagueId: string };
}) {
  const leagueId = Number(params.leagueId);
  const { data: overview, isLoading: overviewLoading } = useGetOverviewQuery();

  const selectedEntry: InSeasonOverviewEntry | undefined = useMemo(
    () =>
      overview?.leagues.find(
        (entry) => entry.league.espn_league_id === leagueId,
      ),
    [overview, leagueId],
  );

  const teams: LeagueTeamInfo[] = selectedEntry?.league.teams ?? [];

  const [teamAId, setTeamAId] = useState<number | null>(null);
  const [teamBId, setTeamBId] = useState<number | null>(null);

  useEffect(() => {
    if (teamAId === null && teams.length > 0) setTeamAId(teams[0].espn_team_id);
  }, [teams, teamAId]);
  useEffect(() => {
    if (teamBId === null && teams.length > 1) setTeamBId(teams[1].espn_team_id);
  }, [teams, teamBId]);

  const teamName = (id: number | null) =>
    teams.find((t) => t.espn_team_id === id)?.name ?? `Team ${id}`;

  const rosterAQuery = useGetRosterQuery(
    { leagueId, teamId: teamAId ?? 0 },
    { skip: teamAId === null },
  );
  const rosterBQuery = useGetRosterQuery(
    { leagueId, teamId: teamBId ?? 0 },
    { skip: teamBId === null },
  );

  const [sendsA, setSendsA] = useState<Set<number>>(new Set());
  const [sendsB, setSendsB] = useState<Set<number>>(new Set());

  // Reset selections when either team changes — a stale selection across a
  // team switch would reference players no longer on that roster.
  useEffect(() => {
    setSendsA(new Set());
  }, [teamAId]);
  useEffect(() => {
    setSendsB(new Set());
  }, [teamBId]);

  const toggle = (set: Set<number>, setSetter: (s: Set<number>) => void) => (
    playerId: number,
  ) => {
    const next = new Set(set);

    if (next.has(playerId)) next.delete(playerId);
    else next.add(playerId);
    setSetter(next);
  };

  const [evaluateTrade, { isLoading: isEvaluating }] = useEvaluateTradeMutation();
  const [countersTrade, { isLoading: isCountering }] = useCountersTradeMutation();
  const [fetchMessage, { data: messageData, isFetching: isFetchingMessage }] =
    useLazyGetTradeMessageQuery();

  const [evaluation, setEvaluation] = useState<TradeEvaluation | null>(null);
  const [counters, setCounters] = useState<TradeCounter[] | null>(null);
  const [countersNote, setCountersNote] = useState<string | null>(null);
  const [error, setError] = useState<string>("");

  const proposalReady =
    teamAId !== null &&
    teamBId !== null &&
    teamAId !== teamBId &&
    (sendsA.size > 0 || sendsB.size > 0);

  const resetResults = () => {
    setEvaluation(null);
    setCounters(null);
    setCountersNote(null);
    setError("");
  };

  const handleEvaluate = async () => {
    if (!proposalReady) return;
    resetResults();
    try {
      const result = await evaluateTrade({
        leagueId,
        team_a: teamAId!,
        team_b: teamBId!,
        sends_a: Array.from(sendsA),
        sends_b: Array.from(sendsB),
      }).unwrap();

      setEvaluation(result.data);
    } catch (e) {
      setError(
        (e as { data?: { detail?: string } })?.data?.detail ??
          "Evaluation failed. Is the backend reachable, and are the players on the right rosters?",
      );
    }
  };

  const handleCounters = async () => {
    if (!proposalReady) return;
    resetResults();
    try {
      const result = await countersTrade({
        leagueId,
        team_a: teamAId!,
        team_b: teamBId!,
        sends_a: Array.from(sendsA),
        sends_b: Array.from(sendsB),
      }).unwrap();

      setEvaluation(result.data.original);
      setCounters(result.data.counters);
      setCountersNote(result.data.note);
    } catch (e) {
      setError(
        (e as { data?: { detail?: string } })?.data?.detail ??
          "Counter search failed.",
      );
    }
  };

  const handleMessage = async () => {
    if (!proposalReady) return;
    try {
      await fetchMessage({
        leagueId,
        teamA: teamAId!,
        teamB: teamBId!,
        sendsA: Array.from(sendsA),
        sendsB: Array.from(sendsB),
      }).unwrap();
    } catch (e) {
      setError(
        (e as { data?: { detail?: string } })?.data?.detail ??
          "Message render failed.",
      );
    }
  };

  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    if (!messageData?.data.message) return;
    try {
      await navigator.clipboard.writeText(messageData.data.message);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard may be unavailable (e.g. non-secure context) — the
      // message stays visible in its textarea for manual copy.
    }
  };

  return (
    <section className="flex flex-col items-center justify-center gap-8">
      <div className="max-w-lg text-center">
        <h1 className={title()}>Trade room.</h1>
        <h2 className={subtitle()}>
          Build a proposal, grade it on both value and roster fit, pull fair
          counters, and render a friendly message to send. Every read is cached
          — no ESPN fetch from this page.
        </h2>
      </div>

      <NextLink
        className="text-sm text-primary underline"
        href="/inseason"
      >
        ← Back to in-season
      </NextLink>

      <div className={cardClass}>
        <h3 className="text-xl">Pick the two teams</h3>
        {overviewLoading ? (
          <Spinner />
        ) : teams.length < 2 ? (
          <div className="flex flex-col items-center gap-2 text-center">
            <EmptyStateHawk size={72} />
            <p className="text-sm text-default-500">
              This league needs at least two synced teams to build a trade.
              Sync the league from the in-season page first.
            </p>
          </div>
        ) : (
          <div className="flex flex-wrap items-end gap-4">
            <label className="flex flex-col gap-1 text-sm">
              Team A (you)
              <select
                className={selectClass}
                value={teamAId ?? ""}
                onChange={(e) => setTeamAId(Number(e.target.value))}
              >
                {teams.map((team) => (
                  <option key={team.espn_team_id} value={team.espn_team_id}>
                    {team.name} ({team.wins}-{team.losses}
                    {team.ties ? `-${team.ties}` : ""})
                  </option>
                ))}
              </select>
            </label>
            <FiArrowRight className="mb-2 text-default-400" />
            <label className="flex flex-col gap-1 text-sm">
              Team B (them)
              <select
                className={selectClass}
                value={teamBId ?? ""}
                onChange={(e) => setTeamBId(Number(e.target.value))}
              >
                {teams.map((team) => (
                  <option key={team.espn_team_id} value={team.espn_team_id}>
                    {team.name} ({team.wins}-{team.losses}
                    {team.ties ? `-${team.ties}` : ""})
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
        {selectedEntry && <StalenessBanner warnings={selectedEntry.warnings} />}
      </div>

      {teamAId !== null && teamBId !== null && teamAId !== teamBId && (
        <div className={cardClass}>
          <h3 className="text-xl">Build the proposal</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="flex flex-col gap-1">
              <h4 className="text-sm font-bold text-default-500">
                {teamName(teamAId)} sends
              </h4>
              {rosterAQuery.isLoading || !rosterAQuery.data ? (
                <Spinner />
              ) : rosterAQuery.data.data ? (
                <RosterPicker
                  entries={rosterAQuery.data.data.entries}
                  selected={sendsA}
                  onToggle={toggle(sendsA, setSendsA)}
                />
              ) : (
                <p className="text-sm text-default-500">
                  No cached roster for this team-week.
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1">
              <h4 className="text-sm font-bold text-default-500">
                {teamName(teamBId)} sends
              </h4>
              {rosterBQuery.isLoading || !rosterBQuery.data ? (
                <Spinner />
              ) : rosterBQuery.data.data ? (
                <RosterPicker
                  entries={rosterBQuery.data.data.entries}
                  selected={sendsB}
                  onToggle={toggle(sendsB, setSendsB)}
                />
              ) : (
                <p className="text-sm text-default-500">
                  No cached roster for this team-week.
                </p>
              )}
            </div>
          </div>

          {rosterAQuery.data && <StalenessBanner warnings={rosterAQuery.data.warnings} />}

          <div className="flex flex-wrap gap-2">
            <Button
              color="primary"
              disabled={!proposalReady || isEvaluating}
              onClick={handleEvaluate}
            >
              {isEvaluating ? <Spinner color="white" size="sm" /> : "Evaluate"}
            </Button>
            <Button
              disabled={!proposalReady || isCountering}
              onClick={handleCounters}
            >
              {isCountering ? <Spinner size="sm" /> : "Counters"}
            </Button>
            <Button
              disabled={!proposalReady || isFetchingMessage}
              onClick={handleMessage}
            >
              {isFetchingMessage ? <Spinner size="sm" /> : "Message"}
            </Button>
          </div>

          {error && (
            <p className="flex items-start gap-2 text-sm text-danger-600">
              <FiAlertTriangle className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </p>
          )}
        </div>
      )}

      {evaluation && (
        <div className={cardClass}>
          <h3 className="text-xl">Evaluation</h3>
          <EvaluationPanel evaluation={evaluation} />
        </div>
      )}

      {counters && (
        <div className={cardClass}>
          <h3 className="text-xl">
            <FiShuffle className="inline mb-1 mr-1" />
            Counterproposals
          </h3>
          {countersNote && (
            <p className="text-sm text-default-500">{countersNote}</p>
          )}
          {counters.length === 0 ? (
            <p className="text-sm text-default-500">
              No fair counter exists within one move of this proposal.
            </p>
          ) : (
            <ul className="flex flex-col gap-3">
              {counters.map((counter, i) => (
                <CounterRow key={i} counter={counter} />
              ))}
            </ul>
          )}
        </div>
      )}

      {messageData?.data && (
        <div className={cardClass}>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <h3 className="text-xl">Message</h3>
            <Button
              size="sm"
              startContent={<FiCopy />}
              onClick={handleCopy}
            >
              {copied ? "Copied!" : "Copy"}
            </Button>
          </div>
          <textarea
            readOnly
            className={`${selectClass} w-full h-48 text-sm`}
            value={messageData.data.message}
          />
        </div>
      )}
    </section>
  );
}
