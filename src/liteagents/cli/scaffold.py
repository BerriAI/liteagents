"""Render the project template and install only what the selected harness needs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from importlib import metadata, resources
from pathlib import Path

from ..project import AgentConfig

AGENTCHAT = (
    "agentchat @ git+https://github.com/BerriAI/agentchat.git"
    "@8790fe927029cb115dab17c5e14e3a0b699b3bcc"
)
DOTENV = "python-dotenv>=1,<2"
RELEASE_WHEEL = (
    "https://github.com/BerriAI/liteagents/releases/download/v{0}/liteagents-{0}-py3-none-any.whl"
)
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


def liteagents_source() -> str:
    """Where this liteagents came from, so the new project installs the same build."""
    distribution = metadata.distribution("liteagents")
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return RELEASE_WHEEL.format(distribution.version)
    origin = json.loads(raw)
    if "vcs_info" in origin:
        return f"git+{origin['url']}@{origin['vcs_info']['commit_id']}"
    return origin["url"]


def dependencies(config: AgentConfig, source: str) -> tuple[str, ...]:
    """The generated pyproject's requirements: one harness extra, Slack only when chosen."""
    items = (f"liteagents[{config.harness}] @ {source}", DOTENV)
    return (*items, AGENTCHAT) if config.channel == "slack" else items


def extra_requirements(extra: str) -> tuple[str, ...]:
    """Requirements the installed liteagents declares for one extra."""
    found = []
    for line in metadata.requires("liteagents") or []:
        requirement, _, marker = line.partition(";")
        match = _EXTRA_MARKER.search(marker)
        if match and match.group(1) == extra:
            found.append(requirement.strip())
    return tuple(found)


def install_commands(root: Path, config: AgentConfig) -> tuple[tuple[str, ...], ...]:
    """Install into this interpreter without replacing the running liteagents build."""
    pip = (sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q")
    extra = (DOTENV, *extra_requirements(config.harness))
    extra = (*extra, AGENTCHAT) if config.channel == "slack" else extra
    return ((*pip, *extra), (*pip, "--no-deps", "-e", str(root)))


def install(root: Path, config: AgentConfig) -> str | None:
    for command in install_commands(root, config):
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            tail = "\n  ".join((result.stderr or result.stdout).strip().splitlines()[-5:])
            return f"pip failed:\n  {tail}\n  Retry with: pip install -e ./{config.name}"
    return None


def env_example(config: AgentConfig) -> str:
    keys = (
        ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
        if config.model.startswith("litellm_proxy/")
        else (f"{config.model.split('/', 1)[0].upper()}_API_KEY",)
    )
    slack = ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN") if config.channel == "slack" else ()
    return "".join(f"{key}=\n" for key in (*keys, *slack))


def render(root: Path, config: AgentConfig, *, source: str) -> None:
    """Write the project. Fails before writing anything if root already exists."""
    values = {
        "name": config.name,
        "project_name": config.name.lower().replace("_", "-"),
        "harness": config.harness,
        "model": config.model,
        "dependencies": "".join(f"    {json.dumps(d)},\n" for d in dependencies(config, source)),
        "env_example": env_example(config),
    }
    root.mkdir()
    template = resources.files("liteagents.cli") / "template"
    with resources.as_file(template) as base:
        for path in sorted(base.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(base)
            target = root / str(relative).replace("dot-", ".", 1).removesuffix(".tmpl")
            target.parent.mkdir(parents=True, exist_ok=True)
            text = path.read_text()
            for key, value in values.items():
                text = text.replace("{{" + key + "}}", value)
            target.write_text(text)
    (root / "agent.toml").write_text(config.to_toml())


def write_env(root: Path, values: dict[str, str]) -> None:
    """Secrets go only to the project's gitignored .env, readable by the owner only."""
    if not values:
        return
    path = root / ".env"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a") as handle:
        handle.write("".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()))
    path.chmod(0o600)
