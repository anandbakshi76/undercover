# Plan: durable state

This plan builds the brief in `docs/DURABLE.md` on top of the harness in `docs/STATUS.md`. It has six phases. Each one builds on the last and ends with a commit through the pre-commit hook. `docs/PLAN.md` and `docs/PLAN_HARNESS.md` are finished plans and stay as they are.

SIDENOTE (from the brief): For consistency with the course, please read `labs/week2day3.ipynb` and build the solution in the same style as the lab. Where the lab and this brief differ, follow the brief.

In practice, the claim graph follows the lab: a `ClaimState` TypedDict with `add_messages`, nodes as plain functions, conditional edges named `after_agent` and `after_decide`, a `ToolNode`, a `decide` node with structured output, `interrupt()` and `Command(resume=...)` for the handler, a pay node of its own, `PostgresSaver` with `setup()`, the connection settings from LangGraph's docs (`autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row`), a `search_path` set on the connection, and `durability="sync"`. Where the brief differs, the brief wins: the decision and the payment go through the backend, not a `payments` table of the graph's own; the graph never applies the limit itself; the thread id is per attempt, not the claim id; the schema is `undercover`, not `lab`.

## The shape of the build

```
dashboard ──▶ backend :8000 (FastAPI, SQLAlchemy Core)          agent service :8002 (FastAPI)
               │ SQLite in tests, Supabase with DATABASE_URL  ──▶  run_queue, run_claim, resume_claim
               │ calculate_payout, decisions, payments,      (runs, and a notice after each sign-off)
               │ sign-off, runs                                     │
               ▲                                                    ▼
               └──────── HTTP: tools, steps, decision, payment ── queue agent (create_agent, checkpointed)
                                                                    └─ claim graph (StateGraph, per claim)
                                                                         PostgresSaver in the `undercover`
                                                                         schema on Supabase
```

The backend still holds every rule and the agent still reaches everything through the API. Three things change:

- **The backend's records move to Supabase** when `DATABASE_URL` is set. The tests run on SQLite.
- **The claim agent becomes a hand-written LangGraph graph** that saves its state after every step. It can stop and wait for a handler, and it can carry on after a crash.
- **The agent runs as a service of its own.** The backend no longer imports the agent. It asks the agent's service to start runs, and tells it when a handler signs off.

### The claim graph

```
START ─▶ agent ⇄ tools          (reads, document lookups, calculate_payout)
           │ the model has finished
           ▼
         decide ── the backend refused ──▶ agent   (the refusal goes back to the model)
           │
           ├─ declined or referred ───────────────────────────▶ END
           ├─ approved, the backend says no sign-off needed ──▶ pay ─▶ END
           └─ approved, the backend says awaiting_sign_off ──▶ wait_for_sign_off ─▶ pay ─▶ END
                                                                 interrupt()
```

- **`agent`** calls the model with the tools, as `create_agent` does, and posts the model's tool calls or reply as steps. It counts model calls in the state and raises an error at 30, which stands in for `ModelCallLimitMiddleware`.
- **`tools`** is a `ToolNode` with `get_claim`, `get_policy`, `get_claim_history`, `search_documents`, `list_sections`, `read_section` and the new `calculate_payout`. It posts each result, and each section read, as a step. A refusal from the backend comes back to the model as the backend's message.
- **`decide`** asks the model for a `ClaimDecision` (reason, decision, payout, policy section), as the lab does, and records it with `record_decision`. If the claim already has a decision (the node ran before a restart), it takes that one and doesn't call the model. If the backend refuses, the refusal goes back to the model and the graph returns to `agent`.
- **`wait_for_sign_off`** runs only when the backend has put the claim in `awaiting_sign_off`. It calls `interrupt()` with the claim and the payout. When it is resumed with the handler's sign-off, it posts a step such as "Signed off by Dana Whitfield" and moves on. Because LangGraph runs this node again from the top on resume, nothing comes before the `interrupt()` call.
- **`pay`** pays the approved payout through `pay_claim` and posts a step. It runs for every approval, and the backend still refuses a payout over the limit without a sign-off.

Retries move from middleware to LangGraph's `RetryPolicy` on the nodes: the model on errors from the provider, and the backend on network errors.

### Claim states

