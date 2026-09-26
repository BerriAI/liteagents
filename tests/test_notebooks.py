"""Validate committed notebooks and execute them in real, disposable kernels."""

import ast
import copy
import json
import os
import shutil
import socket
import sys
from pathlib import Path

import nbformat
import pytest
from IPython.core.inputtransformer2 import TransformerManager
from jupyter_client import AsyncKernelManager
from jupyter_client.kernelspec import KernelSpecManager
from nbclient import NotebookClient

from tests.notebook_provider_fixture import NotebookProvider
from tests.test_native_recovery import available

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted(
    p for p in (ROOT / "cookbook").rglob("*.ipynb") if ".ipynb_checkpoints" not in p.parts
)


def test_every_current_cookbook_has_a_clean_valid_notebook():
    scripts = list((ROOT / "cookbook/recipes").glob("[0-9]*.py"))
    scripts.append(ROOT / "cookbook/compare_harnesses/compare.py")
    assert {p.with_suffix(".ipynb") for p in scripts} == set(NOTEBOOKS)
    for path in NOTEBOOKS:
        notebook = nbformat.read(path, as_version=4)
        nbformat.validate(notebook)
        assert notebook.metadata.kernelspec.language == "python"
        for cell in notebook.cells:
            if cell.cell_type == "code":
                assert cell.execution_count is None and not cell.outputs
                source = TransformerManager().transform_cell(cell.source)
                compile(source, str(path), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                tree = ast.parse(source)
                assert not any(
                    isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "asyncio" and node.func.attr == "run"
                    for node in ast.walk(tree)
                )
                assert "REPO_ROOT" not in cell.source
                assert "from cookbook" not in cell.source
                assert "git clone" not in cell.source
        assert any("Open in Colab" in c.source for c in notebook.cells)
        assert sum("install" in c.metadata.get("tags", []) for c in notebook.cells) == 1


async def execute_notebook(
    path, tmp_path, *, harness="deepagents", approve=True, durable=False,
    direct=False, start_temporal=False,
):
    if path.stem == "00_agent":
        pytest.importorskip("pydantic_ai")
        available("claude-sdk")
    elif harness in ("deepagents", "pydantic-ai"):
        pytest.importorskip(harness.replace("-", "_"))
    else:
        available(harness)
    pytest.importorskip("mcp")
    if durable:
        pytest.importorskip("temporalio")
        if start_temporal:
            if not shutil.which("temporal"):
                pytest.skip("Install Temporal CLI to test the automatic local service")
        else:
            try:
                with socket.create_connection(("127.0.0.1", 7233), timeout=0.2):
                    pass
            except OSError:
                pytest.skip("Start Temporal on localhost:7233 for notebook recovery tests")
    notebook = nbformat.read(path, as_version=4)
    for cell in notebook.cells:
        if "install" in cell.metadata.get("tags", []):
            # Dependencies are installed by CI; execute the real installer separately
            # against the built release wheel in a clean environment.
            cell.source = "import shutil\nimport sys"
        if "parameters" in cell.metadata.get("tags", []):
            cell.source += (
                f"\nAPPROVE_EDIT = {approve!r}\nDELAY_SECONDS = 3\n"
                f"RUN_CRASH_DEMO = {durable!r}\nUSE_TEMPORAL = {durable!r}\n"
            )
    # Rerun all cells in the same kernel: catches fixed run IDs and leaked resources.
    rerun = copy.deepcopy(notebook.cells)
    for cell in rerun:
        cell.id += "-rerun"
    notebook.cells += rerun
    kernel_dir = tmp_path / "kernels" / "liteagents-test"
    kernel_dir.mkdir(parents=True)
    (kernel_dir / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "LiteAgents test", "language": "python",
    }))
    manager = AsyncKernelManager(
        kernel_name="liteagents-test",
        kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(kernel_dir.parent)]),
    )
    client = NotebookClient(
        notebook, km=manager, timeout=180,
        # Run away from the checkout: no sibling scripts/fixtures may be required.
        resources={"metadata": {"path": str(tmp_path)}},
    )
    async with NotebookProvider().running() as provider:
        env = {
            **os.environ, "LITEAGENTS_API_BASE": provider.url,
            "LITELLM_API_KEY": "notebook-synthetic-key", "LITEAGENTS_MODEL": "notebook-model",
            "LITEAGENTS_HARNESS": harness, "LITEAGENTS_NOTEBOOK_WORKSPACE": str(tmp_path / "work"),
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false",
            "PYDANTIC_AI_NO_BANNER": "1", "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "LITEAGENTS_TEMPORAL_ADDRESS": "" if start_temporal else "127.0.0.1:7233",
        }
        if direct:
            env.update(
                LITEAGENTS_API_BASE="", LITEAGENTS_MODEL="openai/notebook-model",
                OPENAI_API_KEY="notebook-synthetic-key", OPENAI_API_BASE=provider.url,
            )
        try:
            executed = await client.async_execute(env=env)
        finally:
            # Passing an explicit manager makes this test responsible for kernel cleanup.
            if await manager.is_alive():
                await manager.shutdown_kernel(now=True)
        assert provider.requests
        expected_model = "gpt-5.4-mini" if path.stem == "00_agent" else "notebook-model"
        assert all(r["model"] == expected_model for r in provider.requests)
    output = "\n".join(
        out.get("text", "") for cell in executed.cells if cell.cell_type == "code"
        for out in cell.outputs
    )
    assert "notebook-synthetic-key" not in output
    if path.stem == "00_agent":
        assert output.count("READY") == 6  # First run, harness switch, new prompt; twice.
    if path.stem == "compare":
        reports = list((tmp_path / "work").rglob("results.json"))
        assert len(reports) == 2
        for report in reports:
            assert all(r["status"] == "completed" and r["tests"]["exit_code"] == 0
                       for r in json.loads(report.read_text()))
    if durable and path.stem == "06_durable":
        logs = list((tmp_path / "work").rglob("worker.log"))
        assert len(logs) == 2
        for log in logs:
            text = log.read_text()
            assert text.count("Receipt saved once") == 1
            assert text.count("Slow check started") == 2


