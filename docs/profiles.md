# Configure agents with JSON or YAML

A profile is the same setup in Python, JSON, or YAML. Choose a model and a
harness; changing the harness leaves your application code unchanged.

## A first profile

Save this as `agent.yaml`:

```yaml
harness: pydantic-ai
model: openai/gpt-5.4-mini
system_prompt: Be concise.
```

Or use the equivalent `agent.json`:

```json
{
  "harness": "pydantic-ai",
  "model": "openai/gpt-5.4-mini",
  "system_prompt": "Be concise."
}
```

Use the [quickstart installation](getting-started.md#1-install) and set
`OPENAI_API_KEY`. Inside your async application or Colab:

```python
from liteagents import ProfileOptions, run

profile = ProfileOptions.from_yaml("agent.yaml")
# For JSON: profile = ProfileOptions.from_json("agent.json")
result = await run("Explain what an agent harness does in one sentence.", profile=profile)
print(result.text)
```

Edit `harness` to use another installed integration. Edit `model` and set the
matching provider credentials to use [another provider](models.md). LiteLLM
handles model translation. Neither a gateway nor Temporal is required.

The loaders accept a **file path**, not the file contents. YAML support is
included. `cwd` belongs on `run()`, the client, or the worker; it is not a profile
field. Paths inside a profile are not automatically relative to the profile file.

[Try this walkthrough in Colab](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/09_profile_files.ipynb).

## Optional gateway connection

If you already use a gateway, replace the model setting and add its connection:

```yaml
model: my-team-model
model_kwargs:
  api_base: ${LITEAGENTS_API_BASE}
  api_key: ${LITELLM_API_KEY}
```

Set those two environment variables before loading the file. `model` is sent as
the gateway's exact alias, including any slashes. The same loaded profile works
across harnesses. [Gateway setup](models.md#optional-litellm-gateway) has more detail.

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
pass typed Python functions or `Tool` instances to `run(..., tools=[...])`,
`LiteAgentClient(profile=profile, tools=[...])`, or
`LiteAgentWorker(tools=[...])` for durable runs. Profile files select tool names;
they do not import or define Python functions.

To use an HTTP MCP server, add this to the YAML profile:

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
Try the [MCP Colab](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/02_mcp.ipynb) for a complete example.

## Optional native controls

Keep harness-specific settings under their harness name:

```yaml
harness_options:
  deepagents:
    debug: true
  claude-sdk:
    max_budget_usd: 1.0
```

Changing only `harness` selects the corresponding controls. The shared model,
tools, and MCP configuration stay the same. A native control affects only the
harness that implements it. Flat options remain supported and apply to the
selected harness; named settings override flat settings of the same name.
See [native controls](sdk.md#native-controls) for the supported settings.

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
