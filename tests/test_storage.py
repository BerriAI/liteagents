import asyncio
from uuid import uuid4

import pytest

from liteagents.errors import ConfigurationError, RunNotFoundError
from liteagents.storage import RunStore
from liteagents.storage.ownership import own_run


@pytest.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "postgres":
        import os

        url = os.environ.get("LITEAGENTS_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Set LITEAGENTS_TEST_POSTGRES_URL for shared-storage coverage")
    else:
        url = "sqlite:///" + str(tmp_path / "state.sqlite")
    store = RunStore(url, max_events=3)
    await store.setup()
    return store


async def test_store_replay_events_approval_ownership_and_retention(store):
    key = "test-" + uuid4().hex
    await store.ensure_run(key, "profile-v1")
    await store.put(key, "model:1", {"response": "hello"})
    assert await store.get(key, "model:1") == {"response": "hello"}
    with pytest.raises(ConfigurationError, match="version"):
        await store.ensure_run(key, "profile-v2")
    await store.event(key, "one", {"kind": "text", "text": "one"})
    await store.event(key, "one", {"kind": "text", "text": "duplicate"})
    await store.event(key, "two", {"kind": "text", "text": "two"})
    events = await store.events(key)
    assert [e["text"] for e in events] == ["one", "two"]
    assert (await store.events(key, events[0]["cursor"]))[0]["text"] == "two"
    await store.put(key, "approval:edit-1", {"id": "edit-1", "tool": "edit"})
    assert await store.pending_approvals(key) == [{"id": "edit-1", "tool": "edit"}]
    await store.approve(key, "edit-1", True)
    await store.approve(key, "edit-1", True)
    with pytest.raises(ConfigurationError, match="different"):
        await store.approve(key, "edit-1", False)
    assert await store.pending_approvals(key) == []
    async with own_run(store, key) as check:
        await check()
        with pytest.raises(ConfigurationError, match="owns"):
            async with own_run(store, key):
                pytest.fail("duplicate ownership")
    async with own_run(store, key) as check:
        await check()
    await store.mark_tool(key)
    assert (await store.get_run(key))["tool_started"]
    await store.cancel(key)
    assert (await store.get_run(key))["cancelled"]
    await store.set_status(key, "completed", result={"answer": "done"})
    assert (await store.get_run(key))["result"] == {"answer": "done"}
    await store.purge(0)
    with pytest.raises(RunNotFoundError):
        await store.get_run(key)
    assert await store.events(key) == []


async def test_limits_and_atomic_approval(store):
    key = "limits-" + uuid4().hex
    await store.ensure_run(key, "p1")
    for i in range(3):
        await store.event(key, str(i), {"i": i})
    await store.event(key, "0", {"i": 0})
    with pytest.raises(ConfigurationError, match="event limit"):
        await store.event(key, "four", {})
    with pytest.raises(ConfigurationError, match="No such"):
        await store.approve(key, "unknown", True)
    await store.put(key, "approval:a", {"id": "a"})
    results = await asyncio.gather(
        store.approve(key, "a", True), store.approve(key, "a", False), return_exceptions=True
    )
    assert sum(isinstance(r, ConfigurationError) for r in results) == 1
    await store.set_status(key, "failed")
    await store.purge(0)


async def test_concurrent_event_bound_is_atomic(store):
    key = "parallel-" + uuid4().hex
    await store.ensure_run(key, "p1")
    results = await asyncio.gather(
        *(store.event(key, str(i), {"kind": "test", "i": i}) for i in range(12)),
        return_exceptions=True,
    )
    assert len(await store.events(key)) == 3
    assert sum(isinstance(result, ConfigurationError) for result in results) == 9
    await store.set_status(key, "completed")
    await store.purge(0)


async def test_lost_postgres_owner_cannot_continue(store):
    import hashlib

    if not store.db.postgres:
        pytest.skip("PostgreSQL connection ownership")
    key = "owner-loss-" + uuid4().hex
    identifier = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    async with own_run(store, key) as check:
        await check()
        rows = await store.db.execute(
            "SELECT pid FROM pg_locks WHERE locktype='advisory' AND classid=?::oid AND objid=?::oid",
            identifier >> 32,
            identifier & 0xFFFFFFFF,
        )
        assert len(rows) == 1
        await store.db.execute("SELECT pg_terminate_backend(?)", rows[0][0])
        with pytest.raises(ConnectionError, match="ownership connection was lost"):
            await check()
    async with own_run(store, key) as replacement:
        await replacement()
