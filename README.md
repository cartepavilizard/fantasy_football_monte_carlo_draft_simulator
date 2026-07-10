# Monte Carlo Fantasy Football Draft Simulator

### Featuring [FastAPI](https://fastapi.tiangolo.com/), [NextUI](https://nextui.org/), and [ODMantic](https://art049.github.io/odmantic/)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![Pydantic v2](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/pydantic/pydantic/5697b1e4c4a9790ece607654e6c02a160620c7e1/docs/badge/v2.json)](https://pydantic.dev)

![image](https://github.com/user-attachments/assets/ea7f2a26-46d9-45c6-bd41-2fe6761d8e82)

## How Does The Simulator Work?

In previous fantasy football drafts, I have struggled to pick the right players. At the start, I selected players whose point projections were not dramatically different than the projections of players who were still available in later rounds. At the end, I failed to draft backups for players who were at the most risk of injury.

This project is my attempt to solve both of those problems. To better estimate whether a player is especially valuable in a given round, a Monte Carlo simulation uses a logistic regression of historical draft data to guess which players will be available in later rounds of the draft, ensuring that I never pick a player who is easily replaceable. And to more accurately anticipate which players need strong, rostered backups (not streamers), injuries and other setbacks are randomly assigned to players, based on historical data, so I always load up on talented individuals in my most at-risk positions.

## Running The Simulator

To get the simulator started, you must have [Docker](https://docs.docker.com/get-started/get-docker/) installed on your machine.

Clone this repository, and run in its directory:

```bash
docker-compose up -d
```

The frontend of the application will then be viewable on `localhost:3000`.

## Automated Data Pipeline

This fork adds two capabilities on top of the original CSV workflow (see
`docs/ARCHITECTURE_REVIEW.md` for the full design):

### Ranking aggregation (no players CSV needed)

Player rankings, ADP, and projections are pulled automatically from
Sleeper, FantasyFootballCalculator, ESPN, FantasyPros, and Yahoo, then
normalized (per-source positional z-scores) into one blended projection.
The Fantasy Footballers Ultimate Draft Kit joins the blend via a file
drop of your subscriber CSV export — deliberately never scraped.

- **Sources** page in the frontend: one-click refresh, per-source
  freshness, UDK upload, and the refresh schedule (pause it on draft day).
- Setup wizard: toggle "Build players from blended rankings" instead of
  uploading a players CSV; toggle "Use ingested ESPN draft history"
  instead of a historical drafts CSV.
- API: `POST /rankings/refresh`, `GET /rankings/blended`,
  `GET /rankings/status`, `POST /rankings/udk`,
  `POST /league/{id}/player/sync`, `GET|POST /rankings/schedule`.
- Scheduled refresh is controlled by `RANKINGS_REFRESH_ENABLED` and
  `RANKINGS_REFRESH_INTERVAL_HOURS` (daily by default in docker-compose).
- Credentials go in `.env`: `ESPN_S2`/`ESPN_SWID` (private-league ESPN
  access), `YAHOO_CLIENT_ID`/`YAHOO_CLIENT_SECRET`/`YAHOO_REFRESH_TOKEN`
  (Yahoo OAuth app), and optionally `FANTASYPROS_API_KEY`.

### Owner tendency profiles

With commissioner access, pick-by-pick draft history is ingested per
owner from your ESPN leagues (`POST /owners/ingest/{espn_league_id}`),
backfilled with historical ADP, and distilled into frequency/average
tendency profiles: position frequency by round, reach behavior vs ADP,
run participation, and inferred post-miss behavior. Map profiles onto a
league with `POST /league/{id}/owners/map`, and the Monte Carlo engine
blends each known owner's tendencies with the generic model (sample-size
gated) and samples picks with reach-aware variance. Validate the lift on
your own history with `POST /owners/backtest`, and disable everything
with `USE_OWNER_PROFILES=false`.

## Manual CSV Setup (original workflow, still supported)

To correctly return results for your league, you'll need to tune the variables in `backend/models/config.py` to your league's settings and create a couple of CSV files:

### Players File

This file includes player names, positions, and projected points for the current NFL season from any resource you choose, like [The Athletic](https://www.nytimes.com/athletic/5475262/2024/05/29/2024-fantasy-football-cheat-sheet-generator-customizable-rankings-and-projections-tool/). The projected points should align with your league's scoring rules.

The following columns are required:

- Season
- Player
- Pos
- Team
- Projected FFP

### Historical Players

Like `players.csv`, this file should include player names, positions, and projected points for previous seasons. Additionally, it should include a column that tallies the actual number of points a player scored in that year.

The following columns are required:

- Season
- Player
- Pos
- Team
- Projected FFP
- Actual FFP

### Historical Drafts

This file informs the logistic regression that models how other teams in your league are predicted to pick. Ideally, it should be a reformatted download of your league's draft history for previous years. For example, [Sleeper](https://docs.sleeper.com/) provides an API for accessing draft histories.

The following columns are required:

- Pick
- Pos

### Teams

This file provides the details for each team in your league. Other information, like whether the simulation should replicate a snake draft, should be contained within the `.env` file for FastAPI, which is read by `backend/models/config.py`.

The following columns are required:

- Name
- Order `(draft order)`
- Owner
- Simulator `(True/False or 1/0)`

Technically, more than one team may be the simulator – or be owned by the user executing the program - allowing the user to test every pick of the draft.

## Screenshots

The following screenshots are samples from the NextUI frontend, which automatically includes both `dark` and `light` themes that require very little customization.

### Dark Theme

![image](https://github.com/user-attachments/assets/600ab879-de0b-470e-bfad-8c6098e25a65)

### Light Theme

![image](https://github.com/user-attachments/assets/c1be0c54-2667-4d4d-8c45-12cb883cd16c)

## Contributing

The program is ready for 2024 fantasy football drafts, but it is still a work in progress. If you find it valuable but notice bugs, need changes, or require additional features, [open an issue](https://github.com/joewlos/fantasy_football_monte_carlo_draft_simulator/issues) or fork to [start a PR](https://github.com/joewlos/fantasy_football_monte_carlo_draft_simulator/pulls).

Thank you for your interest, and good luck in your draft!
