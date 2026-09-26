# Compare the native harnesses

[Open the comparison in Colab](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/compare_harnesses/compare.ipynb)
to edit the harness list and model, then inspect answers, diffs, and independent
test results. It installs its own dependencies and includes the example files.
See the [notebook index](../README.md).

The instructions below use the terminal runner from a repository checkout.

Install `liteagents[all]` from this checkout and OpenCode 1.18.29 as described in
the repository README. The six profiles use the same OpenAI model. Set its key:

```sh
export OPENAI_API_KEY='your-openai-key'
```

Each profile sets `model: openai/gpt-5.4-mini`. LiteLLM translates the native
protocols internally. To use another provider, change the same model setting in
the profiles and set its credentials. A gateway can be configured in
`model_kwargs`; see [profiles](../../docs/profiles.md).

From the repository root:

```sh
python cookbook/compare_harnesses/compare.py \
  cookbook/compare_harnesses/profiles/*.yaml \
  --output /tmp/liteagents-comparison
```

The included fixture has a deliberately broken subtraction function and three
standard-library unit tests. Each harness reads the files, fixes the function,
and runs tests. The runner independently runs the tests afterward. Exit status
is zero only when every run completes and every test command passes.

Every result directory includes:

- `workspace/`: its independent copy of the input.
- `changes.diff`: additions, deletions, and modifications.
- `result.json`: answer, run outcome, test output/exit code, elapsed seconds, and
  native usage reports when the harness supplies them.

The root `README.md` and `results.json` compare runs. Native usage fields have
different meanings; the runner preserves reported data without inventing totals
or costs. Timing includes process startup, and tool/model differences mean this
is a functional comparison rather than a controlled quality benchmark.

Try your own task:

```sh
python cookbook/compare_harnesses/compare.py profiles/*.yaml \
  --workspace /path/to/project \
  --output /tmp/my-comparison \
  --prompt 'Fix the reported bug and run tests' \
  --test-command '["python3", "-m", "pytest", "-q"]' \
  --timeout 300
```

The output directory must be new and outside the source workspace. Copying omits
`.git`, `.venv`, `node_modules`, `.env*`, generated agent state, and symlinks.
Install dependencies in each copied workspace if the task needs them, or choose
a test command using an existing interpreter. The fixture needs no dependencies.
Workspace copying is isolation of files, not an operating-system sandbox.
Profile secrets are redacted from reports, but generated workspaces remain local
artifacts and should be reviewed before sharing.

Codex state is scoped to each copied workspace under `.liteagents/codex`, so it
does not inherit the personal Codex configuration. OpenCode also uses dedicated
state/config/cache directories inside each workspace. Its aliases both run the
same tested server version. All six use their actual native harnesses.

## Reproduce live acceptance checks

The opt-in live acceptance suite uses a gateway fixture. Set `LITEAGENTS_MODEL`,
`LITEAGENTS_API_BASE`, and `LITELLM_API_KEY` for that suite, then run:

```sh
LITEAGENTS_LIVE=1 LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false \
  PYDANTIC_AI_NO_BANNER=1 pytest -q tests/test_live_harnesses.py
```

The tests make real model calls: file tools, token streaming, same-client
follow-up, an actual local MCP server for every harness, native session reopening, and a
real DeepAgents Temporal run (requiring localhost:7233). They have bounded
timeouts, and do not run by default or in ordinary CI. No key is needed for the
scripted Python harness tests or Temporal crash tests.
