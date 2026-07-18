import * as React from "react";
import clsx from "clsx";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";
import {
  FiAlertTriangle,
  FiCheck,
  FiMessageCircle,
  FiRefreshCw,
  FiShuffle,
} from "react-icons/fi";

import { HawkCard, HawkCardHeader, HawkSectionLabel } from "@/components/hawk-cards";
import {
  RosterSlotEntry,
  TradePlayerValue,
} from "@/types";

// One checkbox player row. tone drives the selected-state tint (green for the
// "You Give" side, info for "You Get"), matching the composite's two columns.
// `value` is the E1 market value when we have one for this player (from the
// most recent evaluate/counters/message run); null until then.
function TradePlayerRow({
  entry,
  checked,
  value,
  tone,
  onToggle,
}: {
  entry: RosterSlotEntry;
  checked: boolean;
  value: TradePlayerValue | null;
  tone: "green" | "info";
  onToggle: () => void;
}) {
  const tintBg =
    tone === "green" ? "rgba(105,190,40,0.10)" : "rgba(74,168,255,0.10)";
  const tintBd =
    tone === "green" ? "rgba(105,190,40,0.45)" : "rgba(74,168,255,0.45)";
  const checkBg = tone === "green" ? "var(--green)" : "var(--info)";

  return (
    <li>
      <label
        className="flex items-center gap-2 px-2 py-1.5 rounded-[var(--radius-sm)] cursor-pointer"
        style={{
          border: `1px solid ${checked ? tintBd : "var(--border)"}`,
          background: checked ? tintBg : "var(--surface-2)",
        }}
      >
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="sr-only"
        />
        <span
          className="w-3.5 h-3.5 rounded-[4px] flex items-center justify-center"
          style={
            checked
              ? { background: checkBg, color: "#04240a" }
              : { border: "1px solid var(--border-2)" }
          }
        >
          {checked && (
            <FiCheck className="text-[9px]" style={{ fontWeight: 800 }} />
          )}
        </span>
        <span
          className={clsx(
            "flex-1 text-sm font-semibold truncate",
            !checked && "text-[color:var(--text)]",
          )}
          style={checked ? { color: "var(--text)" } : undefined}
        >
          {entry.player_name}
          <span className="text-[color:var(--text-mute)] font-normal">
            {" "}
            {entry.position ?? "—"} · {entry.nfl_team ?? "—"}
          </span>
          {entry.injury_status && (
            <span className="text-[color:var(--warn)] font-normal">
              {" "}({entry.injury_status})
            </span>
          )}
        </span>
        <span className="text-[9px] text-[color:var(--text-mute)] shrink-0">
          {value ? value.value.toFixed(0) : "—"}
        </span>
      </label>
    </li>
  );
}

