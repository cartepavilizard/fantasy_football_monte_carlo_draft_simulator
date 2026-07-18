"use client";

import { useEffect, useMemo, useState } from "react";
import NextLink from "next/link";

import {
  useCountersTradeMutation,
  useEvaluateTradeMutation,
  useGetDeadlineReportQuery,
  useGetOverviewQuery,
  useGetRosterQuery,
  useGetTradeWillingnessQuery,
  useLazyGetTradeMessageQuery,
} from "@/api/services/inseason";
import {
  InSeasonOverviewEntry,
  LeagueTeamInfo,
  TradeCounter,
  TradeEvaluation,
  TradePlayerValue,
  TradeWillingnessLabel,
} from "@/types";
import { TradeHeader } from "@/components/trade-header";
import { TradeProposalBuilder } from "@/components/trade-proposal-builder";
import { TradeVerdictCard } from "@/components/trade-verdict";
import { TradeMessageCard } from "@/components/trade-message";
import { TradeCountersCard } from "@/components/trade-counters";

// Section header — the composite's "③ Trade Room" green uppercase head-font
// label that opens the section.
function SectionHeader() {
  return (
    <div
      className="font-head font-bold uppercase"
      style={{
        letterSpacing: "0.05em",
        fontSize: "var(--fs-md)",
        color: "var(--green)",
        marginBottom: "var(--sp-2)",
      }}
    >
      ③ Trade Room
    </div>
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

  // E3 trade willingness for the league — drives the partner-name label.
  const tradeWillingnessQuery = useGetTradeWillingnessQuery(
    { leagueId },
    { skip: !overview },
  );
  // E8 deadline report — drives the Buy/Sell Window chip top-right.
  const deadlineReportQuery = useGetDeadlineReportQuery(
    { leagueId },
    { skip: !overview },
  );

  const partnerWillingness: TradeWillingnessLabel | null = useMemo(() => {
    if (!tradeWillingnessQuery.data?.data || teamBId === null) return null;
    const owner = tradeWillingnessQuery.data.data.owners.find(
      (o) => o.team_id === teamBId,
    );
    return owner ? owner.trade_willingness.willingness : null;
  }, [tradeWillingnessQuery.data, teamBId]);

  const partnerDeadline = useMemo(() => {
    if (!deadlineReportQuery.data?.data || teamBId === null) return null;
    return (
      deadlineReportQuery.data.data.teams.find(
        (t) => t.espn_team_id === teamBId,
      ) ?? null
    );
  }, [deadlineReportQuery.data, teamBId]);

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

  // E1 player values are returned inside each evaluation's sends_a/sends_b
  // (TradePlayerValue[]). There is no frontend RTK Query hook for the
  // backend's player_values GET, and the hard rules forbid inventing one, so
  // we accumulate every value we have seen into a lookup that the proposal
  // builder renders next to each player row — matching the composite's
  // per-row E1 value. Values populate as Evaluate / Counters / Message run.
  const valueMap = useMemo(() => {
    const m = new Map<number, TradePlayerValue>();
    const ingest = (ev: TradeEvaluation | null | undefined) => {
      if (!ev) return;
      for (const v of ev.sends_a) m.set(v.player_id, v);
      for (const v of ev.sends_b) m.set(v.player_id, v);
    };
    ingest(evaluation);
    if (counters) {
      for (const c of counters) ingest(c.evaluation);
    }
    if (messageData?.data) ingest(messageData.data.evaluation);
    return m;
  }, [evaluation, counters, messageData]);

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
      // message stays visible in the card for manual copy.
    }
  };

  const rosterA = rosterAQuery.data?.data?.entries ?? null;
  const rosterB = rosterBQuery.data?.data?.entries ?? null;
  const rosterWarnings = rosterAQuery.data?.warnings ?? [];

  const twoTeamsPicked =
    teamAId !== null && teamBId !== null && teamAId !== teamBId;

  return (
    <section className="flex flex-col gap-4 w-full">
      <SectionHeader />

      <NextLink
        className="text-sm text-[color:var(--green)] underline w-fit"
        href="/inseason"
      >
        ← Back to in-season
      </NextLink>

      <TradeHeader
        teams={teams}
        teamAId={teamAId}
        teamBId={teamBId}
        onTeamAChange={setTeamAId}
        onTeamBChange={setTeamBId}
        overviewLoading={overviewLoading}
        partnerWillingness={partnerWillingness}
        partnerWindow={partnerDeadline?.window ?? null}
        inWindow={deadlineReportQuery.data?.data?.in_window ?? false}
        weeksToDeadline={deadlineReportQuery.data?.data?.weeks_to_deadline ?? null}
        warnings={selectedEntry?.warnings ?? []}
      />

      {twoTeamsPicked && (
        <div
          className="grid gap-3 items-start"
          style={{ gridTemplateColumns: "1fr" }}
        >
          <div
            className="grid gap-3 items-start trade-room-body"
            aria-label="Trade Room body: Proposal Builder, Verdict, Message, Counterproposals"
          >
            {/* Proposal Builder — You Give / swap glyph / You Get, with each
                player's E1 value and the Evaluate / Counters / Message CTAs. */}
            <section
              aria-label="Proposal Builder: You Give / You Get"
              className="min-w-0"
            >
              <TradeProposalBuilder
                teamAName={teamName(teamAId)}
                teamBName={teamName(teamBId)}
                rosterA={rosterA}
                rosterB={rosterB}
                rosterALoading={rosterAQuery.isLoading}
                rosterBLoading={rosterBQuery.isLoading}
                sendsA={sendsA}
                sendsB={sendsB}
                valueMap={valueMap}
                onToggleA={toggle(sendsA, setSendsA)}
                onToggleB={toggle(sendsB, setSendsB)}
                proposalReady={proposalReady}
                onEvaluate={handleEvaluate}
                onCounters={handleCounters}
                onMessage={handleMessage}
                isEvaluating={isEvaluating}
                isCountering={isCountering}
                isFetchingMessage={isFetchingMessage}
                error={error}
                rosterWarnings={rosterWarnings}
              />
            </section>

            <div className="flex flex-col gap-3">
              <TradeVerdictCard
                evaluation={evaluation}
                isEvaluating={isEvaluating}
              />
              <TradeMessageCard
                message={messageData?.data.message ?? null}
                copied={copied}
                onCopy={handleCopy}
                isFetching={isFetchingMessage}
              />
            </div>
          </div>

          <TradeCountersCard counters={counters} note={countersNote} />
        </div>
      )}

    </section>
  );
}
