"""`liteagents init` / `liteagents dev` and the generated project's plumbing."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from liteagents import AssistantMessage, TextBlock, UserMessage
from liteagents.cli import main, scaffold
from liteagents.cli.choices import chat_models, provider_for_model, rank
from liteagents.errors import ConfigurationError
from liteagents.project import AgentConfig, Cron, SessionStore, run_crons
from liteagents.runtime.direct import DirectRuntime

SOURCE = "file:///checkout/liteagents"


def config(**overrides) -> AgentConfig:
    values = {"name": "helper", "harness": "claude-sdk", "model": "anthropic/claude-sonnet-5-5",
              "channel": "terminal", **overrides}
    return AgentConfig(**values)


def generate(tmp_path: Path, agent: AgentConfig) -> Path:
    root = tmp_path / agent.name
    scaffold.render(root, agent, source=SOURCE)
    return root


def import_project(root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(root))
    for name in [m for m in sys.modules if m == "agent" or m.startswith("agent.")]:
        monkeypatch.delitem(sys.modules, name)
    return importlib.import_module("agent.core")


@pytest.mark.parametrize("name", ["bot", "My_Agent-2", "7up"])
def test_valid_names(name):
    assert config(name=name).name == name


@pytest.mark.parametrize("name", ["", "-lead", "has space", "a/b", "../up", "dot.name", "x" * 65])
def test_invalid_names_are_rejected(name):
    with pytest.raises(ConfigurationError):
        config(name=name)


@pytest.mark.parametrize(
    ("field", "value"), [("harness", "claude"), ("channel", "discord"), ("model", "has space")]
)
def test_invalid_choices_are_rejected(field, value):
    with pytest.raises(ConfigurationError, match="(?i)unknown|model"):
        config(**{field: value})


def test_flags_flow_through_agent_toml_into_the_profile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scaffold, "install", lambda root, agent: None)
    monkeypatch.setattr("liteagents.cli.verify_model", lambda agent: None)
    code = main(["init", "relay", "--harness", "codex", "--model", "openai/gpt-5.6",
                 "--channel", "terminal", "--no-start", "--yes"])
    assert code == 0
    root = tmp_path / "relay"
    assert AgentConfig.load(root / "agent.toml") == AgentConfig(
        "relay", "codex", "openai/gpt-5.6", "terminal"
    )
    core = import_project(root, monkeypatch)
    profile = core.build_profile(core.load_config())
    assert (profile.harness, profile.model) == ("codex", "openai/gpt-5.6")
    assert profile.tools == ["current_time"]
    assert profile.model_kwargs == {}


def test_gateway_model_uses_gateway_credentials(tmp_path, monkeypatch):
    root = generate(tmp_path, config(model="litellm_proxy/team-sonnet"))
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-gateway")
    core = import_project(root, monkeypatch)
    profile = core.build_profile(core.load_config())
    assert profile.model_kwargs == {"api_base": "https://gateway.example", "api_key": "sk-gateway"}
    assert "ANTHROPIC_AUTH_TOKEN=" in (root / ".env.example").read_text()


def test_instructions_are_reread_every_turn(tmp_path, monkeypatch):
    root = generate(tmp_path, config())
    core = import_project(root, monkeypatch)
    assert "You are helper" in core.build_profile(core.load_config()).system_prompt
    (root / "agent" / "instructions.md").write_text("Answer in French.")
    assert core.build_profile(core.load_config()).system_prompt == "Answer in French."


def test_existing_directory_is_refused_and_left_untouched(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("mine")
    code = main(["init", "taken", "--harness", "claude-sdk", "--model", "anthropic/x",
                 "--channel", "terminal", "--yes"])
    assert code == 2
    assert "already exists" in capsys.readouterr().err
    assert os.listdir(tmp_path / "taken") == ["keep.txt"]
    with pytest.raises(FileExistsError):
        scaffold.render(tmp_path / "taken", config(name="taken"), source=SOURCE)


def test_non_interactive_init_names_every_missing_field(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "--harness", "codex", "--yes"]) == 2
    assert "Missing NAME, --model, --channel" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_failed_step_keeps_project_and_prints_resume(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scaffold, "install", lambda root, agent: None)
    monkeypatch.setattr("liteagents.cli.verify_model", lambda agent: "model rejected")
    code = main(["init", "kept", "--harness", "claude-sdk", "--model", "anthropic/x",
                 "--channel", "terminal", "--yes"])
    out = capsys.readouterr().out
    assert code == 1
    assert "✗ Model access verified" in out and "✓ Model access verified" not in out
    assert "cd kept && liteagents dev" in out
    assert (tmp_path / "kept" / "agent.toml").is_file()


def test_slack_without_tokens_fails_before_connecting(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    monkeypatch.setattr(scaffold, "install", lambda root, agent: None)
    monkeypatch.setattr("liteagents.cli.verify_model", lambda agent: None)
    code = main(["init", "chat", "--harness", "claude-sdk", "--model", "anthropic/x",
                 "--channel", "slack", "--yes"])
    out = capsys.readouterr().out
    assert code == 1
    assert "✗ Slack connected" in out and "SLACK_BOT_TOKEN" in out


@pytest.mark.parametrize(
    ("harness", "channel", "extra", "slack"),
    [("claude-sdk", "terminal", "claude-sdk", False), ("pydantic-ai", "slack", "pydantic-ai", True),
     ("opencode-v2", "terminal", "opencode-v2", False)],
)
def test_dependencies_follow_harness_and_channel(harness, channel, extra, slack):
    deps = scaffold.dependencies(config(harness=harness, channel=channel), SOURCE)
    assert deps[0] == f"liteagents[{extra}] @ {SOURCE}"
    assert any(d.startswith("agentchat") for d in deps) is slack
    assert not any("liteagents[" in d and extra not in d for d in deps)


def test_install_commands_add_only_selected_extras_and_keep_liteagents(tmp_path):
    first, second = scaffold.install_commands(tmp_path, config(harness="claude-sdk"))
    assert first[:3] == (sys.executable, "-m", "pip")
    assert any(r.startswith("claude-agent-sdk") for r in first)
    assert not any(r.startswith(("openai-codex", "deepagents", "agentchat")) for r in first)
    assert second[-3:] == ("--no-deps", "-e", str(tmp_path))


def test_generated_pyproject_is_installable_metadata(tmp_path):
    import tomllib

    root = generate(tmp_path, config(name="Slack_Bot", channel="slack"))
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    assert project["name"] == "slack-bot"
    assert project["dependencies"][0] == f"liteagents[claude-sdk] @ {SOURCE}"
    assert (root / ".gitignore").read_text().splitlines()[0] == ".env"


def test_env_is_private_and_appends(tmp_path):
    scaffold.write_env(tmp_path, {"A": "1"})
    scaffold.write_env(tmp_path, {"B": 'x"y'})
    path = tmp_path / ".env"
    assert path.stat().st_mode & 0o777 == 0o600
    from dotenv import dotenv_values

    assert dotenv_values(path) == {"A": "1", "B": 'x"y'}


def test_model_search_requires_every_word_and_prefers_name_prefix():
    models = ["bedrock/us.anthropic.claude-opus-5-5", "anthropic/claude-opus-5-5",
              "anthropic/claude-sonnet-5-5", "openai/gpt-5.6"]
    assert rank("opus 5-5", models) == [
        "anthropic/claude-opus-5-5", "bedrock/us.anthropic.claude-opus-5-5",
    ]
    assert rank("gpt", models) == ["openai/gpt-5.6"]
    assert rank("", models) == models


def test_gateway_list_drops_non_chat_models():
    assert chat_models(["gpt-image-2", "text-embedding-3", "whisper-1", "claude-opus-5-5",
                        "hd/1024-x-1024/dall-e", "gpt-5.6"]) == ("claude-opus-5-5", "gpt-5.6")


def test_provider_detection_selects_the_key_to_check():
    assert provider_for_model("anthropic/claude-x").key_env == "ANTHROPIC_API_KEY"
    assert provider_for_model("litellm_proxy/anything").key_env == "ANTHROPIC_AUTH_TOKEN"
    assert provider_for_model("gemini/gemini-3") is None


class FakeClient:
    def __init__(self, session_id, history):
        self.session_id, self.history = session_id, history


@pytest.mark.parametrize("harness", ["claude-sdk", "codex"])
def test_sessions_resume_native_harnesses(tmp_path, harness):
    store = SessionStore(tmp_path / ".liteagents" / "sessions.json")
    assert store.client_options("C1", harness) == {}
    store.save("C1", harness, FakeClient("native-1", []))
    assert SessionStore(store.path).client_options("C1", harness) == {"session_id": "native-1"}
    assert store.client_options("C2", harness) == {}
    assert store.client_options("C1", "opencode-v2") == {}


def test_sessions_replay_history_for_python_harnesses(tmp_path):
    store = SessionStore(tmp_path / "sessions.json")
    history = [UserMessage("code is 42"), AssistantMessage([TextBlock("noted")], "m", "end_turn")]
    store.save("C1", "pydantic-ai", FakeClient(None, history))
    restored = SessionStore(store.path).client_options("C1", "pydantic-ai")["history"]
    assert [type(m) for m in restored] == [UserMessage, AssistantMessage]
    assert restored[0].content == "code is 42" and restored[1].content[0].text == "noted"
    assert json.loads(store.path.read_text())["C1"]["harness"] == "pydantic-ai"


async def test_seeded_history_reaches_a_real_python_harness(tmp_path):
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from liteagents import LiteAgentClient, ProfileOptions

    seen = []

    def respond(messages, info):
        seen.extend(part.content for m in messages for part in m.parts if hasattr(part, "content"))
        return ModelResponse(parts=[TextPart("PELICAN-7")])

    profile = ProfileOptions(harness="pydantic-ai", model="openai/test",
                             harness_options={"model_instance": FunctionModel(respond)})
    history = [UserMessage("My code word is PELICAN-7."),
               AssistantMessage([TextBlock("Noted.")], "m", "end_turn")]
    async with LiteAgentClient(profile=profile, cwd=tmp_path, history=history) as client:
        async for _ in client.query("What is my code word?"):
            pass
        assert len(client.history) == 4
    assert "My code word is PELICAN-7." in seen and "Noted." in seen


def test_direct_runtime_exposes_the_resumable_session(tmp_path):
    pytest.importorskip("claude_agent_sdk")
    from liteagents import ProfileOptions

    profile = ProfileOptions(harness="claude-sdk", model="anthropic/claude-sonnet-5-5")
    runtime = DirectRuntime(profile, cwd=tmp_path, tools=[], session_id="resume-me")
    assert runtime.session_id == "resume-me"
    runtime.adapter.native_session_id = "native-2"
    assert runtime.session_id == "native-2"


async def test_crons_run_every_interval_and_survive_failures():
    calls = []

    async def run_turn(prompt, conversation_id):
        calls.append((prompt, conversation_id))
        if len(calls) == 1:
            raise RuntimeError("first turn fails")
        return "ok"

    task = asyncio.create_task(run_crons(
        (Cron(timedelta(milliseconds=10), "ping", "cron-1"),), run_turn
    ))
    while len(calls) < 3:
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert set(calls) == {("ping", "cron-1")}
    with pytest.raises(ValueError):
        Cron(timedelta(0), "x", "y")


def test_generated_project_imports_and_dev_finds_it(tmp_path, monkeypatch):
    root = generate(tmp_path, config(name="imp", channel="slack"))
    result = subprocess.run(
        [sys.executable, "-c",
         ("import agent, agent.__main__, agent.crons, agent.memory, agent.channels.terminal;"
          "from agent.channels import load_channel;"
          "print(agent.load_config().name, agent.crons.CRONS, agent.memory.sessions.path)")],
        cwd=root / "agent", capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:2] == ["imp", "()"]
    assert result.stdout.strip().endswith(str(root / ".liteagents" / "sessions.json"))
    monkeypatch.chdir(root / "agent")
    from liteagents.cli import find_project

    assert find_project(Path.cwd()) == root


def test_dev_outside_a_project_explains_how_to_start(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["dev"]) == 2
    assert "liteagents init" in capsys.readouterr().err


def test_slack_manifest_has_socket_mode_scopes_and_events():
    import yaml

    from liteagents.cli.prompts import slack_manifest

    manifest = yaml.safe_load(slack_manifest("helper"))
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert set(manifest["oauth_config"]["scopes"]["bot"]) == {
        "app_mentions:read", "chat:write", "im:history", "im:read", "im:write",
    }
    assert manifest["settings"]["event_subscriptions"]["bot_events"] == ["app_mention", "message.im"]


@pytest.mark.live
@pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("LITEAGENTS_SMOKE_MODEL")),
    reason="needs ANTHROPIC_API_KEY, or LITEAGENTS_SMOKE_MODEL with its credentials",
)
def test_live_init_then_dev_recalls_a_fact(tmp_path):
    cli = Path(sys.executable).with_name("liteagents")
    init = subprocess.run(
        [str(cli), "init", "smoke", "--harness", "claude-sdk", "--model",
         os.getenv("LITEAGENTS_SMOKE_MODEL", "anthropic/claude-sonnet-5-5"),
         "--channel", "terminal", "--no-start", "--yes"],
        cwd=tmp_path, capture_output=True, text=True, timeout=600, check=False,
    )
    assert init.returncode == 0, init.stdout + init.stderr
    assert "✓ Model access verified" in init.stdout
    chat = subprocess.run(
        [str(cli), "dev"], cwd=tmp_path / "smoke", capture_output=True, text=True, timeout=600, check=False,
        input="My code word is PELICAN-7. Just say noted.\n"
              "What is my code word? Reply with only the code word.\n",
    )
    assert chat.returncode == 0, chat.stderr
    assert "PELICAN-7" in chat.stdout.strip().splitlines()[-1], chat.stdout
