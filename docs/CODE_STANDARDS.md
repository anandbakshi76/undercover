# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. The agent has six parts: the dispatcher, the claim agent, the checkpointer, the service, the tracing and the improver. The dispatcher uses no model, and it reaches the claim agent through one function. The improver reaches the backend only through `agent/improver_api.py`, which nothing else imports, and the claim agent's client has no way to the scores or the handlers' decisions.
8. Keep each process to 4 database connections at most. Claim agents wait for a connection rather than fail, and each claim run gets a checkpoint saver of its own.
9. Write an agent's instructions as goals, not as steps or lists of cases. A recipe the improver follows is data, so it may be steps.
10. Call each outside service from one module of its own, such as the judge, Supabase Auth or OpenRouter's alpha endpoint, with its address read once from the environment.
