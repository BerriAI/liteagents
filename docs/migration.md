# Upgrading LiteAgents

Install the development version from the repository checkout with
`pip install '.[all]' -c constraints-tested.txt`. The PyPI project currently
named `liteagents` is a different package.

## Updating from 0.2.0 to 0.3.0a1

The client and profile API is unchanged. LiteLLM now handles shared model
configuration across every harness. Keep one `provider/model` or gateway alias;
remove harness-specific model substitutions from application code.

Two behavioral changes make application configuration consistent:

- Omitted `profile.tools` now exposes only registered application and discovered
  MCP tools. Select `read_file`, `edit_file`, or `run_tests` explicitly if needed.
  DeepAgents/CLI native built-ins are no longer implicit in shared profiles.
- `start_run()` now always creates an independent job. Use `query()` for
  conversation follow-ups, which now retain completed turns with Temporal too.

Shared `model_kwargs` are validated consistently. Configure native model objects
through `harness_options.model_instance` when native Python model features are
needed. Compatible Codex/OpenCode native configuration still passes through;
shared provider/tool overrides fail clearly instead of being ignored.

Finish existing durable runs using their original SDK, runtime, and profile
versions. Upgrade clients and workers together and assign new profile IDs.

## Updating from the v2 previews

The following changes apply when upgrading from **0.2.0a1**. The **0.2.0a2**
preview already includes them; 0.2.0 packages that implementation as a release.

An omitted `profile.tools` now serializes as `null` and retains defaults.
`tools: []` explicitly disables tools; change old empty lists to `null` if you
relied on defaults. This does not change `LiteAgentOptions.tools=[]`, which is
still an empty application-tool registry.

Application tools, explicit tool selections, and `profile.mcp_servers` now
automatically select managed CLI execution. Supply `model_kwargs.api_base` and
use the shared MCP fields; remove native provider/tool-policy overrides when
using this path. The ordinary native configuration escape hatch remains for
profiles without shared tools, MCP, or managed features. `http_headers` and
`enabled_tools` are accepted shared aliases, but other native-only MCP fields
should be moved to native `harness_options.config` where supported.

Shared tool events now use application names. Code inspecting CLI prefixes
should use `ToolUseBlock.native_name`. Use `client.capabilities` or pass the full
profile to `get_capabilities` for effective support.

Upgrade clients and workers together. Finish existing runs on their original
version; use a new `temporal.profile_id` for changed profiles/tool semantics.
Do not replay an in-flight run across an SDK upgrade.

## Moving from the v1 API

The package root now exposes the profile-driven SDK. Its client delegates the
agent loop to DeepAgents, Pydantic AI, Claude Agent SDK, Codex, or OpenCode.

Install the harness you choose and move model settings into a profile:

```python
from liteagents import LiteAgentClient, LiteAgentOptions, ProfileOptions

options = LiteAgentOptions(
    profile=ProfileOptions(
        harness="deepagents",
        model="openai/gpt-4.1-mini",
        model_kwargs={"max_tokens": 4096},
        system_prompt="Help with this project.",
        max_turns=20,
    ),
    tools=my_tools,
    cwd=".",
)
```

`system` becomes `profile.system_prompt`; `stream` becomes
`profile.features.streaming`; `model_kwargs` and `max_turns` move into the
profile. `Tool`, text/tool messages, and the asynchronous `query()` pattern stay.
Shared model kwargs are translated by LiteLLM. The model must support the
requested settings; native-only parameters belong in the appropriate escape hatch.

`query()` calls on one client share a conversation with or without Temporal.
Register application tools on `LiteAgentWorker` for durable execution. Use
`start_run()` for independent jobs and `get_run()` to attach. Initial typed `history`
is a v1 feature; native session continuation uses `session_id` where supported.

For existing applications that need the old custom loop, JEV router, fusion, or
PR risk agent while migrating:

```sh
python -m pip install 'liteagents[legacy] @ https://github.com/BerriAI/liteagents/releases/download/v0.2.0/liteagents-0.2.0-py3-none-any.whl'
```

```python
from liteagents.legacy import LiteAgentClient, LiteAgentOptions, FusionOptions
```

Their implementation and regression tests live under the explicit `legacy`
namespace. Internal imports also move there (`liteagents.legacy.history`,
`liteagents.legacy.routers`, and so on). New v2 applications do not import LiteLLM
or the old custom agent loop. The [legacy reference](legacy-api.md) documents the
compatibility API.
