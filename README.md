# LiteLLM Agent SDK
# LiteAgents SDK for Python

This is a minimal provider-independent agent interface. Its core loop follows the Claude Agent SDK pattern: configure options, call `query()`, and consume an async message stream
Python SDK for building provider-independent agents with LiteLLM. LiteAgents follows the familiar [`query()` interface from the Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/python), while letting every turn run on the model best suited to the task

**The first interoperable agent SDK with JEV-native routing**

Before every turn, [JEV](https://docs.typesafe.ai/models) can classify the request and select the cheapest model tier predicted to handle it well. Your agent can mix OpenAI, Anthropic, Gemini, Bedrock, Azure, hosted models, and LiteLLM proxy aliases inside one conversation

## Installation

```shell
pip install liteagents
```

**Prerequisites**

- Python 3.10+
- Credentials for at least one [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers)
- `TYPESAFE_API_KEY` when using automatic JEV routing

LiteAgents uses the LiteLLM Python SDK directly. It does not require a provider-specific CLI or lock the agent loop to one model family

## Quick start

```python
from litellm.agent_sdk import AgentOptions, AssistantMessage, TextBlock, query
import anyio

options = AgentOptions(
    model="openai/gpt-5.4-mini",
    system_prompt="Review code changes concisely",
)
from liteagents import LiteAgentOptions, query

async for message in query(prompt="Review this diff", options=options):

async def main():
    options = LiteAgentOptions(model="openai/gpt-5.4-mini")
    async for message in query(prompt="What is 2 + 2?", options=options):
        print(message)


anyio.run(main)
```

## Basic usage: `query()`

`query()` is an async function that returns an `AsyncIterator` of response messages. The interface is intentionally familiar to Claude Agent SDK users, but the model is a standard LiteLLM model string. LiteAgents does not hard-code a default provider, so the first query names a model or a router

```python
from liteagents import AssistantMessage, LiteAgentOptions, TextBlock, query


options = LiteAgentOptions(model="openai/gpt-5.4-mini")

async for message in query(prompt="Hello", options=options):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, TextBlock):
                print(block.text)


options = LiteAgentOptions(
    model="anthropic/claude-sonnet-4-6",
    system_prompt="You are a helpful assistant",
    max_turns=1,
)

async for message in query(prompt="Tell me a joke", options=options):
    print(message)
```

Model names use LiteLLM's `provider/model` format, so the same code works with OpenAI, Anthropic, Gemini, Bedrock, Azure, hosted models, and LiteLLM proxy model aliases. Set `model_router` on `AgentOptions` to choose a model before every turn
## Mix models in one agent

## PR risk agent
A model router runs before every turn. It receives the current prompt, conversation history, and turn number, then returns any LiteLLM model string

`PRRiskAgent` classifies a pull request as low, medium, or high risk. It routes routine changes to a fast model and large or security-sensitive changes to a stronger model
```python
from liteagents import LiteAgentClient, LiteAgentOptions, TurnContext


class CodeRouter:
    async def route(self, context: TurnContext) -> str:
        if "architecture" in context.prompt.lower():
            return "anthropic/claude-opus-4-8"
        return "openai/gpt-5.4-mini"


options = LiteAgentOptions(
    model="openai/gpt-5.4-mini",
    model_router=CodeRouter(),
)

async with LiteAgentClient(options=options) as agent:
    async for message in agent.query("Rename this function"):
        print(message)

    async for message in agent.query("Now review the architecture"):
        print(message)
```

Conversation history stays intact when the selected provider changes. `AssistantMessage.model` records which model handled each turn

## Automatic routing with JEV

Manual rules work when the split is obvious. JEV handles the broader case: it evaluates the actual request against your model tiers and chooses the cheapest tier it predicts can answer correctly

```python
from litellm.agent_sdk import PRRiskAgent, PullRequest
from liteagents import JevModelRouter, JevTier, LiteAgentOptions, query

agent = PRRiskAgent(
    routine_model="openai/gpt-5.4-mini",
    complex_model="anthropic/claude-opus-4-8",

router = JevModelRouter(
    tiers=(
        JevTier(
            name="FAST",
            model="openai/gpt-5.4-mini",
            description="Short factual answers, extraction, and routine edits",
        ),
        JevTier(
            name="BALANCED",
            model="anthropic/claude-sonnet-4-6",
            description="Everyday coding, explanations, and multi-step work",
        ),
        JevTier(
            name="REASONING",
            model="anthropic/claude-opus-4-8",
            description="Architecture, difficult debugging, and careful tradeoffs",
        ),
    ),
    fallback_model="anthropic/claude-opus-4-8",
)

options = LiteAgentOptions(model_router=router)

async for message in query(prompt="Review this pull request", options=options):
    print(message)
```

Set JEV credentials once in the environment

```shell
export TYPESAFE_API_KEY="..."
```

JEV routing is provider-independent. A tier can point to a direct provider model, a LiteLLM proxy model alias, or a model group managed by LiteLLM Router

## Why this can be cheaper and faster

Most agent turns do not need the most capable model. A fixed-model agent pays the strongest model's price and latency for every greeting, lookup, extraction, and small edit

JEV changes the cost shape

```text
total turn cost = JEV classification cost + selected model cost
```

The routing decision pays for itself when the savings from avoiding an unnecessarily expensive model exceed the classification cost. Routine turns can also finish faster when JEV selects a lower-latency model

This is workload-dependent. JEV adds a routing hop, so LiteAgents does not claim that every individual turn is cheaper or faster. Measure end-to-end cost, latency, and answer quality on your traffic, then tune the tier descriptions and fallback model

## PR risk agent

The included PR reviewer classifies deployment risk as `low`, `medium`, or `high`. JEV can use a fast model for routine changes and reserve a stronger model for security, migrations, broad behavior changes, and difficult-to-reverse work

```python
from liteagents import PRRiskAgent, PullRequest


agent = PRRiskAgent(model_router=router)

assessment = await agent.classify(
    PullRequest(
        title="Add API key rotation",
        body="Rotates keys without downtime",
        diff=diff,
        changed_files=4,
        additions=120,
        deletions=35,
    )
)

print(assessment.risk)
print(assessment.reasons)
print(assessment.recommended_checks)
```

The included command accepts a PR diff on standard input, which makes it usable from a GitHub Actions job or a local checkout
## Migrating from the Claude Agent SDK

```shell
gh pr diff 123 | python -m cookbook.agent_sdk.pr_risk_agent \
  --title "Add API key rotation" \
  --changed-files 4 \
  --additions 120 \
  --deletions 35
```
The core concepts map directly

| Claude Agent SDK | LiteAgents SDK |
| --- | --- |
| `query()` | `query()` |
| `ClaudeAgentOptions` | `LiteAgentOptions` |
| `ClaudeSDKClient` | `LiteAgentClient` |
| `AssistantMessage` | `AssistantMessage` |
| `TextBlock` | `TextBlock` |
| One model family | Any LiteLLM model or JEV router |

The main migration change is the import and model configuration. Your async iteration and message handling stay the same