| State | Shown as | Set when |
|---|---|---|
| `waiting` | Waiting | Seeded, or an attempt failed with an error before a decision and the claim has been tried fewer than 2 times |
| `in_progress` | In progress | A claim agent starts on it. It stays in progress after an approval of $1,000 or less until it is paid, and goes back to in progress when a handler signs off, until it is paid |
| `awaiting_sign_off` | Waiting for sign-off | The agent approves a payout over $1,000 |
| `decided` | Decided | Declined or referred, or approved and paid |
| `needs_handler` | Needs a handler | A second attempt failed with an error before a decision |

### Choices for you to review

1. **"In progress" means the agent still owes the claim work.** That covers a claim being worked, an approval not yet paid, and a claim signed off but not yet paid. The brief asks for the signed-off case, and I've used the same rule for small approvals. The agent's next run then finds every claim it has to carry on with from one question to the backend, "which claims are in progress?", whether the agent crashed, was stopped, or was away during a sign-off.
2. **The decision is a node, as in the lab.** The model reads and looks things up with its tools, then `decide` asks it for a structured decision and records it. A claim agent can no longer finish without deciding. `record_decision` and `pay_claim` stop being tools. Paying was always going to leave the model; the decision leaves too, to match the lab. I use `method="function_calling"` for the structured output, so it works with DeepSeek on OpenRouter and with the stub model.
3. **One thread per attempt, stored on the claim.** `start_claim` gives the claim a thread id, `<claim_id>-<run_id>-<attempt>`, such as `CLM-1008-RUN-1a2b3c4d-1`. `get_claim` returns it. A stopped claim keeps its attempt count, so it keeps its thread and carries on from its last checkpoint. A claim that fails with an error is released, its attempt count goes up and its next attempt gets a new, empty thread. The Langfuse session is named after the thread id, which starts with the claim id.
4. **What counts as stopped and what counts as failed.** An error inside the graph (from the model, the backend or the cap of 30 calls) is a failure: `work_claim` releases the claim, as it does today. Ctrl-C (a `KeyboardInterrupt`) or a killed process isn't caught, so the claim stays in progress with its checkpoint, and the next run carries on with it. Releasing a claim that already has a decision changes nothing. A claim that has been decided only needs paying, and that is never a reason to work it again.
5. **The backend no longer cleans up on startup.** Today it releases claims left in progress and fails running runs when it starts. Those claims now belong to the agent, which carries on with them. That cleanup moves into the agent's runs: each run first calls `resume`, which:
   - carries on each claim in progress on its thread. With no checkpoint yet, it starts the claim on the same thread. At a sign-off, it resumes with `Command(resume=...)` if the handler has signed off.
   - resumes each queue run left `running`. The queue agent gets the checkpointer too, with the run id as its thread, so `invoke(None, config)` picks up its to-do list where it stopped and it finishes with a summary. If it was stopped in the middle of `work_claim`, that call runs again. `start_claim` treats a repeat start of a claim in the same run as done, and `work_claim` reports a claim that's already finished in its run without working it again.
