import { FiMoon, FiSlash, FiStar } from "react-icons/fi";

import { PlayerTag } from "./types";

// One consistent icon+color per tag, reused wherever a tagged player's
// name appears: best-available rows, scarcity at-risk lists, the Monte
// Carlo suggestion panel, and the homer-check table.
//
// NOTE: this is the presentational half only. The connected TagControls
// component (per-row tag/untag buttons wired to the live tag/untag
// mutations) stays app-side in frontend/components/draft-tag-badge.tsx,
// which re-exports tagMeta/TagBadge from here.
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
