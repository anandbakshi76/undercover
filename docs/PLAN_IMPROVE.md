# Plan: an improver for the claim agent, and handlers who sign in

This plan builds the brief in `docs/IMPROVE.md` on top of the swarm build in `docs/STATUS.md`. It has seven phases, one more than usual, because the brief holds two large parts, the improver and the dashboard. Each phase builds on the last and ends with a commit through the pre-commit hook. `docs/PLAN.md`, `docs/PLAN_HARNESS.md`, `docs/PLAN_DURABLE.md` and `docs/PLAN_SWARM.md` are finished plans and stay as they are.

SIDENOTE (from the brief): For consistency with the course, please read `labs/week2day5.ipynb` and build the judge, the configuration's versions and the improver in the same style as the lab. Where the lab and this brief differ, follow the brief.

In practice, the build follows the lab's shapes: a `judge` function that asks Jev three questions (`score`, and two `noul`) and returns the grade, its letter from the most likely level, the two checks and the cost; a configuration as `instructions`, `model` and `reasoning_effort` kept in numbered versions; `compare` and `keep_or_roll_back` with `OBJECTIVES`, `NOISE` and `TOLERANCE` (cost and time as fractions of the old value, accuracy and the grade as differences); the improver as a LangChain `create_agent` with the lab's four tools (`read_results`, `propose_change`, `run_evaluation`, `propose_recipe`) and `HumanInTheLoopMiddleware` pausing on the two propose tools; a recipe that ends by improving itself; and `difflib` diffs. Where the brief differs, the brief wins: the records live in the backend and not in lists in a notebook, the judge and the keep-or-roll-back rule run in the backend, the pause survives a restart on a `PostgresSaver`, each round works to a spending limit, and every handler action needs a signed-in handler.

## Before you start

Your `.env` already has `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` and `HANDLER_PASSWORD` with values (I checked their lengths, not what they say). The brief assumes you have also done the lab's "Setting up for the product": the three handlers as users in Supabase Auth with that password, sign-ups turned off, and a credit limit on your OpenRouter key. Phase 1 checks the sign-ins against Supabase. If a handler can't sign in, I'll stop and ask you rather than guess.

## The shape of the build

```
dashboard (React)                         backend :8000                                  agent service :8002
 sign-in (Supabase Auth, browser) ──token─▶ auth.py: JWKS check, handler by email          run_round, approve/reject, stop
 Queue  Scoreboard  Improvement             settings, configurations, recipes              ┌ improver.py: create_agent, HITL,
 Configuration  Settings                    rounds, proposals, grades, spending            │  PostgresSaver of its own, 4 tools
                                            judge.py: Jev via OpenRouter (grades on finish)│   └▶ its own client (improver_api.py)
                                            improvement.py: compare, keep_or_roll_back     └ dispatcher ▶ claim agents built from
                                            ▲                                                 the run's configuration version
                                            └──── HTTP: runs, steps, decisions, usage ◀──────────────┘
```

The backend still holds every rule and every record, and the claim agent still reaches the backend only through `agent/main.py`'s `call`, which has no way to the scores or the handlers' decisions. Five things change:

- **Handlers sign in**, and the backend checks the handler on every action a handler takes.
- **Settings and the configuration's versions** move into the database: the approval limit and the improvement settings, and the claim agent's instructions, model and reasoning effort as numbered versions.
- **A judge** grades each evaluated claim's explanation against the handler's reason.
- **The improver**, a sixth part of the agent, runs rounds that propose a change and a new recipe, which a handler approves, and the backend keeps or rolls back each new version by the rule.
- **The dashboard** gains sign-in, Configuration, Settings and Improvement pages, a shared set of components, and the same standard on every page.

### What a round looks like

```
start (senior handler) ─▶ first evaluations (1, 2, 3 of 3, only if the current version lacks them)
   ─▶ improver reads results ─▶ propose_change ─ waits ─▶ handler approves ─▶ version N is made
   ─▶ evaluations (1, 2, 3 of 3) ─▶ backend verdict: kept (becomes current) or rolled back
   ─▶ improver proposes a recipe ─ waits ─▶ handler approves ─▶ recipe version made ─▶ finished
   (a rejection sends the handler's reason to the improver, which may propose again;
    a proposal with a model that isn't allowed goes back to the improver before any handler sees it)
```

