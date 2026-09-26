# Getting started with LiteAgents

LiteAgents runs real DeepAgents, Pydantic AI, Claude Agent SDK, Codex, and OpenCode
loops through one Python client. Start with a simple agent, add your own tools,
then try durable execution if your task needs it.

This guide uses the **0.3.0a2** source checkout and its runnable cookbooks.
Install from this checkout or the matching preview release.

## 1. Get a first response

Use Python 3.12 and a fresh environment. Check out the repository and install only
DeepAgents:

```sh
git clone https://github.com/BerriAI/liteagents.git
cd liteagents
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
model account. Set `LITEAGENTS_MODEL` to the exact alias configured at that
endpoint. No `litellm_proxy/` prefix is needed; aliases containing `/` are sent
unchanged. The recipes use LiteLLM for every harness's model requests.

Next, try file tools, streaming, and a follow-up in the same conversation:

```sh
python cookbook/recipes/01_quickstart.py
```

Expected: the agent reads `facts.txt`, reports `COBALT-42`, then remembers the
code in the follow-up. Examples use their own `.liteagents/recipes/` workspaces.

Prefer configuration files? Run the equivalent YAML and JSON examples:

```sh
python cookbook/recipes/09_profile_files.py cookbook/recipes/profiles/agent.yaml
python cookbook/recipes/09_profile_files.py cookbook/recipes/profiles/agent.json
```

Both print `READY` using the same environment variables. See the
[profile guide](profiles.md) for loading files in your own application and
configuring tools, MCP, or Temporal.

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

All selectors use the same `LITEAGENTS_MODEL`. LiteLLM handles the protocol
translation inside the SDK. The gateway only needs Chat Completions support.

```sh
python cookbook/recipes/08_application_tools.py --harness codex
python cookbook/recipes/02_mcp.py --harness codex
```

Shared tool names, MCP configuration, and application event handling stay the
same. No separate Claude model alias or protocol configuration is needed.
To check application tools, MCP, and follow-up history across every harness, run
`python cookbook/recipes/10_harness_switch.py --all`.

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

Register the same application tools on `LiteAgentWorker(tools=[...])`.
The client may keep its tool registry, but executable Python code runs on the
worker and is never shipped through Temporal.

## Execution behavior

- Omitted `profile.tools` exposes registered application and MCP tools on every
  harness. `tools=[]` means no tools; explicit names select tools, including
  optional workspace tools.
- `query()` shares a conversation on one client. `start_run()` creates independent
  jobs. Both meanings stay the same with Temporal; `get_run()` attaches without
  resubmitting.
- `harness_options` preserves native controls. Settings that override the shared
  model/tool boundary fail explicitly. Model settings are translated by LiteLLM;
  the selected model must support them.
- Completed recorded operations are reused after worker loss. An interrupted
  external action can run again, so effectful tools need idempotency. Keep the
  workspace and checkpoint stores when restarting the worker.
- See [validation](validation.md) for exactly what was tested, and
  [migration](migration.md) if upgrading from v1 or an earlier v2 preview.

For more examples, the [recipe index](../cookbook/recipes/README.md) covers
approvals, subagents, retries, and fallback.

## Useful feedback

Try one real task with your own tool or MCP server. Tell us which harness and
model you used, the package version, what you expected, and what happened. If a
run failed, include the exception and reproducible profile with credentials
removed. We especially want to learn where setup or adapting your own tools
needed explanation, and whether durability fits your application's jobs.
