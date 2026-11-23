# Plan: a swarm of claim agents and a scoreboard

This plan builds the brief in `docs/SWARM.md` on top of the durable build in `docs/STATUS.md`. It has six phases. Each one builds on the last and ends with a commit through the pre-commit hook. `docs/PLAN.md`, `docs/PLAN_HARNESS.md` and `docs/PLAN_DURABLE.md` are finished plans and stay as they are.

SIDENOTE (from the brief): For consistency with the course, please read `labs/week2day4.ipynb` and build the dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief.

In practice the dispatcher follows the lab: a `Dispatch` TypedDict whose `results` has an `operator.add` reducer, a `list_claims` node, a `fan_out` conditional edge that returns one `Send` per claim, a `work_claim` worker node with a `RetryPolicy`, and `max_concurrency` set to N in the config. The scoring follows Part 3 of the lab (`is_right`, `mistake_cost`, `run_scores`). Where the brief differs, the brief wins: each claim keeps its own thread and checkpoints (never one shared thread or a subgraph), the dispatcher saves no progress of its own, the retries are for rate limits and timeouts only, and the cost comes from the backend's records, not from a list in the graph's state.

## The shape of the build

```
dashboard ──▶ backend :8000 (FastAPI, SQLAlchemy Core)                agent service :8002
 Queue          │ claims, runs, cost, time, answer key, scoring  ──▶   POST /api/dispatches
 Scoreboard     │ start_dispatch / start_evaluation                    POST /api/evaluations
                │   (agent_service.py asks the service)                      │ creates the run through the backend,
                ▲                                                            ▼ then works it in the background
                │                                                     dispatcher (LangGraph, no model)
                │                                                       list_claims ─ Send per claim ─▶ work_claim (N at once)
                │                                                                                            │ RetryPolicy: rate limit, timeout
                │                                                                                            ▼ error_handler: stays in progress
                └───── HTTP: tools, steps, decision, payment,  ◀── claim.work_claim(claim_id, run_id)  ← the only call into the claim agent
                       model calls, work time                          claim graph, one thread and one saver per claim
```

The backend still holds every rule. The claim agent's graph, tools and instructions stay as they are, apart from recording cost and time and the default model. Four things change:

- **The queue agent goes and a dispatcher takes its place.** Code picks the claims, no model.
- **Every model call and every stretch of work is recorded** with the run it happened in.
- **Evaluations** work copies of labelled claims through the same dispatcher and claim agent, and the backend scores them from the answer key.
- **The dashboard** starts dispatches and evaluations, and gets a Scoreboard page.

### Runs

| Kind | Made by | Works | Cap |
|---|---|---|---|
| `dispatch` | `create_dispatch`, from the service | every claim left in progress, then every waiting claim, of the queue | none; N is how many at once |
| `evaluation` | `create_evaluation`, from the service | its own copies of a labelled set | none; N is how many at once |
| `claim` | `start_claim_run` (the Run agent button, `uv run -m agent CLM-1001`) | one claim | none |

A run's status is `running`, `finished`, `failed` or `stopped`. A run has `concurrency` (N), `evaluation_set`, `tiny`, `model` and `reasoning_effort`, which is `low` or `none`. The old `claim_limit`, `todos` and `waiting_in_reach` go.

### The dispatcher

```
START ─▶ list_claims ─ fan_out: one Send per claim ─▶ work_claim ─▶ END      (max_concurrency = N)
          backend: claims to work                     calls claim.work_claim(claim_id, run_id)
          minus any being resumed                     RetryPolicy(retry_on=ModelRateLimitError, ModelTimeoutError)
                                                      error_handler: the claim stays in progress
```

