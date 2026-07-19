"use client";

import * as React from "react";

import { useGetPlayersQuery } from "@/api/services/league";
import { Player, PlayerTag, Players } from "@/types";
import {
  BestAvailable as BestAvailableView,
  tagMeta,
} from "@hawkmode/ui/draft-best-available";
import { TagControls } from "@/components/draft-tag-badge";

// Connected wrapper for the Hawk UI presentational BestAvailable: owns the
// live useGetPlayersQuery fetch, the tag-filter query state, and injects
// TagControls (which call the live tag/untag mutations) per row — none of
// that is portable, so it stays app-side. The presentational half lives in
// frontend/packages/hawk-ui/src/draft-best-available.tsx.

const positions = ["qb", "rb", "wr", "te", "dst", "k"];

export interface BestAvailableProps {
  leagueId: string;
  draftId: string;
  searchFilter: string;
  setSearchFilter: (value: string) => void;
  draftPaused: boolean;
  onDraft: (name: string) => void;
  onRefresh: () => void;
}

export function BestAvailable({
  leagueId,
  draftId,
  searchFilter,
  setSearchFilter,
  draftPaused,
  onDraft,
  onRefresh,
}: BestAvailableProps) {
  // Local tag filter drives the server-side ?tag= query (same hook the
  // original page used), so the rail shows the same filtered set the
  // old "All / Sleepers / My Guys / Avoids" chips toggled.
  const [tagFilter, setTagFilter] = React.useState<PlayerTag | undefined>(
    undefined,
  );

  const { data: filteredPlayers, refetch: refetchFilteredPlayers } =
    useGetPlayersQuery({ id: leagueId, tag: tagFilter }, { skip: !leagueId });

  // Refresh the rail whenever a pick lands so freshly-drafted players
  // drop out and the next best surface to the top.
  React.useEffect(() => {
    refetchFilteredPlayers();
  }, [draftId, refetchFilteredPlayers]);

  // Flatten all positions into one ranked list (already server-ordered
  // within each position). The composite renders one long column, not
  // six grouped columns.
  const allUndrafted: Player[] = React.useMemo(() => {
    const source: Players = filteredPlayers ?? {
      qb: [],
      rb: [],
      wr: [],
      te: [],
      dst: [],
      k: [],
    };
    const flat: Player[] = [];
    positions.forEach((position) => {
      (source[position as keyof Players] ?? []).forEach((player) => {
        if (player.drafted === false) flat.push(player);
      });
    });
    return flat;
  }, [filteredPlayers]);

  return (
    <BestAvailableView
      players={allUndrafted}
      searchFilter={searchFilter}
      setSearchFilter={setSearchFilter}
      tagFilter={tagFilter}
      setTagFilter={setTagFilter}
      draftPaused={draftPaused}
      onDraft={(name) => {
        onDraft(name);
        onRefresh();
      }}
      renderTagControls={(player) => (
        <div
          className="flex items-center justify-end gap-2 px-3 pb-1"
          style={{ fontSize: "var(--fs-xs)" }}
          title="Tag this player"
        >
          <span className="text-[color:var(--text-mute)] text-[10px] uppercase tracking-[0.04em]">
            Tag
          </span>
          <TagControls leagueId={leagueId} player={player} compact />
        </div>
      )}
    />
  );
}

// Re-export tagMeta so any other module that previously imported it
// from this file keeps working.
export { tagMeta };