@pytest.mark.integration
@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
async def test_notebook_runs_and_reruns_in_real_kernel(path, tmp_path):
    await execute_notebook(
        path, tmp_path, durable=path.stem == "06_durable", direct=path.stem == "00_agent",
    )


@pytest.mark.integration
async def test_notebook_approval_can_cancel(tmp_path):
    await execute_notebook(ROOT / "cookbook/recipes/04_approvals.ipynb", tmp_path, approve=False)


@pytest.mark.integration
@pytest.mark.parametrize("harness", ["pydantic-ai", "claude-sdk", "codex", "opencode-v1", "opencode-v2"])
async def test_notebook_switches_harness(harness, tmp_path):
    await execute_notebook(
        ROOT / "cookbook/recipes/10_harness_switch.ipynb", tmp_path, harness=harness,
    )


@pytest.mark.integration
async def test_notebook_switching_with_temporal(tmp_path):
    await execute_notebook(ROOT / "cookbook/recipes/10_harness_switch.ipynb", tmp_path, durable=True)


@pytest.mark.integration
@pytest.mark.parametrize("name", ["00_agent", "02_mcp", "07_model_fallback", "09_profile_files"])
async def test_notebook_direct_provider_without_gateway(name, tmp_path):
    await execute_notebook(ROOT / f"cookbook/recipes/{name}.ipynb", tmp_path, direct=True)


@pytest.mark.integration
async def test_notebook_starts_its_own_temporal_service(tmp_path):
    await execute_notebook(
        ROOT / "cookbook/recipes/06_durable.ipynb", tmp_path,
        durable=True, start_temporal=True,
    )
