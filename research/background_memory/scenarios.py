"""Deterministic workloads with independent oracles and disposable in-memory tools."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from liteagents import Tool


@dataclass
class Step:
    prompt: str
    expected: dict | None = None


@dataclass
class Scenario:
    name: str
    steps: list[Step]
    tools: list[Tool] = field(default_factory=list)
    interrupted_step: int | None = None
    failed_memory_step: int | None = None


def noise(seed: int, lines: int) -> str:
    rng = random.Random(seed)
    return "\n".join(
        f"trace={rng.randrange(10**8):08d} worker={rng.randrange(80)} "
        f"queue={rng.randrange(900)} elapsed_ms={rng.randrange(2000)} "
        f"artifact=build-{rng.randrange(10**6)} status=sampled"
        for _ in range(lines)
    )


def make_release(seed: int, long: bool, *, heldout: bool = False, scale: int = 1) -> Scenario:
    rng = random.Random(seed)
    owner, region = rng.choice(["Mina", "Luis", "Asha"]), rng.choice(["eu-west-2", "ap-south-1"])
    token = f"restore-{rng.randrange(10**9):09d}"
    steps = [Step(
        f"Help maintain a release plan across this conversation. Owner={owner}; region={region}; "
        f"retries=3; deadline=Friday 16:00 UTC; rollback token={token}; storage=Postgres. "
        "Remember exact operational details. Acknowledge briefly."
    )]
    count = 22 * scale if long else 5
    for i in range(count):
        change = "Reference telemetry only; this does not change the release plan."
        if i == 1:
            change = "Propose Redis as storage but do not approve or apply that proposal."
        if i == count // 2:
            owner = "Nadia" if not heldout else "Hector"
            change = f"Correction: release owner is now {owner} and retries=5. Prior owner is superseded."
        if i == count - 2:
            change = "Interrupt the Redis proposal: reject it, keep the original storage choice."
        steps.append(Step(change + " Acknowledge in one sentence.\n" + noise(seed + i, 65 if long else 2)))
    expected = {"owner": owner, "region": region, "retries": 5, "deadline": "Friday 16:00 UTC",
                "rollback_token": token, "storage": "Postgres"}
    steps.append(Step("Give the CURRENT release plan as JSON with keys owner, region, retries, "
                      "deadline, rollback_token, storage. Recover exact earlier details if needed.", expected))
    return Scenario(f"release_{'long' if long else 'short'}_{seed}", steps,
                    interrupted_step=3 if heldout else None, failed_memory_step=4 if heldout else None)


def make_inventory(seed: int, long: bool, *, scale: int = 1) -> Scenario:
    rng = random.Random(seed)
    north, south = rng.randrange(100, 200), rng.randrange(60, 90)
    steps = [Step(f"Track stock movements. Initially north={north}, south={south}. "
                  "Only explicit stock instructions change inventory; telemetry is unrelated. "
                  "Acknowledge each update briefly unless asked for JSON.")]
    for i in range(24 * scale if long else 6):
        amount = rng.randrange(1, 8)
        if i % 3 == 0:
            north -= amount
            south += amount
            instruction = f"Transfer {amount} units from north to south."
        elif i % 3 == 1:
            north += amount
            instruction = f"Receive {amount} new units at north."
        else:
            south -= amount
            instruction = f"Ship {amount} units from south."
        expected = None
        if i % 6 == 5:
            instruction += " Return current counts as JSON with keys north and south."
            expected = {"north": north, "south": south}
        steps.append(Step(instruction + "\nUnrelated telemetry:\n" + noise(seed + i, 50 if long else 0), expected))
    steps.append(Step("Return the final counts as JSON with keys north and south.", {"north": north, "south": south}))
    return Scenario(f"inventory_{'long' if long else 'short'}_{seed}", steps)


class ReadRecord(Tool):
    name = "read_record"
    description = "Read an incident record and its diagnostic log."
    input_schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}

    def __init__(self, records):
        self.records = records

    async def execute(self, input):
        return json.dumps(self.records[input["id"]])


class SetStatus(Tool):
    name = "set_status"
    description = "Set an incident's status. This changes a disposable in-memory fixture only."
    input_schema = {"type": "object", "properties": {"id": {"type": "string"}, "status": {"type": "string"}},
                    "required": ["id", "status"]}

    def __init__(self, records):
        self.records = records
        self.writes = []

    async def execute(self, input):
        self.records[input["id"]]["status"] = input["status"]
        self.writes.append(dict(input))
        return json.dumps({"updated": input["id"], "status": input["status"]})


def make_incidents(seed: int, long: bool, *, scale: int = 1) -> Scenario:
    rng = random.Random(seed)
    count = 12 * scale if long else 3
    records = {f"INC-{seed}-{i}": {
        "id": f"INC-{seed}-{i}", "status": "open", "owner": rng.choice(["Priya", "Sol", "Eli"]),
        "restore_code": f"key-{rng.randrange(10**8):08d}", "service": f"svc-{i % 4}",
        "log": noise(seed + i, 100 if long else 3),
    } for i in range(count)}
    keys = list(records)
    expected = {"first_owner": records[keys[0]]["owner"], "first_restore_code": records[keys[0]]["restore_code"],
                "last_service": records[keys[-1]]["service"], "reviewed_count": count}
    steps = [Step(f"Review incident {key}: read its record, then set its status to reviewed. "
                  "Report completion briefly. Track the review sequence and operational details.") for key in keys]
    steps.append(Step("Using the incidents reviewed in this conversation, return JSON with first_owner, "
                      "first_restore_code, last_service, reviewed_count. First/last refer to review order.", expected))
    return Scenario(f"incidents_{'long' if long else 'short'}_{seed}", steps,
                    [ReadRecord(records), SetStatus(records)])


def make_lookup(seed: int, long: bool, *, scale: int = 1) -> Scenario:
    rng = random.Random(seed)
    steps = [Step("Help maintain onboarding notes from the reference catalogs I send. "
                  "Acknowledge each catalog briefly; there is no need to repeat all records.")]
    target, expected = "", {}
    for i in range(18 * scale if long else 4):
        lines = []
        for j in range(60 if long else 8):
            name = f"svc-{rng.randrange(10**8):08d}"
            timeout, code = rng.randrange(20, 400), f"E{rng.randrange(10**6):06d}"
            path = f"/leases/v{rng.randrange(2, 9)}/{rng.randrange(10**7):07d}"
            lines.append(f"service={name} timeout_ms={timeout} endpoint={path} error_code={code}")
            if i == 1 and j == 3:
                target = name
                expected = {"original_timeout_ms": timeout, "original_endpoint": path,
                            "original_error_code": code, "current_timeout_ms": timeout + 73}
        steps.append(Step(f"Reference catalog {i + 1}:\n" + "\n".join(lines)))
    steps.append(Step(f"Correction to runtime configuration: {target} now uses timeout_ms="
                      f"{expected['current_timeout_ms']}. The original catalog remains a historical record."))
    steps.append(Step(f"For {target}, recover its ORIGINAL catalog 2 timeout, endpoint and error code, "
                      "and its CURRENT timeout. Return JSON keys original_timeout_ms, original_endpoint, "
                      "original_error_code, current_timeout_ms. Exact values required.", expected))
    return Scenario(f"lookup_{'long' if long else 'short'}_{seed}", steps)


def scenarios(seed: int, selected: str = "all", heldout: bool = False, scale: int = 1):
    factories = {"release": make_release, "inventory": make_inventory,
                 "incidents": make_incidents, "lookup": make_lookup}
    for family, factory in factories.items():
        for long in [False, True]:
            if selected != "all" and selected != f"{family}_{'long' if long else 'short'}":
                continue
            extra = {"heldout": heldout} if family == "release" else {}
            yield factory(seed, long, scale=scale, **extra)