- **`list_claims`** asks the backend for the run's claims to work: for a dispatch the queue's `in_progress` claims, then its `waiting` claims, each oldest submitted first; for an evaluation that evaluation's copies. It drops any claim the service is already resuming after a sign-off (its lock in `claim.LOCKS` is held).
- **`work_claim`** (the worker) calls `claim.work_claim(claim_id, run_id)` and returns its line as a result. If the claim comes back `waiting`, it failed with another error and was released, so the worker calls the function once more in the same dispatch. A claim that fails twice is `needs_handler`, so it stops there.
- **`claim.work_claim(claim_id, run_id)`** is the one function in the harness the dispatcher calls. It carries on a claim in progress (joining it to the run, same thread, same attempt) or starts a waiting one, and does nothing for a claim that is decided or held. A `ModelRateLimitError` or `ModelTimeoutError` that gets past the claim graph's own retries is let through without releasing the claim, so the worker's `RetryPolicy` runs the function again and the claim carries on from its last saved step. Any other error releases the claim as it does today.
- **`error_handler`** runs when the retries run out. It returns a result saying the claim is still in progress and does nothing else, so the claim stays in progress for the next dispatch and the dispatch goes on.
- The dispatcher has no checkpointer. The claims' own checkpoints are the only saved progress, so the next dispatch finishes what a stopped one left.
- **One saver for each claim run.** `checkpoints.saver()` makes a new `PostgresSaver` on the process's one shared pool each call, so 25 claims don't take turns on one lock. The pool and the backend's engine are both capped at 4 connections and wait up to five minutes for one instead of failing. Without `DATABASE_URL` it is one shared `InMemorySaver`.

### Cost and time

- **A model call** is recorded by the claim graph's model nodes (`agent` and `decide`) through `record_model_call`: prompt tokens, completion tokens and OpenRouter's `usage.cost` from `response_metadata["token_usage"]`, with the run it happened in. `decide` asks for the raw reply too (`include_raw=True`) so it can read the usage.
- **Work time** is recorded by the harness through `record_work_time` once for each stretch of work on a claim (from picking it up to finishing, pausing at a sign-off, or failing, in a `finally`). The wait for a handler is between two stretches, so it is never counted. A process killed mid-stretch loses that stretch's time.
- **The run recorded** is the dispatch or evaluation whose worker did the work. A sign-off resumed by the service and a single-claim run record none, so each run's totals are its own. A claim carried on by a later dispatch records its cost and time against that dispatch.
- A run's totals (tokens, cost, work time, and wall-clock time from start to finish) and each claim's cost, time and start come from the backend's records.

### Choices for you to review

1. **Order.** A dispatch works the claims left in progress first, oldest submitted first, then the waiting ones oldest first, because the in-progress ones are already started and may owe a payment. The brief's wording could also mean one combined list by submitted date.
2. **Where a retry happens.** A claim that fails with another error is tried once more inside its worker, so it keeps its slot and N stays the cap. The other design is a second round of `Send`s after the first round ends, which makes the retries wait for the slowest claim.
3. **The summary is stored when a run finishes**, built by the backend from the claims' records (counts, payouts waiting for sign-off, claims left for a handler or in progress), as JSON the dashboard lays out. It is not rewritten by later sign-offs.
4. **A copy lives in the `claims` table** with an `evaluation_id` and the id of the claim it was copied from, so the claim agent's tools and operations work on it unchanged. Every queue operation (`list_claims`, the claims to work, the counts) leaves copies out. Its id is `<evaluation id>-<claim id>`, such as `EVAL-3f2a9c1b-CLM-1003`, written into the copy's record too.
5. **The answer key** is two new tables, `eval_claims` (both sets, with the `tiny` flag) and `eval_decisions`, seeded from `data/evals/` and the queue's own files. Only the copying and the scoring read them. I opened `data/evals/` for its file shapes only, as the brief allows.
6. **Time stamps** on runs and work stretches keep milliseconds, so a 25-claim run's time and start times can be compared.
7. **Run ids.** Dispatches keep `RUN-xxxxxxxx`; evaluations are `EVAL-xxxxxxxx`.

### Where the brief and the repository differ

- The brief says `npm run seed`. There is no such script; the reseed is `uv run --env-file .env -m backend.db --reset`. Phase 1 adds `"seed"` to `package.json` with that command, so both work.
- The brief says to drop "the old to-do table". The to-do list here is a `todos` column on `runs`, which goes with the run model, and a reseed drops the whole `undercover` schema anyway.
- The brief names `e2e/run.spec.ts` and rules 10 and 13 of `docs/CODE_STANDARDS.md`. This repository has `e2e/harness.spec.ts` (which holds the run tests) and six rules. I will put the run tests in a new `e2e/swarm.spec.ts`, and add two rules to `docs/CODE_STANDARDS.md` as 7 and 8: the agent's five parts with the dispatcher using no model, and at most 4 connections in each process. Say if you want different wording.
- `docs/PROCESS.md` has no five-claim cap to replace. Phase 6 adds a rule for live runs: reseed once, then keep to the runs in this plan.
- The brief says `uv run -m agent CLM-1001`. Today the flag is `--claim`. The claim id becomes the one positional argument, and `--limit` goes.

