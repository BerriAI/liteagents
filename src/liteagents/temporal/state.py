"""Resolve durable state consistently on application clients and workers."""

from pathlib import Path

from ..profiles import ProfileOptions
from ..storage import RunStore
from ..storage.store import digest


def run_key(profile: ProfileOptions, run_id: str) -> str:
    assert profile.temporal is not None
    return digest([profile.temporal.namespace, run_id])


def run_store(profile: ProfileOptions, cwd: Path) -> RunStore:
    options = profile.temporal
    assert options is not None
    url = options.state_url
    if url is None:
        if options.checkpoint_url:
            url = options.checkpoint_url
        else:
            path = Path(options.checkpoint_path)
            path = path if path.is_absolute() else cwd / path
            url = "sqlite:///" + str(path) + ".runs"
    return RunStore(url, cwd, max_events=options.max_events, max_bytes=options.max_payload_bytes)