### The one-run rule with the improver

Today `new_run` refuses a dispatch or evaluation while another is `running` (the rule is in the backend's `runs` table, not in a lock in the service). The improver joins it:

- A stretch of the improver's work is a run of a new kind, `improver`, with a `round_id`. It counts toward the rule, and it ends when the improver pauses for a handler or finishes.
- An evaluation with a `round_id` is allowed while that round's stretch is the only run going. Anything else is refused as today, and the refusal now names what is running, such as "A dispatch (RUN-1a2b3c4d) is running."
- While a round waits for a handler, no stretch is running, so dispatches and evaluations can run.
- An approval or rejection first asks the backend for a new stretch, so it is refused while something else runs, with nothing recorded. The decision is recorded only after that, and then the improver resumes.

## Choices for you to review

1. **Phases.** Seven, in the brief's order: sign-in, settings and the configuration's versions (two phases, because they are large), the judge, the improver, the Improvement page, the look of the whole dashboard, and the live runs with the documents. The shared components (page header, dialog, diff, states) are built in Phase 2 when they are first needed, so the later pages are built with them, and Phase 6 is the pass over what is left.
2. **The header's Dark mode button stays**, as a quick switch between light and dark, and the Settings page gets the three choices (light, dark, same as the computer). Both write the same value in the browser. Nothing on the existing pages changes how it works.
3. **`#/instructions` becomes `#/configuration`.** The old address goes away, since the Configuration page takes the Instructions page's place. The e2e test that opens it is updated.
4. **Which version a claim uses.** A waiting claim started by a dispatch or evaluation uses that run's version. A claim already in progress that a later dispatch carries on, and one resumed after a sign-off, keeps the version of the run that started its attempt, so a thread never changes its instructions halfway. Its cost and time still go to the dispatch that worked it.
5. **Current version and the pointer.** The current version is a row kept apart from the platform settings. A version exists once its proposal is approved, which is also what makes it appear on the Configuration page. A version that is rolled back stays listed, with its scores and the verdict.
6. **Grading happens when an evaluation finishes**, not when it is stopped, since a stopped evaluation rolls its version back anyway.
7. **The improver's call cap** is 40 model calls for each round (the claim agent's is 30), with `ModelCallLimitMiddleware`. The validation of a proposal's model sits in a small middleware that runs before the approval pause, and a test checks that order.
8. **The environment names.** The judge's address is read once from `JUDGE_SERVER_URL`, defaulting to `https://openrouter.ai`, and Supabase's from `VITE_SUPABASE_URL`, as the brief says. The tests and e2e point them at stand-ins.
9. **Time per claim is noisy here.** The brief's tolerance on time was measured without a database. In this build's live runs a claim took 29.5 seconds at N=3 and about 60 at N=25 because of the connection queue, and the improver's evaluations default to 25 at once. A version can therefore be rolled back because of time alone. I'll report how far version 1's two held-out runs differ beside the noise and tolerance, as the brief asks, and the settings can change.
10. **The seed.** `npm run seed` takes configuration and recipe back to version 1, the settings back to their starting values, and empties the rounds, proposals and grades.

### Where the brief and the repository differ

The brief's notes come from the `week2day4-end` branch. In this build:

- There is no `agent/api.py`. The claim agent's client is `agent.main.call`, and its guard test is `test_the_agent_never_asks_for_the_answer_key`, which I'll extend to the new operations. The improver gets its own client module, `agent/improver_api.py`.
- There is no `handler_decisions` table. `eval_decisions` already stores each decision's whole record, `reason` and `reference` included, so seeding needs no new columns for the reasons.
- `sign_off_claim` is the operation `sign_off`, and the sign-off form is `SignOffForm` inside `ClaimPage.tsx`.
- The one-run rule is in the backend, not a `threading.Lock` in the service (see above).
- `data/handlers.json` already has each handler's email, and the seed stores the whole record, so emails are in the database already. All three handlers have `can_approve_over_limit` true today; Phase 1 sets it to false for Marcus Oyelaran and Priya Raman.
- `docs/CODE_STANDARDS.md` has eight rules, and the brief cites rules 7, 13 and 14. I'll update rule 7 for six parts and add rule 9 (agent instructions are written as goals, and a change never names a claim) and rule 10 (each outside service, the judge, the improver's client and the dashboard's Supabase client, lives in one module and its address is read once from the environment). Say if you want other numbers.
- The model's reasoning effort is `none` today for `MODEL_NAME`; the brief wants `default`, which Phase 2 changes.
- `docs/PROCESS.md` has no rule on screenshots or a design review. The brief's live checks ask for one fresh subagent review at the end, which I'll run through the Agent tool as the brief says.

