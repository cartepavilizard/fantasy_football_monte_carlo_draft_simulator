"use client";

import * as React from "react";
import { FiShield } from "react-icons/fi";

import { HawkCard, HawkCardTitle, HawkChip } from "./hawk-cards";
import {
  HandcuffFlag,
  MatchupsData,
  PlayoffSosData,
  StreamingData,
  WeeklyMatchup,
} from "./types";

// Position token mapping — the kit's --pos-* tokens from globals.css.
// Used by the Streaming chip and any other position-tinted pill.
const POS_COLOR: Record<string, string> = {
  QB: "var(--pos-qb)",
  RB: "var(--pos-rb)",
  WR: "var(--pos-wr)",
  TE: "var(--pos-te)",
  K: "var(--pos-k)",
  DST: "var(--pos-dst)",
  D: "var(--pos-dst)",
  FLEX: "var(--pos-flex)",
};

function posChip(pos: string | null): React.ReactNode {
  if (!pos) return null;
  const color = POS_COLOR[pos.toUpperCase()] ?? "var(--surface-3)";

  return (
    <span
      className="font-head font-bold uppercase"
      style={{
        fontSize: 9,
        color: "#04240a",
        borderRadius: 2,
        padding: "1px 5px",
        background: color,
      }}
    >
      {pos}
    </span>
  );
}

// ---------- Matchups ----------
// The composite's Matchups card: a list of "opponent" rows with a strength
// chip. The cached matchups payload only carries team-vs-team + points (no
// defensive-strength metric), so the chip honestly reports the selected
// team's matchup side (HOME/AWAY) and result (W/L/T once decided). Other
// games this week collapse into compact "AWAY @ HOME" lines.
export const InseasonMatchupsCard: React.FC<{
  data: MatchupsData | undefined;
  loading: boolean;
  teamId: number | null;
  teamName: (id: number | null) => string;
  warnings: string[];
}> = ({ data, loading, teamId, teamName, warnings }) => {
  let body: React.ReactNode;

  if (loading || !data) {
    body = <span style={{ color: "var(--text-mute)" }}>Loading…</span>;
  } else if (data.matchups.length === 0) {
    body = <span style={{ color: "var(--text-mute)" }}>No cached matchups for this week.</span>;
  } else {
    const mine = data.matchups.find(
      (m) => m.home_team_id === teamId || m.away_team_id === teamId,
    );
    const others = data.matchups.filter((m) => m !== mine);

    body = (
      <div className="flex flex-col gap-1.5">
        {mine && (
          <MatchupRow
            matchup={mine}
            mineId={teamId}
            teamName={teamName}
            emphasize
          />
        )}
        {others.map((m, i) => (
          <MatchupRow
            key={i}
            matchup={m}
            mineId={teamId}
            teamName={teamName}
          />
        ))}
      </div>
    );
  }

  return (
    <HawkCard padded>
      <HawkCardTitle>Matchups{data ? ` — wk ${data.week}` : ""}</HawkCardTitle>
      {body}
      {warnings.length > 0 && (
        <p style={{ color: "var(--warn)", fontSize: "var(--fs-xs)", marginTop: 6 }}>
          {warnings[0]}
        </p>
      )}
    </HawkCard>
  );
};

