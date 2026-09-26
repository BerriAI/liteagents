"""Run profiles against isolated copies and write a local comparison report."""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from liteagents import LiteAgentClient, ProfileOptions

IGNORED = {".git", ".venv", "node_modules", "__pycache__", ".liteagents", ".pytest_cache", ".env"}


def ignore_files(directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in IGNORED or name.startswith(".env.") or (Path(directory) / name).is_symlink()
    }


def snapshot(directory: Path) -> dict[str, bytes]:
    result = {}
    for path in directory.rglob("*"):
        relative = path.relative_to(directory)
        if (
            path.is_file()
            and not path.is_symlink()
            and not any(p in IGNORED or p.startswith(".env.") for p in relative.parts)
        ):
            result[str(relative)] = path.read_bytes()
    return result


def changes(before: dict[str, bytes], after: dict[str, bytes]) -> str:
    parts = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name, b""), after.get(name, b"")
        if old == new and (name in before) == (name in after):
            continue
        try:
            if len(old) + len(new) > 500_000 or b"\0" in old + new:
                raise UnicodeError
            parts.extend(
                difflib.unified_diff(
                    old.decode().splitlines(True),
                    new.decode().splitlines(True),
                    fromfile="a/" + name if name in before else "/dev/null",
                    tofile="b/" + name if name in after else "/dev/null",
                )
            )
        except UnicodeError:
            parts.append(f"Binary/large file changed: {name}\n")
        if not old and not new:
            parts.append(f"Empty file {'created' if name in after else 'deleted'}: {name}\n")
    return "".join(parts)


def secrets(profile: ProfileOptions) -> list[str]:
    values = []

    def visit(value: Any, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(
                    item,
                    sensitive
                    or any(
                        word in key.lower()
                        for word in ("key", "token", "password", "authorization", "env", "headers")
                    ),
                )
        elif isinstance(value, list):
            for item in value:
                visit(item, sensitive)
        elif sensitive and isinstance(value, str) and len(value) >= 4:
            values.append(value)

    visit(profile.model_dump())
    return sorted(set(values), key=len, reverse=True)


def redact(text: str, values: list[str]) -> str:
    for value in values:
        text = text.replace(value, "[REDACTED]")
    return text


async def run_tests(command: list[str], cwd: Path, timeout: float) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *command, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
        return {
            "exit_code": process.returncode,
            "output": output.decode(errors="replace")[-100000:],
        }
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


async def compare(
    profiles: list[Path],
    workspace: Path,
    output: Path,
    prompt: str,
    test_command: list[str],
    timeout: float = 180,
) -> list[dict[str, Any]]:
    workspace, output = workspace.resolve(), output.resolve()
    if not workspace.is_dir():
        raise ValueError("Workspace must be an existing directory")
    if output.is_relative_to(workspace):
        raise ValueError("Output must be outside the input workspace")
    loaded = [
        (
            path,
            ProfileOptions.from_json(path)
            if path.suffix == ".json"
            else ProfileOptions.from_yaml(path),
        )
        for path in profiles
    ]
    if any(profile.temporal for _, profile in loaded):
        raise ValueError(
            "The comparison uses direct profiles so each run owns its isolated workspace"
        )
    output.mkdir(parents=True, exist_ok=False)
    results = []
    for index, (path, profile) in enumerate(loaded, start=1):
        name = f"{index:02d}-{profile.harness}"
        run_dir = output / name
        run_dir.mkdir()
        work = run_dir / "workspace"
        shutil.copytree(workspace, work, ignore=ignore_files)
        before = snapshot(work)
        sensitive = secrets(profile)
        result: dict[str, Any] = {
            "name": name,
            "harness": profile.harness,
            "profile_file": path.name,
            "status": "failed",
            "text": "",
            "usage": None,
        }
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                async with LiteAgentClient(profile=profile, cwd=work) as client:
                    _ = [event async for event in client.query(prompt, run_id=name)]
                    run = await (await client.get_run(name)).result()
                    result.update(status="completed", text=run.text, usage=run.usage or None)
        except Exception as exc:  # noqa: BLE001 - report each independent comparison.
            result["error"] = f"{type(exc).__name__}: {exc}"
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        try:
            result["tests"] = await run_tests(test_command, work, min(timeout, 120))
        except Exception as exc:  # noqa: BLE001 - preserve a test failure in the report.
            result["tests"] = {"exit_code": None, "error": f"{type(exc).__name__}: {exc}"}
        (run_dir / "changes.diff").write_text(redact(changes(before, snapshot(work)), sensitive))
        result = json.loads(redact(json.dumps(result), sensitive))
        (run_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        results.append(result)
        print(
            f"{name}: {result['status']}; tests={result['tests']['exit_code']}; {result['duration_seconds']}s",
            flush=True,
        )
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    lines = [
        "# Harness comparison\n",
        "| Harness | Run | Tests | Seconds | Usage |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for result in results:
        tests = result["tests"]["exit_code"]
        usage = "Reported" if result["usage"] else "Unavailable"
        lines.append(
            f"| {result['name']} | {result['status']} | {'Passed' if tests == 0 else 'Failed' if tests is not None else 'Unavailable'} | {result['duration_seconds']} | {usage} |"
        )
    lines += [
        "",
        "Each folder contains the workspace, changes.diff, and result.json with the answer and test output.",
        "Timing includes startup. Model choices and native tools differ; this is a functional comparison, not a controlled benchmark.",
    ]
    (output / "README.md").write_text("\n".join(lines) + "\n")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="+", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).with_name("fixture"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--prompt",
        default="Read calculator.py and test_calculator.py. Fix the bug in calculator.py without changing the tests, then run the tests. Summarize the change.",
    )
    parser.add_argument(
        "--test-command",
        default=json.dumps([sys.executable, "-m", "unittest", "-v"]),
        help="JSON array of executable and arguments; no shell",
    )
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    command = json.loads(args.test_command)
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        parser.error("--test-command must be a nonempty JSON array of strings")
    results = asyncio.run(
        compare(args.profiles, args.workspace, args.output, args.prompt, command, args.timeout)
    )
    raise SystemExit(
        0
        if all(r["status"] == "completed" and r["tests"]["exit_code"] == 0 for r in results)
        else 1
    )


if __name__ == "__main__":
    main()
