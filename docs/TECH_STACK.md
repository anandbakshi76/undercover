# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount.
- `agent/`: two agents built with LangChain's `create_agent` on OpenRouter, and no other agent framework. The queue agent (`run_queue(run_id)`) plans a run as a to-do list and hands each claim to a claim agent (`work_claim(run_id, claim_id)`), a new `create_agent` with a new thread for every claim. LangChain middleware retries the model and the backend, limits a claim agent's model calls, and checks for missed claims before the queue agent finishes. The agents reach claims, documents and their instructions only through the backend's API. They use `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
IF any of the software is missing what describe above or anywhere in the other files, if the versions are not the one mentioned here then downlaod and update the mentioned version or latest versions.