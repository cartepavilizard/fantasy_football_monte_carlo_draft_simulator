// Self-contained CommonJS Tailwind config, scoped to this package's own
// source. Deliberately NOT `presets: [require("../../tailwind.config.js")]`
// - the app's config mixes a top-level ESM `import` with `module.exports`,
// which Next.js's build pipeline transpiles but the bare `tailwindcss` CLI
// (invoked directly for this design-sync build) loads via plain `require()`
// and would fail on. The theme.extend + nextui() plugin block below is a
// verbatim copy of frontend/tailwind.config.js's — see NOTES.md's "Re-sync
// risks" for the drift this creates if that file's tokens/theme change.
const { nextui } = require("@nextui-org/theme");

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-body)", "system-ui", "sans-serif"],
        body: ["var(--font-body)", "system-ui", "sans-serif"],
        head: ["var(--font-head)", "sans-serif"],
        display: ["var(--font-display)", "Impact", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      colors: {
        navy: "var(--navy)",
        green: "var(--green)",
        "green-bright": "var(--green-bright)",
        gold: "var(--gold)",
        grey: "var(--grey)",
        bg: "var(--bg)",
        surface: {
          DEFAULT: "var(--surface)",
          2: "var(--surface-2)",
          3: "var(--surface-3)",
        },
        border: {
          DEFAULT: "var(--border)",
          2: "var(--border-2)",
        },
        hawk: {
          text: "var(--text)",
          "text-dim": "var(--text-dim)",
          "text-mute": "var(--text-mute)",
          qb: "var(--pos-qb)",
          rb: "var(--pos-rb)",
          wr: "var(--pos-wr)",
          te: "var(--pos-te)",
          k: "var(--pos-k)",
          dst: "var(--pos-dst)",
          flex: "var(--pos-flex)",
          win: "var(--win)",
          loss: "var(--loss)",
          warn: "var(--warn)",
          info: "var(--info)",
        },
      },
      fontSize: {
        xs: ["var(--fs-xs)", { lineHeight: "1.1" }],
        sm: ["var(--fs-sm)", { lineHeight: "1.2" }],
        base: ["var(--fs-base)", { lineHeight: "1.3" }],
        md: ["var(--fs-md)", { lineHeight: "1.25" }],
        lg: ["var(--fs-lg)", { lineHeight: "1.1" }],
        xl: ["var(--fs-xl)", { lineHeight: "1.05" }],
        "2xl": ["var(--fs-2xl)", { lineHeight: "1" }],
        display: ["var(--fs-display)", { lineHeight: "0.92" }],
      },
      spacing: {
        1: "var(--sp-1)",
        2: "var(--sp-2)",
        3: "var(--sp-3)",
        4: "var(--sp-4)",
        5: "var(--sp-5)",
        6: "var(--sp-6)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        DEFAULT: "var(--radius)",
        lg: "var(--radius-lg)",
      },
      height: {
        nav: "var(--nav-h)",
        row: "var(--row-h)",
      },
    },
  },
  darkMode: "class",
  plugins: [
    nextui({
      themes: {
        dark: {
          colors: {
            background: "#050f1a",
            foreground: "#eef4fa",
            content1: "#0b1e33",
            content2: "#0f2740",
            content3: "#143154",
            content4: "#143154",
            primary: { DEFAULT: "#69BE28", foreground: "#04240a" },
            success: { DEFAULT: "#69BE28", foreground: "#04240a" },
            warning: { DEFAULT: "#f5b301", foreground: "#1a1500" },
            danger: { DEFAULT: "#ff5c6c", foreground: "#ffffff" },
            divider: "#1c3a5c",
            focus: "#69BE28",
          },
        },
        light: {
          colors: {
            background: "#e7ecf1",
            foreground: "#08192b",
            content1: "#ffffff",
            content2: "#f3f6f9",
            content3: "#e8edf2",
            content4: "#e8edf2",
            primary: { DEFAULT: "#69BE28", foreground: "#04240a" },
            success: { DEFAULT: "#69BE28", foreground: "#04240a" },
            warning: { DEFAULT: "#f5b301", foreground: "#1a1500" },
            danger: { DEFAULT: "#ff5c6c", foreground: "#ffffff" },
            divider: "#d2dae2",
            focus: "#69BE28",
          },
        },
      },
    }),
  ],
};
