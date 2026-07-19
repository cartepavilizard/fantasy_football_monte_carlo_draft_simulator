"use client";

import { useLazyGetScarcityQuery } from "@/api/services/scarcity";
import { PlayerTag } from "@/types";
import { ScarcityPanel as ScarcityPanelView } from "@hawkmode/ui/draft-scarcity-panel";

// Connected wrapper for the Hawk UI presentational ScarcityPanel: owns the
// live useLazyGetScarcityQuery hook and its fetch trigger. The
// presentational half lives in
// frontend/packages/hawk-ui/src/draft-scarcity-panel.tsx.

export interface ScarcityPanelProps {
  draftId: string;
  draftComplete: boolean;
  playerTagByName: Record<string, PlayerTag | null | undefined>;
}

export function ScarcityPanel({
  draftId,
  draftComplete,
  playerTagByName,
}: ScarcityPanelProps) {
  const [
    fetchScarcity,
    { data: scarcityReport, isFetching: scarcityFetching, isError: scarcityError },
  ] = useLazyGetScarcityQuery();

  return (
    <ScarcityPanelView
      draftComplete={draftComplete}
      playerTagByName={playerTagByName}
      scarcityReport={scarcityReport}
      scarcityFetching={scarcityFetching}
      scarcityError={scarcityError}
      onFetch={() => fetchScarcity({ id: draftId, seconds: 10 })}
    />
  );
}
