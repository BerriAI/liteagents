import asyncio
import os
import sys
from pathlib import Path

import pytest

from cookbook.recipes._common import parser, setup
from liteagents import ProfileOptions
from tests.notebook_provider_fixture import NotebookProvider

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def recipe_args(tmp_path, monkeypatch):
    for name in ("LITEAGENTS_MODEL", "LITEAGENTS_API_BASE", "LITELLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    args = parser("test").parse_args([])
    args.workspace = tmp_path
    return args


def test_recipe_setup_needs_no_gateway_configuration(recipe_args):
    _, profile = setup(recipe_args, "test")
    assert profile.harness == "pydantic-ai" and profile.model == "openai/gpt-5.4-mini"
    assert profile.model_kwargs == {}


def test_optional_gateway_requires_its_own_key(recipe_args, monkeypatch):
    monkeypatch.setenv("LITEAGENTS_API_BASE", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key-must-not-be-forwarded")
    with pytest.raises(SystemExit, match="LITEAGENTS_MODEL"):
        setup(recipe_args, "test")
    monkeypatch.setenv("LITEAGENTS_MODEL", "my/team-model")
    with pytest.raises(SystemExit, match="LITELLM_API_KEY"):
        setup(recipe_args, "test")
    monkeypatch.setenv("LITELLM_API_KEY", "gateway-synthetic-key")
    _, profile = setup(recipe_args, "test")
    assert profile.model == "my/team-model"
    assert profile.model_kwargs["api_key"] == "gateway-synthetic-key"


def test_example_profiles_load_without_gateway_environment(recipe_args):
    profiles = list((ROOT / "cookbook/compare_harnesses/profiles").glob("*.yaml"))
    profiles += [ROOT / "cookbook/recipes/profiles/agent.yaml", ROOT / "cookbook/temporal/agent.yaml"]
    for path in profiles:
        profile = ProfileOptions.from_yaml(path)
        assert profile.model == "openai/gpt-5.4-mini" and profile.model_kwargs == {}
    assert ProfileOptions.from_json(ROOT / "cookbook/recipes/profiles/agent.json") == (
        ProfileOptions.from_yaml(ROOT / "cookbook/recipes/profiles/agent.yaml")
    )


@pytest.mark.integration
@pytest.mark.parametrize("recipe", ["00_agent", "07_model_fallback", "08_application_tools", "09_profile_files"])
async def test_terminal_recipe_with_only_provider_setup(recipe, tmp_path):
    pytest.importorskip("pydantic_ai")
    async with NotebookProvider().running() as provider:
        env = {k: v for k, v in os.environ.items()
               if k not in ("LITEAGENTS_MODEL", "LITEAGENTS_API_BASE", "LITELLM_API_KEY")}
        env.update(OPENAI_API_KEY="synthetic-recipe-key", OPENAI_API_BASE=provider.url,
                   PYDANTIC_AI_NO_BANNER="1", LITELLM_LOCAL_MODEL_COST_MAP="True")
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / f"cookbook/recipes/{recipe}.py"),
            "--workspace", str(tmp_path), cwd=tmp_path, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            output, errors = await asyncio.wait_for(process.communicate(), 60)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0, errors.decode()
        assert provider.requests
        assert b"synthetic-recipe-key" not in output + errors
        expected = "USD 12" if recipe == "08_application_tools" else (
            "fallback-ready" if recipe == "07_model_fallback" else "READY"
        )
        assert expected in output.decode()
