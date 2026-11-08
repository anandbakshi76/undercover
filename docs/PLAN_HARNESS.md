# Plan: the agent's harness

This plan builds the brief in `docs/HARNESS.md` on top of the pilot in `docs/STATUS.md`. It has six phases. Each one builds on the last and ends with a commit through the pre-commit hook. `docs/PLAN.md` is the pilot's finished plan and stays as it is.

## The shape of the build

```
dashboard ──HTTP──▶ backend (FastAPI + SQLite) ◀──HTTP── queue agent (create_agent)
                     │ runs, to-do lists, attempts,          │ write_todos, list_waiting_claims,
                     │ the cap, sections of the documents    │ work_claim ──▶ claim agent (fresh each claim)
                     └─ starts the queue agent in the background     get_claim, get_policy, get_claim_history,
                                                                     search_documents, list_sections,
                                                                     read_section, record_decision, pay_claim
```

The pilot fed the agent one claim at a time from a loop in the backend. Now the backend starts one **queue agent**. It plans the run as a to-do list, picks the order, and hands each claim to a **claim agent**: a new `create_agent` run with an empty conversation that works one claim and reports back in a line. The claim agent no longer gets the whole policy wording. It searches the wording and the claims manual and reads the sections it needs.

The split of responsibilities from the pilot stays the same. The backend holds the rules, and the agents only use the API. That now covers the new rules too: the cap on a run, the attempt count, and never deciding or paying a claim twice are all enforced by the backend. A confused model can't break them.

### Claim states

One new state, for a claim the agent couldn't finish:

| State | Shown as | Set when |
|---|---|---|
| `waiting` | Waiting | Seeded, or an attempt failed before a decision and the claim has been tried fewer than 2 times |
| `in_progress` | In progress | A claim agent starts on it |
| `awaiting_sign_off` | Waiting for sign-off | The agent approves a payout over $1,000 |
| `decided` | Decided | The agent declines or refers, approves $1,000 or less, or a handler signs off |
| `needs_handler` | Needs a handler | A second attempt failed before a decision |

### Choices for you to review

1. **Two agents, both built with `create_agent`.** The queue agent's tools are `write_todos`, `list_waiting_claims` and `work_claim`. `work_claim` runs the claim agent with a new thread, so the claim agent never sees the queue agent's conversation or another claim's, and the queue agent only ever sees one line per claim. That keeps each claim's context small and each step's cost flat. We add no new framework, as the brief asks.
2. **Our own `write_todos` tool, not LangChain's `TodoListMiddleware`.** The middleware keeps the list in the agent's memory with three states (pending, in progress, completed). The brief needs a fourth state, left for a handler, and needs the list stored where the dashboard can see it. Our tool takes the whole list each time, the same way the middleware's does, and saves it to the backend under the run.
3. **What "left for a handler" means on the to-do list.** A claim the agent referred, or one that failed twice (`needs_handler`). A claim approved over $1,000 is done for the agent, and the queue already shows it waiting for sign-off.
4. **Runs are records.** A new `runs` table holds each run's cap, to-do list, the claims it has worked, its status (`running`, `finished`, `failed`) and the agent's closing summary. A run on one named claim (Run agent on the claim page) is a run with a cap of 1 that goes straight to the claim agent, with no queue agent and no to-do list. The queue page shows the latest queue run.
5. **The cap is enforced by the backend.** Starting a claim in a run is refused once the run has worked as many different claims as its cap. A retry of a claim already in the run doesn't count again, so a failed claim can still get its second try. The queue agent is told the cap, but the backend doesn't rely on it.
6. **Failure handling at three levels:**
   - **Middleware retries the small things.** `ModelRetryMiddleware` retries provider errors and timeouts with backoff. `ToolRetryMiddleware` retries the backend calls on network errors. `ModelCallLimitMiddleware` stops a claim agent that loops (40 model calls), so it fails cleanly instead of running up cost.
   - **The claim's attempt fails cleanly.** If the claim agent raises an error, or finishes without a decision, `work_claim` calls a new `release_claim` operation. The backend adds 1 to the claim's attempts and puts it back to `waiting`, or to `needs_handler` on the second failure. It records an error step either way, and `work_claim` returns a one-line report. The run goes on.
   - **Nothing stays stuck in progress.** If the backend restarts during a run, it releases any `in_progress` claim on startup, which counts as a failed attempt, and marks any `running` run `failed`. If the queue agent itself fails, its run is marked `failed` with the error, and every claim it didn't reach is still `waiting`.
