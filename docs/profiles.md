# Configure agents with JSON or YAML

`ProfileOptions` accepts the same configuration in Python, JSON, or YAML.
`from_json()` and `from_yaml()` read a **file path** (`str` or `pathlib.Path`),
expand environment variables, and return a validated `ProfileOptions` instance.
YAML support is included in the core package.

## A first profile

Save this as `agent.yaml`:

```yaml
harness: deepagents
model: ${LITEAGENTS_MODEL}
model_kwargs:
  api_base: ${LITEAGENTS_API_BASE}
  api_key: ${LITELLM_API_KEY}
system_prompt: Be concise.
tools: []
max_turns: 10
```

Or save the equivalent configuration as `agent.json`:

```json
{
  "harness": "deepagents",
  "model": "${LITEAGENTS_MODEL}",
  "model_kwargs": {
    "api_base": "${LITEAGENTS_API_BASE}",
    "api_key": "${LITELLM_API_KEY}"
  },
  "system_prompt": "Be concise.",
  "tools": [],
  "max_turns": 10
}
```

`model` is the exact alias configured on the gateway at `model_kwargs.api_base`.
No prefix is needed. A slash in an alias is preserved, even in a name such as
`anthropic/team-model`. The gateway decides which provider serves it.

Set the environment variables in the process that loads the file:

```sh
export LITEAGENTS_API_BASE='https://your-gateway.example/v1'
export LITELLM_API_KEY='your-endpoint-key'
export LITEAGENTS_MODEL='your-chat-compatible-model-alias'
```

After [installing the DeepAgents extra](../README.md#install), run:

```python
import asyncio
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions

async def main():
    profile = ProfileOptions.from_yaml("agent.yaml")
    # For JSON instead: profile = ProfileOptions.from_json("agent.json")
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile, cwd=".")) as agent:
        run = await agent.start_run("Reply with exactly READY.")
        print((await run.result()).text)

asyncio.run(main())
```

Expected: `READY`. This profile disables tools and needs neither Temporal nor
PostgreSQL. `cwd` is a client/worker option and must exist; it is not a profile
field. Paths in the profile are not automatically relative to the profile file.

## Try the checked-in examples

From a checkout with the [cookbook environment](../cookbook/recipes/README.md#setup):

```sh
python cookbook/recipes/09_profile_files.py cookbook/recipes/profiles/agent.yaml
python cookbook/recipes/09_profile_files.py cookbook/recipes/profiles/agent.json
```

The two files describe the same agent. Edit `harness` in either file, or use
`--harness pydantic-ai` after installing that extra. The optional `--harness`
override leaves the model and all other settings unchanged. LiteLLM translates
the native protocols internally, so the same gateway alias works with Claude,
Codex, and the other harnesses.

## Environment variables and validation

`${NAME}` references expand inside string values, including nested MCP headers,
arguments, and subagent settings. A missing variable raises `ConfigurationError`
before the agent connects. The loaders read the current process environment;
they do not load `.env` files themselves. Keep credential placeholders in saved
profiles; the resolved profile contains the actual credentials, so do not log
or commit its serialized contents.

Unknown fields, unknown harness names, duplicate tool selections, and invalid
option values fail validation. Harness-specific compatibility checks also run
when the client or worker is created.

Already have parsed data or a JSON string? Use the standard Pydantic methods:

```python
profile = ProfileOptions.model_validate({"harness": "deepagents", "model": "openai/gpt-4.1-mini"})
profile = ProfileOptions.model_validate_json('{"harness":"deepagents","model":"openai/gpt-4.1-mini"}')
```

These methods and the Python constructor use the supplied values directly;
`${NAME}` expansion is specific to `from_json()` and `from_yaml()`.

## Tools and MCP

Omit `tools` or use `null` for registered application and MCP tools, use `[]` for no tools, or list
the shared tool names you want. Python tool implementations stay in your code:
register them with `LiteAgentOptions(tools=[...])`, or with
`LiteAgentWorker(tools=[...])` for durable runs. Profile files select tool names;
they do not import or define Python functions.

To use an HTTP MCP server, replace `tools: []` in the YAML profile with:

```yaml
mcp_servers:
  orders:
    url: https://your-mcp-server.example/mcp
    transport: http
    headers:
      Authorization: Bearer ${MCP_TOKEN}
    allowed_tools: [lookup_order]
tools: [orders_lookup_order]
```

Install the `mcp` extra, set `MCP_TOKEN`, and supply your server URL. The allowlist
uses the remote tool name; the profile selects the public `<server>_<tool>` name.
See the [MCP cookbook](../cookbook/recipes/README.md) for a local server you can run.

## Durable profiles

The same nested options can be written in either format. To enable Temporal,
add this section to the YAML profile:

```yaml
temporal:
  address: localhost:7233
  profile_id: project-agent-v1
  checkpoint_path: .liteagents/checkpoints.sqlite
```

Install the `temporal` extra and start the service and a separate worker. Load
the same versioned profile with `ProfileOptions.from_yaml()` on the worker and
client; give each process the necessary environment variables. Register Python
tools on the worker. See the [durable cookbook](../cookbook/recipes/README.md#durable-run-and-worker-crash)
for the worker/client lifecycle. Recipe 09 is a direct-execution example.

`query()` calls on one client share history in both direct and Temporal
execution. `start_run()` always submits an independent job. Bump `profile_id` when execution code or configuration changes,
and finish existing runs with their original profile and SDK version.
The [SDK contract](sdk.md) describes every shared feature and its limits.