---

## Phase 1: Handlers sign in

**Builds:** sign-in with Supabase Auth, a backend that checks the handler on every handler action, and a sign-off by the signed-in handler. Every later page and test is signed in.

- `data/handlers.json`: `can_approve_over_limit` false for Marcus Oyelaran and Priya Raman.
- `backend/auth.py`: reads `VITE_SUPABASE_URL` once, fetches the project's public keys with `PyJWKClient` (`pyjwt[crypto]` is already a dependency), checks the token's signature, expiry and audience `authenticated`, and finds the handler by the token's email. Two dependencies: `current_handler` (401 "Sign in to do this." without a valid token) and `senior_handler` (403 saying what that handler can't do and why).
- Operations that need a handler: `start_dispatch`, `start_evaluation`, `sign_off`. `sign_off` loses its `handler_id` body (no optional inputs) and records the signed-in handler, refusing a handler who can't sign off over the limit. A new `get_me` returns the signed-in handler. The service's own writes under `/api/runs` and the claim agent's calls stay open until Week 3.
- `dashboard/`: `@supabase/supabase-js` (new), one module `supabase.ts` that reads `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`, a sign-in page (`#/sign-in`) with email and password and no sign-up, a session provider, and `api.ts` sending the access token with each request. Every route except sign-in needs a session; a 401 from the backend goes back to sign-in. The header shows the handler's name and role with Sign out. The sign-off form drops the handler list and says who will sign off; for a handler who can't, the held payout's panel says what they can't do and why, with no button. `vite.config.ts` reads `.env` from the repo root for `npm run dev` only (`envDir`), and the unit tests set their own values.
- `e2e/`: a stand-in for Supabase Auth (`e2e/auth-stand-in.js`) that signs tokens with a key of its own, serves `/auth/v1/.well-known/jwks.json` and `/auth/v1/token`, and answers the browser's cross-origin checks. Its address reaches the backend and the dashboard from the environment. A helper signs a spec in, and every existing spec uses it.

**Why this way:** the backend checks the token itself, so a page that hides a button is never the only guard. Reading the address once, in one module, is what lets the tests point at a stand-in.

**Success criteria**

- [x] pytest: with a valid token for Dana Whitfield, `start_dispatch`, `start_evaluation` and `sign_off` go through; without a token, with an expired token, with a token for the wrong audience or signed by another key, and with an email that is no handler's, each returns 401 and changes nothing
- [x] pytest: Marcus Oyelaran's `sign_off` of a payout over the limit returns 403 saying why, and the claim stays waiting for sign-off; Dana's records her as the handler on the sign-off and the payment
- [x] pytest: the openapi guard test still passes (an `operation_id`, a one-line docstring and no optional inputs on every operation, `sign_off` without a body)
- [x] pytest: `data/handlers.json` has `can_approve_over_limit` true for Dana only, and the seed stores each handler's email
- [x] vitest: the sign-in page signs in and goes to the queue, shows Supabase's message for a wrong password, and has no sign-up; every other route sends a signed-out visitor to sign-in; the header shows the handler and signs out; the claim page's sign-off has no handler list and says who signs off; a handler who can't sign off sees why and has no button
- [x] e2e: every spec signs in through the Auth stand-in, a sign-off is made by the signed-in handler, and an unauthenticated request to the backend is refused
- [x] Live check: with `.env`'s Supabase settings, each of the three handlers signs in with `HANDLER_PASSWORD` (a read-only call to `/auth/v1/token`), sign-ups are off (`/auth/v1/settings`), and the backend accepts a real token for `get_me`
- [ ] (done with the live runs in Phase 7) Playwright MCP: signed in as Dana through the real Supabase page, the queue, a claim and sign-out work in light and dark mode
- [x] `npm run check` passes with `.env` moved out of the way

