import {
  FiMoon,
  FiSlash,
  FiStar,
  FiX,
} from "react-icons/fi";

import {
  useTagPlayerMutation,
  useUntagPlayerMutation,
} from "@/api/services/league";
import { Player, PlayerTag } from "@/types";

// One consistent icon+color per tag, reused wherever a tagged player's
// name appears: best-available rows, scarcity at-risk lists, the Monte
// Carlo suggestion panel, and the homer-check table.
export const tagMeta: Record<
  PlayerTag,
  { Icon: typeof FiMoon; className: string; label: string }
> = {
  sleeper: { Icon: FiMoon, className: "text-purple-500", label: "Sleeper" },
  my_guy: { Icon: FiStar, className: "text-yellow-500", label: "My Guy" },
  avoid: { Icon: FiSlash, className: "text-danger", label: "Avoid" },
};

// Small icon marker for a tagged player; renders nothing if untagged
export function TagBadge({ tag }: { tag: PlayerTag | null | undefined }) {
  if (!tag) return null;
  const { Icon, className, label } = tagMeta[tag];

  return <Icon aria-label={label} className={className} title={label} />;
}

// Per-row tag/untag controls: clicking the active tag clears it,
// clicking another tag replaces it (one tag at a time). `compact`
// shrinks the icons for the dense left-rail rows; the default size
// keeps the original page.tsx styling for the Monte Carlo / scarcity
// panels that previously hosted these controls.
export function TagControls({
  leagueId,
  player,
  compact = false,
}: {
  leagueId: string;
  player: Player;
  compact?: boolean;
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
    <div
      className={`flex items-center justify-center gap-2 ${
        compact ? "text-xs" : "text-sm"
      }`}
    >
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
