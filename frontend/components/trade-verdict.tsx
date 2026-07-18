import * as React from "react";
import { Spinner } from "@nextui-org/spinner";

import { HawkCard, HawkStatTrio } from "@/components/hawk-cards";
import { TradeEvaluation, TradeVerdict } from "@/types";

// The composite's punchy one-line verdict title, mapped from E1's verdict
// bucket. The plain-terms gap line (evaluation.summary) carries the read.
function verdictTitle(verdict: TradeVerdict): string {
  switch (verdict) {
    case "fair":
      return "FAIR — EVEN UP";
    case "favors_a":
      return "SLIGHT WIN — TAKE IT";
    case "favors_b":
      return "FAVORS THEM — PASS";
  }
}

function verdictTone(verdict: TradeVerdict): "green" | "info" | "loss" {
  switch (verdict) {
    case "fair":
      return "info";
    case "favors_a":
      return "green";
    case "favors_b":
      return "loss";
  }
}

const toneColor = {
  green: "var(--green)",
  info: "var(--info)",
  loss: "var(--loss)",
} as const;

export interface TradeVerdictCardProps {
  evaluation: TradeEvaluation | null;
  isEvaluating: boolean;
}

export const TradeVerdictCard: React.FC<TradeVerdictCardProps> = ({
  evaluation,
  isEvaluating,
}) => {
  return (
    <HawkCard
      padded
      style={{
        background:
          "linear-gradient(120deg, rgba(105,190,40,0.12), transparent 65%), var(--navy)",
        border: "1px solid var(--green)",
        position: "relative",
      }}
    >
      <div
        aria-hidden
        className="hawk-feather"
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
        }}
      />
      <div style={{ position: "relative" }}>
        {isEvaluating ? (
          <div className="flex items-center justify-center py-6">
            <Spinner />
          </div>
        ) : evaluation ? (
          <>
            <div
              className="font-display uppercase"
              style={{
                fontSize: "var(--fs-xl)",
                color: toneColor[verdictTone(evaluation.verdict)],
                lineHeight: 1,
              }}
            >
              {verdictTitle(evaluation.verdict)}
            </div>
            <p
              className="my-2 text-sm text-[color:var(--text-dim)]"
              style={{ margin: "var(--sp-2) 0 var(--sp-3)" }}
            >
              {evaluation.summary}
            </p>
            <HawkStatTrio
              send={evaluation.value_sent_a.toFixed(0)}
              get={evaluation.value_sent_b.toFixed(0)}
              gap={
                <span
                  style={{
                    color:
                      evaluation.market_gap >= 0
                        ? "var(--green)"
                        : "var(--loss)",
                  }}
                >
                  {evaluation.market_gap > 0 ? "+" : ""}
                  {evaluation.market_gap.toFixed(0)}
                </span>
              }
            />
            <p className="mt-2 text-xs text-[color:var(--text-mute)]">
              fair band ±{evaluation.fair_bound.toFixed(1)} · wk{" "}
              {evaluation.week} · {evaluation.weeks_remaining}w left
            </p>
          </>
        ) : (
          <>
            <div
              className="font-display uppercase"
              style={{ fontSize: "var(--fs-xl)", color: "var(--green)", lineHeight: 1 }}
            >
              GRADE THE PROPOSAL
            </div>
            <p
              className="text-sm text-[color:var(--text-dim)]"
              style={{ margin: "var(--sp-2) 0 var(--sp-3)" }}
            >
              Pick pieces on both sides, then run Evaluate for the verdict and
              the Send / Get / Gap value split.
            </p>
            <HawkStatTrio send="—" get="—" gap="—" />
          </>
        )}
      </div>
    </HawkCard>
  );
};
