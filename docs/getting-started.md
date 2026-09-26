# Getting started with LiteAgents

Install LiteAgents, choose a harness and model, and call `query()`.
No repository checkout, gateway, Temporal, or database is needed for a first agent.

**Follow the browser walkthrough:** install, add your key, run an agent, then switch harnesses.
[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb)

## Install

Use Python 3.11 or newer. This preview installs from a GitHub release wheel;
the `liteagents` name on PyPI currently belongs to a different package.

```sh
python -m pip install "liteagents[deepagents] @ https://github.com/BerriAI/liteagents/releases/download/v0.3.0a3/liteagents-0.3.0a3-py3-none-any.whl"
```

`[deepagents]` installs that harness integration along with the SDK. Existing
compatible dependencies are reused. Install only the harnesses you want to try.

## Run an agent

Set your provider key:

```sh
export OPENAI_API_KEY="your-openai-key"
```

Then copy and run this Python example:

```python
import asyncio
from liteagents import (
    AssistantMessage, LiteAgentOptions, ProfileOptions, TextBlock, query,
)

profile = ProfileOptions(
    harness="deepagents",
    model="openai/gpt-5.4-mini",
    tools=[],
)

async def main():
    async for message in query(
        prompt="Explain what an agent harness does in one sentence.",
        options=LiteAgentOptions(profile=profile),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)

asyncio.run(main())
```

In Colab, use `await main()` instead of `asyncio.run(main())`.

The harness runs the agent loop. LiteAgents translates its model calls through
the **LiteLLM Python SDK** and returns a consistent stream of messages.
The public interface is modeled after the Claude Agent SDK: text is in
`AssistantMessage.content` as `TextBlock.text`, even when the model is OpenAI.

### Use Anthropic or OpenRouter

Keep the same code and change `model` and the provider key:

| Provider | `model` | Environment variable |
| --- | --- | --- |
| OpenAI | `openai/gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet-4.6` | `OPENROUTER_API_KEY` |

Requests go directly to the selected provider through LiteLLM. You do not need
a Claude Agent SDK installation to use an Anthropic model with DeepAgents.

For Gemini, Groq, Mistral, DeepSeek, Together AI, xAI, Azure, Bedrock, Vertex AI,
and local Ollama, see [Model setup](models.md).

## Switch the harness

Install the integrations you want into the same environment:

```sh
python -m pip install "liteagents[deepagents,pydantic-ai] @ https://github.com/BerriAI/liteagents/releases/download/v0.3.0a3/liteagents-0.3.0a3-py3-none-any.whl"
```

Change one field and run the same application:

```python
profile.harness = "pydantic-ai"
```

The model, shared tools, MCP configuration, and response-handling code stay the
same. This starts a run with the chosen harness; it does not transfer an existing
native session to another harness.

| Harness selector | Install extra |
| --- | --- |
| `deepagents` | `deepagents` |
| `pydantic-ai` | `pydantic-ai` |
| `claude-sdk` | `claude-sdk` |
| `codex` | `codex` |
| `opencode-v1` / `opencode-v2` | Either selector; also `npm install -g opencode-ai@1.18.29` |

The Claude and Codex extras include their runtimes. `[all]` installs all Python
integrations; OpenCode still needs its executable. Colab's setup cell installs
your selected integrations for you. Each fresh Colab runtime needs setup again.

Try [switching harnesses with tools and MCP](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/10_harness_switch.ipynb)
or [comparing fixes on a coding task](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/compare_harnesses/compare.ipynb).

## Continue a conversation

Keep one client open for follow-up turns. If you only need the final text,
`start_run()` returns an independent job with `result.text`.

```python
from liteagents import LiteAgentClient

async def conversation():
    async with LiteAgentClient(options=LiteAgentOptions(profile=profile)) as agent:
        async for message in agent.query("Remember the code COBALT-42."):
            print(message)
        async for message in agent.query("What code did I give you?"):
            print(message)

asyncio.run(conversation())
```

See the [streaming and conversation notebook](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/01_quickstart.ipynb)
for typed streaming events and inspecting results.

## Add tools, MCP, and native controls

Application tools implement `Tool.execute()` and are registered through
`LiteAgentOptions(tools=[...])`. For example:

```python
from liteagents import Tool

class LookupOrder(Tool):
    name = "lookup_order"
    description = "Look up an order's payment status."
    input_schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
    }

    async def execute(self, input):
        return f"Order {input['order_id']} is paid."

profile.tools = ["lookup_order"]
options = LiteAgentOptions(profile=profile, tools=[LookupOrder()])
# Use options in the same query() call shown above.
```

Run the [application tools notebook](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/08_application_tools.ipynb)
to inspect tool calls. The [MCP notebook](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/02_mcp.ipynb)
includes a working MCP server and shows `profile.mcp_servers`.

Omitting `profile.tools` exposes registered application and MCP tools.
`tools=[]` disables tools; a list selects named tools. Native harness settings
go in `harness_options`, for example `{"debug": True}` for DeepAgents.
These native options stay specific to their harness. See the
[profile guide](profiles.md), including [JSON/YAML examples](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/09_profile_files.ipynb).

## Optional: use a LiteLLM gateway

Replace the model configuration with your gateway's exact alias, URL, and key.
Your application does not need the underlying provider keys. These settings can
be passed directly; environment variables are optional:

```python
from getpass import getpass

profile = ProfileOptions(
    harness="deepagents",
    model="my-model",
    model_kwargs={
        "api_base": "https://your-gateway.example/v1",
        "api_key": getpass("Gateway API key: "),
    },
    tools=[],
)
```

The alias is sent unchanged, including any slashes. No routing prefix is needed.
Pass this profile to the same `query()` example above. To start a new gateway,
follow the [LiteLLM Gateway quickstart](https://docs.litellm.ai/docs/proxy/docker_quick_start).
In Colab, the gateway must be reachable from its cloud runtime; your laptop's
`localhost` endpoint is not reachable there by default.

## Optional: add durability with Temporal

A durable run adds `TemporalOptions` to the profile and runs on a
`LiteAgentWorker` connected to Temporal. Your application still uses the same
client. The [durable notebook](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/06_durable.ipynb)
starts a demo service and worker, then lets you kill the worker and reconnect to
the original run.

Completed recorded operations are reused after worker loss. Interrupted external
actions can run again and need application idempotency. Keep the workspace and
checkpoint stores across worker restarts. Colab runtime resets erase its local
demo state; use the [self-hosting guide](self-hosting.md) for persistent deployment.

Explore [all Colab cookbooks](../cookbook/README.md), including approvals,
subagents, retries, and model fallback. See [validation](validation.md) for test
coverage and [migration](migration.md) when updating an existing installation.
