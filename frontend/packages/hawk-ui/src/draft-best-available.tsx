import * as React from "react";

import { Player, PlayerTag } from "./types";
import { HawkCard, HawkCardHeader } from "./hawk-cards";
import { TagBadge, tagMeta } from "./draft-tag-badge";

// HAWK MODE Best Available — the left rail of the draft page composite.
// A single dense column: search box, All/Sleepers/My Guys/Avoids filter
// chips, then one compact row per undrafted player with a position color
// dot, name, a tabular ADP/rank metric, and a quick-draft "+" button.
//
// This is the presentational half. The connected half (the live
// useGetPlayersQuery fetch, the tag-filter query state, and the per-row
// TagControls that call the live tag/untag mutations) lives in
// frontend/components/draft-best-available.tsx, which supplies `players`
// already flattened/filtered/drafted-excluded and injects tag controls
// via `renderTagControls`.

const positions = ["qb", "rb", "wr", "te", "dst", "k"];

function positionDotColor(position: string): string {
  const p = position.toUpperCase();
  if (p === "QB") return "var(--pos-qb)";
  if (p === "RB") return "var(--pos-rb)";
  if (p === "WR") return "var(--pos-wr)";
  if (p === "TE") return "var(--pos-te)";
  if (p === "K") return "var(--pos-k)";
  if (p === "DST" || p === "DEF") return "var(--pos-dst)";
  return "var(--grey)";
}

// The composite's right-aligned tabular "pts" column. The Player model
// has no projected_points field exposed on the league player list, so
// we surface the next-best scalar: ADP (rounded), falling back to the
// consensus rank, then the position tier. Returns null when none are
// set (CSV-only players predate the rankings blend).
function playerMetric(player: Player): string | null {
  if (player.adp != null) return `ADP ${Math.round(player.adp)}`;
  if (player.consensus_rank != null)
    return `ECR ${Math.round(player.consensus_rank)}`;
  if (player.tier != null) return `T${player.tier}`;
  return null;
}

export interface BestAvailableProps {
  // Already flattened across positions, tag-filtered, and drafted-excluded
  // — the connected wrapper owns that query/derivation.
  players: Player[];
  searchFilter: string;
  setSearchFilter: (value: string) => void;
  tagFilter: PlayerTag | undefined;
  setTagFilter: (tag: PlayerTag | undefined) => void;
  // When the simulator team is on the clock and the Monte Carlo sim is
  // still running, drafting is paused (the original page disabled the
  // draft buttons in this state). The rail inherits the same gate.
  draftPaused: boolean;
  onDraft: (name: string) => void;
  // Per-row tag controls are connected (live tag/untag mutations), so the
  // wrapper injects them; omit for a read-only render (e.g. a design mock).
  renderTagControls?: (player: Player) => React.ReactNode;
}

export function BestAvailable({
  players,
  searchFilter,
  setSearchFilter,
  tagFilter,
  setTagFilter,
  draftPaused,
  onDraft,
  renderTagControls,
}: BestAvailableProps) {
  const visible = React.useMemo(() => {
    const q = searchFilter.trim().toLowerCase();
    if (q.length === 0) return players;
    return players.filter((player) =>
      player.name.toLowerCase().includes(q),
    );
  }, [players, searchFilter]);

  return (
    <HawkCard
      className="hawk-scroll"
      style={{
        maxHeight: "calc(100vh - 96px)",
        position: "sticky",
        top: "calc(var(--nav-h) + 16px)",
        overflowY: "auto",
      }}
    >
      <HawkCardHeader title="Best Available" />

      {/* Search box — matches the composite's compact ⌕ input row */}
      <div
        className="flex items-center gap-2 px-3"
        style={{
          background: "var(--surface-2)",
          borderBottom: "1px solid var(--border)",
          padding: "5px var(--sp-3)",
        }}
      >
        <span className="text-[color:var(--text-mute)]">⌕</span>
        <input
          placeholder="Search…"
          value={searchFilter}
          onChange={(e) => setSearchFilter(e.target.value)}
          className="w-full bg-transparent border-none outline-none font-body text-sm text-[color:var(--text)]"
        />
      </div>

      {/* All/Sleepers/My Guys/Avoids chips — the original page's tag
          filter, folded into the rail so the existing ?tag= query
          path stays the source of truth. */}
      <div
        className="flex flex-wrap items-center gap-1 px-3 py-2"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        {(
          [
            { label: "All", tag: undefined },
            { label: "Sleepers", tag: "sleeper" },
            { label: "My Guys", tag: "my_guy" },
            { label: "Avoids", tag: "avoid" },
          ] as { label: string; tag: PlayerTag | undefined }[]
        ).map(({ label, tag }) => {
          const active = tagFilter === tag;
          return (
            <button
              key={label}
              type="button"
              onClick={() => setTagFilter(tag)}
              className="font-head text-[10px] font-bold uppercase tracking-[0.04em]"
              style={{
                color: active ? "#04240a" : "var(--text-dim)",
                background: active ? "var(--green)" : "var(--surface-3)",
                border: "1px solid",
                borderColor: active ? "var(--green)" : "var(--border-2)",
                borderRadius: 100,
                padding: "2px 8px",
                cursor: "pointer",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* Dense player rows. Each row is the composite's 30px name/pts/+
          line; below it a thin tag strip keeps the per-player tag
          controls one tap away without inflating the row height. */}
      {visible.length === 0 ? (
        <div
          className="px-3 py-4 text-xs text-[color:var(--text-mute)]"
        >
          No players match.
        </div>
      ) : (
        visible.map((player) => {
          const metric = playerMetric(player);
          return (
            <div
              key={player.name}
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <div
                className="grid items-center gap-2 px-3"
                style={{
                  gridTemplateColumns: "1fr 56px 26px",
                  height: 30,
                  fontSize: "var(--fs-sm)",
                }}
              >
                <span className="flex min-w-0 items-center gap-1">
                  <span
                    style={{
                      width: 5,
                      height: 5,
                      borderRadius: "50%",
                      flexShrink: 0,
                      background: positionDotColor(player.position),
                    }}
                  />
                  <TagBadge tag={player.tag} />
                  <span
                    className="truncate"
                    style={{
                      textDecoration:
                        player.tag === "avoid" ? "line-through" : "none",
                      opacity: player.tag === "avoid" ? 0.55 : 1,
                    }}
                    title={player.name}
                  >
                    {player.name}
                  </span>
                </span>
                <span
                  className="text-right tabular-nums text-[color:var(--text-dim)]"
                  style={{ fontSize: "var(--fs-xs)" }}
                >
                  {metric ?? ""}
                </span>
                <button
                  type="button"
                  aria-label={`Draft ${player.name}`}
                  title={`Draft ${player.name}`}
                  disabled={draftPaused}
                  onClick={() => onDraft(player.name)}
                  className="font-head font-extrabold"
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: 3,
                    fontSize: 11,
                    background: draftPaused
                      ? "var(--surface-3)"
                      : "var(--green)",
                    color: draftPaused ? "var(--text-mute)" : "#04240a",
                    border: "none",
                    cursor: draftPaused ? "not-allowed" : "pointer",
                  }}
                >
                  ＋
                </button>
              </div>
              {renderTagControls?.(player)}
            </div>
          );
        })
      )}
    </HawkCard>
  );
}

export { tagMeta };
export { positions as bestAvailablePositions };
