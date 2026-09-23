"""Exercise the public client against request-validating, bounded fake providers.

Only the provider transport is replaced. These tests exercise the real loop,
strategies, history edits, tools, and event replay. They verify context plumbing
and continued progress, not the semantic quality of a real model's summaries.
"""

from __future__ import annotations

import json
import re
from contextlib import nullcontext
from copy import deepcopy

import pytest

from liteagents import (
    CompactionCompleted,
    CompactionError,
    ContextBudgetExceeded,
    FusionOptions,
    LiteAgentClient,
    LiteAgentOptions,
    PruneToolResults,
    RecentTokens,
    Summarize,
    SummaryMessage,
    TextDelta,
    TokenThreshold,
    UserMessage,
)
from liteagents._internal.compaction_runtime import CompactionRuntime
from liteagents.history import ConversationHistory

from .compaction_helpers import old_history, policy, replay_events, run
from .conftest import text_response, tool_use_response
from .test_loop import EchoTool
from .test_streaming import Stream, response_events


class ProviderContextLimitError(RuntimeError):
    pass


def request_size(request):
    # The test provider bills one character per token, including instructions
    # and tool schemas. Its limit is enforced on the actual outgoing request.
    return len(json.dumps({key: request[key] for key in ("messages", "system", "tools")},
                          ensure_ascii=False))


def assert_complete_tool_groups(messages):
    """Independently validate wire messages, without the SDK's history helpers."""
    pending = set()
    for message in messages:
        content = message["content"]
        if pending:
            assert message["role"] == "user" and isinstance(content, list)
        for block in content if isinstance(content, list) else []:
            if block["type"] == "tool_use":
                assert message["role"] == "assistant"
                assert block["id"] not in pending
                pending.add(block["id"])
            elif block["type"] == "tool_result":
                assert message["role"] == "user"
                assert block["tool_use_id"] in pending
                pending.remove(block["tool_use_id"])
        if message["role"] == "user":
            assert not pending
    assert not pending


DEPLOYMENT_FACT = "deployment_id=deploy-7391"
CONVERSATION_INPUT_LIMIT = 3500
CONVERSATION_SYSTEM = "Keep deployment identifiers exact."


@pytest.fixture
def conversation_provider(monkeypatch):
    """A bounded provider with an extractive summarizer and request validation."""
    main_sizes, summary_inputs = [], []

    async def provider(**request):
        if request["model"] == "summary":
            transcript = request["messages"][0]["content"]
            # A deterministic extractive summarizer must find the original fact
            # in the supplied history, including after earlier history was removed.
            matches = re.findall(r"deployment_id=deploy-\d+", transcript)
            assert matches
            summary_inputs.append(transcript)
            return text_response(matches[0], model="summary")
        size = request_size(request)
        if size > CONVERSATION_INPUT_LIMIT:
            raise ProviderContextLimitError("request exceeds provider context limit")
        main_sizes.append(size)
        assert DEPLOYMENT_FACT in json.dumps(request["messages"])
        assert request["system"] == CONVERSATION_SYSTEM
        answer = DEPLOYMENT_FACT + " diagnostic detail" * 100
        if request.get("stream"):
            events = response_events(text=answer)
            events[0]["message"]["usage"]["input_tokens"] = size
            return Stream(events)
        response = text_response(answer, model="main")
        response["usage"]["input_tokens"] = size
        return response

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    return main_sizes, summary_inputs


@pytest.mark.parametrize("streaming", [
    pytest.param(False, id="buffered"),
    pytest.param(True, id="streamed"),
])
@pytest.mark.parametrize("compaction,error,expected_turns,min_compactions", [
    pytest.param(None, ProviderContextLimitError, 2, 0, id="disabled-hits-context-limit"),
    pytest.param(
        policy(trigger=TokenThreshold(tokens=2200),
               context_windows={"main": CONVERSATION_INPUT_LIMIT + 100, "summary": 100_000}),
        None, 12, 3, id="summary-finishes-conversation",
    ),
])
async def test_conversation_under_context_limit(
    conversation_provider, streaming, compaction, error, expected_turns, min_compactions,
):
    main_sizes, summary_inputs = conversation_provider
    prompts = [f"Remember {DEPLOYMENT_FACT}",
               *[f"Continue investigation, step {i}" for i in range(1, 12)]]
    options = LiteAgentOptions(model="main", system=CONVERSATION_SYSTEM, max_tokens=100,
                               stream=streaming, compaction=compaction)
    mirror, completed = [], []
    async with LiteAgentClient(options=options) as agent:
        with pytest.raises(error) if error else nullcontext():
            for prompt in prompts:
                mirror.append(UserMessage(prompt))
                events = [event async for event in agent.query(prompt)]
                mirror = replay_events(mirror, events)
                completed.extend(e for e in events if isinstance(e, CompactionCompleted))
                assert mirror == agent.history
                assert events[-1].content[0].text.startswith(DEPLOYMENT_FACT)
                deltas = "".join(e.text for e in events if isinstance(e, TextDelta))
                assert deltas == (events[-1].content[0].text if streaming else "")
        assert sum(isinstance(m, SummaryMessage) for m in agent.history) == int(compaction is not None)
    assert len(main_sizes) == expected_turns
    assert max(main_sizes) <= CONVERSATION_INPUT_LIMIT
    assert len(completed) == len(summary_inputs)
    assert len(completed) >= min_compactions
    if compaction is None:
        assert completed == []
    assert all(event.tokens_after < event.tokens_before for event in completed)
    assert all("Summary of earlier conversation" in text for text in summary_inputs[1:])


