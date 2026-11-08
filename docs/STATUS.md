# Status

The claims pilot from `docs/SPEC.md` is built, following `docs/PLAN.md` (all six phases done). The harness from `docs/HARNESS.md` is built on top of it, following `docs/PLAN_HARNESS.md` (all six phases done). The harness changed the database schema, so a database from the pilot needs `uv run -m backend.db --reset` once.

## What's built

- `backend/`: FastAPI and SQLite. The database (`backend/undercover.db`, or the file in `UNDERCOVER_DB`) is seeded from `data/` on first start, leaving out `data/evals/` and `data/security/`.
  - Read: `list_claims`, `get_claim`, `get_policy`, `get_claim_history` (earlier claims only, no personal details), `get_instructions`, `list_handlers`, `list_steps`
  - Documents: `list_sections`, `read_section` and `search_documents` look up the numbered sections of the policy wording and the claims manual (`backend/documents.py`)
  - Write: `add_step`, `record_decision`, `pay_claim`, `sign_off`, `run_agent`, `run_queue`
  - Runs: `start_run`, `get_run`, `get_latest_run`, `update_todos`, `finish_run`, `list_waiting_claims`, `start_claim`, `release_claim`. A run has a cap on the number of claims, enforced by `start_claim`, and a to-do list.
  - Claim states: `waiting`, `in_progress`, `awaiting_sign_off`, `decided`, `needs_handler`. A claim that fails before a decision goes back to `waiting`; on its second failure it is `needs_handler`. On startup the backend releases any claim left `in_progress`. Approving a payout over $1,000 moves a claim to `awaiting_sign_off`, and `pay_claim` refuses it until a handler signs off. Signing off pays the claim.
  - `run_agent` (a run with a cap of 1) and `run_queue` (takes `limit`) run the agent in a background task. Steps from every attempt are kept.
- `backend/instructions/queue.md` and `backend/instructions/claim.md`: the agent's instructions for running the queue and for working a claim, served by `get_instructions`.
- `agent/`: the UnderCover Agent on OpenRouter (`deepseek/deepseek-v4.1-flash` with low reasoning effort, or `MODEL_NAME`).
  - `agent/claim.py`: the claim agent. `work_claim(run_id, claim_id)` starts a claim in a run, works it with a new `create_agent` and a new thread, releases it if it fails, and returns one line such as "CLM-1001: approved, $420.00 paid". Its tools call the backend: `get_claim`, `get_policy`, `get_claim_history`, `search_documents`, `list_sections`, `read_section`, `record_decision` and `pay_claim`. Middleware retries the model and the backend, and stops an agent after 40 model calls. Each tool call, result, section read and reply is posted as a step.
  - `agent/queue_agent.py`: the queue agent. `run_queue(run_id)` plans the run with `write_todos`, finds claims with `list_waiting_claims` and hands each to a claim agent with `work_claim`, one at a time. A middleware check sends it back when it tries to finish with waiting claims still within its cap (up to 3 times). Its final reply is the run's summary; if it fails, the run is marked `failed`.
  - `uv run -m agent --limit 5` runs the queue on five claims, and `uv run -m agent --claim CLM-1001` works one claim, against a running backend.
- `dashboard/`: React, Vite and TypeScript in the UnderCover brand. It starts in the system's light or dark setting, and the Dark mode button in the header switches it and remembers the choice in the browser.
  - `#/`: the queue, with a count for each state, a Claims to work box (starting at 5) and a Run the queue button, and the latest run's to-do list, progress and summary, refreshed while the run is running
  - `#/claims/<id>`: the claim, its policy, the decision with its reason and section, the payment, a Run agent button, what the agent read, the agent's steps as it works, and sign-off by a handler for payouts over $1,000
  - `#/instructions`: the agent's instructions for running the queue and for working a claim, read only
- `e2e/`: Playwright tests of the queue, the steps and decision on a claim, sign-off of a payout over $1,000, dark mode (`pilot.spec.ts`), and the sections read, a claim that needs a handler, a run's to-do list being ticked off, and both sets of instructions (`harness.spec.ts`). They run their own backend (port 8001, `e2e/test.db`, rebuilt each run) and dashboard (port 5174), and stand in for the agent through the API, so they never call a model.

## Running it

- `npm run dev` starts the backend on port 8000 and the dashboard on http://localhost:5173
- Set Claims to work and press Run the queue, or open a claim and press Run agent. Each run calls the model in `MODEL_NAME`, or DeepSeek V4.1 Flash by default; `MODEL_NAME=openrouter/free` in `.env` is free. For trials, reseed first and keep the cap at 5
- From the command line, with the backend running: `uv run -m agent --limit 5` or `uv run -m agent --claim CLM-1001`
- `uv run -m backend.db --reset` rebuilds the database from `data/`
- `npm run check` runs every check

## Notes

- Unused: `policy_wording_short.md`, `knowledge.json` and `security/`.
- The agent's accuracy hasn't been measured against `data/evals/`. In the harness's live run (DeepSeek V4.1 Flash, capped at 5, after a reseed) the queue agent chose the five oldest claims, wrote and ticked off its to-do list, and left the other 20 waiting. It approved and paid CLM-1001 ($420.00), approved CLM-1008 ($2,240.00) and CLM-1011 ($1,150.00), which wait for sign-off, referred CLM-1018 (no proof of value for a $420 jacket, manual section 5) and declined CLM-1010 (treatment while intoxicated, wording section 9). Each claim agent read 10 to 14 sections; nothing failed or went back to the queue.
- Stopping `npm run dev` from a tool on Windows can leave uvicorn and vite running; stop the whole process tree (`taskkill /PID <npm pid> /T /F`).