---

## Phase 1: GPT-6 Luna by default, and cost and time for every claim

**Builds:** the new default model, and the records of what each claim costs and how long it takes. The queue agent still works at this point.

- `agent/main.py`: `DEFAULT_MODEL = "openai/gpt-6-luna"` with `reasoning_effort="low"`. `MODEL_NAME` chooses another model with no effort set. A `model_settings()` returns the name and the effort (`"low"` or `"none"`) so a run can record them.
- `backend/db.py`: tables `model_calls` (claim, run, time, prompt tokens, completion tokens, cost) and `work_time` (claim, run, started, seconds). `package.json` gets the `seed` script.
- Operations: `record_model_call` (`POST /api/claims/{claim_id}/model-calls`, with the run id, which may be null, as a required field) and `record_work_time` (`POST /api/claims/{claim_id}/work-time`). `get_claim` gets a `cost` and `seconds` total.
- `agent/claim.py`: the `agent` and `decide` nodes record each reply's usage; `decide` uses `include_raw=True`. The graph is built knowing its run id (or none). `work_thread` records each stretch of work in a `finally`.
- Tests: `StubModel` replies carry a `token_usage` with a cost.

**Why this way:** the claim graph's model nodes are where a reply arrives, so they are the one place to read the cost, as the lab does with `response_metadata`. Recording work time around the whole stretch, not per node, is what leaves the sign-off wait out.

**Success criteria**

