"use client";

import * as React from "react";
import clsx from "clsx";

import { CornerBadge } from "./mascots";

// HAWK MODE image slots — navy/green framed cutouts with a caption bar.
// The user drops real hype images into frontend/public/hype/ and we render
// them by filename; a missing file (or an onError) falls back to the hawk
// mascot placeholder so the slot never renders broken.

export type ImageSlotVariant = "hero" | "card" | "thumb";

export interface ImageSlotProps {
  variant?: ImageSlotVariant;
  // filename inside frontend/public/hype/ (e.g. "reeve-hero.png"). Omit to
  // force the placeholder.
  filename?: string;
  // caption bar text (hero/card). Omit on thumb.
  caption?: string;
  // small corner label, e.g. "HERO" / "CARD"
  label?: string;
  className?: string;
}

const variantSizes: Record<
  ImageSlotVariant,
  { width: number | string; height: number; label?: string }
> = {
  hero: { width: "100%", height: 180, label: "HERO" },
  card: { width: 200, height: 120, label: "CARD" },
  thumb: { width: 76, height: 76, label: "THUMB" },
};

export function ImageSlot({
  variant = "card",
  filename,
  caption,
  label,
  className,
}: ImageSlotProps) {
  const [broken, setBroken] = React.useState(false);
  const sizes = variantSizes[variant];
  const src = filename ? `/hype/${filename}` : undefined;
  const showImage = src != null && !broken;
  const cornerLabel = label ?? sizes.label;

  return (
    <div
      className={clsx("relative shrink-0 overflow-hidden", className)}
      style={{
        width: sizes.width,
        maxWidth: "100%",
        border: "2px solid var(--green)",
        borderRadius: "var(--radius)",
        background: "var(--navy)",
        boxShadow: "0 6px 22px rgba(0,0,0,0.35)",
      }}
    >
      <div
        className="relative flex items-center justify-center"
        style={{
          height: sizes.height,
          backgroundImage:
            "repeating-linear-gradient(135deg, var(--surface-2), var(--surface-2) 11px, var(--surface-3) 11px, var(--surface-3) 22px)",
        }}
      >
        {showImage ? (
          <img
            src={src}
            alt={caption ?? ""}
            className="h-full w-full object-cover"
            onError={() => setBroken(true)}
          />
        ) : (
          <div className="flex flex-col items-center gap-1 text-center">
            <CornerBadge size={variant === "thumb" ? 26 : 34} />
            <span
              className="font-mono text-grey"
              style={{
                fontSize: variant === "thumb" ? 9 : 11,
                background: "var(--bg)",
                border: "1px dashed var(--border-2)",
                borderRadius: 4,
                padding: variant === "thumb" ? "2px 4px" : "4px 10px",
              }}
            >
              {cornerLabel}
              {filename ? ` · ${filename}` : ""}
            </span>
          </div>
        )}
        {cornerLabel && (
          <span
            className="absolute left-2 top-2 font-head font-bold uppercase tracking-[0.1em] text-green"
            style={{
              fontSize: "var(--fs-xs)",
              background: "rgba(0,18,32,0.7)",
              borderRadius: 3,
              padding: "2px 7px",
            }}
          >
            {cornerLabel}
          </span>
        )}
      </div>
      {variant !== "thumb" && (
        <div
          className="flex items-center justify-between"
          style={{
            background: "var(--navy)",
            borderTop: "2px solid var(--green)",
            padding: variant === "hero" ? "7px 11px" : "5px 9px",
          }}
        >
          <span
            className="font-head font-bold uppercase tracking-[0.04em] text-white"
            style={{ fontSize: variant === "hero" ? "var(--fs-md)" : "var(--fs-sm)" }}
          >
            {caption ?? "caption bar"}
          </span>
          <span className="text-xs text-grey">caption bar</span>
        </div>
      )}
    </div>
  );
}