function SideColumn({
  label,
  tone,
  entries,
  loading,
  selected,
  valueMap,
  onToggle,
}: {
  label: string;
  tone: "green" | "info";
  entries: RosterSlotEntry[] | null;
  loading: boolean;
  selected: Set<number>;
  valueMap: Map<number, TradePlayerValue>;
  onToggle: (playerId: number) => void;
}) {
  return (
    <div className="p-3">
      <HawkSectionLabel tone={tone}>{label}</HawkSectionLabel>
      {loading ? (
        <Spinner size="sm" />
      ) : !entries || entries.length === 0 ? (
        <p className="text-sm text-[color:var(--text-mute)]">
          No cached roster for this team-week.
        </p>
      ) : (
        <ul className="flex flex-col gap-1 max-h-72 overflow-auto">
          {entries.map((entry) => (
            <TradePlayerRow
              key={entry.player_id}
              entry={entry}
              checked={selected.has(entry.player_id)}
              value={valueMap.get(entry.player_id) ?? null}
              tone={tone}
              onToggle={() => onToggle(entry.player_id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export interface TradeProposalBuilderProps {
  teamAName: string;
  teamBName: string;
  rosterA: RosterSlotEntry[] | null;
  rosterB: RosterSlotEntry[] | null;
  rosterALoading: boolean;
  rosterBLoading: boolean;
  sendsA: Set<number>;
  sendsB: Set<number>;
  valueMap: Map<number, TradePlayerValue>;
  onToggleA: (playerId: number) => void;
  onToggleB: (playerId: number) => void;
  proposalReady: boolean;
  onEvaluate: () => void;
  onCounters: () => void;
  onMessage: () => void;
  isEvaluating: boolean;
  isCountering: boolean;
  isFetchingMessage: boolean;
  error: string;
  rosterWarnings: string[];
}

export const TradeProposalBuilder: React.FC<TradeProposalBuilderProps> = ({
  teamAName,
  teamBName,
  rosterA,
  rosterB,
  rosterALoading,
  rosterBLoading,
  sendsA,
  sendsB,
  valueMap,
  onToggleA,
  onToggleB,
  proposalReady,
  onEvaluate,
  onCounters,
  onMessage,
  isEvaluating,
  isCountering,
  isFetchingMessage,
  error,
  rosterWarnings,
}) => {
  const subtitle = `${teamBName} · ${sendsA.size + sendsB.size} selected`;

  return (
    <HawkCard>
      <HawkCardHeader title="Proposal Builder" right={<span className="text-xs text-[color:var(--green)]">{subtitle}</span>} />
      <div
        className="grid"
        style={{ gridTemplateColumns: "1fr 26px 1fr" }}
      >
        <SideColumn
          label={`${teamAName} → You Give`}
          tone="green"
          entries={rosterA}
          loading={rosterALoading}
          selected={sendsA}
          valueMap={valueMap}
          onToggle={onToggleA}
        />
        <div
          className="flex items-center justify-center text-[color:var(--green)]"
          style={{
            background: "var(--surface-2)",
            borderLeft: "1px solid var(--border)",
            borderRight: "1px solid var(--border)",
          }}
        >
          <FiShuffle />
        </div>
        <SideColumn
          label={`${teamBName} → You Get`}
          tone="info"
          entries={rosterB}
          loading={rosterBLoading}
          selected={sendsB}
          valueMap={valueMap}
          onToggle={onToggleB}
        />
      </div>

      <div
        className="flex flex-wrap gap-2 p-3 border-t"
        style={{
          background: "var(--surface-2)",
          borderColor: "var(--border)",
        }}
      >
        <Button
          size="sm"
          disabled={!proposalReady || isEvaluating}
          onClick={onEvaluate}
          style={{
            background: "var(--green)",
            color: "#04240a",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {isEvaluating ? <Spinner color="white" size="sm" /> : "Evaluate"}
        </Button>
        <Button
          size="sm"
          variant="bordered"
          disabled={!proposalReady || isCountering}
          onClick={onCounters}
          className="border border-[color:var(--border-2)] text-[color:var(--text)] rounded-[var(--radius-sm)]"
        >
          {isCountering ? <Spinner size="sm" /> : <span className="inline-flex items-center gap-1"><FiRefreshCw /> Counters</span>}
        </Button>
        <Button
          size="sm"
          variant="bordered"
          disabled={!proposalReady || isFetchingMessage}
          onClick={onMessage}
          className="border border-[color:var(--border-2)] text-[color:var(--text)] rounded-[var(--radius-sm)]"
        >
          {isFetchingMessage ? (
            <Spinner size="sm" />
          ) : (
            <span className="inline-flex items-center gap-1">
              <FiMessageCircle /> Message
            </span>
          )}
        </Button>
        {rosterWarnings.length > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-[color:var(--warn)]">
            <FiAlertTriangle />
            {rosterWarnings.length} roster warning{rosterWarnings.length > 1 ? "s" : ""}
          </span>
        )}
      </div>

      {error && (
        <p className="flex items-start gap-2 text-sm text-[color:var(--loss)] px-3 pb-3">
          <FiAlertTriangle className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </p>
      )}
    </HawkCard>
  );
};
