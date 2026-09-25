# Migrating from v1

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
Model kwargs must be supported by the chosen harness. The old LiteLLM loop's
arbitrary provider parameters are not automatically forwarded by native SDKs.

Direct queries on one client share a native conversation. Durable queries are
independent runs: register application tools on `LiteAgentWorker`, use
`start_run()` to submit, and use `get_run()` to attach. Initial typed `history`
is a v1 feature; native session continuation uses `session_id` where supported.

For existing applications that need the old custom loop, JEV router, fusion, or
PR risk agent while migrating:

```sh
pip install 'liteagents[legacy]'
```

```python
from liteagents.legacy import LiteAgentClient, LiteAgentOptions, FusionOptions
```

Their implementation and regression tests live under the explicit `legacy`
namespace. Internal imports also move there (`liteagents.legacy.history`,
`liteagents.legacy.routers`, and so on). New v2 applications do not import LiteLLM
or the old custom agent loop. The [legacy reference](legacy-api.md) documents the
compatibility API.
