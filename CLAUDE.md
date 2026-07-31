# Fantasy Football Monte Carlo Draft Simulator

## Stack

- FastAPI backend in `backend/`, Python 3.12. The interpreter is
  `backend/venv312/Scripts/python.exe` — always use it, there is no other
  environment.
- Next.js 14 App Router frontend in `frontend/` (TypeScript, Tailwind, NextUI).
- MongoDB **7.0.28** as a native Windows service on `:27017`, database
  `fantasy-football`. Docker is unavailable (virtualization disabled in
  BIOS) and unnecessary.
- `frontend/packages/hawk-ui/` is a local package (`@hawkmode/ui`, Seahawks
  "Hawk Mode" theme) resolved through tsconfig path aliases to **source**,
  so edits are live with no rebuild step.

## Commands

- Tests: `cd backend && venv312/Scripts/python.exe -m pytest -q` —
  currently **682 passing**.
- Servers: `.claude/launch.json` defines `backend` (uvicorn, port 8000) and
  `frontend` (npm run dev, port 3000). Start them with the Claude Code
  `preview_start` tool **by name**, not by hand.

## Gotchas that waste time if you don't know them

- **The backend does not auto-reload.** uvicorn runs with `--reload` but
  falls back to StatReload because `watchfiles` isn't installed, and on
  this Windows setup it never picks up changes. Always restart the backend
  after backend edits or you will test stale code and reach false
  conclusions. (Fix if desired:
  `venv312/Scripts/python.exe -m pip install watchfiles`.)
- **Do not upgrade MongoDB past 7.0** — 8.x does not support Windows 10.
- Production builds corrupt a running dev server's `.next`. `next.config.js`
  honors `NEXT_DIST_DIR`; never delete a shared `.next` while another
  session's dev server is running.
- For scratch/diagnostic scripts, build an ODMantic engine directly:
  `AIOEngine(database="fantasy-football", client=AsyncIOMotorClient("mongodb://localhost:27017"))`
  — there is no `models.database` module. Player projections are nested:
  `player.points["2026"].projected_points`.

## Where the plan lives

- `docs/EXECUTION_PLAN_FEATURES.md` is the **source of truth for what to
  work on next**. Every task carries a status stamp. Update that file when
  work lands.
- `docs/specs/` holds the design specs individual plan rows reference.
  `docs/BRAINSTORM.md` is the feature reference. `EXECUTION_PLAN.md` at the
  root is a **historical** architecture-audit plan from 2026-07-03 —
  superseded, do not work from it.
