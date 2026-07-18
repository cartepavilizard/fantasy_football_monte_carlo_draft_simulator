"use client";

import * as React from "react";
import clsx from "clsx";

// Minimal tab strip in the hawk-cards card-grid language — the existing
// page already had no tabs and no Tabs primitive is installed
// (@nextui-org/tabs isn't a dep). This is a styled row of buttons that
// swaps a single active section. Selected tab mirrors the navbar's
// active-route treatment (green tint + inset green underline).
export interface HawkTab {
  key: string;
  label: React.ReactNode;
}

export const HawkTabs: React.FC<{
  tabs: HawkTab[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}> = ({ tabs, active, onChange, className }) => (
  <div
    className={clsx("flex flex-wrap gap-0.5", className)}
    style={{ borderBottom: "1px solid var(--border)" }}
  >
    {tabs.map((tab) => {
      const selected = tab.key === active;

      return (
        <button
          key={tab.key}
          type="button"
          onClick={() => onChange(tab.key)}
          className={clsx(
            "font-head text-sm font-bold uppercase tracking-[0.04em] no-underline transition-colors",
            selected ? "text-white" : "text-grey hover:text-white",
          )}
          style={{
            padding: "6px 12px",
            marginBottom: "-1px",
            borderRadius: "var(--radius-sm) var(--radius-sm) 0 0",
            fontSize: "var(--fs-sm)",
            background: selected ? "rgba(105,190,40,0.16)" : "transparent",
            boxShadow: selected ? "inset 0 -2px 0 var(--green)" : "none",
            cursor: "pointer",
            border: "none",
          }}
        >
          {tab.label}
        </button>
      );
    })}
  </div>
);