6. **The agent's service is the agent's way in.** `agent/service.py` is a small FastAPI app on port 8002 (or `UNDERCOVER_AGENT`), with `run_queue` (takes `limit`), `run_claim` and `resume_claim`. The first two start the run through the backend, return it, and work in the background. The backend's `run_queue` and `run_agent` pass the request on to it, and return 503 "The agent isn't running." if it can't be reached. After a sign-off is saved, the backend calls `resume_claim` as a background task, so it doesn't wait for the payment and a failed call leaves the sign-off in place. The backend no longer imports `agent/`, as it won't in the cloud. In one process, a lock stops two threads working the same claim at once.
7. **Connections.** The backend's SQLAlchemy engine has `pool_size=4, max_overflow=0, pool_pre_ping=True`. The agent's checkpointer uses a `psycopg_pool.ConnectionPool` with `max_size=4`. During the live run the three processes are the backend, the agent's service and, for the stop-and-carry-on check, `uv run -m agent`, which makes at most 12. Uvicorn's reloader process opens none.
8. **The database code.** The tables are SQLAlchemy Core `Table` objects in `backend/db.py`, created with `metadata.create_all`. On Postgres, `CREATE SCHEMA IF NOT EXISTS undercover` runs first and every connection gets `options="-c search_path=undercover"`, so tables have no schema in the code. Queries are written once in SQLAlchemy Core. Two SQLite-only habits go: `INSERT OR IGNORE` becomes a check, and ordering by `rowid` becomes ordering by an id column. `--reset` deletes the SQLite file, or on Postgres drops and recreates the `undercover` schema, which clears the checkpoints too. The agent's `setup()` makes its tables again when it next starts, so stop `npm run dev` before reseeding.
9. **`.env` is loaded only by the entry points.** Today `agent/main.py` loads `.env` when it's imported, so a pytest run would pick up `DATABASE_URL`. That moves to `agent/__main__.py` and to `npm run dev`, which uses `uv run --env-file .env`. The test fixtures also remove `DATABASE_URL` and the Langfuse keys from the environment, so the tests never reach the network.
10. **The payout rules.** `backend/payout.py` takes the rules from `labs/week2day1_payout.py`, reading the plans and the approval limit from the seeded settings (`plans.json` and `company.json`) rather than from files and a constant. `POST /api/policies/{policy_id}/payout` (`calculate_payout`) takes the benefit, the lines and what others paid, and returns the calculation, the payout and `needs_sign_off`. Every input is required, as the API rules ask, so a line's `quantity`, `nights` and `hours` and `paid_by_others` have no defaults. The agent's `calculate_payout` tool imports the `Line` and `Benefit` types from `backend/payout.py`, so the descriptions the model sees are written once, and it calls the API, so the rules run in the backend.
11. **Langfuse.** When the Langfuse keys are set, each graph call gets a Langfuse `CallbackHandler`, with `langfuse_session_id` set to the claim's thread id in the config's metadata. The work before a pause and the work after a sign-off are two traces in the same session. The queue agent is traced too, in a session named after its run. Without keys there are no callbacks.
12. **Without Supabase** (no `DATABASE_URL`), the agent uses LangGraph's `InMemorySaver`. A pause and a resume then work only inside one process, which is enough for the tests. Supabase is where the product runs.
13. **Not in this build:** rejecting a held payout (the brief leaves it for later), running claims in parallel, a button to put a `needs_handler` claim back in the queue, and row-level security. Supabase's advisor flags the lab's tables in the `lab` schema for having row-level security off. The `undercover` tables will be in the same position. Supabase's public API only serves the `public` schema unless you add others, so neither schema is reachable through it, and the backend connects as the tables' owner, which row-level security doesn't restrict. If you want it turned on anyway, it's a small addition to Phase 2.

---

## Phase 1: The payout rules and tracing

**Builds:** the payout operation in the backend, the claim agent's payout tool and Langfuse tracing, on today's `create_agent` claim agent.

- `backend/payout.py`: the rules from the lab, taking the plans and the approval limit as inputs. `calculate_payout` in `backend/main.py` loads the policy, the plans and the company settings and runs them.
- `agent/claim.py`: a `calculate_payout(policy_id, benefit, lines, paid_by_others)` tool that calls the operation.
- `backend/instructions/claim.md`: work out the payout with `calculate_payout` and record the payout it returns. The agent still judges what's covered, which lines count and what counts as one item.
- `agent/main.py`: `tracing(session_id)` returns the config's callbacks and metadata for Langfuse, or nothing without keys. The claim agent passes its thread id and the queue agent its run id. `load_dotenv` moves to `agent/__main__.py`, and the test fixtures remove `DATABASE_URL` and `LANGFUSE_*` from the environment.

**Why this way:** the brief asks for these first, and they don't depend on the graph. The payout rules are business logic, so they go in the backend with the rest of the rules, where the agent, the tests and a handler all get the same answer.

**Success criteria**

