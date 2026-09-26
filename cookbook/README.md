# LiteAgents cookbooks

Each notebook walks through one task in Google Colab. Run its cells from top to
bottom: install, enter an OpenAI key, then try the SDK. No repository clone is
needed. The examples use the LiteLLM SDK directly; a gateway is optional.

## Start here

| Notebook | What you will do |
| --- | --- |
| [Your first agent](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb) | Get an answer, change the harness, and run again |
| [Python tools](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/08_application_tools.ipynb) | Give the agent an order lookup |
| [Switch harnesses with a tool](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/10_harness_switch.ipynb) | Keep the same tool, model, and prompt across two harnesses |
| [Streaming and conversations](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/01_quickstart.ipynb) | Stream an answer and ask a follow-up |
| [MCP tools](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/02_mcp.ipynb) | Connect the included orders server and call its tool |

## Add a capability

| Notebook | What you will do |
| --- | --- |
| [JSON/YAML profiles](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/09_profile_files.ipynb) | Load your agent settings from a file |
| [Subagents](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/03_subagents.ipynb) | Delegate a receipt check to an auditor |
| [Approvals](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/04_approvals.ipynb) | Review a proposed edit before allowing it |
| [Tool retries](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/05_retries.ipynb) | Recover from a tool that temporarily fails |
| [Model fallback](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/07_model_fallback.ipynb) | Use a fallback after a simulated model outage |
| [Coding comparison](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/compare_harnesses/compare.ipynb) | Compare two harnesses fixing a bug, with independent tests |
| [Temporal recovery](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/06_durable.ipynb) | Stop a worker mid-task, restart it, and retrieve the original run |

## Make the examples yours

- **Another provider:** change `profile.model` and set that provider's key instead
  of the OpenAI key. [Model setup](../docs/models.md) has copyable examples for
  Anthropic, OpenRouter, Gemini, Bedrock, Azure, and other providers, plus an
  optional LiteLLM gateway.
- **Another harness:** install its integration and change `profile.harness`.
  [Installation options](../docs/getting-started.md#install-other-harnesses) cover
  all six selectors. The first examples use Pydantic AI; comparisons also
  install Claude Agent SDK.
- **Keep your changes:** use **File → Save a copy in Drive**. Clear outputs before
  sharing; they can contain prompts, tool data, and model responses.

Colab runs on a cloud machine. Each new runtime needs its installation cell;
rerunning installation in the same runtime reuses compatible packages. Provider
keys are entered through a hidden prompt and stay in that runtime. API calls use
your provider account. No GPU is needed.

File examples use temporary workspaces. Download any results you want to keep
before the runtime resets. The Temporal notebook includes a cleanup cell for
its worker and service; a permanent deployment needs persistent storage.

These notebooks also work in JupyterLab or VS Code. For terminal examples, see
the [recipe scripts](recipes/README.md) and [comparison runner](compare_harnesses/README.md).
