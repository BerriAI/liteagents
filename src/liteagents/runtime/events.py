from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..runs import RunResult
from ..types import AgentEvent, TextDelta
from .serialization import dump_result, load_result


@dataclass(frozen=True)
class RunEvent:
    cursor: int
    kind: str
    data: dict[str, Any]

    @property
    def message(self) -> AgentEvent | None:
        if self.kind == "message":
            return load_result(
                {"run_id": "", "harness": "", "messages": [self.data["message"]]}
            ).messages[0]
        if self.kind == "text_delta" and self.data.get("scope", "root") == "root":
            return TextDelta(self.data["text"], self.data["model"])
        return None


def payload(event: AgentEvent) -> dict[str, Any]:
    if isinstance(event, TextDelta):
        return {"kind": "text_delta", **asdict(event)}
    return {"kind": "message", "message": dump_result(RunResult("", "", [event]))["messages"][0]}


def envelope(row: dict[str, Any]) -> RunEvent:
    copy = dict(row)
    return RunEvent(copy.pop("cursor"), copy.pop("kind"), copy)
