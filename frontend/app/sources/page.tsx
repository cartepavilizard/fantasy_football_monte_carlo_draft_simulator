"use client";

import { useState } from "react";
import { Button } from "@nextui-org/button";
import { Code } from "@nextui-org/code";
import { Input } from "@nextui-org/input";
import { Spinner } from "@nextui-org/spinner";
import { Switch } from "@nextui-org/switch";
import { useTheme } from "next-themes";

import {
  useGetRankingsStatusQuery,
  useGetScheduleQuery,
  useRefreshRankingsMutation,
  useSetScheduleMutation,
  useUploadUdkMutation,
} from "@/api/services/rankings";
import { title, subtitle } from "@/components/primitives";
import { SourceStatus } from "@/types";

// Human labels for the backend's source keys
const sourceLabels: Record<string, string> = {
  sleeper: "Sleeper",
  ffc: "FF Calculator",
  espn: "ESPN",
  fantasypros: "FantasyPros",
  yahoo: "Yahoo",
  udk: "Ultimate Draft Kit",
};

function age(seconds: number | null): string {
  if (seconds === null) {
    return "never";
  }
  if (seconds < 3600) {
    return `${Math.max(1, Math.round(seconds / 60))}m ago`;
  }
  if (seconds < 86400) {
    return `${Math.round(seconds / 3600)}h ago`;
  }

  return `${Math.round(seconds / 86400)}d ago`;
}

function sourceHealth(source: SourceStatus): {
  label: string;
  color: "primary" | "danger" | "warning" | "default";
} {
  if (!source.configured) {
    return { label: "Not configured", color: "default" };
  }
  if (source.last_attempt && !source.last_attempt.success) {
    return source.last_success
      ? { label: "Failing (stale data in blend)", color: "warning" }
      : { label: "Failing", color: "danger" };
  }
  if (!source.last_success) {
    return {
      label: source.kind === "push" ? "Awaiting upload" : "Never fetched",
      color: "default",
    };
  }

  return { label: "OK", color: "primary" };
}