- [x] pytest: `calculate_payout` applies the per-item limit (and the Electronics Plus limit with that add-on), the $100 cash cap, the hotel and meal allowances, what others paid, the deductible where it applies, the benefit's maximum and the trip cost cap, with each step in the calculation
- [x] pytest: `needs_sign_off` is true over the limit in `company.json` and false at or under it; changing the limit in the seeded settings changes the answer
- [x] pytest: an unknown policy returns 404, an unknown benefit or line kind returns 422, and the OpenAPI rule test still passes
- [x] pytest (stub model): the claim agent's `calculate_payout` call reaches the backend and its result comes back to the model, and is posted as a step
- [x] pytest: with Langfuse keys set (and the handler replaced by a stand-in), the claim agent's config carries a callback handler and `langfuse_session_id` equal to its thread id; with no keys it carries no callbacks
- [x] pytest: importing `agent` doesn't load `.env`
- [x] `curl -X POST http://localhost:8000/api/policies/<policy>/payout` with two lines and a paid-by-others amount returns the calculation and the payout
- [x] Live check (a cent or two): after a reseed, `uv run -m agent --claim CLM-1001` prints a decision, its steps show a `calculate_payout` call, and Langfuse's API lists a trace for it in the claim's session
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 2: The database on SQLAlchemy Core, and on Supabase

**Builds:** the backend's database on SQLite or Supabase, and a repeat of a decision or a payment counting as done.

- `pyproject.toml`: `sqlalchemy` joins the main dependencies (it's only in the `server` group today, which `uv sync` doesn't install).
- `backend/db.py`: the tables as SQLAlchemy Core `Table`s, with an id column in place of `rowid` for `run_claims` and `runs`. `engine()` is SQLite at `UNDERCOVER_DB` (or `backend/undercover.db`), or, with `DATABASE_URL`, Postgres through psycopg with the `undercover` search path and at most 4 connections. `init`, `seed` and `reset` work on both.
- `backend/main.py`: every query rewritten in SQLAlchemy Core, with the same API.
- Repeats count as done:
  - `record_decision` on a claim with the same decision and payout returns the claim. A different decision is still refused with 409.
  - `pay_claim` on a claim already paid that amount returns the claim. A different amount is still refused.
  - Both use a conditional `UPDATE ... WHERE decision IS NULL` (and `WHERE paid_amount IS NULL`), so two processes can't both win.

**Why this way:** Core keeps the SQL visible and the same on both databases, without an ORM. Setting the schema on the connection keeps every query free of `undercover.`, so the tests run the very same code on SQLite.

**Success criteria**

- [x] pytest: every existing backend test passes on SQLite, with no network
- [x] pytest: `engine()` is SQLite without `DATABASE_URL`, and with it is Postgres with a pool of 4, no overflow and the `undercover` search path (checked on the engine object, without connecting)
- [x] pytest: a repeated decision returns the claim with one decision; a different decision returns 409
- [x] pytest: a repeated payment returns the claim with one payment; a different amount returns 409
- [x] pytest: `--reset` restores 25 waiting claims on SQLite; the latest run and the order of a run's claims come back right without `rowid`
- [x] `uv run --env-file .env -m backend.db --reset` seeds Supabase; the Supabase MCP server lists `claims`, `records`, `runs`, `run_claims` and `steps` in `undercover`, with 25 claims, and nothing new in `public`
- [x] With `npm run dev`, `curl http://localhost:8000/api/claims` returns 25 waiting claims from Supabase
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 3: The claim agent as a graph

**Builds:** the claim graph with its checkpointer, the pause before a payout over the limit, and the backend's new statuses.

