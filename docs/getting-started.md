# Getting started with LiteAgents

Run an agent, then switch its harness while keeping the same model and code.

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb)

## 1. Install

Use Python 3.11+. This installs LiteAgents and the two harnesses used below:

```sh
python -m pip install "liteagents[pydantic-ai,claude-sdk] @ https://github.com/BerriAI/liteagents/releases/download/v0.3.0a5/liteagents-0.3.0a5-py3-none-any.whl"
```

The preview uses a GitHub release wheel because the PyPI name belongs to another
package. No repository checkout is needed.

## 2. Set your API key

Use an [OpenAI API key](https://platform.openai.com/api-keys) with API credit:

```sh
export OPENAI_API_KEY="your-openai-key"
```

## 3. Run an agent

Save this as `agent.py` and run `python agent.py`. It prints the agent's answer.

```python
import asyncio
from liteagents import ProfileOptions, run

profile = ProfileOptions(
    harness="pydantic-ai",
    model="openai/gpt-5.4-mini",
)
prompt = "Explain what an agent harness does in one sentence."

async def main():
    result = await run(prompt, profile=profile)
    print(result.text)

asyncio.run(main())
```

`harness` chooses the agent framework. `model` chooses the model it calls.
LiteLLM's Python SDK connects to the provider using your key. No gateway is required.

In Colab, use `await main()` instead of `asyncio.run(main())`.

## 4. Switch the harness

Change `harness="pydantic-ai"` to `harness="claude-sdk"` and run the same example.
Claude Agent SDK now runs your prompt with the same OpenAI model and key.
The response still comes back as `result.text`.

Or, inside `main()` after your first run (or directly in Colab):

```python
profile.harness = "claude-sdk"
result = await run(prompt, profile=profile)
print(result.text)
```

Both harnesses were installed in step 1. Each `run()` is an independent task;
switching does not transfer a running session. Edit `prompt` to try your own task.

## Use another provider

Set the matching key and change `profile.model`. The harness can stay the same.

| Provider | Model example | Key |
| --- | --- | --- |
| OpenAI | `openai/gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet-4.6` | `OPENROUTER_API_KEY` |

[Model setup](models.md) covers these providers plus Gemini, Groq, Mistral,
DeepSeek, Together AI, xAI, Azure, Bedrock, Vertex AI, and Ollama.
Choose a model enabled for your account that supports the tools and settings you use.

## Install other harnesses

Select the integrations you need in the installation command's square brackets.
Compatible packages already installed in that Python environment are reused.

| Harness | Install extra |
| --- | --- |
| `deepagents` | `deepagents` |
| `pydantic-ai` | `pydantic-ai` |
| `claude-sdk` | `claude-sdk` |
| `codex` | `codex` |
| `opencode-v1` / `opencode-v2` | Either selector; also `npm install -g opencode-ai@1.18.29` |

Claude and Codex extras include their runtimes. `[all]` installs all Python
integrations; OpenCode still needs its executable. LiteAgents uses installed
harnesses and does not download them during a run.

## Next steps

| Try | Walkthrough |
| --- | --- |
| Give the agent a Python tool | [Look up an order](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/08_application_tools.ipynb) |
| Stream output and keep conversation history | [Streaming and conversations](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/01_quickstart.ipynb) |
| Connect an MCP server | [MCP tools](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/02_mcp.ipynb) |
| Keep native controls or load JSON/YAML | [Profiles](profiles.md) |
| Recover work after a worker stops | [Temporal walkthrough](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/06_durable.ipynb) |

`run()` returns a `RunResult` with final text, normalized messages, and available
usage. For live messages, `query()` uses the Claude Agent SDK's interface style;
for follow-ups, keep a `LiteAgentClient` open. See the [SDK reference](sdk.md).

## Optional: use a LiteLLM gateway

If you already use a gateway, set its exact model alias, endpoint, and key:

```python
from getpass import getpass

profile = ProfileOptions(
    harness="pydantic-ai",
    model="my-model",
    model_kwargs={
        "api_base": "https://your-gateway.example/v1",
        "api_key": getpass("Gateway API key: "),
    },
)
```

Use this profile with the same `run()` call. The alias is sent unchanged,
including slashes. Your application uses the gateway key instead of provider
keys. [Gateway setup](models.md#optional-litellm-gateway) has more detail.