LOOKUP_STEPS = 8
LOOKUP_INPUT_LIMIT = 6000
LOOKUP_PROMPT = "Perform eight lookups"


@pytest.fixture
def lookup_provider(monkeypatch):
    """Advance only when the actual request contains the preceding tool result."""
    executed, sizes = [], []

    class LookupTool(EchoTool):
        async def execute(self, input):
            step = int(input["text"])
            executed.append(step)
            return f"step={step};" + "lookup payload " * 120

    async def provider(**request):
        if request["model"] == "summary":
            return text_response("Earlier lookup steps completed. Continue from the latest result.", model="summary")
        sizes.append(request_size(request))
        assert sizes[-1] <= LOOKUP_INPUT_LIMIT, "Oversized request reached the provider"
        messages = request["messages"]
        assert_complete_tool_groups(messages)
        assert any(message["content"] == LOOKUP_PROMPT for message in messages)
        latest = messages[-1]["content"]
        step = 0
        if isinstance(latest, list):
            result = latest[0]
            assert result["type"] == "tool_result"
            # Progress depends on the retained output, not the fake's call count.
            step = int(re.match(r"step=(\d+);", result["content"])[1])
            assert result["tool_use_id"] == f"lookup-{step}"
        if step == LOOKUP_STEPS:
            response = text_response("All eight lookups complete", model="main")
        else:
            response = tool_use_response(tool_use_id=f"lookup-{step + 1}", name="echo",
                                         input={"text": str(step + 1)}, model="main")
        response["usage"]["input_tokens"] = sizes[-1]
        return response

    monkeypatch.setattr("litellm.anthropic_messages", provider)
    return LookupTool(), executed, sizes


@pytest.mark.parametrize("strategy", [
    pytest.param(Summarize(model="summary", keep=RecentTokens(1), max_tokens=100), id="summary"),
    pytest.param(PruneToolResults(keep=1), id="prune-old-results"),
])
async def test_long_tool_loop_compacts_repeatedly_and_executes_each_step_once(lookup_provider, strategy):
    tool, executed, sizes = lookup_provider
    config = policy(strategy=strategy, trigger=TokenThreshold(tokens=2800),
                    context_windows={"main": LOOKUP_INPUT_LIMIT + 100, "summary": 100_000})
    options = LiteAgentOptions(model="main", tools=[tool], max_turns=LOOKUP_STEPS + 1,
                               max_tokens=100, compaction=config)
    async with LiteAgentClient(options=options) as agent:
        events = [event async for event in agent.query(LOOKUP_PROMPT)]
        mirror = replay_events([UserMessage(LOOKUP_PROMPT)], events)
        completed = [event for event in events if isinstance(event, CompactionCompleted)]
        assert mirror == agent.history
        assert agent.history[-1].content[0].text == "All eight lookups complete"
    assert executed == list(range(1, LOOKUP_STEPS + 1))
    assert len(sizes) == LOOKUP_STEPS + 1
    assert len(completed) >= 3
    assert all(event.tokens_after < event.tokens_before for event in completed)


async def test_failed_manual_summary_can_be_retried_then_used_by_next_query(mock_anthropic_messages):
    def unavailable(**kwargs):
        raise ConnectionError("summary provider unavailable")

    mock_anthropic_messages.push(unavailable)
    mock_anthropic_messages.push(text_response("Recovered checkpoint", model="summary"))
    mock_anthropic_messages.push(text_response("Continued successfully", model="main"))
    history = [*old_history(), UserMessage("current request")]
    options = LiteAgentOptions(model="main", compaction=policy(trigger=None))
    async with LiteAgentClient(options=options, history=history) as agent:
        with pytest.raises(CompactionError, match="summary provider unavailable"):
            await agent.compact()
        assert agent.history == history
        result = await agent.compact()
        assert isinstance(result, CompactionCompleted)
        assert result.tokens_after < result.tokens_before
        events = [event async for event in agent.query("continue")]
        assert events[-1].content[0].text == "Continued successfully"
    assert [call["model"] for call in mock_anthropic_messages.calls] == ["summary", "summary", "main"]
    outgoing = mock_anthropic_messages.calls[-1]["messages"]
    assert "Recovered checkpoint" in outgoing[0]["content"]
    assert "old detail" not in json.dumps(outgoing)


