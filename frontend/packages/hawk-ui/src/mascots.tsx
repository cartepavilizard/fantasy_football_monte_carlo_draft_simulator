import * as React from "react";

// HAWK MODE mascot art — extracted from design/hawk-mode.dc.html and
// rewritten as JSX (the kit renders these via React.createElement). All four
// use the token CSS vars (--navy/--green/--gold/...) so they recolor with
// the theme. Cartoon mascots: a swaggering hawk dominating a dazed ram and a
// defeated gold-rush miner (hero), a hawk head (corner badge), a hawk with
// a trophy (victory), and a shrug-clipboard hawk (empty state).

export interface MascotProps extends React.SVGProps<SVGSVGElement> {
  size?: number;
}

// Hawk head — the corner badge / wordmark logo. viewBox 0 0 64 64.
export const CornerBadge: React.FC<MascotProps> = ({
  size = 28,
  className,
  ...props
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 64 64"
    fill="none"
    className={className}
    aria-hidden="true"
    {...props}
  >
    <path
      d="M12 30 Q10 12 30 10 Q52 8 54 26 Q56 40 44 50 Q34 58 22 52 Q10 46 12 30 Z"
      fill="var(--navy)"
      stroke="var(--green)"
      strokeWidth={2}
    />
    <path d="M30 10 L20 2 L26 12 Z M40 9 L34 0 L44 10 Z M49 13 L48 3 L54 14 Z" fill="var(--green)" />
    <path d="M18 24 L34 26 L20 32 Z" fill="#0a1826" />
    <circle cx={26} cy={29} r={6} fill="#fff" />
    <circle cx={27} cy={29} r={3} fill="#0a1826" />
    <circle cx={26} cy={28} r={1} fill="#fff" />
    <path d="M8 30 L20 27 L18 33 Z" fill="var(--gold)" />
    <path d="M8 30 L18 33 L16 37 Z" fill="#b8860b" />
  </svg>
);

// Hero banner — swaggering hawk front & center, dazed ram (bent horns) and
// defeated miner (hat rolling, pickaxe snapped). viewBox 0 0 210 180.
export const HeroMascot: React.FC<MascotProps> = ({
  size,
  className,
  ...props
}) => (
  <svg
    width={size ?? 210}
    height={size ? (size * 180) / 210 : 180}
    viewBox="0 0 210 180"
    fill="none"
    className={className}
    role="img"
    aria-label="Hawk dominating a dazed ram and a defeated gold-rush miner"
    {...props}
  >
    {/* ground shadow */}
    <ellipse cx={105} cy={166} rx={92} ry={11} fill="#000" opacity={0.28} />
    {/* dazed ram (behind, toppled) */}
    <g opacity={0.95}>
      <ellipse cx={52} cy={150} rx={34} ry={16} fill="#8b93a0" />
      <circle cx={40} cy={146} r={15} fill="#aab2bd" />
      {/* bent horns */}
      <path d="M30 140 Q18 132 22 146 Q26 156 34 150" stroke="#5f6874" strokeWidth={5} fill="none" strokeLinecap="round" />
      <path d="M50 138 Q60 128 54 142" stroke="#5f6874" strokeWidth={5} fill="none" strokeLinecap="round" />
      {/* spiral daze eyes */}
      <circle cx={36} cy={145} r={3.4} fill="#fff" />
      <circle cx={36} cy={145} r={1.4} fill="#0a1826" />
      <circle cx={45} cy={145} r={3.4} fill="#fff" />
      <path d="M45 143.5 a1.5 1.5 0 1 1 -1.4 1.9" stroke="#0a1826" strokeWidth={1} fill="none" />
      <path d="M26 150 l-6 4 M24 145 l-7 1" stroke="#5f6874" strokeWidth={1.4} />
    </g>
    {/* defeated miner (right, hat rolling away) */}
    <g>
      <circle cx={172} cy={152} r={13} fill="#c98a5a" />
      <path d="M162 158 q10 8 20 0" stroke="#7a4a26" strokeWidth={2} fill="none" />
      <circle cx={167} cy={150} r={1.5} fill="#3a2414" />
      <circle cx={177} cy={150} r={1.5} fill="#3a2414" />
      {/* rolling gold-rush hat */}
      <ellipse cx={196} cy={162} rx={13} ry={6} fill="#d4a017" />
      <path d="M188 162 q8 -14 16 0 Z" fill="#e0b23a" />
      <path d="M198 156 l6 -6 M200 160 l7 -3" stroke="#d4a017" strokeWidth={1.4} />
      {/* broken pickaxe */}
      <rect x={150} y={120} width={4} height={22} rx={2} fill="#6b4a2b" transform="rotate(30 152 130)" />
      <path d="M138 118 q10 -6 18 2" stroke="#9aa2ac" strokeWidth={4} fill="none" strokeLinecap="round" />
    </g>
    {/* HAWK — swaggering, dominating, front & center */}
    <g>
      {/* tail */}
      <path d="M118 150 L150 120 L152 138 L138 150 Z" fill="var(--navy)" stroke="var(--green)" strokeWidth={2} />
      {/* body */}
      <path d="M78 60 Q118 52 122 100 Q124 138 96 148 Q66 152 60 118 Q56 82 78 60 Z" fill="var(--navy)" stroke="var(--green)" strokeWidth={2.5} />
      {/* wing flexed */}
      <path d="M96 78 Q132 82 128 118 Q112 112 100 96 Z" fill="#0a2a49" stroke="var(--green)" strokeWidth={1.5} />
      <path d="M104 92 L126 100 M102 102 L124 112" stroke="var(--green)" strokeWidth={1.5} opacity={0.5} />
      {/* chest feathers */}
      <path d="M74 96 l8 6 8 -6 M74 108 l8 6 8 -6 M74 120 l8 6 8 -6" stroke="var(--green)" strokeWidth={1.4} fill="none" opacity={0.55} />
      {/* leg / talon on ram */}
      <path d="M84 146 L80 158 M80 158 l-5 3 M80 158 l5 3 M80 158 l0 5" stroke="var(--gold)" strokeWidth={3} strokeLinecap="round" />
      {/* head */}
      <path d="M70 40 Q100 30 108 54 Q112 72 90 78 Q66 80 62 58 Q60 46 70 40 Z" fill="var(--navy)" stroke="var(--green)" strokeWidth={2.5} />
      {/* crest feathers */}
      <path d="M78 34 L70 20 L82 32 Z M90 32 L86 16 L98 30 Z M100 36 L102 22 L108 36 Z" fill="var(--green)" />
      {/* brow (angry) */}
      <path d="M70 52 L92 48 L90 56 Z" fill="#0a1826" />
      {/* eye */}
      <circle cx={82} cy={56} r={7} fill="#fff" />
      <circle cx={84} cy={56} r={3.5} fill="#0a1826" />
      <circle cx={82.5} cy={54.5} r={1.2} fill="#fff" />
      {/* beak */}
      <path d="M60 58 L100 60 L96 70 Z" fill="var(--gold)" />
      <path d="M60 58 L96 70 L92 76 Z" fill="#b8860b" />
    </g>
  </svg>
);

// Victory badge — hawk with a trophy. viewBox 0 0 64 64. Sits on the Monte
// Carlo suggested-pick panel.
export const VictoryBadge: React.FC<MascotProps> = ({
  size = 48,
  className,
  ...props
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 64 64"
    fill="none"
    className={className}
    aria-hidden="true"
    {...props}
  >
    <path
      d="M14 30 Q12 12 32 10 Q52 8 52 28 Q52 44 38 50 Q22 54 16 40 Q13 35 14 30 Z"
      fill="var(--navy)"
      stroke="var(--green)"
      strokeWidth={2}
    />
    <path d="M28 10 L20 -1 L30 9 Z M40 9 L36 -2 L46 9 Z" fill="var(--green)" />
    <circle cx={28} cy={28} r={6} fill="#fff" />
    <circle cx={29} cy={28} r={3} fill="#0a1826" />
    <path d="M10 30 L24 27 L22 33 Z" fill="var(--gold)" />
    {/* trophy */}
    <path d="M40 40 h12 v4 a6 6 0 0 1 -12 0 Z" fill="var(--gold)" />
    <rect x={44} y={48} width={4} height={5} fill="var(--gold)" />
    <rect x={40} y={53} width={12} height={3} rx={1} fill="#b8860b" />
    <path d="M38 38 l4 -3 M54 38 l-4 -3" stroke="var(--green)" strokeWidth={2} />
  </svg>
);

// Empty-state hawk — a shrug-clipboard hawk for "nothing queued". viewBox
// 0 0 96 96. Used on the empty draft board, notifications, and in-season
// empty panels.
export const EmptyStateHawk: React.FC<MascotProps> = ({
  size = 96,
  className,
  ...props
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 96 96"
    fill="none"
    className={className}
    role="img"
    aria-label="Empty state"
    {...props}
  >
    <ellipse cx={48} cy={86} rx={30} ry={5} fill="#000" opacity={0.18} />
    <path
      d="M30 40 Q28 20 50 18 Q72 16 72 40 Q72 60 54 68 Q34 72 30 50 Z"
      fill="var(--surface-3)"
      stroke="var(--border-2)"
      strokeWidth={2}
    />
    <path d="M44 18 L36 6 L48 16 Z M58 17 L54 4 L64 16 Z" fill="var(--border-2)" />
    <circle cx={46} cy={38} r={6} fill="#fff" />
    <circle cx={46} cy={39} r={2.6} fill="var(--text-mute)" />
    <path d="M42 48 q6 4 12 0" stroke="var(--text-mute)" strokeWidth={2} fill="none" />
    <path d="M28 44 L14 40 L18 50 Z" fill="var(--grey)" />
    {/* shrug clipboard */}
    <rect x={54} y={56} width={26} height={32} rx={3} fill="var(--surface-2)" stroke="var(--border-2)" strokeWidth={2} />
    <rect x={62} y={52} width={10} height={6} rx={2} fill="var(--border-2)" />
    <path d="M60 66 h14 M60 72 h14 M60 78 h8" stroke="var(--text-mute)" strokeWidth={2} />
  </svg>
);