7. **No claim is decided or paid twice.** That already holds in the pilot: `record_decision` refuses a decided claim, `pay_claim` refuses a paid one, and only a `waiting` claim can be started. The claim agent's instructions also say to check `get_claim` first and stop if the claim is already decided. Retried tool calls are safe for the same reason.
8. **A check for missed claims, in middleware.** A small `after_model` middleware on the queue agent runs when the model tries to finish. It asks the backend which waiting claims are still in reach: ones this run worked that went back to waiting, plus any others if the run is under its cap. If there are any, it tells the model which ones and sends it back to work, up to 3 times. The instructions also tell the agent to check before finishing, but the middleware makes sure the check happens.
9. **Looking things up the way a handler does.** The backend splits the policy wording and the claims manual into their numbered `##` sections. The claim agent gets three tools: `search_documents(text)` finds the sections that mention a phrase (such as "wild animal" or "receipts"), `list_sections(document)` gives a document's contents page, and `read_section(document, section)` returns one section. `get_policy_wording` is removed. Each section read is recorded as a `section` step, such as "Claims manual 9. Repeat claims", and the claim page lists them under "What the agent read".
10. **Steps are kept across attempts.** The pilot cleared a claim's steps when a run started. Now every attempt's steps stay on the claim, with an error step between attempts, so the record shows what went wrong the first time.
11. **Two sets of instructions.** `backend/instructions/queue.md` covers running the queue and `backend/instructions/claim.md` covers working a claim. `get_instructions` returns both, and the instructions page shows them in two sections. The claim instructions follow the manual's order of work (section 4), say which sections to look up for what, and are written from the wording and the manual only, never from `data/evals/`.
12. **The model.** `deepseek/deepseek-v4.1-flash` with low reasoning effort is the default. `MODEL_NAME` still overrides it. Both agents use the same model.
13. **Starting a run.** On the queue page, "Run the queue" sits next to a number box, "Claims to work", which starts at 5. From the command line, `uv run -m agent.main --limit 5` starts a capped run against a running backend, and `uv run -m agent.main --claim CLM-1001` runs one claim.
14. **Not in this build:** a way for a handler to put a `needs_handler` claim back in the queue (the brief doesn't ask for one; `uv run -m backend.db --reset` restores everything), summarizing the queue agent's conversation (one line per claim stays small for 25 claims), and running claims in parallel.

---

## Phase 1: The documents, in sections

**Builds:** the claims manual in the database and the operations that look up sections.

- `backend/db.py` seeds `claims_manual.md` next to `policy_wording.md`.
- `backend/documents.py`: splits a Markdown document into sections on its `## ` headings, keyed by number ("9") with the title ("Repeat claims").
- Operations, with `document` one of `policy_wording` or `claims_manual`:

| Method and path | `operation_id` | Returns |
|---|---|---|
| `GET /api/documents/{document}/sections` | `list_sections` | The document's title and its sections' numbers and titles |
| `GET /api/documents/{document}/sections/{section}` | `read_section` | One section's number, title and text |
| `GET /api/search/{text}` | `search_documents` | The sections of both documents whose text contains the words, each with the first matching line |

- `get_policy_wording` stays until Phase 3, which moves the agent to the new tools and removes it.

**Why this way:** the manual is twice the length of the wording and will grow. A contents page and a search let the agent pull in a few hundred words where it needed thousands before, and the steps show exactly what it relied on.

**Success criteria**

- [x] pytest: `list_sections` for the wording has sections 1 to 12, and for the manual 1 to 14, with the titles from the files
- [x] pytest: `read_section("claims_manual", "9")` returns "Repeat claims" and its text, and nothing from section 10
- [x] pytest: `search_documents("wild animal")` finds manual section 7; `search_documents("receipts")` finds manual section 10; an unknown document or section returns 404
- [x] pytest: the OpenAPI rule test still passes (operation ids, docstrings, no optional inputs)
- [x] `curl http://localhost:8000/api/documents/claims_manual/sections/10` returns "Meals without receipts"
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 2: Runs, the cap and attempts

**Builds:** the records and rules for a run: the to-do list, the cap, retries and the new `needs_handler` state.

- `runs` table: `run_id`, `kind` (`queue` or `claim`), `claim_limit`, `status`, `todos` (JSON), `summary`, `started_at`, `finished_at`. Claims get `attempts` and `run_id` columns.
- Operations:

| Method and path | `operation_id` | Does |
|---|---|---|
| `POST /api/runs` | `start_run` | Creates a queue run with a cap and returns it, without starting an agent |
| `GET /api/runs/latest` | `get_latest_run` | The latest queue run, or null |
| `GET /api/runs/{run_id}` | `get_run` | The run: cap, status, to-do list, summary, claims worked, and the waiting claims still in reach |
| `PUT /api/runs/{run_id}/todos` | `update_todos` | Replaces the run's to-do list: each item has `content` and `status` (`to_do`, `in_progress`, `done`, `left_for_handler`) |
| `POST /api/runs/{run_id}/finish` | `finish_run` | Records the run's final status and summary |
| `GET /api/claims/waiting` | `list_waiting_claims` | The waiting claims: id, type, summary, amount claimed, submitted date, attempts |
| `POST /api/claims/{claim_id}/start` | `start_claim` | Moves a waiting claim to `in_progress` in a run; refuses with 409 if the claim isn't waiting or the run has reached its cap |
| `POST /api/claims/{claim_id}/release` | `release_claim` | If the claim is still `in_progress`, adds an attempt, records the problem as an error step, and sets `waiting` or, on the second failure, `needs_handler` |

- `run_queue` now takes `limit` and creates a queue run. `run_agent` creates a claim run with a cap of 1. For now both still start the pilot's agent, which Phases 3 and 4 replace.
- On startup, claims left `in_progress` are released ("The backend restarted during the run.") and `running` runs are marked `failed`.
- `start_claim` no longer clears steps.

**Why this way:** these are the rules the brief says must always hold: the cap, two tries, never stuck. Putting them in the backend keeps them true whatever the model does, and lets them be tested without any agent.

**Success criteria** (pytest)

- [ ] A run with a cap of 2 can start two claims; a third is refused with 409 and stays `waiting`
- [ ] A claim released once goes back to `waiting` with `attempts` 1 and an error step; the same run can start it again without counting it twice against the cap
- [ ] A claim released a second time is `needs_handler`, and `start_claim` refuses it
- [ ] Releasing a claim that's already decided changes nothing and adds no step
- [ ] Steps from both attempts are kept, in order
- [ ] `update_todos` stores the list, `get_run` and `get_latest_run` return it; an unknown status returns 422
- [ ] `get_run` lists the waiting claims in reach: a claim the run worked that went back to waiting, and other waiting claims only while the run is under its cap
- [ ] Startup releases a claim left `in_progress` and marks a `running` run `failed`
- [ ] `run_queue` with `limit` 0 returns 422; `run_agent` on a claim that isn't waiting returns 409
- [ ] `npm run check:python` passes with coverage of 80% or more

## Phase 3: The claim agent

**Builds:** the agent that works one claim, with its lookups, its middleware and its new instructions.

- `agent/claim.py`: `work_claim(run_id, claim_id, llm)`. It starts the claim through `start_claim`, runs a new `create_agent` with a new thread (`<run_id>-<claim_id>-<attempt>`), posts each step as the pilot did, and calls `release_claim` if the agent raised an error or finished with the claim still in progress. It returns one line: "CLM-1001: approved, $420.00 paid", "CLM-1008: approved $2,240.00, waiting for sign-off", "CLM-1010: referred", or "CLM-1004: failed (timed out), back in the queue (attempt 1 of 2)".
- Tools: `get_claim`, `get_policy`, `get_claim_history`, `search_documents`, `list_sections`, `read_section`, `record_decision`, `pay_claim`. The result of `read_section` is posted as a `section` step.
- Middleware: `ModelRetryMiddleware(max_retries=2, on_failure="error")`, `ToolRetryMiddleware` on network errors, `ModelCallLimitMiddleware(run_limit=40, exit_behavior="error")`.
- `agent/main.py`: the model and the backend client, shared by both agents. Default model `deepseek/deepseek-v4.1-flash` with low reasoning effort, or `MODEL_NAME`, with a request timeout.
- `backend/instructions/claim.md`: the order of work from manual section 4. Read the claim (stop if it's already decided), the policy and the history, find the benefit's section of the wording and the manual's notes on it, check the documents (manual 5), repeat claims (manual 9), conflicts (manual 8), work out the payout (wording 10, manual 6), decide, record, pay if $1,000 or less, and end with one sentence.
- `run_agent` in the backend now runs `work_claim` for its claim run. The `get_policy_wording` operation and tool are removed.

**Success criteria** (pytest with a stub model, never a real one)

- [ ] A stub run that reads the claim, searches, reads manual section 9, approves $420.00 and pays leaves the claim `decided` and paid, with a `section` step "Claims manual 9. Repeat claims", and returns "CLM-1001: approved, $420.00 paid"
- [ ] A stub model that raises an error leaves the claim `waiting` with `attempts` 1 and an error step, and the reply line says it's back in the queue
- [ ] A stub run that ends without a decision is released the same way
- [ ] A model that keeps calling tools past the call limit fails that attempt instead of running on
- [ ] Each attempt gets a new thread: the second attempt's first model call sees only the system prompt and the task, not the first attempt's messages
- [ ] A claim already decided is not decided or paid again (the backend refuses, and the stub's second `record_decision` gets the refusal)
- [ ] The claim agent's system prompt comes from `instructions/claim.md`, and nothing under `agent/` refers to `data/`
- [ ] With no `MODEL_NAME`, the model is `deepseek/deepseek-v4.1-flash` with low reasoning effort; with `MODEL_NAME` set, it's that model
- [ ] Live check (a few cents): after `uv run -m backend.db --reset`, `uv run -m agent.main --claim CLM-1001` with the backend running prints a decision line, and the claim's steps show the sections read
- [ ] `npm run check:python` passes with coverage of 80% or more

## Phase 4: The queue agent

**Builds:** the agent that runs the queue: the to-do list, a fresh claim agent per claim, the check for missed claims and the summary.

- `agent/queue.py`: `run_queue(run_id, llm)`. A `create_agent` with three tools for this run:
  - `write_todos(todos)`: saves the whole list through `update_todos`
  - `list_waiting_claims()`: the waiting claims, with the run's cap and how many it has left
  - `work_claim(claim_id)`: runs the claim agent from Phase 3 and returns its one line. A refusal from the backend (over the cap, not waiting) comes back as the backend's message.
- Middleware: `ModelRetryMiddleware`, and the missed-claims check, an `after_model` hook that, when the model tries to finish, reads `get_run` and, if waiting claims are still in reach, adds a message naming them and jumps back to the model (at most 3 times).
- When it finishes, the runner records the final reply as the run's summary with `finish_run("finished")`. If the queue agent raises an error, it records `finish_run("failed")` with the error.
- `backend/instructions/queue.md`: list the waiting claims, write a to-do list of the claims to work within the cap, oldest submitted first unless there's a reason not to, mark each item in progress and then done or left for a handler as each line comes back, retry a claim that went back to the queue once, check for missed claims before finishing, and end with a short summary for the handlers: how many claims were worked, the decisions and payouts, and what's waiting for a handler.
- `run_queue` in the backend starts `run_queue` in the background. `uv run -m agent.main --limit 5` does the same from the command line.

**Success criteria** (pytest with stub models)

- [ ] A stub queue run with a cap of 2 writes a to-do list, works two claims through stub claim agents and finishes: the run is `finished` with the to-do list and summary stored, two claims are decided and the other 23 are still `waiting`
- [ ] The cap holds even when the stub tries to work a third claim: it gets the backend's refusal and the claim stays `waiting`
- [ ] A claim whose claim agent fails doesn't stop the run: the next claim is still worked
- [ ] A failed claim goes back in the queue and is tried again in the same run, then decided
- [ ] A claim that fails twice ends `needs_handler`, and the run still finishes
- [ ] The missed-claims check: a stub that tries to finish after one claim with a cap of 2 is told the waiting claims and sent back, and then works the second
- [ ] A queue agent that raises an error leaves its run `failed` and no claim `in_progress`
- [ ] The queue agent's system prompt comes from `instructions/queue.md`
- [ ] `npm run check:python` passes with coverage of 80% or more

## Phase 5: The dashboard

**Builds:** the run controls and the to-do list on the queue page, the sections on the claim page, the new state and both sets of instructions.

- `api.ts`: types for runs and to-do items, `needs_handler`, the `section` step, and the new calls (`latestRun`, `runQueue(limit)`, `instructions` returning both parts).
- **Queue page:** a "Claims to work" number box (starting at 5, at most the number waiting) next to "Run the queue". A "Current run" panel with the run's status, cap and claims worked, then the to-do list with each item's state (To do, In progress, Done, Left for a handler), and the summary when the run finishes. The page refreshes every few seconds while the run is running. "Needs a handler" joins the state counts, in Raspberry.
- **Claim page:** a "What the agent read" panel listing the sections read, in order. `section` steps show in the steps list too. A claim that needs a handler says so and has no Run agent button.
- **Instructions page:** "Running the queue" and "Working a claim", both read only.

**Success criteria**

- [ ] vitest: the queue page shows the latest run's to-do list with each item's state label, and the summary once it's finished
- [ ] vitest: Run the queue sends the number from the box; the box starts at 5
- [ ] vitest: a running run turns on refreshing, and new to-do states appear on the next refresh
- [ ] vitest: the state counts include Needs a handler
- [ ] vitest: the claim page lists the sections read, and a `needs_handler` claim shows no Run agent button
- [ ] vitest: the instructions page shows both sets, with no editing controls
- [ ] Playwright MCP: the queue page with a to-do list (posted through the API) in light and dark mode, with no horizontal scroll at phone width
- [ ] `npm run check:ts` passes with coverage of 80% or more

## Phase 6: End to end, a live run and the handover

**Builds:** Playwright tests of the new pieces, a capped live run, and the docs.

- E2E specs, standing in for the agents through the API as before (`start_run`, `update_todos`, `start_claim`, `add_step`, `release_claim`, `finish_run`), so they never call a model:
  - The queue shows a run's to-do list as items move from To do to Done, and the summary when it finishes
  - A claim's page shows the sections the agent read
  - A claim released twice shows Needs a handler on the queue and on its page
  - The instructions page shows both sets
- Docs: `docs/SPEC.md` (the manual is in, the new state, runs with a cap, the to-do list), `docs/TECH_STACK.md` (two agents, middleware, DeepSeek V4.1 Flash) and `docs/STATUS.md`.

**Success criteria**

- [ ] `npm run e2e` passes all specs
- [ ] Live run on the default model: `uv run -m backend.db --reset`, `npm run dev`, then Run the queue with 5. The to-do list fills in and is ticked off, five claims are worked, each claim's steps include the sections read, and the other 20 stay Waiting
- [ ] Playwright MCP: the same run watched in the browser, including one claim's "What the agent read" panel
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` describe the product as it now is
- [ ] `npm run check` passes: ruff, pyright, pytest at 80% or more, Prettier, ESLint, tsc, vitest at 80% or more, and Playwright
