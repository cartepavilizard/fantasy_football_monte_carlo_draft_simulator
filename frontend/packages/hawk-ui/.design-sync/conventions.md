# Hawk UI — conventions for building with this library

Seahawks-themed, dense UI kit for a fantasy football draft/season manager.
Dark navy surfaces, green accents, condensed display type. No wrapper
provider needed — every component is self-contained and styles itself
from the CSS custom properties `styles.css` ships (verified: `provider`
is `null` in this bundle).

## Theme: dark is default, light via an attribute

Dark renders with no setup. For light mode, set `data-theme="light"` on
an ancestor element (root `<html>` in the source app) — the shipped
`styles.css` defines a `[data-theme="light"]` override block that
re-points every surface/border/text token to light values. There is no
separate light component variant to choose; the same components
re-render correctly under either attribute value.

## Styling idiom: CSS custom properties, not props

Components read color/spacing/type from `var(--token)` internally — you
rarely need to pass color or spacing props. The real token vocabulary
(all shipped in `styles.css`):

- **Brand**: `--navy`, `--green`, `--green-bright`, `--gold`, `--grey`
- **Surfaces (dark)**: `--bg`, `--surface`, `--surface-2`, `--surface-3`,
  `--border`, `--border-2`, `--text`, `--text-dim`, `--text-mute`
- **Position colors**: `--pos-qb`, `--pos-rb`, `--pos-wr`, `--pos-te`,
  `--pos-k`, `--pos-dst`, `--pos-flex`
- **Semantic**: `--win`, `--loss`, `--warn`, `--info`
- **Density/spacing**: `--sp-1` (4px) through `--sp-6` (24px)
- **Type scale**: `--fs-xs` through `--fs-display`, plus `--font-display`
  (Anton), `--font-head` (Barlow Condensed), `--font-body` (Barlow) —
  applied via the `.font-display` / `.font-head` / `.font-body` helper
  classes shipped alongside the tokens
- **Radius**: `--radius-sm`, `--radius`, `--radius-lg`

For your own layout glue AROUND these components (positioning, grids,
flex wrapping), use plain Tailwind utility classes (`flex`, `gap-3`,
`grid-cols-2`, etc.) — the bundle ships compiled Tailwind utilities
alongside the tokens, so standard utility classnames work in composed
designs without extra setup.

## Where the truth lives

Read `styles.css` (the token/theme source) and each component's
`<Name>.d.ts` (real prop contract) and `<Name>.prompt.md` before styling
or composing — they're bound alongside this library and are more
current than any summary here.

## Component families

- **Cards/chips** (`HawkCard`, `HawkCardHeader`, `HawkCardTitle`,
  `HawkChip`, `HawkSectionLabel`, `HawkStatTrio`) — the shared shell
  every panel below is built from. Start here for any new composition.
- **Mascots** (`CornerBadge`, `HeroMascot`, `VictoryBadge`,
  `EmptyStateHawk`) — pure decorative SVG, no props required.
- **Draft** (`DraftBoard`, `BestAvailable`, `MyRoster`, `OnTheClock`,
  `Suggested`, `MonteCarloPanel`, `ScarcityPanel`).
- **In-season** (`InseasonLineupOptimizer`, `InseasonMatchupsCard`,
  `InseasonPlayoffSosCard`, `InseasonStreamingCard`,
  `InseasonHandcuffsCard`, `HawkStalenessBanner`/`HawkStalenessInline`,
  `VarianceFlag`, `NotificationsPanel`).
- **Trade** (`TradeHeader`, `TradeProposalBuilder`, `TradeVerdictCard`,
  `TradeCountersCard`, `TradeMessageCard`).
- **Tagging** (`TagBadge`, `ImageSlot`).

## Example composition

```tsx
import { HawkCard, HawkCardHeader, HawkChip } from "@hawkmode/ui";

<HawkCard className="flex flex-col gap-3">
  <HawkCardHeader
    title="Week 5 Matchup"
    right={<HawkChip tone="green">Favored</HawkChip>}
  />
  <div className="flex flex-col gap-2 p-3">
    <p className="font-body text-sm text-[color:var(--text-dim)]">
      Projected 118.4 vs 104.2
    </p>
  </div>
</HawkCard>
```
