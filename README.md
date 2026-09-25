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
- opt-in context compaction with pluggable triggers and strategies

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

## Context compaction

Compaction is disabled by default. Enable it to summarize older context while
keeping recent messages verbatim:

```python
from liteagents import (
    CompactionCompleted, CompactionOptions, LiteAgentClient, LiteAgentOptions,
    RecentTokens, Summarize, TokenThreshold,
)

options = LiteAgentOptions(
    model="openai/gpt-5.4-mini",
    compaction=CompactionOptions(
        trigger=TokenThreshold(fraction=0.8),
        strategy=Summarize(
            # Omit model to summarize with the model selected for this round.
            model="openai/gpt-5.4-mini",
            keep=RecentTokens(12_000),
            max_tokens=2_000,
            instructions="Preserve decisions, exact identifiers, and unfinished work.",
        ),
    ),
)

async with LiteAgentClient(options=options) as agent:
    async for event in agent.query("Continue the migration"):
        if isinstance(event, CompactionCompleted):
            print(event.before.tokens, event.after.tokens, event.usage)

    result = await agent.compact(instructions="Focus on remaining migration work.")
```

`CompactionOptions()` defaults to an 80% trigger and `Summarize()`. Use
`TokenThreshold(tokens=100_000)` for an absolute threshold. Set `trigger=None`
for manual-only compaction; `agent.compact()` requires configured compaction and
an idle client. A client rejects overlapping queries and compactions. With a
router, manual compaction uses the last selected model's budget; before the first
query, supply `agent.compact(model="provider/model")`. It never calls the router
or increments the turn counter. The strategy's `model` selects the summarizer.

Automatic checks run **after routing, before every model request**, including
requests within one tool loop. A known input-budget overflow triggers reduction
even if an automatic threshold has not been reached. Manual-only configuration
instead raises `ContextBudgetExceeded`. Compaction calls do not consume
`max_turns` and do not execute tools. Each `Summarize` invocation makes at most
one summary call; provider errors propagate without internal tool replay or
compaction retry.

The three budgets are independent: `trigger` determines when to compact, `keep`
targets recent history to retain, and `max_tokens` caps summary output. Cuts
retain complete tool-call/result groups, so the tail can exceed `keep`. The latest
human request also survives verbatim, including when older tool rounds within
that same request are summarized. Previous summaries feed into the next summary.
System instructions and tool definitions remain outside the replaced history.

Context size uses **reported input usage plus estimated new content**. After a
completed model response, LiteAgents retains that request's `input_tokens` plus
`cache_read_input_tokens` and `cache_creation_input_tokens`. It estimates the
replayed assistant response and subsequent messages separately; billed output
can include reasoning that will not be sent again. Usage comes from the current
client's actual requests, never from unverified usage attached to imported history.

The default estimator is `litellm.token_counter`, receiving structured messages,
system instructions, and tool schemas. Fresh requests, summary requests, individual
messages, and compaction previews use this estimator. Successful compaction or
changes to the model, system, tools, request settings, or measured history invalidate
the usage baseline. Each client and sidekick owns its own baseline.

These are **context estimates**, not exact billing totals. LiteLLM can use a fallback
tokenizer for unknown models, and image counts use default dimensions without
fetching image URLs. Unsupported content/counting errors propagate. To explicitly
use the old byte heuristic, set `CompactionOptions(token_counter=heuristic_tokens)`
(import `heuristic_tokens` from `liteagents`). It is unsuitable for accurate
multimodal counting; there is no automatic byte fallback.

Custom counters now implement
`token_counter(model, request: TokenCountRequest) -> TokenEstimate`. The detached
request exposes `messages` and `tools` as tuples of Anthropic-format dictionaries,
plus `system`; connection credentials are excluded. This replaces the previous
serialized-text callback. Counters must be deterministic. Events expose
`before: TokenEstimate` and completed events also expose `after: TokenEstimate`;
each estimate carries its own `tokens` and `source` because edited context is
counted afresh. Request counts are cached within one compaction attempt, and
per-message counts are computed only when a strategy needs them. Reduction checks
compare both histories using the same local estimator, so changing counting methods cannot make a no-op
look like a reduction.

