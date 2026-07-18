import {
  Anton as FontDisplay,
  Barlow_Condensed as FontHead,
  Barlow as FontBody,
  JetBrains_Mono as FontMono,
} from "next/font/google";

// HAWK MODE: Anton (display), Barlow Condensed (headings), Barlow (body).
// NextUI's --font-sans is mapped to Barlow so every default control inherits
// the body face without per-component overrides.
export const fontDisplay = FontDisplay({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-display",
  display: "swap",
});

export const fontHead = FontHead({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-head",
  display: "swap",
});

export const fontBody = FontBody({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-body",
  display: "swap",
});

// Kept for back-compat with anything that still imports fontSans/fontMono
// (e.g. the home page's mono snippet). next/font requires each loader to be
// called and assigned at module scope, so fontSans is its own call (mapped to
// --font-sans so NextUI's default controls inherit Barlow).
export const fontSans = FontBody({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-sans",
  display: "swap",
});

export const fontMono = FontMono({
  subsets: ["latin"],
  variable: "--font-mono",
});