const MatchupRow: React.FC<{
  matchup: WeeklyMatchup;
  mineId: number | null;
  teamName: (id: number | null) => string;
  emphasize?: boolean;
}> = ({ matchup, mineId, teamName, emphasize }) => {
  const isHome = matchup.home_team_id === mineId;
  const opponentId = isHome ? matchup.away_team_id : matchup.home_team_id;
  const myPts = isHome ? matchup.home_points : matchup.away_points;
  const oppPts = isHome ? matchup.away_points : matchup.home_points;
  const decided = matchup.winner != null;
  const won =
    matchup.winner === "home" && isHome
      ? true
      : matchup.winner === "away" && !isHome
        ? true
        : matchup.winner === "tie";

  // For the selected team's own game: render the composite's "opp + chip"
  // form (opponent name + a side/result chip). For other games: compact
  // "AWAY @ HOME" line with a small score chip.
  if (emphasize) {
    return (
      <div className="flex items-center gap-2">
        <span className="flex-1 truncate" style={{ fontSize: "var(--fs-sm)" }}>
          vs {teamName(opponentId)}
        </span>
        <HawkChip
          tone={
            !decided ? "neutral" : won ? "green" : matchup.winner === "tie" ? "warn" : "loss"
          }
        >
          {decided
            ? matchup.winner === "tie"
              ? `T ${myPts.toFixed(0)}-${oppPts.toFixed(0)}`
              : won
                ? `W ${myPts.toFixed(0)}-${oppPts.toFixed(0)}`
                : `L ${myPts.toFixed(0)}-${oppPts.toFixed(0)}`
            : isHome
              ? "HOME"
              : "AWAY"}
        </HawkChip>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2" style={{ color: "var(--text-mute)" }}>
      <span className="flex-1 truncate" style={{ fontSize: "var(--fs-sm)" }}>
        {teamName(matchup.away_team_id)} @ {teamName(matchup.home_team_id)}
      </span>
      <span style={{ fontSize: "var(--fs-xs)" }} className="tabular-nums">
        {matchup.away_points.toFixed(0)}-{matchup.home_points.toFixed(0)}
      </span>
    </div>
  );
};

// ---------- Playoff SOS ----------
// The composite's Playoff SOS mini table: rows = top-N teams at the
// selected position, columns = playoff weeks, each cell = the multiplier
// from C2's matchup tilt, color-coded hard/easy. A bye week renders as a
// muted "bye" cell.
function sosCellColor(mult: number): string {
  if (mult >= 1.15) return "var(--loss)";
  if (mult < 0.95) return "var(--green)";
  return "var(--warn)";
}

export const InseasonPlayoffSosCard: React.FC<{
  data: PlayoffSosData | undefined;
  loading: boolean;
  position: string;
  onPositionChange: (p: string) => void;
  positions: readonly string[];
}> = ({ data, loading, position, onPositionChange, positions }) => {
  let body: React.ReactNode;

  if (loading || !data) {
    body = <span style={{ color: "var(--text-mute)" }}>Loading…</span>;
  } else {
    const teams = Object.entries(data.positions[position] ?? {}).sort(
      ([, a], [, b]) => a.rank - b.rank,
    );
    const weeks = data.weeks;
    const top = teams.slice(0, 4);

    if (top.length === 0) {
      body = <span style={{ color: "var(--text-mute)" }}>No SOS data yet.</span>;
    } else {
      body = (
        <div
          className="grid"
          style={{
            gridTemplateColumns: `44px repeat(${weeks.length}, 1fr)`,
            gap: 4,
            fontSize: 10,
          }}
        >
          <span className="font-head font-bold uppercase text-[var(--text-mute)]">
            TEAM
          </span>
          {weeks.map((w) => (
            <span
              key={w}
              className="font-head font-bold uppercase text-center text-[var(--text-mute)]"
            >
              {w}
            </span>
          ))}
          {top.map(([team, entry]) => {
            const byWeek = new Map(entry.opponents.map((o) => [o.week, o]));

            return (
              <React.Fragment key={team}>
                <span
                  className="font-head font-bold"
                  style={{ alignSelf: "center", fontSize: 10 }}
                >
                  {team}
                </span>
                {weeks.map((w) => {
                  const o = byWeek.get(w);
                  if (!o) {
                    return (
                      <span
                        key={w}
                        className="text-center"
                        style={{
                          fontSize: 9,
                          padding: "2px 0",
                          color: "var(--text-mute)",
                          background: "var(--surface-3)",
                          borderRadius: 3,
                        }}
                      >
                        bye
                      </span>
                    );
                  }

                  return (
                    <span
                      key={w}
                      className="text-center font-bold"
                      style={{
                        fontSize: 9,
                        padding: "2px 0",
                        color: "#04240a",
                        background: sosCellColor(o.multiplier),
                        borderRadius: 3,
                      }}
                    >
                      {o.multiplier.toFixed(2)}
                    </span>
                  );
                })}
              </React.Fragment>
            );
          })}
        </div>
      );
    }
  }

  return (
    <HawkCard padded>
      <div className="flex items-center justify-between" style={{ marginBottom: "var(--sp-2)" }}>
        <HawkCardTitle>Playoff SOS</HawkCardTitle>
        <select
          value={position}
          onChange={(e) => onPositionChange(e.target.value)}
          className="font-head font-bold uppercase"
          style={{
            background: "var(--surface-2)",
            border: "1px solid var(--border-2)",
            color: "var(--text)",
            borderRadius: "var(--radius-sm)",
            padding: "2px 6px",
            fontSize: "var(--fs-xs)",
          }}
        >
          {positions.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      {body}
      {data?.note && (
        <p style={{ color: "var(--text-mute)", fontSize: "var(--fs-xs)", marginTop: 6 }}>
          {data.note}
        </p>
      )}
    </HawkCard>
  );
};

// ---------- Streaming ----------
// The composite's Streaming card: pos chip + name + adj pts (green), top
// recommendations from the existing streamingQuery.
export const InseasonStreamingCard: React.FC<{
  data: StreamingData | undefined;
  loading: boolean;
  warnings: string[];
  maxRows?: number;
}> = ({ data, loading, warnings, maxRows = 4 }) => {
  let body: React.ReactNode;

  if (loading || !data) {
    body = <span style={{ color: "var(--text-mute)" }}>Loading…</span>;
  } else if (data.recommendations.length === 0) {
    body = <span style={{ color: "var(--text-mute)" }}>No streaming options cached.</span>;
  } else {
    const rows = data.recommendations.slice(0, maxRows);
    body = (
      <div className="flex flex-col gap-1.5">
        {rows.map((rec) => (
          <div key={rec.player_id} className="flex items-center gap-2">
            {posChip(rec.position)}
            <span
              className="flex-1 truncate"
              style={{ fontSize: "var(--fs-sm)" }}
              title={`${rec.player_name} — ${rec.opponent ?? "bye"}`}
            >
              {rec.player_name}
            </span>
            <span
              className="font-bold"
              style={{ color: "var(--green)", fontSize: "var(--fs-sm)" }}
            >
              {rec.matchup_adjusted_points?.toFixed(1) ??
                 rec.projected_points?.toFixed(1) ??
                 "—"}
            </span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <HawkCard padded>
      <HawkCardTitle>Streaming{data ? ` — wk ${data.week}` : ""}</HawkCardTitle>
      {body}
      {warnings.length > 0 && (
        <p style={{ color: "var(--warn)", fontSize: "var(--fs-xs)", marginTop: 6 }}>
          {warnings[0]}
        </p>
      )}
    </HawkCard>
  );
};

// ---------- Handcuffs (compact) ----------
// The composite's Handcuffs card: "starter → cuff" + priority chip. The
// curated map CRUD itself stays in the Curation tab (it's much larger);
// this is the at-a-glance "who's available this week" list.
export const InseasonHandcuffsCard: React.FC<{
  flags: HandcuffFlag[] | undefined;
  loading: boolean;
}> = ({ flags, loading }) => {
  let body: React.ReactNode;

  if (loading || flags === undefined) {
    body = <span style={{ color: "var(--text-mute)" }}>Loading…</span>;
  } else if (flags.length === 0) {
    body = (
      <span style={{ color: "var(--text-mute)" }}>
        No rostered starter&apos;s cuff available this week.
      </span>
    );
  } else {
    const rows = flags.slice(0, 4);
    body = (
      <div className="flex flex-col gap-1.5">
        {rows.map((flag) => (
          <div key={flag.starter_name} className="flex items-center gap-2">
            <span
              className="flex-1 truncate"
              style={{ fontSize: "var(--fs-sm)" }}
              title={`${flag.starter_name} → ${flag.handcuff_name}`}
            >
              {flag.starter_name} → <b>{flag.handcuff_name}</b>
            </span>
            <HawkChip tone={flag.priority === "high" ? "loss" : "neutral"}>
              {flag.priority === "high" ? (
                <span className="inline-flex items-center gap-1">
                  <FiShield /> high
                </span>
              ) : (
                "normal"
              )}
            </HawkChip>
          </div>
        ))}
      </div>
    );
  }

  return (
    <HawkCard padded>
      <HawkCardTitle>Handcuffs</HawkCardTitle>
      {body}
    </HawkCard>
  );
};