The usable input budget is the model's LiteLLM input limit minus
`LiteAgentOptions.max_tokens` and `safety_margin` (default 1,024). LiteLLM >=1.102.0
is required for the structured Anthropic-content counter used here.

For gateway aliases or custom limits, set
`context_windows={"litellm_proxy/agent": 128_000, "litellm_proxy/summary": 128_000}`
on `CompactionOptions`. Fractional triggers require a known limit. The summarizer
also requires a known limit and checks its own input plus output reservation
before making a request. Absolute triggers can work without a known target
window, but cannot then enforce that target's maximum. Summary requests inherit
the client's connection/provider `model_kwargs`, excluding main-turn reasoning
budgets, structured-output constraints, stop sequences, and native context-management
settings. `Summarize.model_kwargs` can explicitly override these settings or use a
separate summarizer connection.

For verbose tool output, use a deterministic strategy that needs no model call:

```python
from liteagents import PruneToolResults

compaction = CompactionOptions(
    trigger=TokenThreshold(tokens=60_000),
    strategy=PruneToolResults(keep=3),  # retain the last three tool results
)
```

Pruning replaces older result contents with a marker, preserving calls, IDs,
error flags, and other provider metadata. Already-pruned or shorter results are
left alone. It does not archive the removed contents or make them retrievable.

Compose triggers and strategies with small factories:

```python
from liteagents import TurnThreshold, all_of, any_of, cascade

compaction = CompactionOptions(
    trigger=any_of([
        TokenThreshold(fraction=0.8),
        TurnThreshold(turns=20),
    ]),
    strategy=cascade([
        PruneToolResults(keep=3),
        Summarize(keep=RecentTokens(8_000)),
    ]),
    target_tokens=32_000,
)
```

`any_of` and `all_of` return ordinary `CompactionTrigger` implementations. They
evaluate children in order and short-circuit; each child receives a detached
context. `TurnThreshold` counts retained human requests, including the current
request, excluding generated summaries and tool-result-only messages. It does
not count elapsed turns since the last compaction. Empty compositions are errors.

`cascade` returns an ordinary `CompactionStrategy`: it implements the same
`async compact(context) -> CompactionResult | None` protocol, including for custom
children and nested cascades. Each stage previews validated edits against a
detached candidate, recounts the complete request, and passes successful
reductions to the next stage. It stops once `target_tokens` is met. No-op or
non-shrinking proposals are skipped; errors propagate without running later
stages. History is committed only once, after the entire result is validated.

`target_tokens` is a positive, optional reduction goal, required by `cascade`.
It is independent of the activation trigger, includes system/tool overhead, and
is capped at the selected model's usable input budget. It is a **soft target**:
if all stages finish above it, a smaller result can still be committed provided
it fits the hard model budget. A cascade already at its target does no work,
even when called manually. Individual strategies can inspect the target but
need not achieve it. The SDK always enforces the hard model budget.

Cascade configuration copies the child sequence. Per-conversation state is
stored separately for each child's position, including nested cascades; it
commits with history and rolls back on failure. Treat stage order as fixed for
a client session. Compaction usage is a `TokenUsage` record with optional typed
counts, provider-specific `details`, and a `stages` tuple retaining each child's
usage, including non-shrinking proposals. Only reported counts are summed.
Nested cascade usage retains its nested records. More than one stage can incur
model costs; a later failure still reports earlier stages' returned usage.

The event stream can include `CompactionStarted`, `CompactionCompleted`,
`CompactionSkipped`, and `CompactionFailed`. Summary text is context, not an
ordinary assistant answer or `TextDelta`. A skipped attempt may still have paid
summary usage. Failure events precede `CompactionError`; its `usage` carries
reported summary usage when available. Cancellation propagates without committing
an unfinished compaction. Failed, invalid, stale, or non-shrinking edits never
replace history or strategy state. If reduction cannot fit a known input budget,
the SDK raises `ContextBudgetExceeded` before calling the main model.

