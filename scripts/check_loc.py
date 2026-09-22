#!/usr/bin/env python3
"""Fails if any src/liteagents/*.py file exceeds 500 non-blank lines. Run in CI."""
from __future__ import annotations

import sys
from pathlib import Path

LIMIT = 500
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "liteagents"


def count_nonblank_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def main() -> int:
    if not SRC_ROOT.is_dir():
        print(f"error: source root not found: {SRC_ROOT}", file=sys.stderr)
        return 1

    offenders = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        count = count_nonblank_lines(path)
        if count > LIMIT:
            offenders.append((path, count))

    if offenders:
        print(f"Files exceeding the {LIMIT}-line limit:", file=sys.stderr)
        for path, count in offenders:
            print(f"  {path.relative_to(REPO_ROOT)}: {count} lines", file=sys.stderr)
        return 1

    print(f"OK: all files under {LIMIT} non-blank lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
