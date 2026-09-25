# liteagents

A provider-independent agent SDK with the same query() interface as the Claude Agent SDK, **allowing you to use the right model for every turn**. Auto-routing automatically selects the best-fit model for each step across providers, balancing quality, speed, and cost. Use OpenAI, Anthropic, Deepseek, Gemini, xAI all within one agent run.

<img width="1540" height="1080" alt="area3" src="https://github.com/user-attachments/assets/5fd27ec5-1de0-4d69-af4c-87f819dcacea" />


## Features

- cross-provider: mix OpenAI, Anthropic, Gemini, Bedrock, Azure in one agent run
- JEV-native routing: auto-pick the cheapest model tier per turn
- fusion mode: a frontier model plus a cheap sidekick, running in parallel
- write your own router instead, no classifier required
- same `query()` / `AssistantMessage` / `TextBlock` shapes as the Claude Agent SDK
- MCP client tools from initialized stdio or remote sessions (`pip install 'liteagents[mcp]'`)
- opt-in text streaming, per-client gateway options, and typed initial history

## Installation

```shell
pip install liteagents
```

requires Python 3.11+ and credentials for at least one [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers).

## One agent, any provider, per turn

```python
from liteagents import LiteAgentClient, LiteAgentOptions, TurnContext


class CodeRouter:
    async def route(self, context: TurnContext) -> str:
        if "architecture" in context.prompt.lower():
            return "anthropic/claude-opus-4-8"
        return "openai/gpt-5.4-mini"


options = LiteAgentOptions(model="openai/gpt-5.4-mini", model_router=CodeRouter())

async with LiteAgentClient(options=options) as agent:
    async for message in agent.query("Rename this function"):
        print(message)  # openai/gpt-5.4-mini

    async for message in agent.query("Now review the architecture"):
        print(message)  # anthropic/claude-opus-4-8
```

history stays intact across the switch. `AssistantMessage.model` records which model handled each turn.

## JevAgent picks the best model for every turn

```python
from liteagents import JevAgent, JevTier

async with JevAgent(
    tiers=(
        JevTier(name="FAST", model="openai/gpt-5.4-mini", description="Routine edits and extraction"),
        JevTier(name="BALANCED", model="anthropic/claude-sonnet-4-6", description="Everyday coding"),
        JevTier(name="REASONING", model="anthropic/claude-opus-4-8", description="Architecture, hard debugging"),
    ),
    fallback_model="anthropic/claude-opus-4-8",
) as agent:
    async for message in agent.query("Review this pull request"):
        print(message)
```

```shell
export TYPESAFE_API_KEY="..."
```