`agent.history` is a detached copy of **active model context**, including
`SummaryMessage`s. Yielded messages and router histories are detached as well;
mutating them does not modify the client's context. Applications own transcript
storage. To maintain a context mirror when using stateless `query()`, append the
incoming `UserMessage` first, apply `apply_compaction(messages, event.update)` on
`CompactionCompleted`, and append completed `UserMessage`/`AssistantMessage`
events. Do not append text deltas or lifecycle events. This typed mirror has the
same existing limitation as `agent.history`: unmodeled provider blocks are not
exported. Inside the live client, retained raw provider blocks are preserved.

Custom strategies implement `async compact(context) -> CompactionResult | None`:

```python
from liteagents import (
    CompactionContext, CompactionResult, HistoryEdit, RecentTokens, ReplacePrefix,
)

class TaskSummary:
    async def compact(self, context: CompactionContext) -> CompactionResult | None:
        stop = RecentTokens(8_000).boundary(context)
        if stop == 0:
            return None
        summary = await summarize_for_my_domain(context.messages[:stop])
        return CompactionResult(
            update=HistoryEdit(
                message_count=len(context.messages),
                prefix=ReplacePrefix(stop=stop, summary=summary),
            ),
            state=context.state,
        )
```

`summarize_for_my_domain` above is application-owned. `CompactionContext` provides
a detached typed snapshot, valid cut boundaries, per-message estimates, selected
model, input budget, reason, instructions, and optional state. It is a protocol
implemented by the runtime, rather than a configuration dataclass callers construct.
Triggers receive a smaller `TriggerContext` with history, model, tokens, input
budget, and reason. It exposes no credentials, strategy state, or execution services.
A custom trigger implements `should_compact(context: TriggerContext) -> bool`.

`CompactionUpdate` is the union `HistoryEdit | BatchUpdate`. `HistoryEdit` contains
a prefix replacement and/or `ReplaceToolResult` edits against one snapshot.
`BatchUpdate(message_count=..., steps=(...))` contains only sequential updates;
each step's indices and `message_count` refer to the preceding candidate.
`apply_compaction` replays either variant. Strategies use `context.preview(update)`
to validate and recount a detached candidate, including retained raw provider blocks.
`context.local_tokens` exposes the comparable local estimate; `context.tokens`
may instead use a provider-usage baseline. `message_tokens` is computed lazily.

`context.count_tokens(request, model=...)` counts structured content.
`context.generate_summary(text, system=..., model=..., max_tokens=..., model_kwargs=...)`
checks the summary budget, inherits connection settings, and returns summary text
plus `TokenUsage`. These services keep provider configuration out of strategy data.
`context.with_state(state)` creates an isolated child context for composition.

The SDK validates and atomically applies the final result, retaining raw entries
outside edits. Keep bookkeeping in the result's `state`, not on shared strategy
instances; state commits only with a successful update. Custom strategies making
their own model calls should return `TokenUsage` with the result (or attach it to
`CompactionError`). Use `TokenUsage.from_dict(provider_usage)` to normalize provider
data and `usage.to_dict()` when a dictionary is needed. Ordinary
`AssistantMessage.usage` retains its dictionary API.

Numeric configuration rejects booleans, fractional counts, and nonfinite values.
Configuration mappings own their values; changing caller dictionaries or values
read from a mapping cannot alter an existing configuration.

Fusion has independent opt-in `FusionOptions.sidekick_compaction` configuration
and runtime state. Main-agent policy is not implicitly applied to the sidekick;
sidekick events remain inside the delegated loop, like its other messages.

This implementation provides portable client-side reduction. Provider-native
compaction blocks, background scheduling, and durable observational memory are
not included.
