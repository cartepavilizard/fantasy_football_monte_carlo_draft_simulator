"use client";

import { createContext, useCallback, useEffect, useState } from "react";
import { Button } from "@nextui-org/button";
import { Code } from "@nextui-org/code";
import { Input } from "@nextui-org/input";
import { Link } from "@nextui-org/link";
import { Progress } from "@nextui-org/progress";
import { Spinner } from "@nextui-org/spinner";
import { Switch } from "@nextui-org/switch";
import { useTheme } from "next-themes";

import {
  useCreateLeagueMutation,
  useAddHistoricalDraftsMutation,
  useAddHistoricalPlayersMutation,
  useAddPlayersMutation,
  useSyncHistoricalDraftsMutation,
  useSyncPlayersMutation,
} from "@/api/services/league";
import { title, subtitle } from "@/components/primitives";
import { LeagueSimple } from "@/types";

const TOTAL_STEPS = 6;

interface SetupContextType {
  theme: string | undefined;
  progressStep: number;
  isValidationError: boolean;
  isCreationError: boolean;
  isCreated: boolean;
  setLeagueName: (name: string) => void;
  setTeamsFile: (file: File) => void;
  setHistoricalDraftFile: (file: File) => void;
  setPlayersFile: (file: File) => void;
  setHistoricalPlayersFile: (file: File) => void;
}

const SetupContext = createContext<SetupContextType>({
  theme: undefined,
  progressStep: 0,
  isValidationError: false,
  isCreationError: false,
  isCreated: false,
  setLeagueName: () => {},
  setTeamsFile: () => {},
  setHistoricalDraftFile: () => {},
  setPlayersFile: () => {},
  setHistoricalPlayersFile: () => {},
});

