"""The interactive questions of `liteagents init`. Esc returns to the previous one."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..project.config import model_error, name_error
from . import ui
from .choices import (
    DEFAULT_GATEWAY_URL,
    GATEWAY,
    GATEWAY_URL_ENV,
    HARNESSES,
    PROVIDERS,
    Provider,
    gateway_models,
)


@dataclass
class Answers:
    name: str | None = None
    harness: str | None = None
    model: str | None = None
    channel: str | None = None
    secrets: dict[str, str] = field(default_factory=dict)


def directory_error(name: str) -> str | None:
    error = name_error(name)
    if error:
        return error
    return f"./{name} already exists, pick another name" if Path(name).exists() else None


def ask_name(answers: Answers) -> int:
    text = "What do you want to call your agent?"
    answers.name = ui.text_input(text, initial=answers.name or "", validate=directory_error)
    ui.answered(text, answers.name)
    return 1


def ask_harness(answers: Answers) -> int:
    text = "Which harness?"
    current = next((i for i, h in enumerate(HARNESSES) if h.id == answers.harness), 0)
    harness = HARNESSES[ui.menu(text, [(h.label, h.hint) for h in HARNESSES], selected=current)]
    answers.harness = harness.id
    ui.answered(text, harness.label)
    return 1


def _provider_hint(provider: Provider) -> str:
    if provider is GATEWAY:
        return "Any model on your gateway, one key"
    found = " (found)" if os.environ.get(provider.key_env) else ""
    return f"Uses {provider.key_env}{found}"


def _ask_secret(text: str, key: str, answers: Answers, lines: list[int]) -> None:
    answers.secrets[key] = ui.text_input(text, secret=True)
    ui.answered(text, "•" * 8)
    lines[0] += 1


def ask_model(answers: Answers) -> int:
    """Provider, its credentials, then model. Returns how many answer lines it printed."""
    lines = [0]
    try:
        text = "Which provider?"
        preferred = next((i for i, p in enumerate(PROVIDERS) if os.environ.get(p.key_env)), 0)
        provider = PROVIDERS[
            ui.menu(text, [(p.label, _provider_hint(p)) for p in PROVIDERS], selected=preferred)
        ]
        ui.answered(text, provider.label)
        lines[0] += 1
        models: tuple[str, ...] = provider.models
        if provider is GATEWAY:
            url_text = "Gateway base URL"
            base_url = ui.text_input(
                url_text, default=os.environ.get(GATEWAY_URL_ENV, DEFAULT_GATEWAY_URL)
            )
            ui.answered(url_text, base_url)
            lines[0] += 1
            answers.secrets[GATEWAY_URL_ENV] = base_url
            _ask_secret("Gateway API key", GATEWAY.key_env, answers, lines)
            try:
                models = gateway_models(base_url, answers.secrets[GATEWAY.key_env])
            except Exception as exc:  # noqa: BLE001 - a custom model ID still works
                print(f"  {ui.RED}Could not list models at {base_url}: {exc}{ui.RESET}")
                lines[0] += 1
        elif not os.environ.get(provider.key_env):
            _ask_secret(provider.key_env, provider.key_env, answers, lines)
        text = "Which model?"
        picked = ui.search(text, models)
        model = picked if picked.startswith(provider.prefix) else provider.prefix + picked
        if model_error(model):
            raise ui.Back
        answers.model = model
        ui.answered(text, model)
        return lines[0] + 1
    except ui.Back:
        ui.erase_lines(lines[0])
        for key in (GATEWAY_URL_ENV, GATEWAY.key_env, *(p.key_env for p in PROVIDERS)):
            answers.secrets.pop(key, None)
        raise


def ask_channel(answers: Answers) -> int:
    text = "Where should your agent live?"
    options = [("Slack", "Socket Mode, no public URL needed"), ("Terminal", "Chat right here")]
    current = 1 if answers.channel == "terminal" else 0
    answers.channel = ("slack", "terminal")[ui.menu(text, options, selected=current)]
    ui.answered(text, answers.channel.capitalize())
    return 1


def interview(answers: Answers) -> Answers:
    """Ask each missing answer in order, keeping completed answers visible above."""
    steps: list[tuple[str, Callable[[Answers], int]]] = [
        ("name", ask_name), ("harness", ask_harness), ("model", ask_model),
        ("channel", ask_channel),
    ]
    pending = [(key, ask) for key, ask in steps if getattr(answers, key) is None]
    printed: list[int] = []
    index = 0
    while index < len(pending):
        _, ask = pending[index]
        try:
            printed.append(ask(answers))
            index += 1
        except ui.Back:
            if index == 0:
                continue
            index -= 1
            ui.erase_lines(printed.pop())
    return answers


def slack_tokens(name: str, ask: Callable[[str], str]) -> dict[str, str]:
    print(f"\n{ui.BOLD}Create a Slack app for {name}{ui.RESET}")
    print(f"{ui.DIM}https://api.slack.com/apps?new_app=1 -> From a manifest, paste:{ui.RESET}\n")
    print(slack_manifest(name))
    print(f"\n{ui.DIM}Install it, then create an app-level token with connections:write.{ui.RESET}")
    return {key: ask(key) for key in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")}


def slack_manifest(name: str) -> str:
    import yaml

    manifest: dict[str, Any] = {
        "display_information": {"name": name},
        "features": {
            "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False},
            "bot_user": {"display_name": name, "always_online": True},
        },
        "oauth_config": {"scopes": {"bot": [
            "app_mentions:read", "chat:write", "im:history", "im:read", "im:write",
        ]}},
        "settings": {
            "event_subscriptions": {"bot_events": ["app_mention", "message.im"]},
            "interactivity": {"is_enabled": False},
            "socket_mode_enabled": True,
        },
    }
    return yaml.safe_dump(manifest, sort_keys=False).rstrip()
