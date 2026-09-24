# liteagents v2 proposal

A proposed Python SDK for running agents across frameworks, with Temporal for checkpointing and recovery.

## Usage

Pick a harness, define a profile, and call `query()`.

```python
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions

options = LiteAgentOptions(
    profile=ProfileOptions(
        harness="deepagents",
        model="openai/gpt-5.4-mini",
        model_kwargs={"reasoning_effort": "high"},
    )
)

async with LiteAgentClient(options=options) as agent:
    async for message in agent.query("Rename this function"):
        print(message)
```

## Harnesses

The harness runs the agent loop. Select it through the profile and keep the same application code.

| Framework | `harness` |
| --- | --- |
| DeepAgents | `deepagents` |
| Pydantic AI | `pydantic-ai` |
| Claude Agent SDK | `claude-sdk` |
| Codex | `codex` |
| OpenCode v1 | `opencode-v1` |
| OpenCode v2 | `opencode-v2` |

Models and tools must be supported by the selected harness. Its native MCP support, subagents, and framework options remain accessible through the SDK.

## Profiles

A profile holds the agent’s configuration: harness, model parameters, tools, MCP servers, subagents, feature toggles, and recovery settings. Define it in Python or save it as YAML or JSON.

```python
profile = ProfileOptions.from_yaml("agent.yaml")
options = LiteAgentOptions(profile=profile)
```

```yaml
# agent.yaml
harness: deepagents
model: openai/gpt-5.4-mini
model_kwargs:
  reasoning_effort: high

tools:
  - read_file
  - edit_file
  - run_tests

mcp_servers:
  project_tools:
    url: ${MCP_SERVER_URL}

subagents:
  reviewer:
    description: Review the changes for correctness.
    model: anthropic/claude-sonnet-4-6
    tools:
      - read_file
  test_runner:
    description: Run tests and report failures.
    model: openai/gpt-5.4-mini
    model_kwargs:
      reasoning_effort: low
    tools:
      - read_file
      - run_tests

features:
  streaming: true
  subagents: true

temporal:
  address: ${TEMPORAL_ADDRESS}
  namespace: default
  task_queue: liteagents

recovery:
  retries:
    max_attempts: 3
  model_fallbacks:
    - anthropic/claude-sonnet-4-6
  harness_fallbacks:
    - pydantic-ai

harness_options: {}
```

`ProfileOptions` validates the shared fields and passes native options to the selected harness. Tool names refer to implementations provided by the application or harness. `${NAME}` reads an environment variable.

Each subagent can use a different model/provider, tool set, and model configuration. The harness determines how subagents are created and run.

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

The profile combines these settings with the model’s proxy configuration. Settings such as reasoning effort can be changed here without separately editing the proxy’s `config.yaml`.

## Tools

Tools come from three places:

| Source | Definition and execution |
| --- | --- |
| Harness | Built-in tools supplied by the selected framework. For example, DeepAgents supplies `read_file` and `edit_file`. |
| Application | A `Tool` implementation supplied by your code, such as `run_tests`. The adapter exposes it to the selected harness. |
| MCP server | Tools discovered from a configured server and called through MCP. The server supplies their names, descriptions, and input schemas. |

Names such as `read_file` are framework-specific. MCP standardizes how tools are discovered and called; it does not standardize their names or behavior.

### Define an application tool

A `Tool` has a name, a description, a JSON input schema, and an async `execute()` method:

```python
# my_app/tools.py
import asyncio
import sys

from liteagents import Tool


class RunTestsTool(Tool):
    name = "run_tests"
    description = "Run pytest on a test file or directory."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(self, input: dict) -> str:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pytest", "--", input["path"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return f"Exit code: {process.returncode}\n{output.decode(errors='replace')}"
```

For an in-process run, supply the implementation alongside the profile:

```python
from liteagents import LiteAgentOptions, ProfileOptions
from my_app.tools import RunTestsTool

options = LiteAgentOptions(
    profile=ProfileOptions(
        harness="deepagents",
        model="openai/gpt-5.4-mini",
        tools=["run_tests"],
    ),
    tools=[RunTestsTool()],
)
```

The profile selects the tool by name; `options.tools` supplies its implementation. Saved YAML/JSON profiles contain names, not executable code. Unknown or ambiguous tool names raise a configuration error.

For Temporal runs, supply application tools to the worker instead. The application and its workers must provide the tool’s dependencies and workspace; this example requires pytest in the environment where it executes.

## Temporal

Add `temporal` settings to the profile to run the agent through Temporal. Start a worker against an existing Temporal service:

```python
# worker.py
from liteagents import ProfileOptions
from liteagents.temporal import LiteAgentWorker
from my_app.tools import RunTestsTool

profile = ProfileOptions.from_yaml("agent.yaml")
await LiteAgentWorker(
    profile=profile,
    tools=[RunTestsTool()],
).run()
```

From the application, run the agent with an ID:

```python
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions

profile = ProfileOptions.from_yaml("agent.yaml")
options = LiteAgentOptions(profile=profile)

async with LiteAgentClient(options=options) as agent:
    async for message in agent.query(
        "Rename this function and run the tests",
        run_id="rename-function-123",
    ):
        print(message)
```

Temporal records the results of model and tool calls. When a worker restarts, recovery reuses recorded results and continues the run. Interrupted operations follow the retry policy. Closing the application’s client leaves the durable run running on the worker.

Reconnect to an existing run to retrieve its result:

```python
async with LiteAgentClient(options=options) as agent:
    run = await agent.get_run("rename-function-123")
    result = await run.result()
```

`get_run()` attaches to the existing run. It does not submit the task again. Temporal executes the agent through the selected harness’s adapter, which connects model and tool calls to durable steps.

## Retries and fallbacks

The profile’s `recovery` settings control what happens when an operation fails:

- `retries.max_attempts`: maximum attempts for an eligible model or tool operation, including the first attempt.
- `model_fallbacks`: alternative models to try after a model request exhausts its retries.
- `harness_fallbacks`: alternative harnesses to try when an agent attempt fails.

With Temporal enabled, retries use recorded progress. A tool that changes external state must tolerate being retried. If the configured recovery options are exhausted, the failure is returned to the caller.

## Framework-specific controls

Use `harness_options` for the selected framework’s own configuration. Python profiles can pass native objects, such as DeepAgents middleware:

```python
from my_app.middleware import tool_budget

profile = ProfileOptions(
    harness="deepagents",
    model="openai/gpt-5.4-mini",
    harness_options={
        "middleware": [tool_budget],
        "interrupt_on": {"edit_file": True},
    },
)
```

Here, `tool_budget` is a native middleware instance supplied by the application, and `interrupt_on` asks DeepAgents to pause before editing a file. Other harnesses expose their own options through the same field.

This applies to loop behavior, tools, and subagent configuration. Unsupported options should raise an error. Callbacks and other executable controls stay in application code; saved profiles can reference them.