export default function SetupPage() {
  const { theme } = useTheme();
  const [progressStep, setProgressStep] = useState<number>(0);
  const [isValidationError, setIsValidationError] = useState<boolean>(false);
  const [isCreationError, setIsCreationError] = useState<boolean>(false);
  const [isCreated, setIsCreated] = useState<boolean>(false);

  // Mutations for creating a new league
  const [createLeague] = useCreateLeagueMutation();
  const [addHistoricalDrafts] = useAddHistoricalDraftsMutation();
  const [addHistoricalPlayers] = useAddHistoricalPlayersMutation();
  const [addPlayers] = useAddPlayersMutation();
  const [syncPlayers] = useSyncPlayersMutation();
  const [syncHistoricalDrafts] = useSyncHistoricalDraftsMutation();

  // State to store the name, sizes, and files
  const [leagueName, setLeagueName] = useState<string | null>(null);
  const [teamsFile, setTeamsFile] = useState<File | null>(null);
  const [historicalDraftFile, setHistoricalDraftFile] = useState<File | null>(
    null,
  );
  const [playersFile, setPlayersFile] = useState<File | null>(null);
  const [historicalPlayersFile, setHistoricalPlayersFile] =
    useState<File | null>(null);

  // No-CSV paths: players from the blended rankings, and the opponent
  // model from ingested ESPN draft history
  const [playersFromSources, setPlayersFromSources] = useState<boolean>(false);
  const [useEspnHistory, setUseEspnHistory] = useState<boolean>(false);
  const [espnLeagueId, setEspnLeagueId] = useState<string>("");

  // Optional league settings; blank means "use the backend's default"
  const [roundSize, setRoundSize] = useState<string>("");
  const [rosterSize, setRosterSize] = useState<string>("");
  const [snakeDraft, setSnakeDraft] = useState<boolean>(true);
  const [qbSize, setQbSize] = useState<string>("");
  const [rbSize, setRbSize] = useState<string>("");
  const [wrSize, setWrSize] = useState<string>("");
  const [teSize, setTeSize] = useState<string>("");
  const [flexSize, setFlexSize] = useState<string>("");
  const [dstSize, setDstSize] = useState<string>("");
  const [kSize, setKSize] = useState<string>("");

  // Parse an optional single-row settings CSV and fill in the fields above,
  // so the fields stay the single source of truth (still editable after)
  const handleSettingsFile = (file: File) => {
    const reader = new FileReader();

    reader.onload = (e) => {
      const text = (e.target?.result as string) ?? "";
      const lines = text.trim().split(/\r?\n/);

      if (lines.length < 2) return;
      const headers = lines[0].split(",").map((h) => h.trim().toLowerCase());
      const values = lines[1].split(",").map((v) => v.trim());
      const row: Record<string, string> = {};

      headers.forEach((h, i) => {
        row[h] = values[i];
      });

      if (row["round size"]) setRoundSize(row["round size"]);
      if (row["roster size"]) setRosterSize(row["roster size"]);
      if (row["snake draft"]) {
        setSnakeDraft(
          row["snake draft"].toLowerCase() === "true" ||
            row["snake draft"] === "1",
        );
      }
      if (row["qb"]) setQbSize(row["qb"]);
      if (row["rb"]) setRbSize(row["rb"]);
      if (row["wr"]) setWrSize(row["wr"]);
      if (row["te"]) setTeSize(row["te"]);
      if (row["flex"]) setFlexSize(row["flex"]);
      if (row["dst"]) setDstSize(row["dst"]);
      if (row["k"]) setKSize(row["k"]);
    };
    reader.readAsText(file);
  };

  // Validate for each step that the required fields are filled
  const validateStep = () => {
    switch (progressStep) {
      case 0:
        return leagueName !== null && leagueName !== "";
      case 1:
        return true; // League settings are all optional
      case 2:
        return teamsFile !== null;
      case 3:
        return useEspnHistory
          ? espnLeagueId !== ""
          : historicalDraftFile !== null;
      case 4:
        return playersFromSources || playersFile !== null;
      case 5:
        return historicalPlayersFile !== null;
      default:
        return false;
    }
  };

  // Handle next button click
  const handleNext = useCallback(() => {
    if (validateStep()) {
      setProgressStep(progressStep + 1);
      setIsValidationError(false);
    } else {
      setIsValidationError(true);
    }
  }, [progressStep, validateStep]);

  // Async leauge creation
  const handleCreateLeague = useCallback(async () => {
    const newLeague: LeagueSimple = await createLeague({
      name: leagueName as string,
      teams: teamsFile as File,
      round_size: roundSize === "" ? undefined : Number(roundSize),
      roster_size: rosterSize === "" ? undefined : Number(rosterSize),
      snake_draft: snakeDraft,
      qb_size: qbSize === "" ? undefined : Number(qbSize),
      rb_size: rbSize === "" ? undefined : Number(rbSize),
      wr_size: wrSize === "" ? undefined : Number(wrSize),
      te_size: teSize === "" ? undefined : Number(teSize),
      flex_size: flexSize === "" ? undefined : Number(flexSize),
      dst_size: dstSize === "" ? undefined : Number(dstSize),
      k_size: kSize === "" ? undefined : Number(kSize),
    })
      .unwrap()
      .catch(() => {
        setIsCreationError(true);

        return {} as LeagueSimple;
      });

    if (useEspnHistory) {
      await syncHistoricalDrafts({
        id: newLeague.id,
        espnLeagueId: Number(espnLeagueId),
      })
        .unwrap()
        .catch(() => {
          setIsCreationError(true);
        });
    } else {
      await addHistoricalDrafts({
        id: newLeague.id,
        drafts: historicalDraftFile as File,
      }).catch(() => {
        setIsCreationError(true);
      });
    }
    await addHistoricalPlayers({
      id: newLeague.id,
      players: historicalPlayersFile as File,
    }).catch(() => {
      setIsCreationError(true);
    });
    if (playersFromSources) {
      await syncPlayers({ id: newLeague.id })
        .unwrap()
        .catch(() => {
          setIsCreationError(true);
        });
    } else {
      await addPlayers({
        id: newLeague.id,
        players: playersFile as File,
      }).catch(() => {
        setIsCreationError(true);
      });
    }
    setIsCreated(true);
  }, [
    createLeague,
    leagueName,
    teamsFile,
    historicalDraftFile,
    historicalPlayersFile,
    playersFile,
    playersFromSources,
    useEspnHistory,
    espnLeagueId,
    syncPlayers,
    syncHistoricalDrafts,
    roundSize,
    rosterSize,
    snakeDraft,
    qbSize,
    rbSize,
    wrSize,
    teSize,
    flexSize,
    dstSize,
    kSize,
  ]);

  // When on the last step, create the league
  useEffect(() => {
    if (progressStep === TOTAL_STEPS) {
      handleCreateLeague();
    }
  }, [progressStep, handleCreateLeague]);

  // Return the form
  return (
    <section className="flex flex-col items-center justify-center gap-8">
      <div className="max-w-lg text-center">
        <h1 className={title()}>{`Configure settings for a new draft.`}</h1>
        <h2 className={subtitle()}>
          Upload your {`league's`} teams and draft order, player projections,
          and historical draft data. Once your settings are configured,
          {`you'll`} be ready to{" "}
          <Link className={"text-lg lg:text-xl"} href="/draft">
            enter your draft room
          </Link>
          .
        </h2>
      </div>
      <SetupContext.Provider
        value={{
          theme,
          progressStep,
          isValidationError,
          isCreationError,
          isCreated,
          setLeagueName,
          setTeamsFile,
          setHistoricalDraftFile,
          setPlayersFile,
          setHistoricalPlayersFile,
        }}
      >
        {/* Progress bar with color and text for status updates */}
        <Progress
          color={isCreationError ? "danger" : "primary"}
          label={
            isCreationError ? (
              "Error"
            ) : isCreated ? (
              "Success"
            ) : progressStep < TOTAL_STEPS ? (
              `Step ${progressStep + 1} of ${TOTAL_STEPS}`
            ) : (
              <span className="flex items-center">
                <Spinner size="sm" />
                <span className="ml-2">Creating</span>
              </span>
            )
          }
          size="lg"
          value={(progressStep / TOTAL_STEPS) * 100}
        />

        {/* Step 1 */}
        {progressStep === 0 && (
          <div className="flex flex-col gap-4 w-full items-center">
            <Input
              className="rounded-full"
              id="league-name"
              label="League Name"
              size="lg"
              variant={theme === "light" ? "faded" : "flat"}
              onChange={(e) => setLeagueName(e.target.value)}
            />
            <p className="text-left w-full">
              Make sure to name your league something unique and memorable.
            </p>
          </div>
        )}

        {/* Step 2 */}
        {progressStep === 1 && (
          <div className="flex flex-col gap-4 w-full items-center">
            <p className="text-left w-full">
              These settings are optional — leave any field blank to use the
              default. Match them to your real league (see your league&apos;s
              roster settings) so simulation results are accurate.
            </p>
            <Input
              id="settings-csv"
              label="Settings CSV (optional)"
              size="lg"
              type="file"
              variant={theme === "light" ? "faded" : "flat"}
              onChange={(e) => {
                if (!e.target.files || e.target.files.length === 0) {
                  return;
                }
                handleSettingsFile(e.target.files[0]);
              }}
            />
            <p className="text-left w-full">
              Upload a CSV to fill in the fields below instead of typing them
              in. To see a template of this file, please{" "}
              <Link href="/settings.csv">click here</Link>. Fields stay editable
              after upload.
            </p>
            <div className="flex gap-4 w-full">
              <Input
                id="round-size"
                label="Rounds"
                size="lg"
                type="number"
                value={roundSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setRoundSize(e.target.value)}
              />
              <Input
                id="roster-size"
                label="Roster Size"
                size="lg"
                type="number"
                value={rosterSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setRosterSize(e.target.value)}
              />
            </div>
            <div className="flex items-center gap-4 w-full">
              <span>Snake Draft</span>
              <Switch isSelected={snakeDraft} onValueChange={setSnakeDraft} />
            </div>
            <p className="text-left w-full">Starting roster spots:</p>
            <div className="flex flex-wrap gap-4 w-full">
              <Input
                className="max-w-[7rem]"
                id="qb-size"
                label="QB"
                size="lg"
                type="number"
                value={qbSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setQbSize(e.target.value)}
              />
              <Input
                className="max-w-[7rem]"
                id="rb-size"
                label="RB"
                size="lg"
                type="number"
                value={rbSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setRbSize(e.target.value)}
              />
              <Input
                className="max-w-[7rem]"
                id="wr-size"
                label="WR"
                size="lg"
                type="number"
                value={wrSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setWrSize(e.target.value)}
              />
              <Input
                className="max-w-[7rem]"
                id="te-size"
                label="TE"
                size="lg"
                type="number"
                value={teSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setTeSize(e.target.value)}
              />
              <Input
                className="max-w-[7rem]"
                id="flex-size"
                label="FLEX"
                size="lg"
                type="number"
                value={flexSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setFlexSize(e.target.value)}
              />
              <Input
                className="max-w-[7rem]"
                id="dst-size"
                label="DST"
                size="lg"
                type="number"
                value={dstSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setDstSize(e.target.value)}
              />
              <Input
                className="max-w-[7rem]"
                id="k-size"
                label="K"
                size="lg"
                type="number"
                value={kSize}
                variant={theme === "light" ? "faded" : "flat"}
                onChange={(e) => setKSize(e.target.value)}
              />
            </div>
          </div>
        )}

        {/* Step 3 */}
        {progressStep === 2 && (
          <div className="flex flex-col gap-4 w-full items-center">
            <Input
              id="teams-csv"
              label="Teams CSV"
              size="lg"
              type="file"
              variant={theme === "light" ? "faded" : "flat"}
              onChange={(e) => {
                if (!e.target.files) {
                  return;
                } else setTeamsFile(e.target.files[0]);
              }}
            />
            <p className="text-left">
              This CSV file lists the teams in your league and their draft
              order. To see a template of this file, please{" "}
              <Link href="/teams.csv">click here</Link>.
            </p>
          </div>
        )}

        {/* Step 4 */}
        {progressStep === 3 && (
          <div className="flex flex-col gap-4 w-full items-center">
            <div className="flex items-center gap-4 w-full">
              <span>Use ingested ESPN draft history</span>
              <Switch
                isSelected={useEspnHistory}
                onValueChange={setUseEspnHistory}
              />
            </div>
            {useEspnHistory ? (
              <>
                <Input
                  id="espn-league-id"
                  label="ESPN League ID"
                  size="lg"
                  type="number"
                  value={espnLeagueId}
                  variant={theme === "light" ? "faded" : "flat"}
                  onChange={(e) => setEspnLeagueId(e.target.value)}
                />
                <p className="text-left w-full">
                  The opponent model will train on the pick-by-pick history
                  already ingested for this ESPN league — no CSV needed. Run the
                  owner ingest for the league first.
                </p>
              </>
            ) : (
              <>
                <Input
                  id="historical-drafts-csv"
                  label="Historical Drafts CSV"
                  size="lg"
                  type="file"
                  variant={theme === "light" ? "faded" : "flat"}
                  onChange={(e) => {
                    if (!e.target.files) {
                      return;
                    } else setHistoricalDraftFile(e.target.files[0]);
                  }}
                />
                <p className="text-left">
                  This CSV file records the round-by-round outcomes of previous
                  drafts for your league. To see a template of this file, please{" "}
                  <Link href="/historical_drafts.csv">click here</Link>.
                </p>
              </>
            )}
          </div>
        )}

        {/* Step 5 */}
        {progressStep === 4 && (
          <div className="flex flex-col gap-4 w-full items-center">
            <div className="flex items-center gap-4 w-full">
              <span>Build players from blended rankings</span>
              <Switch
                isSelected={playersFromSources}
                onValueChange={setPlayersFromSources}
              />
            </div>
            {playersFromSources ? (
              <p className="text-left w-full">
                Players and projections will come from the automatically blended
                ranking sources — no CSV needed. Check the{" "}
                <Link href="/sources">Sources page</Link> to refresh the blend
                and see per-source freshness first.
              </p>
            ) : (
              <>
                <Input
                  id="players-csv"
                  label="Players CSV"
                  size="lg"
                  type="file"
                  variant={theme === "light" ? "faded" : "flat"}
                  onChange={(e) => {
                    if (!e.target.files) {
                      return;
                    } else setPlayersFile(e.target.files[0]);
                  }}
                />
                <p className="text-left">
                  This CSV file lists current players and their projected
                  fantasy football points. To see a template of this file,
                  please <Link href="/players.csv">click here</Link>.
                </p>
              </>
            )}
          </div>
        )}

        {/* Step 6 */}
        {progressStep === 5 && (
          <div className="flex flex-col gap-4 w-full items-center">
            <Input
              id="historical-players-csv"
              label="Historical Players CSV"
              size="lg"
              type="file"
              variant={theme === "light" ? "faded" : "flat"}
              onChange={(e) => {
                if (!e.target.files) {
                  return;
                } else setHistoricalPlayersFile(e.target.files[0]);
              }}
            />
            <p className="text-left">
              This CSV file compares {`players'`} projected and actual fantasy
              football points in previous seasons. To see a template of this
              file, please{" "}
              <Link href="/historical_players.csv">click here</Link>.
            </p>
          </div>
        )}

        {/* Creation errors require the user to restart the process */}
        {isCreationError ? (
          <Code color="danger">
            There was an error creating your league. Please try again.
          </Code>
        ) : isCreated ? (
          <>
            <Code color="primary">Your league has been created!</Code>
            <p className="text-left">
              Next step? Visit the <Link href="/draft">draft page</Link>.
            </p>
          </>
        ) : null}

        {/* Next step button */}
        {progressStep < TOTAL_STEPS && (
          <div className="flex w-full justify-between">
            <Button size="lg" variant="bordered" onClick={() => handleNext()}>
              Next
            </Button>
            {/* Validation error */}
            {isValidationError && (
              <Code color="danger">Please complete this field.</Code>
            )}
          </div>
        )}
      </SetupContext.Provider>
    </section>
  );
}