## Phase 2: Settings, and the configuration's versions

**Builds:** the settings in the database, the claim agent's configuration and the recipe as versions, the Settings page and the Configuration page, and the shared dashboard components they need.

- `data/improvement.json` (new): the lab's starting values: objectives (accuracy 0.6, grade 1.0), noise and tolerance for accuracy, grade, cost per claim and time per claim, the spending limit for each round (0.50), and the improver's evaluations at once (25). The approval limit comes from `data/company.json`. `data/models.json` (new): the models allowed, each with its reasoning efforts and its price per million input and output tokens, as numbers.
- `backend/db.py`: tables `settings` (key, value, changed by, changed at) with `setting_changes` (every change, who and when), `configurations` (version, instructions, model, reasoning effort, reason, proposed by, approved by, approved at) and `recipes` (the same, with the recipe text). A `current_version` row, kept apart from the settings. Seeding makes version 1 of each: the configuration from `backend/instructions/claim.md` with `openai/gpt-6-luna` at low, and the recipe from the lab's first recipe in a new file beside it, `backend/instructions/recipe.md`. Runs gain a `version`.
- Operations: `get_settings`, `change_setting` (a senior handler only; checks the value, records who and when), `list_setting_changes`, `list_models`, `list_configurations` (each version with its held-out scores, which are empty until Phase 5), `get_configuration_diff` (what a version changed from the one before, by `difflib`, as lines the dashboard can colour), `list_recipes`, `get_recipe_diff`, and `get_claim_configuration` (the instructions, model and reasoning effort of a version, with no scores, for the claim agent).
- The approval limit moves to the settings table: `record_decision`, `calculate_payout` and the payment rules read it from there, and a new limit applies to the decisions recorded after it.
- `agent/`: the claim agent builds its model and instructions from the version of the run it works for (`get_claim_configuration`), not from `claim.md` and `MODEL_NAME` directly. A dispatch and an evaluation record their version, and a dispatch always uses the current version. With `MODEL_NAME` set, that model is the only one allowed, at reasoning effort `default` and a price of 0, and its runs record `default`.
- `dashboard/`: shared components, each with a test: a page header (what the page is for, with its actions), a confirmation dialog on the native `<dialog>`, a diff view (additions and removals in colour, with a word as well as a colour), loading, empty and error states that say what to do next, and a status badge (a colour and a word). The Settings page (every handler sees it; a senior handler edits each setting behind a confirmation; others see why they can't), listing the models and the appearance. The Configuration page in place of Instructions: the current version, every version with its scores, and the diff of any version against the one before it. The Scoreboard and the evaluation page show the version each run used.

**Why this way:** a version is a record the backend owns, and the claim agent fetches only what it needs, so it can't reach a score. Reading the approval limit from the settings table in one place keeps the rule where it was.

**Success criteria**

- [ ] pytest: seeding makes configuration version 1 (the claim instructions, Luna, low), recipe version 1 and the starting settings, and `npm run seed` takes a changed database back to them
- [ ] pytest: `change_setting` records who and when, refuses a value that makes no sense (a negative limit, an agents-at-once outside 1 to 25), and returns 403 for Marcus and 401 with no sign-in
- [ ] pytest: a new approval limit applies to the next decision recorded and leaves earlier decisions alone
- [ ] pytest: a dispatch uses the current version and records it; an evaluation started on the Scoreboard does too; the claim agent built for a version uses that version's instructions, model and reasoning effort
- [ ] pytest: a claim carried on keeps the version of the run that started its attempt
- [ ] pytest: `get_claim_configuration` returns no scores, and the diff of a version against the one before matches `difflib` for a known pair
- [ ] pytest: with `MODEL_NAME` set the only model allowed is that one at `default`, priced at 0
- [ ] vitest: the Settings page shows every setting, lets a senior handler change one only after a confirmation, tells another handler what they can't do and why, and keeps the appearance in the browser; the Configuration page shows the versions, marks the current one and shows the diff; the shared components render their states
- [ ] e2e: a handler changes a setting through the dialog, switches the theme, and opens the Configuration page
- [ ] Playwright MCP: Settings and Configuration in light and dark mode, with no horizontal scroll at laptop width
- [ ] `npm run check` passes with `.env` moved out of the way

## Phase 3: The judge

**Builds:** the judge, the grades in the backend, and the average grade on the scoreboard.

- `backend/judge.py` (one module, the call written once): Jev, `typesafe/jev-1.13`, through the `openrouter` package's `alpha.decisions.create`, with the lab's rubric and three questions. The state is the handler's decision, reason and reference (from `eval_decisions`) beside the agent's decision, reason and section. The score is Jev's `score`, and the letter is the level with the highest probability, never the score rounded. The two checks are kept as probabilities and shown as yes or no. The address is read once from `JUDGE_SERVER_URL` and passed as `server_url` on each call; each call times out after 10 seconds with the SDK's retries turned off.
- Grading happens inside `finish_run` for an evaluation that finishes, in parallel, so finishing stays under 20 seconds (the agent's client waits 30). A claim the agent didn't decide gets an F without asking the judge; a claim the judge can't grade is "not graded" and left out of the average. Each grade, with its two checks and its cost, is stored in a `grades` table, and the evaluation's judge cost is recorded apart from the claim agent's cost.
- The measures gain `grade` (the average, 0 to 3, or none when no claim was graded) and `judge_cost`. `get_evaluation` rows gain the grade, the letter and the two checks. With `MODEL_NAME` set the judge isn't called and claims show as not graded.
- `dashboard/`: the Scoreboard adds the average grade beside the other measures; an evaluation's page shows each claim's letter, score and the two checks as yes or no, and says "Not graded" where that is so.

**Success criteria** (pytest with a stub judge, no network)

- [ ] the letter is the most likely level, not the rounded score (a case where they differ), and the score is Jev's `score`
- [ ] an undecided claim is graded F with no call to the judge; a claim whose call fails or times out is not graded and left out of the average; an evaluation with no graded claims has no grade
- [ ] the grades are made as the evaluation finishes, in parallel, with the 10-second timeout and retries off passed on each call, and `server_url` is the one read from the environment
- [ ] the judge's cost is recorded with the evaluation and is not in the claim agent's cost
- [ ] the average grade matches one worked out by hand, and shows on the scoreboard's list and the evaluation page
- [ ] nothing under `agent/` can reach the judge or the grades (the guard test covers the new operations)
- [ ] vitest: the grade, the letter and the two checks show on an evaluation's claims, with "Not graded" and an empty average handled
- [ ] Live check: Jev grades the lab's four test rationales for CLM-1001 as the lab says it should, through the real endpoint (a few thousandths of a cent)
- [ ] `npm run check` passes with `.env` moved out of the way

## Phase 4: The improver

**Builds:** the rounds, the proposals, the keep-or-roll-back rule, the spending limit and the improver itself, with no dashboard page yet.

- `backend/db.py`: `rounds` (the recipe version, the settings it works to as they stood at its start, its stage, the version tried, the improver's reason, the held-out scores before and after, the verdict and why, who started it, its improver thread, its times), `proposals` (round, change or recipe, what is proposed, pending, approved or rejected, who and when, a rejection's reason) and `improver_calls` (tokens and cost, with the round). Runs gain a `round_id`.
- `backend/improvement.py` (pure functions, as the lab's): `compare`, `keep_or_roll_back` with the round's settings, and a version's scores as the averages of the whole-set evaluations that rounds ran on it. A version without a grade is rolled back, as is one whose evaluations were stopped or that the round couldn't afford to evaluate.
- Operations: `start_round` (a senior handler; forwards to the service) and `create_round` (the service's; one open round at a time, snapshots the settings), `get_round`, `list_rounds`, `stop_round` (a senior handler; marks it stopped, after which the backend refuses anything the round records, and forwards to the service), `set_round_stage`, `record_proposal`, `approve_proposal` and `reject_proposal` (a senior handler; they forward to the service with the handler and pass on a refusal) and `record_proposal_decision` (the service's, which makes the version or recipe on an approval), `get_improver_view` (what `read_results` shows), `estimate_evaluation`, `conclude_round` (the verdict), `record_improver_call`. Evaluations take a `version` and a `round_id`, and the stretch of improver work is a run of kind `improver` under the one-run rule above.
- The improver's view holds the current version and its scores on both sets; from its latest whole-set evaluation on the evaluation set, the wrong claims and the five right ones explained least well, with the handler's decision and reason and the judge's grade and its two checks beside the agent's; the earlier rounds; the objectives, the noise, the tolerance, the rule; what is left of the round's spending limit; and the models allowed. From the held-out set it sees only the scores.
- The spending limit covers the improver's own model calls, the round's evaluations (the first ones too) and the judge. `run_evaluation` is refused when an evaluation's estimate is more than what is left: its claims times the highest cost per claim seen so far for its model, or for a model with no runs, the current model's scaled by the larger of the input and output price ratios. Once a round has spent its limit, `start_claim` refuses new claims in its evaluations, and an evaluation cut short that way is stopped.
- `agent/improver.py` (the sixth part): `create_agent` on `openai/gpt-6-luna` at medium effort with `parallel_tool_calls=False`, `ModelCallLimitMiddleware` for the round, a validation middleware that sends a proposal whose model or effort isn't allowed back to the improver before the approval pause, and `HumanInTheLoopMiddleware` on `propose_change` and `propose_recipe`. Its checkpointer is its own `PostgresSaver` on the shared pool with `durability="sync"`, and each round has a new thread id. Its instructions wrap the recipe and are written as goals: what the claim agent works from (its instructions with the policy wording and the claims manual, the claim, the policy and the customer's earlier claims, and its tools), and that a change never names a claim.
- The four tools (`read_results`, `propose_change`, `run_evaluation`, `propose_recipe`) reach the backend through `agent/improver_api.py`, which the claim agent never imports. `run_evaluation` works its three evaluations (one on the evaluation set, two on the held-out set) through the dispatcher in a fresh `contextvars.Context`, so the dispatcher doesn't save into the improver's thread, then calls `conclude_round`. A round starts by running any of the current version's missing whole-set evaluations (1, 2 or 3 of 3).
- `agent/service.py`: `run_round`, the approval and rejection operations (which first take a new stretch, then record the decision and the handler with the backend, then resume the improver), and a stop that starts no more claims or model calls for the round and lets go of the one-run rule when the work in hand ends. On startup the service marks a round that was working as stopped and leaves one that was waiting for a handler waiting. A round in which the improver proposes nothing finishes with no version tried. The improver's work is in one Langfuse session named after the round, and the claims of its evaluations are tagged with the round as well as their run.

**Why this way:** the model proposes and a person approves, and the code that keeps or rolls back a version is plain Python on held-out scores the improver never reads. The settings the improver can't change aren't reachable by any of its tools.

**Success criteria** (pytest, stub model, stub judge, no network)

- [ ] a version kept and one rolled back by the rule, with measures worked out by hand, cost and time compared as fractions of the old value and accuracy and the grade as differences, and a version with no grade rolled back
- [ ] a version's scores count only the whole-set evaluations its rounds ran (not a tiny one, not one started on the Scoreboard)
- [ ] the improver's view holds nothing from the held-out set but its scores, the wrong claims and five weakest right ones come from the latest evaluation-set run, and the handler's reasons are beside the agent's
- [ ] a proposal changes nothing until it is approved; a rejection's reason reaches the improver, which may propose again; a proposal outside the models allowed goes back to the improver without a pending proposal, and the validation runs before the pause (the middleware order is tested)
- [ ] an approval or a rejection is refused while a dispatch or an evaluation runs, naming what is running, with nothing recorded; dispatches and evaluations run while a round waits for a handler; a round's own evaluations are not refused
- [ ] an evaluation refused when its estimate is over what is left of the round's limit, the estimate for a model with no runs scaled by the larger price ratio, and an evaluation cut short by the limit shown as stopped and its version rolled back
- [ ] a stopped round leaves the current version as it was when stopped before its verdict and keeps it when stopped after, the backend refuses what a stopped round records, and the service starts no more claims for it
- [ ] a round keeps the settings it started with when the settings change
- [ ] a round that waits for a handler survives a restart (the improver resumes from its saved thread); a round that was working when the service stopped is marked stopped
- [ ] a round in which the improver proposes nothing finishes with no version tried
- [ ] the improver's evaluations don't save into its thread (its checkpoints hold none of the dispatcher's), and its model calls and the judge's calls count against the round's limit
- [ ] only a senior handler can start or stop a round or approve or reject a proposal (403 for Marcus, 401 with no sign-in), and nothing in the improver's tools can change the objectives, noise, tolerance, spending limit, models allowed, approval limit, evaluation sets or judge
- [ ] the claim agent still can't reach the scores or the handlers' decisions (the guard test covers every new operation and `agent/improver_api.py` is imported only by `agent/improver.py`)
- [ ] the openapi guard test passes, and `npm run check:python` passes with coverage of 80% or more

## Phase 5: The Improvement page

**Builds:** the page where a handler starts and watches a round, approves or rejects what the improver proposes, and sees the rounds, the chart and the versions.

- `#/improvement`, built with the shared components:
  - A header saying what the page is for, with Start a round and Stop the round (each behind a confirmation, for a senior handler; other handlers are told what they can't do and why).
  - Where the open round has got to, live: the first evaluations (1, 2 or 3 of 3), the improver working, a change waiting for approval, the evaluations (1, 2 or 3 of 3), the verdict, a recipe waiting for approval, and finished or stopped. The stages keep their place, so nothing jumps as the page refreshes.
  - The waiting proposal: the change to the instructions as a diff, with the model and reasoning effort before and after, and Approve and Reject, with a reason that goes back to the improver, each behind a confirmation. A recipe proposal is shown as a diff in the same way.
  - The rounds so far, newest first: the recipe version, the settings it worked to, the version tried, the improver's reason, the held-out scores before and after, the verdict and why, who approved or rejected what and when, and what it cost.
  - A chart, drawn in SVG with no new library, of the held-out accuracy and grade of each version tried.
  - The recipe's versions, and one diff view that shows the change of any configuration version or recipe version from the one before.
- The Configuration page's versions show their held-out scores, and every page that polls keeps its place on refresh.

**Success criteria**

- [ ] vitest: the page shows each stage as the round moves through them, with 1, 2 or 3 of 3 for the evaluations, and nothing re-orders or resizes when the data refreshes
- [ ] vitest: a waiting change shows as a diff with the model and effort before and after; Approve and Reject each open the dialog first; a rejection sends its reason; a recipe shows as a diff
- [ ] vitest: Start and Stop need a confirmation; a handler who can't sign off over the limit sees what they can't do and why, and the backend's refusal shows if it comes
- [ ] vitest: the rounds list shows each round's recipe version, settings, version tried, reason, scores before and after, verdict, approvals and cost; the chart plots one point for each version tried; the diff view works for a version and a recipe
- [ ] e2e: a handler signs in, a round played out through the API (a proposal made, approved through the dialog, a verdict) shows its stages, the diff, the verdict and the cost on the page, and a second handler without the right sees the explanation and no buttons
- [ ] Playwright MCP, against the stand-ins: the page in each state (waiting for approval, evaluating, finished, stopped, empty) in light and dark mode, with no horizontal scroll at laptop width
- [ ] `npm run check` passes with `.env` moved out of the way

## Phase 6: The look of the whole dashboard

**Builds:** the small improvements that bring the existing pages up to the standard of the new ones, without changing how any of them works.

- The same page header and spacing on every page (Queue, Claim, Scoreboard, Evaluation, Configuration, Improvement, Settings, sign-in), navigation that shows where you are (the current page, and the parent of the claim and evaluation pages), tables whose numbers are right-aligned with their units and whose columns line up, statuses as a colour and a word everywhere, buttons that look alike, and loading, empty and error states that say what to do next.
- A confirmation in the dashboard's own dialog before any action that changes the agent or a setting (a sign-off, a start of a dispatch, an evaluation or a round, a setting, an approval or a rejection), and nothing that jumps about as a page refreshes: sizes reserved while loading, old data kept until new arrives.
- `docs/BRAND.md` in light and dark mode: Instrument Sans, Instrument Serif and JetBrains Mono, the colours as tokens, panels rounded with no outline, no border on one edge, and no heavy gradients. The pages work on a laptop screen.

**Success criteria**

- [ ] vitest: every page renders the shared header, the navigation marks the current page, numbers are right-aligned in each table (a class test on the cells), and each state (loading, empty, error) says what to do next
- [ ] vitest: each action that changes the agent or a setting opens the confirmation before it calls the backend, and Cancel calls nothing
- [ ] e2e: the existing specs still pass unchanged in what they do (only the confirmation clicks are added), and the pages refresh without their layout moving (the heights of the header and the main panels are the same before and after a refresh)
- [ ] Playwright MCP: every page in light and dark mode, at a laptop width and at phone width, with no horizontal page scroll, checked against `docs/BRAND.md` and point 8 of the brief
- [ ] `npm run check` passes with `.env` moved out of the way

## Phase 7: Live runs and the handover

**Builds:** the live checks with the real model and the documents.

- Documents: `docs/SPEC.md` (the approval limit as a setting, the improver reading the handlers' decisions and reasons on the evaluation set while the claim agent still never sees them and nothing in the agent reads the held-out set's, the sign-in, the improver and what the team needs to see; the Rules otherwise stay), `docs/TECH_STACK.md` (the sixth part, the configuration's versions, the judge, the sign-in and the settings), `docs/PROCESS.md` (the live-run rule for these checks), `docs/CODE_STANDARDS.md` (rule 7 updated and rules 9 and 10), `docs/STATUS.md` and `data/README.md` (the two new data files and the handlers' flags).
- Live runs (real model, `.env` in place), after one `npm run seed`, with `npm run dev` running, checked through the Playwright MCP server, with the handlers' password from `.env`:
  1. Signed in as Dana Whitfield, run two rounds from the Improvement page. Reject the first proposal with a reason, then approve the next, and approve the rest. Each round shows on the page as it happens, and afterwards the rounds show the scores before and after, the verdict and the cost. The Configuration page shows the versions and their diffs. Through Langfuse's API, each round's improver traces are found in one session. Report how far version 1's two held-out runs differed on each measure, beside the noise and the tolerance.
  2. Start a dispatch at N=25 on the current version. Signed in as Marcus Oyelaran, a payout waiting for sign-off can't be signed off and the settings can't be changed. Signed in as Dana Whitfield, the same payout is signed off and paid.
  3. Take screenshots of every page in light and dark mode. A fresh subagent reviews them once, as a senior product designer would, against `docs/BRAND.md` and point 8 of the brief, and I fix what it finds.
  4. Report what the live checks spent. On GPT-6 Luna they should come to under $1. Before them I'll read the OpenRouter key's credit limit and usage (a read-only call) so the spend is measured from the key.

**Success criteria**

- [ ] live step 1: two rounds, the first proposal rejected with a reason and the improver's next proposal approved, each round's stages seen on the page as they happen, the scores before and after, the verdict and the cost shown afterwards, and the Configuration page's versions and diffs
- [ ] Langfuse's API returns each round's improver traces in one session, and the claims of its evaluations by the round's tag
- [ ] version 1's two held-out runs: how far they differed on each measure, beside the noise and the tolerance, reported in `docs/STATUS.md`
- [ ] live step 2: a dispatch at N=25 on the current version records that version; Marcus can't sign off the held payout or change a setting, and the page and the backend both say why; Dana signs it off and it is paid
- [ ] live step 3: screenshots of every page in light and dark mode, one fresh subagent review, and what it found fixed (or noted, with a reason, in `docs/STATUS.md`)
- [ ] live step 4: the live checks' spend is reported and is under $1
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md` and `data/README.md` describe the product as it now is
- [ ] `npm run check` passes with `.env` moved out of the way: ruff, pyright, pytest at 80% or more, Prettier, ESLint, tsc, vitest at 80% or more, and Playwright
