# LiteAgents SDK for Python

a provider-independent agent SDK built on LiteLLM. same `query()` interface as the Claude Agent SDK, but any turn can run on any provider.

## Features

- cross-provider: mix OpenAI, Anthropic, Gemini, Bedrock, Azure, and LiteLLM proxy aliases in one agent
- JEV-native routing: auto-pick the cheapest model tier per turn
- fusion mode: a frontier model plus a cheap sidekick, running in parallel
- write your own router instead, no classifier required
- same `query()` / `AssistantMessage` / `TextBlock` shapes as the Claude Agent SDK

## Installation

```shell
pip install liteagents
```

requires Python 3.10+ and credentials for at least one [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers).

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

## JEV picks the best model for every turn

```python
from liteagents import JevModelRouter, JevTier, LiteAgentOptions, query

router = JevModelRouter(
    tiers=(
        JevTier(name="FAST", model="openai/gpt-5.4-mini", description="Routine edits and extraction"),
        JevTier(name="BALANCED", model="anthropic/claude-sonnet-4-6", description="Everyday coding"),
        JevTier(name="REASONING", model="anthropic/claude-opus-4-8", description="Architecture, hard debugging"),
    ),
    fallback_model="anthropic/claude-opus-4-8",
)

options = LiteAgentOptions(model_router=router)

async for message in query(prompt="Review this pull request", options=options):
    print(message)
```

```shell
export TYPESAFE_API_KEY="..."
```

[JEV](https://docs.typesafe.ai/models) classifies each turn against your tiers before it runs. a tier can be a direct provider model, a LiteLLM proxy alias, or a Router model group.

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

## PR risk agent

```python
from liteagents import PRRiskAgent, PullRequest

agent = PRRiskAgent(model_router=router)
assessment = await agent.classify(PullRequest(title="Add API key rotation", diff=diff))

print(assessment.risk)  # low / medium / high
```

```shell
gh pr diff 123 | python -m cookbook.agent_sdk.pr_risk_agent --title "Add API key rotation"
```

## Migrating from the Claude Agent SDK

| Claude Agent SDK | LiteAgents SDK |
| --- | --- |
| `query()` | `query()` |
| `ClaudeAgentOptions` | `LiteAgentOptions` |
| `ClaudeSDKClient` | `LiteAgentClient` |
| one model family | any LiteLLM model, JEV router, or fusion |

mostly an import and model-config change.
