"""`liteagents init` creates an agent project; `liteagents dev` runs one."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from ..errors import ConfigurationError
from ..profiles import HarnessName
from ..project.config import CHANNELS, AgentConfig, Channel
from . import scaffold, ui
from .choices import GATEWAY, GATEWAY_URL_ENV, HARNESSES, provider_for_model
from .prompts import Answers, directory_error, interview, slack_tokens

SLACK_KEYS = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="liteagents", description="Create and run LiteAgents agents")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create an agent project in ./NAME")
    init.add_argument("name", nargs="?")
    init.add_argument("--harness", choices=[h.id for h in HARNESSES] + ["opencode-v1"])
    init.add_argument("--model", help="LiteLLM model ID, e.g. anthropic/claude-sonnet-5-5")
    init.add_argument("--channel", choices=CHANNELS)
    init.add_argument("--no-start", action="store_true", help="Create and verify, but do not run")
    init.add_argument("--yes", "-y", action="store_true", help="Never prompt; fail if anything is missing")
    dev = commands.add_parser("dev", help="Run the agent in this directory")
    dev.add_argument("channel", nargs="?", choices=CHANNELS, help="Override agent.toml's channel")
    return root


def fail(message: str) -> int:
    print(f"{ui.RED}error:{ui.RESET} {message}", file=sys.stderr)
    return 2


def collect(args: argparse.Namespace, *, interactive: bool) -> AgentConfig | str:
    """Flags first, then questions for the rest. Returns a config or an error message."""
    answers = Answers(args.name, args.harness, args.model, args.channel)
    if answers.name is not None and (error := directory_error(answers.name)):
        return error
    if not interactive:
        missing = [f"--{k}" if k != "name" else "NAME" for k in ("name", "harness", "model",
                   "channel") if getattr(answers, k) is None]
        if missing:
            return f"Missing {', '.join(missing)} (running non-interactively)"
    else:
        ui.banner()
        for key, label in (("name", "Name"), ("harness", "Harness"), ("model", "Model"),
                           ("channel", "Channel")):
            if getattr(answers, key) is not None:
                ui.answered(label, getattr(answers, key))
        with ui.keys():
            interview(answers)
        print()
    try:
        config = AgentConfig(
            name=cast(str, answers.name), harness=cast(HarnessName, answers.harness),
            model=cast(str, answers.model), channel=cast(Channel, answers.channel),
        )
    except ConfigurationError as exc:
        return str(exc)
    os.environ.update(answers.secrets)
    args.secrets = answers.secrets
    return config


def verify_model(config: AgentConfig) -> str | None:
    import litellm

    litellm.suppress_debug_info = True
    provider = provider_for_model(config.model)
    if provider and not os.environ.get(provider.key_env):
        return f"Set {provider.key_env} in ./{config.name}/.env, then run: cd {config.name} && liteagents dev"
    kwargs = {}
    if provider is GATEWAY:
        kwargs = {"api_base": os.environ.get(GATEWAY_URL_ENV), "api_key": os.environ[GATEWAY.key_env]}
    try:
        asyncio.run(litellm.acompletion(
            model=config.model, messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=16, timeout=60, **kwargs,
        ))
    except Exception as exc:  # noqa: BLE001 - shown to the user as an actionable step failure
        message = str(exc).splitlines()[0].split("Received API Key")[0][:240]
        return f"{type(exc).__name__}: {message}\n  Check the key and model ID, then: cd {config.name} && liteagents dev"
    return None


def verify_slack() -> str | None:
    import httpx

    bot, app = (os.environ[key] for key in SLACK_KEYS)
    checks = (("auth.test", bot, "SLACK_BOT_TOKEN"), ("apps.connections.open", app, "SLACK_APP_TOKEN"))
    for method, token, key in checks:
        response = httpx.post(
            f"https://slack.com/api/{method}", headers={"Authorization": f"Bearer {token}"}, timeout=15,
        ).json()
        if not response.get("ok"):
            return f"Slack rejected {key}: {response.get('error', 'unknown error')}"
    return None


def slack_setup(config: AgentConfig, root: Path, *, interactive: bool) -> str | None:
    from dotenv import dotenv_values

    local = {k: v for k, v in dotenv_values(".env").items() if k in SLACK_KEYS and v}
    tokens = {key: os.environ.get(key) or local.get(key, "") for key in SLACK_KEYS}
    if not all(tokens.values()):
        if not interactive:
            return f"Set {' and '.join(SLACK_KEYS)} in ./{config.name}/.env, then: cd {config.name} && liteagents dev"
        tokens = slack_tokens(config.name, lambda key: getpass.getpass(f"{key}: ").strip())
        print()
    unsaved = {k: v for k, v in tokens.items() if k not in os.environ}
    scaffold.write_env(root, unsaved)
    os.environ.update(tokens)
    return None


def init(args: argparse.Namespace) -> int:
    interactive = not args.yes and sys.stdin.isatty() and sys.stdout.isatty()
    try:
        collected = collect(args, interactive=interactive)
    except KeyboardInterrupt:
        print()
        return 130
    if isinstance(collected, str):
        return fail(collected)
    config, root = collected, Path(collected.name)
    resume = f"cd {config.name} && liteagents dev"
    steps = [
        (f"Created ./{config.name}", lambda: create(config, root, args.secrets)),
        ("Installed dependencies", lambda: scaffold.install(root, config)),
        ("Model access verified", lambda: verify_model(config)),
    ]
    if config.channel == "slack":
        steps.append(("Slack connected", lambda: slack_setup(config, root, interactive=interactive)
                      or verify_slack()))
    for index, (label, action) in enumerate(steps):
        if ui.run_step(label, action, animate=interactive):
            if index:
                print(f"\n{ui.DIM}./{config.name} was kept. Fix the error above, then: {resume}{ui.RESET}")
            return 1
    if args.no_start:
        print(f"\n{config.name} is ready\n\n{ui.DIM}Start it with: {resume}{ui.RESET}")
        return 0
    print(f"\n{ui.BOLD}{config.name} is running{ui.RESET}\n")
    if config.channel == "slack":
        print(f"Message @{config.name} in Slack")
    print(f"{ui.DIM}Edit ./{config.name}/agent/instructions.md to change its behavior{ui.RESET}")
    print(f"{ui.DIM}Running locally · Ctrl+C to stop{ui.RESET}")
    return run_project(root, config.channel)


def create(config: AgentConfig, root: Path, secrets: dict[str, str]) -> str | None:
    if root.exists():
        return f"./{config.name} already exists"
    scaffold.render(root, config, source=scaffold.liteagents_source())
    scaffold.write_env(root, secrets)
    return None


def find_project(start: Path) -> Path | None:
    return next((p for p in (start, *start.parents) if (p / "agent.toml").is_file()), None)


def run_project(root: Path, channel: str | None) -> int:
    root = root.resolve()
    if not (root / "agent" / "__main__.py").is_file():
        return fail(f"{root} has agent.toml but no agent/ package")
    sys.path.insert(0, str(root))
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        del sys.modules[name]
    from importlib import import_module

    import_module("agent.__main__").main(channel)
    return 0


def dev(args: argparse.Namespace) -> int:
    root = find_project(Path.cwd())
    if root is None:
        return fail("No agent.toml here. Create a project with: liteagents init")
    try:
        AgentConfig.load(root / "agent.toml")
    except (ConfigurationError, ValueError) as exc:
        return fail(str(exc))
    return run_project(root, args.channel)


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return init(args) if args.command == "init" else dev(args)
