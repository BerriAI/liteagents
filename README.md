# LiteAgents SDK for Python

a provider-independent agent SDK built on LiteLLM. same `query()` interface as the Claude Agent SDK, but any turn can run on any provider.

## Features

- cross-provider by design: mix OpenAI, Anthropic, Gemini, Bedrock, Azure, and LiteLLM proxy aliases in one conversation, one client
- JEV-native routing: classify each turn and route it to the cheapest model tier predicted to handle it well
- deterministic routing is a first-class option: write your own router, no classifier required
- fusion mode (opt-in): run a frontier model and a cheaper sidekick in parallel, each with its own cached context, and let the frontier model delegate
- same `query()` / `AssistantMessage` / `TextBlock` shapes as the Claude Agent SDK, so migration is mostly an import change

## Installation

```shell
pip install liteagents
```

requires Python 3.10+, credentials for at least one [LiteLLM-supported provider](https://docs.litellm.ai/docs/providers), and `TYPESAFE_API_KEY` if you use JEV routing.

## One agent, any provider, per turn

route different turns to different providers mid-conversation. history stays intact across the switch.

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
        print(message)  # handled by openai/gpt-5.4-mini

    async for message in agent.query("Now review the architecture"):
        print(message)  # handled by anthropic/claude-opus-4-8
```

`AssistantMessage.model` records which model handled each turn. model names use LiteLLM's `provider/model` format, so the same code works with OpenAI, Anthropic, Gemini, Bedrock, Azure, hosted models, and LiteLLM proxy model aliases.

routing is code you write and can read. `CodeRouter` above is the whole implementation, no external classifier call, no black box.

## JEV picks the best model for every turn

manual rules work when the split is obvious. [JEV](https://docs.typesafe.ai/models) handles the broader case: before every turn it classifies the request against your tiers and picks the cheapest one predicted to answer it correctly.

```python
from liteagents import JevModelRouter, JevTier, LiteAgentOptions, query

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

```shell
export TYPESAFE_API_KEY="..."
```

a tier can point to a direct provider model, a LiteLLM proxy model alias, or a model group managed by LiteLLM Router. JEV routing is provider-independent.

**cost math, not vibes.**

```text
total turn cost = JEV classification cost + selected model cost
```

routing pays for itself when the savings from skipping an unnecessarily expensive model exceed the classification cost. this is workload-dependent: measure cost, latency, and answer quality on your own traffic before trusting it in production.

**when JEV picks wrong.** a misclassified turn falls through to `fallback_model`, and every routed call still goes through the same LiteLLM request path, so you can log, cache, and rate-limit it exactly like a direct call. tier descriptions and thresholds are plain arguments you set and can change, not a hidden hosted policy.

## Fusion: a frontier main agent with a cheap sidekick

a single frontier model on every turn pays that model's price and latency for greetings, lookups, extraction, and small edits, not just the hard turns. `model_router` and JEV both solve this by picking one model per turn. fusion is a different, opt-in mode: run a frontier model and a cheaper sidekick model side by side, each with its own persistent, cached context, and let the frontier model decide what to hand off.

```python
from liteagents import FusionOptions, LiteAgentClient, LiteAgentOptions

options = LiteAgentOptions(
    model="anthropic/claude-opus-4-8",
    fusion=FusionOptions(
        sidekick_model="openai/gpt-5.4-mini",
        # the main agent decides what to delegate; it plans, resolves
        # ambiguity, and does final review, the sidekick executes.
    ),
)

async with LiteAgentClient(options=options) as agent:
    async for message in agent.query("Modernize search.js to ES6 and verify with the full test suite"):
        print(message)  # diff from the main model, test run delegated to the sidekick
```

fusion is not the same as calling a second model as a tool. a "smart friend" or "advisor" tool call re-sends the task context on every call, so you pay full, uncached input cost each time. in fusion, both models keep their own context warm across the session, so delegating a subtask does not cost a cache miss.

**why this beats picking one model per turn.** the main model stays fully capable, it is not "benchmark-score" intelligence swapped out for a cheaper model on hard-looking turns. it also generalizes past single-prompt classification: a task can start simple and grow a hard follow-up mid-session, and the main model can pull work back from the sidekick or hand off more as it learns the task, without committing to a model up front.

**when to delegate and when not to.** mechanical, well-scoped work (a rewrite with a slow test suite, a deprecation removal, boilerplate across many files) delegates cleanly: same or better quality, meaningfully lower cost. work where the judgment call is the deliverable (an ambiguous product decision, a subtle intent behind a feature request) does not: handing that to the sidekick can silently lose the point of the task even while looking done. fusion helps most when your main model is disciplined about which bucket a subtask falls into.

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

LiteAgents does not hard-code a default provider. the first query names a model or a router.

## PR risk agent

classifies a pull request as `low`, `medium`, or `high` risk. JEV can send routine changes to a fast model and reserve a stronger model for security, migrations, and hard-to-reverse work.

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

takes a PR diff on stdin, so it drops straight into a GitHub Actions job or a local checkout:

```shell
gh pr diff 123 | python -m cookbook.agent_sdk.pr_risk_agent \
  --title "Add API key rotation" \
  --changed-files 4 \
  --additions 120 \
  --deletions 35
```

## Migrating from the Claude Agent SDK

| Claude Agent SDK | LiteAgents SDK |
| --- | --- |
| `query()` | `query()` |
| `ClaudeAgentOptions` | `LiteAgentOptions` |
| `ClaudeSDKClient` | `LiteAgentClient` |
| `AssistantMessage` | `AssistantMessage` |
| `TextBlock` | `TextBlock` |
| one model family | any LiteLLM model or JEV router |

mostly an import and model-config change. async iteration and message handling stay the same.