@pytest.mark.parametrize("keep_tokens", [
    pytest.param(1, id="minimal-retention"),
    pytest.param(1000, id="inside-one-result"),
    pytest.param(2500, id="inside-result-batch"),
])
async def test_retention_boundary_cannot_split_a_batch_of_tool_results(mock_anthropic_messages, keep_tokens):
    history = ConversationHistory([*old_history(), UserMessage("Compare all three artifacts")])
    calls = [{"type": "tool_use", "id": f"artifact-{i}", "name": "echo", "input": {"text": str(i)}}
             for i in range(3)]
    # Reversed result order, structured content, and an error must survive intact.
    results = [{"type": "tool_result", "tool_use_id": f"artifact-{i}",
                "content": [{"type": "text", "text": f"artifact {i} " + "data " * 250}],
                "is_error": i == 1} for i in reversed(range(3))]
    history.add_assistant_response(calls, "main")
    history.add_user_tool_results(results)
    retained = deepcopy(history.raw()[-2:])
    mock_anthropic_messages.push(text_response("Earlier investigation checkpoint", model="summary"))
    config = policy(strategy=Summarize(model="summary", keep=RecentTokens(keep_tokens), max_tokens=100))
    events = await run(CompactionRuntime(config), history)
    assert isinstance(events[-1], CompactionCompleted)
    assert history.raw()[-2:] == retained
    assert history.raw()[1]["content"] == "Compare all three artifacts"
    assert_complete_tool_groups(history.raw())
    transcript = json.loads(mock_anthropic_messages.calls[0]["messages"][0]["content"])
    assert "artifact-" not in json.dumps(transcript["history"])


async def test_shrinking_but_still_oversized_summary_is_rejected_atomically(mock_anthropic_messages):
    mock_anthropic_messages.push(text_response("checkpoint " * 200, model="summary"))
    history = [*old_history(), UserMessage("current request")]
    options = LiteAgentOptions(model="main", max_tokens=100, compaction=policy(
        trigger=None, context_windows={"main": 1000, "summary": 100_000}))
    async with LiteAgentClient(options=options, history=history) as agent:
        with pytest.raises(ContextBudgetExceeded, match="still exceeds") as error:
            await agent.compact()
        assert agent.history == history
        assert error.value.usage == {"input_tokens": 1, "output_tokens": 1}
    assert [call["model"] for call in mock_anthropic_messages.calls] == ["summary"]


async def test_sidekick_really_compacts_between_delegations_without_editing_main_history(mock_anthropic_messages):
    for index in range(2):
        mock_anthropic_messages.push(tool_use_response(
            tool_use_id=f"delegate-{index}", name="delegate_to_sidekick",
            input={"task": f"Inspect artifact {index}"}, model="main"))
        if index:
            mock_anthropic_messages.push(text_response("Artifact 0 was inspected", model="summary"))
        mock_anthropic_messages.push(text_response("inspection details " * 300, model="sidekick"))
        mock_anthropic_messages.push(text_response(f"Acknowledged {index}", model="main"))

    options = LiteAgentOptions(model="main", fusion=FusionOptions(
        sidekick_model="sidekick", sidekick_compaction=policy()))
    async with LiteAgentClient(options=options) as agent:
        first = [event async for event in agent.query("first delegation")]
        before = agent.history
        second = [event async for event in agent.query("second delegation")]
        assert agent.history[:len(before)] == before
        assert not any(isinstance(message, SummaryMessage) for message in agent.history)
        assert not any(isinstance(event, CompactionCompleted) for event in [*first, *second])
        assert second[-1].content[0].text == "Acknowledged 1"
    assert [call["model"] for call in mock_anthropic_messages.calls] == [
        "main", "sidekick", "main", "main", "summary", "sidekick", "main"]
    sidekick_request = mock_anthropic_messages.calls[5]
    assert "Artifact 0 was inspected" in sidekick_request["messages"][0]["content"]
    assert sidekick_request["messages"][1]["content"] == "Inspect artifact 1"
    assert "inspection details" not in json.dumps(sidekick_request["messages"])
