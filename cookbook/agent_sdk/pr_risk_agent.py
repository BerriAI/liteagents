"""CLI: gh pr diff 123 | python -m cookbook.agent_sdk.pr_risk_agent --title "..."
Reads the diff from stdin, classifies it with PRRiskAgent, prints the result.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from liteagents.legacy.pr_risk_agent import PRRiskAgent, PullRequest


async def _run(title: str, model: str) -> None:
    diff = sys.stdin.read()
    agent = PRRiskAgent(model=model)
    assessment = await agent.classify(PullRequest(title=title, diff=diff))

    print(f"risk: {assessment.risk}")
    print("reasons:")
    for reason in assessment.reasons:
        print(f"  - {reason}")
    print("recommended checks:")
    for check in assessment.recommended_checks:
        print(f"  - {check}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify a pull request's deployment risk.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--model", default="anthropic/claude-opus-4-8")
    args = parser.parse_args()
    asyncio.run(_run(args.title, args.model))


if __name__ == "__main__":
    main()
