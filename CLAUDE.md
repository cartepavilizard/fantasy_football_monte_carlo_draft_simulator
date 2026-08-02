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
  currently **706 passing**. Update this number when it changes.
- Servers: `.claude/launch.json` defines `backend` (uvicorn, port 8000) and
  `frontend` (npm run dev, port 3000). Start them with the Claude Code
  `preview_start` tool **by name**, not by hand.

## Branching — one branch per plan row

**Never work a plan row directly on `main`.** Naming:
`<row-id-lowercase>-<short-slug>` (`h1-push-sources`,
`h5-adoption-measurement`). Merge to `main` when the row is done and you
are satisfied; delete the branch after.

**Start every row session by checking whether the row is already in
flight.** An unmerged row branch means a previous session stalled partway
— it is NOT a clean start, and re-cutting would either fail or orphan
that work.

```
git status --porcelain                    # must be empty; commit or stash first
git branch --list h1-push-sources         # does this row already have a branch?
```

- **No branch → new row.** Cut it:
  `git checkout main && git pull && git checkout -b h1-push-sources`
- **Branch exists → a prior session stalled. RESUME, do not re-cut.**
  `git checkout h1-push-sources && git log --oneline main..h1-push-sources`
  Read those commits before doing anything: they tell you how far the row
  actually got, which is the one thing the stalled session never got to
  write down.

**Branch existence is the source of truth for "is a row in flight,"** and
that is deliberate — it needs no discipline from the session that dies.
A row's plan-doc status can go stale (a session that crashes never
reaches its closeout), but the branch is created the moment work starts.
`git branch --list` with no argument shows every row currently in flight.

- **The Ringer harness follows you.** `check_task_generic.sh` runs
  `cd "$REPO"; git commit` on whatever branch is checked out — it does not
  hardcode `main`. Branch first and its auto-commits land on your branch.
- **Cut the branch BEFORE the run.** The harness's ownership gate reads
  `git status --porcelain` and fails on a dirty tree, so switching branches
  mid-run is not an option.
- Phases A–E used branch + PR (PRs #7–#14). Phases F/G/H drifted onto
  `main` when the direct-repo harness arrived — that was convenience, not a
  decision, and it is reverted by this rule. H2's commits (`a18d957`,
  `4cc6c00`) are on `main` and stay there; do not rewrite that history.

**Git protects the code, not the data.** MongoDB has no branches. Anything
that runs `POST /league/{id}/player/sync` — or otherwise writes league
documents — mutates `projected_points`, `position_tier` and
`position_max_points` in place, and checking out a different branch will
NOT roll that back. Before adopting an engine recalibration, copy the
league document or `mongodump` the collection. Branching is not a
substitute.

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
- **Read its `## Session Protocol` section before starting any plan row.**
  It is normative and cross-cutting: how to pick the next row (including
  why a `[human]` row blocks the queue instead of being routed around),
  what to do when the Ringer harness is unreachable, authority limits
  (no push / no branch deletion / no league re-sync without being asked),
  the five-step session closeout, and the contract for the handoff prompt
  a session must print before it finishes.
- **A kickoff prompt never overrides that section.** Prompts get truncated
  and paraphrased in transit; the repo does not. If a prompt and the
  Session Protocol disagree, follow the protocol and say so in the
  closeout. Do not reconstruct a missing protocol from memory — read it.
- `docs/specs/` holds the design specs individual plan rows reference.
  `docs/BRAINSTORM.md` is the feature reference. `EXECUTION_PLAN.md` at the
  root is a **historical** architecture-audit plan from 2026-07-03 —
  superseded, do not work from it.