- [x] pytest: with no `MODEL_NAME`, `main.model()` is `openai/gpt-6-luna` with low effort; with `MODEL_NAME=openrouter/free` it has no effort set, and `model_settings()` says `none`
- [x] pytest: a stub claim run records one model call for each reply (the agent's replies and the decision), with the tokens and cost from the reply
- [x] pytest: a claim that raises an error after one model call keeps that call's cost, and a stretch of work time with the seconds it took
- [x] pytest: a claim held for sign-off and resumed has two stretches of work time, and the gap between them is in neither
- [x] pytest: costs recorded with a run id add up under that run, and a run id of null adds up under none
- [x] pytest: the openapi guard test still passes (every operation has an `operation_id`, a one-line docstring and no optional inputs)
- [x] `npm run seed` is in `package.json` (not run here: it resets Supabase, which waits for the reseed in Phase 6)
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 2: The dispatcher in place of the queue agent

**Builds:** the dispatcher with the retries, the connection limits and the Langfuse tags, and removes the queue agent. Starting a dispatch from the service and the dashboard is Phase 3, so the dashboard's Run the queue button is switched off from this commit until Phase 3.

- `backend/`:
  - Runs: `kind` is `dispatch`, `evaluation` or `claim`; `concurrency` replaces `claim_limit`; `stopped` is a status. `start` no longer has a cap, and starting a claim already in progress in another run joins it to the run (a `run_claims` row) without changing its thread or attempts.
  - `list_claims_to_work` (`GET /api/runs/{run_id}/claims-to-work`): the order above.
  - `get_instructions` returns the claim instructions only.
  - Removed: `run_queue`, `start_run`, `get_latest_run`, `update_todos`, `list_waiting_claims`, `list_unfinished_claims`, `list_running_runs`, the `todos` column, `backend/instructions/queue.md`.
- `agent/dispatcher.py`: the graph above, with `dispatch(run_id, concurrency)` that invokes it with `max_concurrency`, and the `error_handler`.
- `agent/claim.py`: `work_claim(claim_id, run_id)` as described, `is_working(claim_id)`, the pass-through of `ModelRateLimitError` and `ModelTimeoutError`. The trace of each claim is tagged with its run: `main.tracing(thread_id, tags=[run_id])` puts `langfuse_tags` in the metadata.
- `agent/checkpoints.py`: `saver()` on a shared pool as above, and the longer wait for a connection in the pool and in the backend's engine.
- `uv run -m agent CLM-1001` works one claim, with no flags.
- Removed: `agent/queue_agent.py`, `agent/resume.py` (a dispatch finishes what was left), `run_queue` and the queue parts of `agent/service.py` and `agent/tests/test_queue.py`, `uv run -m agent --limit`, the to-do list in the dashboard's run panel and in `e2e/harness.spec.ts`.
- Spike first: write a test that proves how `add_node(..., error_handler=...)` behaves after the retries run out, before building on it. Result: in LangGraph 1.2.12 the handler ran but the error was still re-raised when more than one claim ran in a step, so the project moved to LangGraph 1.2.24 (`langgraph>=1.2.24` in `pyproject.toml`), where the handler's result stands and the dispatch carries on.

**Why this way:** the claim agent keeps its own thread, so a sign-off, a restart or a retry only ever touches that one claim. Letting only rate limits and timeouts through without releasing is what keeps a retry the same attempt, and it is safe because the backend never decides or pays twice.

**Success criteria** (pytest, stub model, no network)

- [x] never more than N claims are worked at once: a stand-in for `claim.work_claim` that counts overlaps, with 12 claims, at N=1, 3 and 25
- [x] a dispatch works the claims left in progress, then the waiting ones, oldest submitted first, and never a decided, held or `needs_handler` claim
- [x] a claim whose model raises a rate limit past the graph's own retries carries on as the same attempt (same thread, `attempts` unchanged), with exactly one decision recorded and one payment
- [x] a claim that keeps hitting rate limits or timeouts after the worker's retries stays `in_progress`, the dispatch still finishes, and the other claims are decided
- [x] a claim that fails with another error goes back to the queue and is tried once more in the same dispatch; one that fails twice is `needs_handler`; neither stops the other claims
- [x] a claim carried on from `in_progress` in a new dispatch keeps its thread, and shows in the new dispatch's claims
- [x] a claim the service is already resuming (its lock held) is skipped
- [x] each claim's trace carries the run's id in `langfuse_tags` (checked through the config the tracing helper returns)
- [x] two savers from `checkpoints.saver()` share one pool and are not the same object
- [x] nothing in `agent/` mentions `queue_agent`, `write_todos` or `data/`; the queue tests, the to-do e2e test and the `todos` column are gone
- [x] `uv run -m agent CLM-1001` works one claim against a stub (`cli(["CLM-1001"])` in a test)
- [x] `npm run check:python` and `npm run check:ts` pass with coverage of 80% or more

## Phase 3: Starting a dispatch from the dashboard

**Builds:** the agent service and the backend that start a dispatch, the one-at-a-time rule, and the run panel.

- `agent/service.py`: `POST /api/dispatches` (`run_dispatch`, body N). It calls the backend's `create_dispatch`, answers with the run at once, or passes on a 409, and works the run in the background (`dispatcher.dispatch`), then calls `finish_run`. A `lifespan` calls `stop_running_runs` on startup. `resume_claim` stays. The claim agent gets no tool for starting a run.
- `backend/agent_service.py`: `forward(...)` and the client (moved out of `backend/main.py`), so the backend asks the service through it. `start_dispatch` (`POST /api/dispatches`, body N from 1 to 25) forwards the request; a dead service is a 503 "The agent isn't running."
- `backend/`: `create_dispatch` (refuses with 409 "A dispatch or evaluation is already running." when one is `running`), `stop_running_runs`, `finish_run` (just the status; the backend builds the summary from the records), `get_latest_dispatch` and `get_run` returning the dispatch's claims (state, decision, start, time, cost) and its totals (tokens, cost, work time, wall-clock time). The summary: counts approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or in progress.
- `dashboard/`: the queue page has Claim agents at once (1 to 25, starting at 3) and Start a dispatch. The run panel lists the dispatch's claims with links, their state, start, time and cost, the dispatch's cost and time, and the summary when it finishes. A refusal or "The agent isn't running." shows as a message. The run's status can be Stopped.
- `e2e/agent-stand-in.js` keeps each request's body as well as its path.

**Why this way:** the backend writes nothing before it asks the service, so the service is the one place a run is created and there is nothing to undo if the service is down. The one-at-a-time rule sits in the backend's `create_*`, inside the transaction, so two starts can't both get in.

**Success criteria**

- [x] pytest: `start_dispatch` sends N to the stand-in service and returns its answer; with the service down it returns 503 "The agent isn't running." and no run is created
- [x] pytest: a second `create_dispatch` while one is running returns 409, and works again after `finish_run`; N outside 1 to 25 returns 422
- [x] pytest: on startup the service marks a running dispatch `stopped` and its claims in progress stay in progress
- [x] pytest: the summary of a finished dispatch matches its records: counts of approved, declined and referred, a payout waiting for sign-off, a claim left for a handler and one still in progress
- [x] pytest: a run's cost and time are its own, failed attempts included, a second run's totals don't change the first's, and a sign-off wait is not in the time
- [x] pytest: a service test: `run_dispatch` creates the run, works it with a stub model in the background, and finishes it
- [x] vitest: the form starts at 3, limits 1 to 25, and calls `startDispatch` with N; a refusal shows its message; the run panel shows each claim, the cost and the time, the summary when finished and Stopped for a stopped run
- [x] e2e: starting a dispatch on the dashboard with N=7 reaches the stand-in service with `{"concurrency": 7}`
- [ ] (deferred to the live app in Phase 6) Playwright MCP: the queue page against a stub dispatch played out through the API (claims in different states, a summary) in light and dark mode, with no horizontal scroll at phone width
- [x] `npm run check` runs the unit checks and e2e green

## Phase 4: Evaluations

**Builds:** the answer key, the copies, the evaluation's run and its scores, in the backend and the service.

- `backend/db.py`: `eval_claims` and `eval_decisions`, seeded for both sets (the evaluation set from `claims.json` and `data/evals/decisions.json`, the held-out set from `data/evals/held_out_claims.json` and `held_out_decisions.json`, each with its `tiny` flag). The `claims` table gets `evaluation_id` and `source_id`. The queue's seeding is unchanged.
- `backend/scoring.py`: the lab's measures as plain functions: `decision_accuracy`, `accuracy` (decision matches and the payout is within a dollar), the cost of mistakes (a wrongful decline $1,500, a wrongful approval its payout whether or not it was paid, a needless referral $50, a wrong amount on a right approval the difference), the straight-through rate, model cost per claim, time per claim and the time for the whole run. A claim the agent didn't decide counts as a referral.
- Operations: `create_evaluation` (set, tiny, N, model, effort; refuses with 409 while a run is going; makes the copies with new ids, written into their records, and a `run_claims` row for each), `start_evaluation` (`POST /api/evaluations`, forwards to the service), `list_evaluations` (newest first, with the measures) and `get_evaluation` (every claim with the handler's decision and payout beside the agent's, cost, time and a `right` flag).
- Copies: `list_claims`, the counts and `list_claims_to_work` for a dispatch leave them out. `sign_off` on a copy returns 409 and `start_claim_run` on a copy returns 409. `get_claim` on a copy shows `evaluation_id` and nothing from `eval_decisions`.
- `agent/claim.py`: when a copy's approval is held for sign-off, the claim counts as finished and its thread is deleted from the checkpointer (`delete_thread`); no sign-off form is needed, because none can be filed.
- `agent/service.py`: `POST /api/evaluations` (`run_evaluation`, body N, set, tiny) does what `run_dispatch` does, through `create_evaluation` and the same `dispatcher.dispatch`.

**Why this way:** a copy is an ordinary claim to the claim agent, so there is only one agent to trust and no special path to maintain. The answer key is read in two places only, and the agent's service never asks for it, so the agent can't see how it will be marked.

**Success criteria**

- [x] pytest: after seeding, `eval_claims` holds 25 claims in each set and `eval_decisions` 25 decisions in each, 5 `tiny` in each; the queue still has the same 25 claims
- [x] pytest: an evaluation of the evaluation set and one of the held-out set's `tiny` claims leave `list_claims`, the state counts and the claims to work for a dispatch exactly as they were
- [x] pytest: the copies have new ids that are also in their records, and a dispatch started afterwards never carries one on
- [x] pytest: `get_claim` on a copy holds nothing from the handlers' decisions (no `handler_id`, reason or payout of theirs), and `sign_off` and `start_claim_run` on a copy return 409
- [x] pytest: a copy approved over the limit counts as finished, its checkpoints are gone, and the evaluation can finish
- [x] pytest: an evaluation's measures match ones worked out by hand on a small set with every kind of mistake (a wrongful decline, a wrongful approval, a needless referral, a wrong amount, a claim not decided counted as a referral)
- [x] pytest: a stopped evaluation shows as stopped, and its undecided claims count as referrals
- [x] pytest: the evaluation records its set, N, the model and the effort (`none` for another model), and each claim's decision, payout, reason, cost and time
- [x] pytest: nothing under `agent/` reads `eval_decisions` or the evaluation scoring operations
- [x] pytest: `run_evaluation` in the service works the copies of a `tiny` set with a stub model
- [x] `npm run check:python` passes with coverage of 80% or more

## Phase 5: The Scoreboard page

**Builds:** the Scoreboard on the dashboard and the end-to-end tests.

- `dashboard/`: a Scoreboard link in the header, `#/scoreboard` and `#/evaluations/<id>`.
  - Start an evaluation: choose the set (Evaluation set or Held-out set), Whole set or Tiny (5 claims), N from 1 to 25 (3 to start with). A refusal or "The agent isn't running." shows as a message.
  - The list, newest first: set, size, N, model and effort, status (Running, Finished, Stopped), and each measure. Money as `$1,250.00`, the measures in JetBrains Mono, states in the brand colors.
  - An evaluation opens to its claims: the handler's decision and payout beside the agent's, misses marked (Raspberry) and easy to spot, each claim linked to its page. The page of a copy has a note that it is an evaluation copy and no sign-off form or Run agent button.
- `e2e/swarm.spec.ts`: starting an evaluation on the dashboard reaches the stand-in with N, the set and tiny; an evaluation played out through the API (create, record decisions and payments on the copies, finish) shows on the scoreboard with every measure and opens to its claims; the queue page's counts are the same before and after.

**Success criteria**

- [ ] vitest: the list shows each evaluation's measures formatted and newest first; a stopped one shows Stopped; the detail marks the misses and links each claim
- [ ] vitest: the form sends the set, tiny and N; a refusal shows its message
- [ ] vitest: a copy's claim page shows the note and no sign-off form or Run agent button
- [ ] e2e: the evaluation test above, and the start test, pass
- [ ] Playwright MCP: the Scoreboard and an evaluation's page in light and dark mode, and at phone width with no horizontal scroll
- [ ] `npm run check:ts` passes with coverage of 80% or more, and `npm run e2e` passes

## Phase 6: Live runs and the handover

**Builds:** the live checks with the real model, and the documents.

- Documents:
  - `docs/SPEC.md`: the sections on working the queue and on what the team needs to see (the Rules stay as they are).
  - `docs/TECH_STACK.md`: the dispatcher as the agent's fifth part, GPT-6 Luna as the default, at most 4 connections in each process, the answer key.
  - `docs/CODE_STANDARDS.md`: the two new rules (7 and 8), if you agree.
  - `docs/PROCESS.md`: the rule for live runs.
  - `docs/STATUS.md`: what's built, how to run it, and the results of these runs.
- Live runs (real model, `.env` in place), after one `npm run seed` and with `npm run dev` running, checked through the Playwright MCP server. On Windows, the processes on port 8002 are found with `Get-NetTCPConnection -LocalPort 8002` and stopped with `Stop-Process -Force`, which stops uvicorn's reloader and its worker.
  1. Start a dispatch at N=3 and stop it partway by killing every process on port 8002. `npm run dev` stops the others. Start it again.
  2. Start a dispatch at N=25. It carries on the claims left in progress as the same attempts and works the rest with 25 at once; the claims' start times show it. Compare the run's time with the sum of its claims' times, and the claims' times with the N=3 claims' times; if they are slower, report where the time goes, keeping to 4 connections. Sign off a held claim during the run and time the payment. Read `pg_stat_activity` through the Supabase MCP server during the run.
  3. An evaluation of the evaluation set at N=25, and one of the held-out set's five `tiny` claims at N=5. Check the queue page's counts before and after.
  4. Through Langfuse's observations API, find the N=25 dispatch's claims together by its tag.

**Success criteria**

- [ ] the live run steps 1 to 4 above, each ticked with what I saw
- [ ] the N=25 dispatch carried on the claims left in progress with the same threads and attempts, its claims' start times overlap, and its run time is well under the sum of its claims' times
- [ ] no claim failed for want of a database connection, and `pg_stat_activity` showed at most 12 connections whose user is `postgres` during the dispatch
- [ ] a claim held for sign-off waited, and a sign-off paid it within five seconds
- [ ] both evaluations show on the scoreboard with every measure, and the queue page's counts are the same before and after
- [ ] Langfuse returns the N=25 dispatch's claims by its tag
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` and `docs/STATUS.md` describe the product as it now is
- [ ] `npm run check` passes with `.env` moved out of the way: ruff, pyright, pytest at 80% or more, Prettier, ESLint, tsc, vitest at 80% or more, and Playwright
