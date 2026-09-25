# Try the LiteAgents developer preview

LiteAgents runs real DeepAgents, Pydantic AI, Claude Agent SDK, Codex, and OpenCode
loops through one Python client. Start with a simple agent, add your own tools,
then try durable execution if your task needs it.

This guide targets **0.2.0a2**. It is a source preview; installing an unqualified
`liteagents` package from PyPI does not select this tested revision.

## 1. Get a first response

Use Python 3.12 and a fresh environment. Set `LITEAGENTS_PREVIEW_REF` to the full
commit supplied in the preview handoff. Check out that revision, then install
only DeepAgents:

```sh
git clone https://github.com/BerriAI/liteagents.git
cd liteagents
git checkout --detach "$LITEAGENTS_PREVIEW_REF"
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install '.[deepagents]' -c constraints-tested.txt
```

Point the example at your model endpoint:

```sh
export LITEAGENTS_API_BASE='https://your-gateway.example/v1'
export LITELLM_API_KEY='your-endpoint-key'
export LITEAGENTS_MODEL='your-chat-compatible-model-alias'
python cookbook/recipes/00_agent.py
```

Expected: `READY`. No Temporal or PostgreSQL service is needed. The model
endpoint can be an existing LiteLLM gateway or a compatible provider endpoint;
the variable names do not require you to deploy a LiteLLM server. Calls use your
model account.

Next, try file tools, streaming, and a follow-up in the same conversation:

```sh
python cookbook/recipes/01_quickstart.py
```

Expected: the agent reads `facts.txt`, reports `COBALT-42`, then remembers the
code in the follow-up. Examples use their own `.liteagents/recipes/` workspaces.

## 2. Add an application tool and MCP, then switch harnesses

```sh
python cookbook/recipes/08_application_tools.py
python -m pip install '.[mcp]' -c constraints-tested.txt
python cookbook/recipes/02_mcp.py
```

The application-tool example implements a small `Tool` class and registers it
with `LiteAgentOptions(tools=[...])`. It prints `Tool: lookup_order` and reports
order A123 as paid, total USD 12. Replace its `execute()` body with a call to your
own application to try a real task.

The MCP example starts its own local server and discovers the allowed
`lookup_order` tool. Expected: A123 is paid, total USD 12. No external MCP account
is needed. The model sees only the selected tool.

Try Pydantic AI using the same endpoint and tool code:

```sh
python -m pip install '.[pydantic-ai]' -c constraints-tested.txt
python cookbook/recipes/08_application_tools.py --harness pydantic-ai
python cookbook/recipes/02_mcp.py --harness pydantic-ai
```

For the complete comparison, install `.[all]` with the same constraints. The
Claude and Codex extras supply their runtimes. OpenCode additionally requires
`npm install -g opencode-ai@1.18.29`.

| Harness selector | Endpoint/model requirements |
| --- | --- |
| `deepagents`, `pydantic-ai` | Chat Completions for these recipes |
| `claude-sdk` | Anthropic Messages; set `LITEAGENTS_CLAUDE_MODEL` to a compatible alias |
| `codex` | Responses; set `LITEAGENTS_MODEL` to a compatible alias |
| `opencode-v1`, `opencode-v2` | OpenAI-compatible Chat Completions; both use the same OpenCode server |

```sh
python cookbook/recipes/08_application_tools.py --harness codex
python cookbook/recipes/02_mcp.py --harness codex
```

Shared tool names, MCP configuration, and application event handling stay the
same. Model aliases must support the selected harness's protocol. Native CLI
shared tools require an explicit `api_base`; the examples already provide it.

## 3. Add Temporal and recover a worker

Install `.[temporal]` using the same constraints. Follow the
[durable cookbook](../cookbook/recipes/README.md#durable-run-and-worker-crash):

1. Start a local Temporal service, then the recipe's worker in another terminal.
2. Submit a run and retrieve its result from a separate client process.
3. Submit another run, kill the printed recipe worker PID during `slow_check`,
   and restart it with the same workspace.

Expected: `receipt-verified`; the completed receipt operation is reused after
the worker restarts. Local SQLite is sufficient. The
[self-hosting guide](self-hosting.md) covers PostgreSQL and shared deployment.

Application tools move to `LiteAgentWorker(tools=[...])` in this mode. Clients
submit the profile and prompt; executable Python tools remain on the worker.

## Behavior to know during the trial

- Omitted `profile.tools` uses adapter defaults. `tools=[]` means no tools.
  An explicit list selects shared tools; use it when comparing harnesses.
- Direct queries on one client share a conversation. Each Temporal submission
  is an independent job; attach to the same job with `get_run()`.
- Shared tools automatically select managed CLI execution. Native tool-policy
  overrides cannot be mixed with that mode. Unsupported model settings fail
  explicitly; not every harness supports `temperature` or the same native options.
- Completed recorded operations are reused after worker loss. An interrupted
  external action can run again, so effectful tools need idempotency. Keep the
  workspace and checkpoint stores when restarting the worker.
- This preview is for evaluation. See [validation](validation.md) for exactly
  what was tested, and [migration](migration.md) if using the earlier preview.

For more examples, the [recipe index](../cookbook/recipes/README.md) covers
approvals, subagents, retries, and fallback.

## Useful feedback

Try one real task with your own tool or MCP server. Tell us which harness and
model you used, the preview commit, what you expected, and what happened. If a
run failed, include the exception and reproducible profile with credentials
removed. We especially want to learn where setup or adapting your own tools
needed explanation, and whether durability fits your application's jobs.
