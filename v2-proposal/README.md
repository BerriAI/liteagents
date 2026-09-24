# liteagents v2 proposal

A proposed Python SDK for running agents across frameworks, with Temporal for checkpointing and recovery.

## Usage

This example gives DeepAgents access to a local project and renames a function. Set `OPENAI_API_KEY` before running.

```python
import asyncio
from pathlib import Path

from deepagents.backends import FilesystemBackend
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions


async def main():
    project = Path("example-project").resolve()
    project.mkdir(exist_ok=True)
    source = project / "greeting.py"
    source.write_text("def greet():\n    return 'hello'\n")

    profile = ProfileOptions(
        harness="deepagents",
        model="openai/gpt-5.4-mini",
        tools=["read_file", "edit_file"],
        harness_options={
            "backend": FilesystemBackend(root_dir=project, virtual_mode=True),
        },
    )
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as agent:
        async for message in agent.query("Rename greet to welcome in /greeting.py."):
            print(message)
    print(source.read_text())


asyncio.run(main())
```

The backend maps `/greeting.py` to the real file at `example-project/greeting.py`.

## Harnesses

The harness runs the agent loop. The client, `query()` call, and message types stay the same when you switch.

| Framework | `harness` |
| --- | --- |
| DeepAgents | `deepagents` |
| Pydantic AI | `pydantic-ai` |
| Claude Agent SDK | `claude-sdk` |
| Codex | `codex` |
| OpenCode v1 | `opencode-v1` |
| OpenCode v2 | `opencode-v2` |

`query()` yields shared `AssistantMessage` and `UserMessage` types containing text, tool calls, and tool results. With streaming enabled, it also yields `TextDelta` events. Each adapter maps its harness's output into these types.

Switching this example to Pydantic AI keeps the prompt, client, and message-handling code. Replace DeepAgents' backend and built-in tools with Pydantic AI tools, or connect both to the same MCP server. Models and native options must be supported by the chosen harness.

## Profiles

Profiles can be Python, YAML, or JSON. Start with three fields and add the settings below as needed.

```yaml
# agent.yaml
harness: deepagents
model: openai/gpt-5.4-mini
tools: [read_file, edit_file]
```

Load YAML with `ProfileOptions.from_yaml("agent.yaml")`. Pass Python objects, like the filesystem backend, through `profile.harness_options`. `${NAME}` reads an environment variable.

Tool names refer to implementations supplied by the harness, registered through its native API, or exposed by an MCP server.

### LiteLLM proxy settings

Use a proxy alias as the model and keep its model-call configuration in the profile:

```yaml
model: litellm_proxy/code-agent
model_kwargs:
  api_base: ${LITELLM_PROXY_URL}
  api_key: ${LITELLM_API_KEY}
  reasoning_effort: high
  temperature: 0.2
```

Profile settings override the model's proxy defaults, so reasoning effort can change without editing the proxy's `config.yaml`.

### MCP servers

Add a server's tools. The same server can be used by harnesses that support its connection type.

```yaml
mcp_servers:
  project_tools:
    url: ${MCP_SERVER_URL}
```

### Subagents

Each subagent can have its own model, parameters, and tools. The selected harness controls how it runs.

```yaml
subagents:
  reviewer:
    description: Review the changes for correctness.
    model: anthropic/claude-sonnet-4-6
    tools: [read_file]
features:
  subagents: true
```

### Streaming

Enable incremental text alongside completed messages:

```yaml
features:
  streaming: true
```

## Temporal

Add Temporal settings to run through an existing Temporal service:

```yaml
temporal:
  address: ${TEMPORAL_ADDRESS}
  namespace: default
  task_queue: liteagents
```

Start a worker with access to the project. Here, the project is mounted at `/srv/projects/example-project`:

```python
# worker.py
import asyncio

from deepagents.backends import FilesystemBackend
from liteagents import ProfileOptions
from liteagents.temporal import LiteAgentWorker

profile = ProfileOptions.from_yaml("agent.yaml")
profile.harness_options["backend"] = FilesystemBackend(
    root_dir="/srv/projects/example-project", virtual_mode=True
)
asyncio.run(LiteAgentWorker(profile=profile).run())
```

From the application, run the agent with an ID:

```python
options = LiteAgentOptions(profile=ProfileOptions.from_yaml("agent.yaml"))
async with LiteAgentClient(options=options) as agent:
    async for message in agent.query(
        "Rename greet to welcome in /greeting.py.",
        run_id="rename-greeting-123",
    ):
        print(message)
```

The adapter records model and tool results through Temporal. For a task that edits a file and then runs tests, a worker crash between those steps has two cases:

- **The edit result was recorded:** recovery reuses it and continues to the next step. The completed edit is not repeated.
- **The file changed, but the result was not recorded:** the edit may run again. The tool must recognize an already-applied change or otherwise tolerate retries.

Replacement workers need access to the same persistent project files. Temporal records execution history; it does not back up the workspace.

Closing the client leaves the run on the worker. Reconnect with its ID to retrieve the result without submitting the task again:

```python
async with LiteAgentClient(options=options) as agent:
    run = await agent.get_run("rename-greeting-123")
    result = await run.result()
```

## Retries and fallbacks

Configure retries and compatible fallbacks in the profile:

```yaml
recovery:
  retries:
    max_attempts: 3
  model_fallbacks: [anthropic/claude-sonnet-4-6]
  harness_fallbacks: [pydantic-ai]
```

`max_attempts` limits eligible model/tool operations to three total attempts here. A model fallback then retries the model request with the same conversation and tools.

A harness fallback starts over with the original prompt and a fresh conversation. By default, it is automatic only before the first tool call. After that, recovery uses the existing harness and returns an error if exhausted.

Fallbacks must pass compatibility checks before the run. The example's DeepAgents backend cannot carry over to Pydantic AI; shared MCP tools are an option for both.

## Framework-specific controls

Use `harness_options` for native controls. For example, add DeepAgents middleware and pause before editing files:

```python
from my_app.middleware import tool_budget

profile.harness_options.update(
    middleware=[tool_budget],
    interrupt_on={"edit_file": True},
)
```

`tool_budget` is application-provided middleware. Native loop, tool, and subagent options stay accessible; unsupported options raise an error.
