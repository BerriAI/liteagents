from pathlib import Path

from liteagents.project import SessionStore

sessions = SessionStore(Path(__file__).resolve().parents[2] / ".liteagents" / "sessions.json")
