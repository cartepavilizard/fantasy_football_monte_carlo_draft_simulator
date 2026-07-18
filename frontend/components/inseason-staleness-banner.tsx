"use client";

import * as React from "react";
import { Spinner } from "@nextui-org/spinner";
import { FiAlertTriangle, FiCheckCircle } from "react-icons/fi";

// The composite's staleness banner: amber tinted strip with a warning glyph,
// a bold "projections N days stale" line, a muted "refresh before setting
// lineups" tail, and the only ESPN-touching control on the page — the
// "Refresh" button (the existing useSyncLeagueMutation hook, surfaced here
// as `onRefresh`). When there are no warnings, render the composite's
// quiet "all fresh" pill instead.
export const HawkStalenessBanner: React.FC<{
  warnings: string[];
  onRefresh: () => void;
  refreshing?: boolean;
  message?: string;
}> = ({ warnings, onRefresh, refreshing, message }) => {
  if (warnings.length === 0) {
    return (
      <div
        className="flex items-center gap-2 rounded"
        style={{
          background: "rgba(105,190,40,0.10)",
          border: "1px solid rgba(105,190,40,0.45)",
          padding: "5px 12px",
          fontSize: "var(--fs-sm)",
        }}
      >
        <FiCheckCircle style={{ color: "var(--green)" }} />
        <span style={{ color: "var(--green)", fontWeight: 600 }}>
          Projections fresh.
        </span>
        <span style={{ color: "var(--text-mute)" }}>
          Last sync cached — no refresh needed.
        </span>
      </div>
    );
  }

  // Summarize the first warning as the headline (mirrors the composite's
  // "Projections 3 days stale." line); the rest collapse into the muted
  // tail so a long list never blows out the strip.
  const headline = warnings[0];
  const tail =
    warnings.length > 1
      ? `${warnings.length - 1} more warning${warnings.length - 1 === 1 ? "" : "s"} — refresh before setting lineups.`
      : "Refresh before setting lineups.";

  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded"
      style={{
        background: "rgba(245,179,1,0.10)",
        border: "1px solid rgba(245,179,1,0.5)",
        padding: "5px 12px",
        fontSize: "var(--fs-sm)",
      }}
    >
      <FiAlertTriangle style={{ color: "var(--warn)" }} />
      <span style={{ color: "var(--warn)", fontWeight: 600 }}>{headline}</span>
      <span style={{ color: "var(--text-dim)" }}>{tail}</span>
      <button
        type="button"
        onClick={onRefresh}
        disabled={refreshing}
        className="ml-auto font-head font-bold uppercase"
        style={{
          fontSize: "var(--fs-xs)",
          background: "var(--warn)",
          color: "#2a1e00",
          border: "none",
          borderRadius: "var(--radius-sm)",
          padding: "3px 10px",
          cursor: refreshing ? "default" : "pointer",
          letterSpacing: "0.04em",
        }}
      >
        {refreshing ? (
          <span className="flex items-center gap-1">
            <Spinner color="white" size="sm" /> Refreshing…
          </span>
        ) : (
          "Refresh"
        )}
      </button>
      {message && (
        <span
          className="w-full"
          style={{ color: "var(--text-mute)", fontSize: "var(--fs-xs)" }}
        >
          {message}
        </span>
      )}
    </div>
  );
};

// Inline warning list rendered inside a card body — keeps B4's "stale or
// auth-expired cache never looks fresh" hard constraint visible under every
// section whose data the banner doesn't cover directly.
export const HawkStalenessInline: React.FC<{ warnings: string[] }> = ({
  warnings,
}) => {
  if (warnings.length === 0) return null;

  return (
    <ul
      className="flex flex-col gap-1"
      style={{ fontSize: "var(--fs-xs)", color: "var(--warn)" }}
    >
      {warnings.map((w, i) => (
        <li key={i} className="flex items-start gap-1">
          <FiAlertTriangle className="mt-0.5 shrink-0" />
          <span>{w}</span>
        </li>
      ))}
    </ul>
  );
};