export default function SourcesPage() {
  const { theme } = useTheme();
  const { data: status, isLoading } = useGetRankingsStatusQuery();
  const { data: schedule } = useGetScheduleQuery();
  const [refreshRankings, { isLoading: isRefreshing }] =
    useRefreshRankingsMutation();
  const [uploadUdk, { isLoading: isUploading }] = useUploadUdkMutation();
  const [setSchedule] = useSetScheduleMutation();

  const [udkFile, setUdkFile] = useState<File | null>(null);
  const [udkMessage, setUdkMessage] = useState<string>("");
  const [refreshMessage, setRefreshMessage] = useState<string>("");
  const [intervalHours, setIntervalHours] = useState<string>("");

  const handleRefresh = async () => {
    setRefreshMessage("");
    try {
      const summary = await refreshRankings().unwrap();
      const failed = Object.entries(summary.sources)
        .filter(([, s]) => !s.success)
        .map(([name]) => sourceLabels[name] ?? name);

      setRefreshMessage(
        failed.length === 0
          ? `Blend rebuilt with ${summary.blend.records} players from ${summary.blend.sources_used.length} sources.`
          : `Blend rebuilt (${summary.blend.records} players); failed: ${failed.join(", ")}.`,
      );
    } catch {
      setRefreshMessage("Refresh failed. Is the backend reachable?");
    }
  };

  const handleUdkUpload = async () => {
    if (!udkFile) {
      return;
    }
    setUdkMessage("");
    try {
      const summary = await uploadUdk({ file: udkFile }).unwrap();

      setUdkMessage(
        summary.warning ??
          `${summary.batch.records} UDK rows ingested (${summary.batch.unresolved} unresolved); blend rebuilt.`,
      );
    } catch {
      setUdkMessage("Upload failed — check that the file is a UDK CSV export.");
    }
  };

  return (
    <section className="flex flex-col items-center justify-center gap-8">
      <div className="max-w-lg text-center">
        <h1 className={title()}>Ranking sources.</h1>
        <h2 className={subtitle()}>
          Rankings and ADP are pulled automatically and blended into one
          projection. Drop in your Ultimate Draft Kit export, and pause the
          schedule on draft day.
        </h2>
      </div>

      {/* One-click full refresh */}
      <div className="flex flex-col gap-2 w-full border-medium rounded-large p-4 border-default">
        <div className="flex items-center justify-between w-full">
          <h3 className="text-xl">
            Blend
            {status?.blend
              ? `: ${status.blend.records} players from ${status.blend.sources_used.length} sources`
              : ": not built yet"}
          </h3>
          <Button
            color="primary"
            disabled={isRefreshing}
            size="lg"
            onClick={handleRefresh}
          >
            {isRefreshing ? (
              <span className="flex items-center">
                <Spinner color="white" size="sm" />
                <span className="ml-2">Refreshing…</span>
              </span>
            ) : (
              "Refresh all sources"
            )}
          </Button>
        </div>
        {status?.blend && (
          <p className="italic text-sm text-default-500">
            Season {status.season} ({status.scoring_format}) — last blended{" "}
            {new Date(status.blend.generated_at).toLocaleString()}
          </p>
        )}
        {refreshMessage && <Code color="primary">{refreshMessage}</Code>}
      </div>

      {/* Per-source freshness */}
      <div className="flex flex-col gap-2 w-full border-medium rounded-large p-4 border-default">
        <h3 className="text-xl">Source status</h3>
        {isLoading || !status ? (
          <Spinner />
        ) : (
          <ul className="flex flex-col gap-3 w-full">
            {Object.entries(status.sources).map(([name, source]) => {
              const health = sourceHealth(source);

              return (
                <li
                  key={name}
                  className="flex flex-wrap items-center justify-between gap-2 border-b border-default-200 pb-2"
                >
                  <div>
                    <p className="font-bold">
                      {sourceLabels[name] ?? name}
                      <span className="ml-2 font-normal text-sm text-default-500">
                        {source.kind === "push" ? "file drop" : "automatic"}
                        {source.access_mode ? ` · ${source.access_mode}` : ""}
                      </span>
                    </p>
                    <p className="text-sm text-default-500">
                      {source.last_success
                        ? `${source.last_success.records} players, ${age(source.age_seconds)}` +
                          (source.last_success.unresolved > 0
                            ? ` (${source.last_success.unresolved} unresolved names)`
                            : "")
                        : "no data yet"}
                    </p>
                  </div>
                  <Code color={health.color}>{health.label}</Code>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* UDK file drop — the one deliberately manual source */}
      <div className="flex flex-col gap-2 w-full border-medium rounded-large p-4 border-default">
        <h3 className="text-xl">Ultimate Draft Kit upload</h3>
        <p className="text-sm text-default-500">
          The UDK is paid, login-walled content, so it is never scraped:
          download your CSV export and drop it here. Refresh the other sources
          first so player names can be matched.
        </p>
        <div className="flex items-center gap-4 w-full">
          <Input
            id="udk-csv"
            label="UDK CSV export"
            size="lg"
            type="file"
            variant={theme === "light" ? "faded" : "flat"}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                setUdkFile(e.target.files[0]);
              }
            }}
          />
          <Button
            color="primary"
            disabled={!udkFile || isUploading}
            size="lg"
            onClick={handleUdkUpload}
          >
            {isUploading ? <Spinner color="white" size="sm" /> : "Upload"}
          </Button>
        </div>
        {udkMessage && <Code color="primary">{udkMessage}</Code>}
      </div>

      {/* Schedule + draft-day switch */}
      <div className="flex flex-col gap-2 w-full border-medium rounded-large p-4 border-default">
        <h3 className="text-xl">Automatic refresh schedule</h3>
        <div className="flex items-center gap-4">
          <span>Enabled</span>
          <Switch
            isSelected={schedule?.enabled ?? false}
            onValueChange={(value) => setSchedule({ enabled: value })}
          />
          <Input
            className="max-w-[12rem]"
            id="interval-hours"
            label="Interval (hours)"
            placeholder={String(schedule?.interval_hours ?? 24)}
            size="sm"
            type="number"
            value={intervalHours}
            variant={theme === "light" ? "faded" : "flat"}
            onChange={(e) => setIntervalHours(e.target.value)}
          />
          <Button
            disabled={intervalHours === ""}
            size="sm"
            variant="bordered"
            onClick={() =>
              setSchedule({ interval_hours: Number(intervalHours) })
            }
          >
            Save interval
          </Button>
        </div>
        <p className="text-sm text-default-500">
          {schedule?.enabled
            ? `Next run ${schedule.next_run ? new Date(schedule.next_run).toLocaleString() : "pending"}.`
            : "Paused — nothing scheduled will run (the draft-day setting)."}
          {schedule?.last_error
            ? ` Last run failed: ${schedule.last_error}`
            : ""}
        </p>
      </div>
    </section>
  );
}