`JevAgent` is a `LiteAgentClient` pre-wired with [JEV](https://docs.typesafe.ai/models) routing: it classifies each turn against your tiers before running it. A tier can be a direct provider model, a LiteLLM proxy alias, or a Router model group.

Need JEV routing alongside other `LiteAgentOptions` (fusion, a custom `tool_choice`, etc.)? Use `JevModelRouter` directly as a `model_router`:

```python
from liteagents import JevModelRouter, JevTier, LiteAgentOptions, query

router = JevModelRouter(
    tiers=(JevTier(name="FAST", model="openai/gpt-5.4-mini", description="Routine edits and extraction"),),
    fallback_model="anthropic/claude-opus-4-8",
)
options = LiteAgentOptions(model_router=router)

async for message in query(prompt="Review this pull request", options=options):
    print(message)
```

## Fusion: a frontier main agent with a cheap sidekick

```python
from liteagents import FusionOptions, LiteAgentClient, LiteAgentOptions

options = LiteAgentOptions(
    model="anthropic/claude-opus-4-8",
    fusion=FusionOptions(sidekick_model="openai/gpt-5.4-mini"),
)

async with LiteAgentClient(options=options) as agent:
    async for message in agent.query("Modernize search.js to ES6 and verify with the full test suite"):
        print(message)  # diff from the main model, test run delegated to the sidekick
```

the main model plans, resolves ambiguity, and reviews. the sidekick executes what gets delegated. both keep their own cached context, so delegating a subtask doesn't cost a cache miss the way calling another model as a tool does.

## Basic usage: `query()`

```python
from liteagents import AssistantMessage, LiteAgentOptions, TextBlock, query

options = LiteAgentOptions(model="openai/gpt-5.4-mini")

async for message in query(prompt="Hello", options=options):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)
```

## Migrating from the Claude Agent SDK

| Claude Agent SDK | LiteAgents SDK |
| --- | --- |
| `query()` | `query()` |
| `ClaudeAgentOptions` | `LiteAgentOptions` |
| `ClaudeSDKClient` | `LiteAgentClient` |
| one model family | any LiteLLM model, JEV router, or fusion |

mostly an import and model-config change.

## MCP tools

Install `liteagents[mcp]`, create and initialize an MCP `ClientSession`, then
adapt its tools. The application owns the transport, credentials and session
lifetime. The same adapter works with stdio, Streamable HTTP and SSE sessions.

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from liteagents import LiteAgentOptions, query
from liteagents.mcp import load_mcp_tools

params = StdioServerParameters(command="python", args=["memory_server.py"])
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await load_mcp_tools(
            session,
            allowed_tool_names=["search_memories", "read_memory"],
            prefix="memory_",  # optional; disambiguates tools from multiple servers
        )
        options = LiteAgentOptions(model="openai/gpt-5.4-mini", tools=tools)
        async for message in query(prompt="What did I work on?", options=options):
            print(message)
```

`allowed_tool_names` matches remote names before adding a prefix. `None` exposes
all discovered tools; `[]` exposes none. Discovery follows pagination and rejects
duplicate names. Tool schemas, text, images, structured results and resource data
are preserved; MCP tool failures become error results the model can handle.
Keep the session open for the full query and rediscover tools explicitly if the
server's catalog changes. Cancellation propagates to MCP calls. This is a client
adapter; it does not host MCP servers or manage authentication/approvals.

## Gateways, streaming and existing chat history

`model_kwargs` forwards connection and provider settings to LiteLLM on every
round, including fusion sidekick calls. Core fields such as `model`, `tools` and
`stream` belong on `LiteAgentOptions` and cannot be overridden through this dict.
Use `litellm_proxy/<alias>` for opaque LiteLLM gateway model names.

```python
import os
from contextlib import aclosing
from liteagents import (
    AssistantMessage, LiteAgentClient, LiteAgentOptions, TextBlock, TextDelta, UserMessage,
)

options = LiteAgentOptions(
    model="litellm_proxy/my-agent-model",
    model_kwargs={
        "api_base": "https://my-gateway.example/v1",
        "api_key": os.environ["LITELLM_API_KEY"],
        "timeout": 90,
        "num_retries": 0,
        "extra_headers": {"x-litellm-enable-message-redaction": "true"},
        "extra_body": {"no-log": True},
        "no-log": True,
    },
    stream=True,
)
history = [UserMessage("Earlier question"), AssistantMessage([TextBlock("Earlier answer")], model="")]
async with LiteAgentClient(options=options, history=history) as agent:
    async with aclosing(agent.query("A follow-up question")) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, AssistantMessage):
                print(event.stop_reason, event.usage)
```

Streaming defaults to off. When enabled, `TextDelta` events provide incremental
display text; complete `AssistantMessage` and tool-result messages still follow.
Do not append both deltas and completed text to the same answer. Only completed
messages enter history. Usage is per model response. A truncated or failed stream
raises instead of yielding a completed response. Use `aclosing` when consuming a
query that may stop early, so the provider stream closes promptly.

Initial history is copied and remains in memory for this client only. When
`max_turns` is reached during tool use, no final answer is produced: the last
assistant message has `stop_reason="tool_use"`. Applications should detect this
instead of delivering tool-planning text as a finished answer.
