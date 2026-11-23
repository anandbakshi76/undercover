# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. The agent has five parts: the dispatcher, the claim agent, the checkpointer, the service and the tracing. The dispatcher uses no model, and it reaches the claim agent through one function.
8. Keep each process to 4 database connections at most. Claim agents wait for a connection rather than fail, and each claim run gets a checkpoint saver of its own.
