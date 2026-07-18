import * as React from "react";
import clsx from "clsx";

// HAWK MODE shared card primitives — the FULL-PAGE COMPOSITES' card language
// extracted as reusable building blocks so the draft, in-season, and trade
// room pages share one dense, token-driven style. Every value is a CSS token
// from globals.css (--surface/--border/--green/...) so the cards recolor with
// the theme the same way the landed draft board + mascots do.

type CardVariant = "surface" | "navy" | "green";

const variantBg: Record<CardVariant, string> = {
  surface: "var(--surface)",
  navy: "var(--navy)",
  green:
    "linear-gradient(120deg, rgba(105,190,40,0.14), transparent 65%), var(--surface)",
};

const variantBorder: Record<CardVariant, string> = {
  surface: "1px solid var(--border)",
  navy: "1px solid var(--border)",
  green: "1px solid var(--green)",
};

export interface HawkCardProps
  extends React.HTMLAttributes<HTMLDivElement> {
  variant?: CardVariant;
  // compact (no header bar) cards pad their body; header-bar cards do not
  padded?: boolean;
}

// The base card shell — surface bg, hairline border, kit radius, overflow
// clipped. Use <HawkCardHeader> for the bar-style header, or pass padded +
// a <HawkCardTitle> for the compact form.
export const HawkCard: React.FC<HawkCardProps> = ({
  variant = "surface",
  padded = false,
  className,
  style,
  children,
  ...rest
}) => (
  <div
    className={clsx("flex flex-col", padded && "p-3", className)}
    style={{
      background: variantBg[variant],
      border: variantBorder[variant],
      borderRadius: "var(--radius)",
      overflow: "hidden",
      ...style,
    }}
    {...rest}
  >
    {children}
  </div>
);

// The bar-style card header — surface-2 bg, hairline bottom border, flex row.
// title is the uppercase head-font label; right hosts a chip/stat/button.
export interface HawkCardHeaderProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  right?: React.ReactNode;
  className?: string;
}

export const HawkCardHeader: React.FC<HawkCardHeaderProps> = ({
  title,
  subtitle,
  right,
  className,
}) => (
  <div
    className={clsx(
      "flex items-center gap-2 border-b px-3 py-2",
      className,
    )}
    style={{
      background: "var(--surface-2)",
      borderColor: "var(--border)",
    }}
  >
    <span className="font-head text-sm font-bold uppercase tracking-[0.05em]">
      {title}
    </span>
    {subtitle != null && (
      <span className="text-xs text-[color:var(--text-mute)]">{subtitle}</span>
    )}
    {right != null && <span className="ml-auto">{right}</span>}
  </div>
);

// Standalone title for compact (no-bar) cards — uppercase head font, with an
// optional `tone` for the green/info accent the composite uses per section.
export interface HawkCardTitleProps {
  tone?: "default" | "green" | "info" | "warn" | "loss";
  className?: string;
  children: React.ReactNode;
}

const toneColor: Record<NonNullable<HawkCardTitleProps["tone"]>, string> = {
  default: "var(--text)",
  green: "var(--green)",
  info: "var(--info)",
  warn: "var(--warn)",
  loss: "var(--loss)",
};

export const HawkCardTitle: React.FC<HawkCardTitleProps> = ({
  tone = "default",
  className,
  children,
}) => (
  <div
    className={clsx(
      "font-head text-sm font-bold uppercase tracking-[0.05em]",
      className,
    )}
    style={{ color: toneColor[tone], marginBottom: "var(--sp-2)" }}
  >
    {children}
  </div>
);

// Small uppercase colored label like "You Give" (green) / "You Get" (info).
export const HawkSectionLabel: React.FC<{
  tone?: "green" | "info";
  className?: string;
  children: React.ReactNode;
}> = ({ tone = "green", className, children }) => (
  <div
    className={clsx(
      "font-head text-xs font-bold uppercase tracking-[0.06em]",
      className,
    )}
    style={{
      color: tone === "green" ? "var(--green)" : "var(--info)",
      marginBottom: "var(--sp-2)",
    }}
  >
    {children}
  </div>
);

type ChipTone = "green" | "info" | "warn" | "loss" | "neutral";

const chipColors: Record<ChipTone, { fg: string; bg: string; bd: string }> = {
  green: {
    fg: "var(--green)",
    bg: "rgba(105,190,40,0.14)",
    bd: "var(--green)",
  },
  info: {
    fg: "var(--info)",
    bg: "rgba(74,168,255,0.14)",
    bd: "var(--info)",
  },
  warn: {
    fg: "var(--warn)",
    bg: "rgba(245,179,1,0.14)",
    bd: "var(--warn)",
  },
  loss: {
    fg: "var(--loss)",
    bg: "rgba(255,92,108,0.14)",
    bd: "var(--loss)",
  },
  neutral: {
    fg: "var(--text-dim)",
    bg: "var(--surface-3)",
    bd: "var(--border-2)",
  },
};

// Pill badge — the composite's strength/priority chip. Defaults to the
// rounded-full head-font form used across matchups, handcuffs, deadlines.
export const HawkChip: React.FC<{
  tone?: ChipTone;
  className?: string;
  children: React.ReactNode;
}> = ({ tone = "neutral", className, children }) => {
  const c = chipColors[tone];
  return (
    <span
      className={clsx(
        "inline-flex items-center font-head text-[10px] font-bold uppercase",
        className,
      )}
      style={{
        color: c.fg,
        background: c.bg,
        border: `1px solid ${c.bd}`,
        borderRadius: 100,
        padding: "2px 8px",
        letterSpacing: "0.04em",
      }}
    >
      {children}
    </span>
  );
};

// The Send/Get/Gap stat trio from the trade-room verdict card. `gap` is the
// highlighted middle/emphasis cell (green tint + green text + green border).
export const HawkStatTrio: React.FC<{
  send: React.ReactNode;
  get: React.ReactNode;
  gap: React.ReactNode;
}> = ({ send, get, gap }) => (
  <div
    className="grid"
    style={{ gridTemplateColumns: "1fr 1fr 1fr", gap: "var(--sp-2)" }}
  >
    <StatCell label="Send" value={send} />
    <StatCell label="Get" value={get} />
    <StatCell label="Gap" value={gap} emphasis />
  </div>
);

const StatCell: React.FC<{
  label: string;
  value: React.ReactNode;
  emphasis?: boolean;
}> = ({ label, value, emphasis }) => (
  <div
    className="text-center"
    style={{
      background: emphasis ? "rgba(105,190,40,0.16)" : "rgba(0,0,0,0.25)",
      border: emphasis
        ? "1px solid var(--green)"
        : "1px solid var(--border)",
      borderRadius: "var(--radius-sm)",
      padding: "var(--sp-2)",
    }}
  >
    <div
      className="font-head text-[9px] uppercase"
      style={{ color: emphasis ? "var(--green)" : "var(--grey)" }}
    >
      {label}
    </div>
    <div
      className="font-display"
      style={{
        fontSize: "var(--fs-lg)",
        lineHeight: 1,
        color: emphasis ? "var(--green)" : "#fff",
      }}
    >
      {value}
    </div>
  </div>
);