- `agent/claim.py`: `ClaimState` (messages, claim id, run id, instructions, model calls, decision), the five nodes and two conditional edges above, `RetryPolicy` on each node, and `claim_graph(llm, checkpointer)`. `work_started` runs the graph on the claim's thread with `durability="sync"` and streams it. On an error it releases the claim as today. On a pause it reports "approved $2,240.00, waiting for sign-off".
- `agent/checkpoints.py`: `checkpointer()`. With `DATABASE_URL`, a `PostgresSaver` on a `ConnectionPool` (`max_size=4`, the lab's three settings, the `undercover` search path), after `CREATE SCHEMA IF NOT EXISTS undercover` and `setup()`. Without it, `InMemorySaver`. One per process.
- `backend/main.py`:
  - `start_claim` sets the claim's `thread_id`. Starting a claim already in progress in the same run returns the claim.
  - An approval of $1,000 or less leaves the claim `in_progress`, and `pay_claim` moves it to `decided`.
  - `sign_off` records who signed off and when, moves the claim to `in_progress` and no longer pays.
  - `release_claim` changes nothing on a claim with a decision.
  - The claim view's payment carries the sign-off: who signed off and when.
- `backend/instructions/claim.md`: the order of work stays. The end changes: when you've worked it out, finish with your decision, the payout, your reasons and the section, and the system records it and pays. No `pay_claim`.
- `pay_claim` and `record_decision` are no longer claim agent tools.

**Why this way:** these are the lab's graph and checkpointer, with the backend still holding the rules. The graph asks the backend whether a claim needs a sign-off and never compares the payout with the limit itself, so the rule lives in one place.

**Success criteria** (pytest with stub models and `InMemorySaver`, never a real model or database)

- [x] An approval of $420.00 runs agent → tools → decide → pay: the claim is `decided` and paid once, the steps include the tool calls, the decision and the payment, and the line is "CLM-1001: approved, $420.00 paid"
- [x] The pause: an approval of $2,240.00 leaves the claim `awaiting_sign_off` with no payment, and the graph's state on the claim's thread has `next == ("wait_for_sign_off",)` and an interrupt with the claim id and payout
- [x] A decline ends without a payment; a refusal from `record_decision` goes back to the model, which decides again
- [x] Stop and carry on: a stub that raises `KeyboardInterrupt` after the claim's first tool calls leaves the claim `in_progress` with `attempts` 0. A new graph on the same checkpointer carries on from the last checkpoint on the same thread (the model isn't asked again for the steps already done), and the claim ends with one decision and `attempts` still 0
- [x] A rerun of `decide` on a claim that already has a decision takes the recorded one without calling the model
- [x] A stub that raises an error releases the claim to `waiting` with `attempts` 1, and its next attempt runs on a new, empty thread
- [x] A stub that keeps calling tools fails at 30 model calls and is released
- [x] Backend: an approval at or under the limit is `in_progress` until `pay_claim`, then `decided`; `sign_off` makes the claim `in_progress` with no payment; paying over the limit without a sign-off is still refused; releasing a decided claim changes nothing
- [x] `checkpointer()` is `InMemorySaver` without `DATABASE_URL`; with it, the pool has `max_size=4` and the three settings (checked without connecting)
- [x] The claim agent's tools are the seven read and lookup tools, and nothing under `agent/` refers to `data/`
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 4: The agent's service, sign-off and carrying on

**Builds:** the agent's service, the sign-off that picks the claim back up, and every run finishing what was left before.

- `agent/service.py`: FastAPI on port 8002 with `health`, `run_queue` (`limit`), `run_claim` and `resume_claim`, each with an `operation_id`, a one-line docstring and no optional inputs. `run_queue` and `run_claim` start the run through the backend, pass on the backend's refusal if there is one, and work in a background task. `resume_claim` carries on one claim in the background.
- `agent/resume.py`: `resume(skip_run_id)` carries on the claims in progress, then the queue runs left running, as in choice 5. It runs first in every run, from the service or from `uv run -m agent`.
- `agent/queue_agent.py`: the queue agent's `create_agent` gets the checkpointer, with the run id as its thread. `work_claim` reports a claim already finished in its run.
- Backend:
  - `run_queue` and `run_agent` forward to the agent's service, with 503 if it isn't running.
  - `sign_off` calls `resume_claim` in a background task, logging a failed call.
  - The startup cleanup goes.
  - New operations: `list_unfinished_claims` (in progress, with the thread, the run and the sign-off) and `list_running_runs`.
- `package.json`: `dev` starts the backend, the agent's service and the dashboard. `dev:backend` and `dev:agent` use `uv run --env-file .env`.

**Why this way:** the agent's state lives in the checkpointer and the backend says which claims are unfinished, so any run in any process can pick up the work. That's what the move to the cloud needs, where the agent runs on its own beside the app.

