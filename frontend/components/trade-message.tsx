import * as React from "react";
import { Button } from "@nextui-org/button";
import { Spinner } from "@nextui-org/spinner";
import { FiCopy, FiCheck } from "react-icons/fi";

import { HawkCard, HawkCardHeader } from "@/components/hawk-cards";

export interface TradeMessageCardProps {
  message: string | null;
  copied: boolean;
  onCopy: () => void;
  isFetching: boolean;
}

export const TradeMessageCard: React.FC<TradeMessageCardProps> = ({
  message,
  copied,
  onCopy,
  isFetching,
}) => {
  return (
    <HawkCard>
      <HawkCardHeader
        title="Message"
        right={
          <Button
            size="sm"
            variant="bordered"
            disabled={!message || isFetching}
            onClick={onCopy}
            className="border border-[color:var(--border-2)] text-[color:var(--text)] rounded-[var(--radius-sm)]"
            startContent={copied ? <FiCheck /> : <FiCopy />}
          >
            {copied ? "Copied!" : "Copy"}
          </Button>
        }
      />
      <div className="p-3">
        {isFetching ? (
          <Spinner size="sm" />
        ) : message ? (
          <p className="text-sm text-[color:var(--text-dim)] leading-relaxed">
            &ldquo;{message}&rdquo;
          </p>
        ) : (
          <p className="text-sm text-[color:var(--text-mute)]">
            Run Message to render the friendly, non-salesy proposal note. Copy
            is the real CTA — there is no send mechanism.
          </p>
        )}
      </div>
    </HawkCard>
  );
};
