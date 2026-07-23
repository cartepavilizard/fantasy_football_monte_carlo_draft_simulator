import { TagBadge } from "@hawkmode/ui";

// TagBadge renders nothing for a null/undefined tag by design (the
// floor-card default prop) — sweep the three real PlayerTag values so the
// card shows what it actually looks like in use.
export const MyGuy = () => <TagBadge tag="my_guy" />;
export const Sleeper = () => <TagBadge tag="sleeper" />;
export const Avoid = () => <TagBadge tag="avoid" />;