**Success criteria** (pytest with stub models; the backend's call to the service is replaced by a stand-in)

- [x] Sign-off resumes and pays once: a claim paused for sign-off, signed off by HND-01, is `in_progress` with no payment. The backend has called `resume_claim` for it. Resuming pays it once, and the claim is `decided` with the payment showing HND-01 and the time of the sign-off. A second resume pays nothing more
- [x] A sign-off still stands when the agent's service can't be reached, and the response doesn't wait for the payment
- [x] `run_queue` and `run_agent` return 503 with "The agent isn't running." when the service can't be reached, and change nothing
- [x] A run first carries on a claim signed off while the agent was away, and a claim stopped partway, before it starts new claims
- [x] A queue run stopped in the middle of a claim is finished by the next run: the claim is decided once, the stopped run's to-do list is finished and the run is `finished` with a summary
- [x] The service's `run_claim` on a claim that isn't waiting returns the backend's 409; `run_queue` with `limit` 0 returns 422; every service operation passes the same OpenAPI rule test as the backend
- [x] A backend restart leaves claims in progress and running runs as they were
- [x] `npm run dev` starts all three on Windows; `curl http://127.0.0.1:8002/api/health` returns ok
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 5: The dashboard and end-to-end tests

**Builds:** the dashboard for the new sign-off, and end-to-end tests with a stand-in for the agent's service.

- `api.ts`: the claim's `thread_id`, and the payment with its sign-off.
- **Claim page:** the button says **Sign off**. After a sign-off the claim shows In progress and the page refreshes every two seconds until it is paid. The payment reads "Paid $2,240.00 on 22 Nov 2026 at 14:03, signed off by Dana Whitfield at 14:02". An approval not yet paid shows "Payment pending".
- **Run buttons:** a 503 from the backend shows its message in Raspberry, on the queue page and the claim page.
- `e2e/agent-stand-in.mjs`: a small Node server on port 8003 that records the requests it gets and returns them from `GET /requests`. `playwright.config.ts` starts it, and gives the e2e backend `UNDERCOVER_AGENT=http://127.0.0.1:8003`.
- E2E spec `durable.spec.ts`, standing in for the agent through the API (`start_run`, `start_claim`, `record_decision`, `pay_claim`) as before:
  - An approval over $1,000 shows Waiting for sign-off. Signing off on the dashboard shows In progress, the stand-in receives `resume_claim` for that claim, and after a payment through the API the page shows Paid, with the handler and the time
  - `pilot.spec.ts` is updated for the new button and for sign-off no longer paying

**Why this way:** the e2e tests check the dashboard and the backend together without a model. The stand-in proves the sign-off reaches the agent's service without ever touching the one `npm run dev` starts.

**Success criteria**

- [ ] vitest: a claim waiting for sign-off shows a Sign off button; signing off calls `sign_off` and shows the claim In progress and refreshing
- [ ] vitest: a paid claim with a sign-off shows the amount, the handler's name and both times; an unpaid approval shows Payment pending
- [ ] vitest: a 503 on Run agent and on Run the queue shows the backend's message
- [ ] `npm run e2e` passes every spec, with the stand-in recording the `resume_claim` request
- [ ] Playwright MCP: a claim's page after sign-off in light and dark mode, with no horizontal scroll at phone width
- [ ] `npm run check:ts` passes with coverage of 80% or more

## Phase 6: A live run on Supabase and the handover

**Builds:** the live checks from the brief, and the docs.

- Docs:
  - `docs/SPEC.md`: Working the queue covers waiting for a sign-off, carrying on after a restart, and repeats counting as done. The Rules stay as they are.
  - `docs/TECH_STACK.md`: Supabase and SQLAlchemy Core, the claim graph and its checkpointer, the agent's service and Langfuse.
  - `docs/PROCESS.md`: `npm run dev` starts three parts, and `.env` is passed by `npm run dev` only.
  - `docs/STATUS.md`.

**Success criteria**

- [ ] Live run on the default model: stop `npm run dev`, `uv run --env-file .env -m backend.db --reset`, `npm run dev`, then Run the queue with 5. Five claims are worked and the other 20 stay Waiting
- [ ] A claim over the limit waits for sign-off. Signed off on the dashboard through the Playwright MCP server, it shows as Paid within five seconds, with the handler and the time
- [ ] Stop and carry on: `uv run -m agent --claim <a waiting claim>` is killed (`taskkill /PID <pid> /T /F`) once its first steps show. The claim is In progress. The next `uv run -m agent --limit 1` carries it on with the same `thread_id`, its `attempts` unchanged, and one decision
- [ ] During the live run, the Supabase MCP server shows no more than 12 connections in `pg_stat_activity` whose user is postgres
- [ ] Langfuse's API: the held claim's session (its thread id) has traces from before and after the sign-off
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md` and `docs/STATUS.md` describe the product as it now is
- [ ] With `.env` moved out of the way, `npm run check` passes: ruff, pyright, pytest at 80% or more, Prettier, ESLint, tsc, vitest at 80% or more, and Playwright
