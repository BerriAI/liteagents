# LiteAgents preview for Mindfort

This is **LiteAgents 0.2.0a2**, a developer preview of the shared Python SDK for
DeepAgents, Pydantic AI, Claude Agent SDK, Codex, and OpenCode. Each harness runs
its own loop. The SDK provides common tools, MCP, events, and optional Temporal
recovery.

## Pinned install

Tested SDK revision: **`61cddea73a7057a5e5ae912ea432e64c91c2a1d3`**.
Use Python 3.12. This revision is distributed from source, not PyPI.

```sh
export LITEAGENTS_PREVIEW_REF=61cddea73a7057a5e5ae912ea432e64c91c2a1d3
git clone https://github.com/BerriAI/liteagents.git
cd liteagents
git fetch origin "$LITEAGENTS_PREVIEW_REF"
git checkout --detach "$LITEAGENTS_PREVIEW_REF"
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install '.[deepagents]' -c constraints-tested.txt

export LITEAGENTS_API_BASE='https://your-gateway.example/v1'
export LITELLM_API_KEY='your-endpoint-key'
export LITEAGENTS_MODEL='your-chat-compatible-model-alias'
python cookbook/recipes/00_agent.py
```

Expected: `READY`. This first step needs no Temporal or PostgreSQL service.
Use your existing compatible model endpoint and its API key. An OpenAI-compatible
provider endpoint also works; deploying LiteLLM is optional.

## What to try

Follow the [trial guide](preview.md), starting at step 2 after the install above:

1. Run `01_quickstart.py` to read a file, stream an answer, and ask a follow-up.
2. Run `08_application_tools.py` and `02_mcp.py`, then change `--harness`.
   Replace the application tool with one useful for a real Mindfort task.
3. If durability matters, run the Temporal cookbook and recover from a killed worker.

The guide includes additional installs, model/protocol requirements, expected
results, and the worker restart instructions. The [recipe index](../cookbook/recipes/README.md)
also covers approvals, subagents, retries, and fallback.

## Trial boundaries

- Use explicit tool selections when comparing harnesses. Native default tool
  sets and model capabilities differ.
- Direct queries on one client share history. Temporal submissions are
  independent jobs; reconnect to the same job with `get_run()`.
- Shared CLI tools/MCP require `api_base` and cannot combine native tool-policy
  overrides. Unsupported settings produce errors.
- Recovery reuses completed recorded operations. Interrupted external effects
  still need idempotency, and worker restarts need the same workspace and stores.
- This is an evaluation preview. The [validation record](validation.md) separates
  deterministic native-runtime tests, live models, and deployment checks.

Useful feedback: which harness/model you used, whether setup was clear, what
needed changing to use your own tools, and the first real task that worked or
failed. Include the pinned revision and a reproducible profile with credentials
removed when reporting a problem.
