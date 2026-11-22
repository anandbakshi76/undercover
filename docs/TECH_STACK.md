# Tech stack

- `backend/`: FastAPI and SQLAlchemy Core. It uses Supabase when `DATABASE_URL` is in its environment, and SQLite otherwise, which is what the tests use. All our tables, and the agent's checkpoints, are in the `undercover` schema on Supabase, with at most 4 connections in each process. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount. The payout rules are an operation of the backend (`calculate_payout`), which takes the approval limit from `data/company.json`.
- `agent/`: the UnderCover Agent on OpenRouter, in three parts and a service.
  - The queue agent (`run_queue(run_id)`) is built with LangChain's `create_agent`. It plans a run as a to-do list and hands each claim to a claim agent (`work_claim(run_id, claim_id)`). LangChain middleware retries its model and checks for missed claims before it finishes. It saves its progress with the checkpointer, with the run as its thread.
  - The claim agent is a hand-written LangGraph graph with its own state, nodes and edges. It looks the claim up with its tools, including the payout tool, then a decide node records its decision, a wait node pauses with `interrupt()` when the backend says a claim needs a sign-off, and a pay node pays every approval. Each node has a retry policy, and at most 30 model calls are made. A claim has a thread for each attempt.
  - Progress is saved after every step with LangGraph's synchronous Postgres checkpointer in the `undercover` schema, with `durability="sync"`, or in memory without `DATABASE_URL`.
  - The agent's service (`agent/service.py`, port 8002 or `UNDERCOVER_AGENT`) is the agent's entry point. The backend asks it to start runs and tells it after each sign-off. `uv run -m agent` still starts runs from the command line. Every run first carries on any claim or queue run that an earlier run left.
  - Langfuse traces every claim, in one session named after the claim's thread, and every queue run.
  - The agents reach claims, documents and their instructions only through the backend's API. They use `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The keys are in `.env`: `OPENROUTER_API_KEY`, `DATABASE_URL` (the Supabase session pooler connection string) and the three `LANGFUSE_` values.
IF any of the software is missing what describe above or anywhere in the other files, if the versions are not the one mentioned here then downlaod and update the mentioned version or latest versions.