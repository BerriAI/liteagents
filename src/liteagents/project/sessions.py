"""Continue each chat conversation across fresh clients and process restarts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..runtime.conversation import dump_history, load_history

# These adapters reopen their own native session; the others replay shared history.
NATIVE_RESUME = frozenset({"claude-sdk", "codex", "opencode-v1", "opencode-v2"})


class SessionStore:
    """Maps a conversation ID to its native session (or history) in one JSON file.

    A stored conversation is ignored after the harness changes; native sessions
    do not move between frameworks.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def client_options(self, conversation_id: str, harness: str) -> dict[str, Any]:
        """Keyword arguments for LiteAgentClient that continue this conversation."""
        entry = self._load().get(conversation_id)
        if not entry or entry.get("harness") != harness:
            return {}
        if harness in NATIVE_RESUME:
            return {"session_id": entry["session_id"]} if entry.get("session_id") else {}
        return {"history": load_history(entry.get("history", []))}

    def save(self, conversation_id: str, harness: str, client: Any) -> None:
        entry: dict[str, Any] = {"harness": harness}
        if harness in NATIVE_RESUME:
            entry["session_id"] = client.session_id
        else:
            entry["history"] = dump_history(client.history)
        data = {**self._load(), conversation_id: entry}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True))
        temporary.replace(self.path)
