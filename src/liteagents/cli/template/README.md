# {{name}}

A LiteAgents agent running on `{{harness}}` with `{{model}}`.

```sh
liteagents dev            # start the channel in agent.toml
liteagents dev terminal   # or chat in this terminal
```

Edit `agent/instructions.md` to change its behavior; it is re-read on every turn.
Add Python functions to `TOOLS` in `agent/tools/__init__.py`, and scheduled
prompts to `CRONS` in `agent/crons/__init__.py`. Change `harness` or `model` in
`agent.toml` to try another framework, then run `pip install -e .` again if the
harness needs a different extra. Secrets live in `.env`, which is gitignored.
