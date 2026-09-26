# LiteAgents cookbooks in Google Colab

Open a notebook, run its setup cell, add your provider key, and try the SDK.
Each notebook contains its own example files and displays the actual
`ProfileOptions`, `query()`, or `LiteAgentClient` calls. No repository clone is needed.

Start with [your first agent](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb),
then try [switching harnesses](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/10_harness_switch.ipynb).
For a copyable Python snippet outside Colab, see [Getting started](../docs/getting-started.md).

## Open a notebook

| Notebook | What to try |
| --- | --- |
| [First agent](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/00_agent.ipynb) | A guided first run, then switch Pydantic AI to Claude Agent SDK |
| [Streaming and conversation](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/01_quickstart.ipynb) | Stream a response and ask a follow-up |
| [Application tools](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/08_application_tools.ipynb) | Define a Python tool and inspect calls |
| [MCP](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/02_mcp.ipynb) | Connect a demo server included in the notebook |
| [Switch harnesses](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/10_harness_switch.ipynb) | Same model, tools, MCP, and follow-up across harnesses |
| [Coding comparison](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/compare_harnesses/compare.ipynb) | Compare answers, diffs, and independent tests |
| [JSON/YAML profiles](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/09_profile_files.ipynb) | Edit and load equivalent profile files |
| [Subagents](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/03_subagents.ipynb) | Give a child its own model and tools |
| [Approvals](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/04_approvals.ipynb) | Inspect a proposed edit and approve or cancel |
| [Retries](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/05_retries.ipynb) | Retry a failed tool with the same operation key |
| [Model fallback](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/07_model_fallback.ipynb) | Simulate an outage, then call a real fallback |
| [Durable runs](https://colab.research.google.com/github/BerriAI/liteagents/blob/main/cookbook/recipes/06_durable.ipynb) | Submit, reconnect, and crash/restart the worker |

## Setup in Colab

The **First agent** walkthrough walks you through installation, a hidden API-key
prompt, your first response, and a one-line harness switch. Start there; it
does not require Colab Secrets or environment configuration.

The feature notebooks below use a more configurable setup:

1. Click a notebook above and connect to a **Python CPU runtime**.
2. Choose `HARNESS` (or `HARNESSES` for comparisons) and `MODEL` at the top.
3. Run the install cell. It installs the SDK preview wheel and selected integrations.
4. Add a provider key using **Secrets** (key icon), enable notebook access, or
   enter it at the hidden prompt. Run the remaining cells in order.

| Provider | Example `MODEL` | Secret name |
| --- | --- | --- |
| OpenAI | `openai/gpt-5.4-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| OpenRouter | `openrouter/anthropic/claude-sonnet-4.6` | `OPENROUTER_API_KEY` |

LiteLLM's Python SDK handles translation. Leave `API_BASE` empty for direct
provider access. A LiteLLM gateway is optional: set `API_BASE`, its exact model
alias, and enter its key when prompted (or save it as the `LITELLM_API_KEY`
Colab secret). No environment-variable setup is required. Model calls use your
provider or gateway account.

Other providers, including cloud and local authentication, are covered in
[Model setup](../docs/models.md). Configure those credentials in the Colab
runtime; credentials on your laptop are not available there automatically.

## Do I reinstall for every notebook?

Colab runs Python on a cloud machine, not your laptop. Different notebooks
normally have separate runtimes, so each needs its setup cell. Rerunning setup
inside the same runtime reuses installed compatible packages. A runtime reset
requires installation again.

OpenCode is installed only when selected and missing from that runtime.
The Claude and Codex extras include their runtimes. Changing the selected
harness may require rerunning installation; changing only a prompt does not.
No GPU is required. If Colab asks for a restart after installation, restart once
and run the cells again.

## Experiments and saved copies

Use **File → Save a copy in Drive** to keep your edits. Downloading an `.ipynb`
saves the notebook file to your computer; its Python environment and installed
harnesses are not included. Download generated files separately from Colab's
Files sidebar before the runtime resets.

Workspaces are fresh temporary directories. Client connections close after
each example; the Temporal notebooks include cleanup cells for the processes
they own. Their demo service and checkpoints live in the Colab runtime.
Persistent applications need an external Temporal deployment and durable storage.

Saved outputs may include prompts, tool arguments, and model responses.
Clear outputs before sharing. Keys are read from Secrets, environment variables,
or a hidden prompt, not written into code cells.

## Other ways to run

These are standard Jupyter notebooks, so they also work in JupyterLab or VS Code
with Python 3.11+. A single downloaded notebook is enough; its setup cell installs
the needed package and its example files are embedded.

The existing [recipe scripts](recipes/README.md),
[coding comparison runner](compare_harnesses/README.md), and
[standalone Temporal proof](temporal/README.md) remain available for terminal use
from a checkout.
