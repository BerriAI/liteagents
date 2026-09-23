"""Live Anthropic-compatible gateway boundary with a persistent spend ceiling.

Only this experiment replaces litellm.anthropic_messages. The library itself
continues to call LiteLLM. Credentials are read from a caller-owned private file.
"""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
import time
import uuid
from pathlib import Path

import httpx

MAIN = "openai/gpt-6-astra"
MEMORY = "openai/gpt-5.6-luna"
# Snapshot of the user gateway's /model/info, 2026-09-23 UTC, dollars/token.
RATES = {MAIN: (0.000010, 0.000050, 0.000001), MEMORY: (0.0000002, 0.0000012, 0.00000002)}


class BudgetExceeded(RuntimeError):
    pass


class Gateway:
    def __init__(self, root: Path, *, budget: float = 190.0):
        self.root = root
        self.budget = min(budget, 190.0)  # leave headroom below the user's $200 ceiling
        self.ledger_path = root / "ledger.json"
        self.ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else []
        self.lock = asyncio.Lock()
        self.client = httpx.AsyncClient(
            base_url="https://gateway.litellm-sandbox.ai",
            headers={"Authorization": "Bearer " + (root / "gateway.key").read_text().strip(),
                     "anthropic-version": "2023-06-01"},
            timeout=180,
        )
        self.label = "probe"
        self.fail_memory = False
        self.layout = "prefix"
        self.stable_notes = False

    def write(self):
        temp = self.ledger_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.ledger, indent=2))
        temp.replace(self.ledger_path)

    @property
    def committed(self):
        return sum(r.get("charged_or_reserved", 0) for r in self.ledger)

    async def __call__(self, **kwargs):
        model = kwargs["model"].removeprefix("litellm_proxy/")
        if model not in RATES:
            raise ValueError("This experiment only authorizes Astra and Luna")
        role = "main" if (kwargs.get("system") or "").startswith("Complete the user's tasks") else "observer"
        if role == "observer" and self.fail_memory:
            self.fail_memory = False
            raise ConnectionError("Injected observer interruption; no paid request sent")
        payload = {k: kwargs[k] for k in ("messages", "system", "max_tokens", "tools", "tool_choice")
                   if kwargs.get(k) is not None}
        payload["model"] = model
        payload["stream"] = False
        if role == "main" and (self.stable_notes or self.layout != "prefix"):
            messages = deepcopy(payload["messages"])
            if messages and isinstance(messages[0].get("content"), str) and messages[0]["content"].startswith("Summary of earlier conversation"):
                note = messages[0]
                if self.stable_notes:
                    note["content"] = re.sub(r"Working memory version [^\n]*\n\n", "Working notes from prior conversation:\n\n", note["content"])
                if self.layout == "before_current_request":
                    messages = messages[1:]
                    current = max((i for i, m in enumerate(messages) if m["role"] == "user"
                                   and isinstance(m["content"], str)), default=0)
                    messages.insert(current, note)
                payload["messages"] = messages
        # Keep reasoning settings equal across all modes and tasks.
        payload["reasoning_effort"] = "low"
        serialized_bytes = len(json.dumps(payload).encode())
        input_rate, output_rate, cache_rate = RATES[model]
        reserve = ((serialized_bytes + 4096) * input_rate * 2
                   + payload["max_tokens"] * output_rate * 1.5)
        record = {"id": str(uuid.uuid4()), "label": self.label, "model": model, "role": role,
                  "charged_or_reserved": reserve, "reserve": reserve, "status": "pending",
                  "request_bytes": serialized_bytes, "max_tokens": payload["max_tokens"]}
        async with self.lock:
            if self.committed + reserve > self.budget:
                raise BudgetExceeded(f"Research spend ceiling reached: ${self.committed:.2f}")
            self.ledger.append(record)
            self.write()
        start = time.monotonic()
        try:
            response = await self.client.post("/v1/messages", json=payload)
            record["http_status"] = response.status_code
            if response.status_code != 200:
                # Never put request headers or raw credentials in error reports.
                detail = response.json().get("error", {})
                record["error_type"] = detail.get("type") if isinstance(detail, dict) else "gateway_error"
                raise RuntimeError(f"Gateway HTTP {response.status_code}: {record['error_type']}")
            data = response.json()
            record["stop_reason"] = data.get("stop_reason")
            record["tool_names"] = [b.get("name") for b in data.get("content", []) if b.get("type") == "tool_use"]
            usage = data.get("usage", {})
            record["usage"] = usage
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_tokens = usage.get("cache_read_input_tokens", 0)
            creation_tokens = usage.get("cache_creation_input_tokens", 0)
            record["estimated_cost"] = (input_tokens * input_rate + output_tokens * output_rate
                                        + cache_tokens * cache_rate + creation_tokens * input_rate * 1.25)
            record["header_cost"] = response.headers.get("x-litellm-response-cost")
            actual = float(record["header_cost"]) if record["header_cost"] is not None else record["estimated_cost"]
            if not usage or actual <= 0:
                actual = reserve  # missing accounting must never create free budget
            record["charged_or_reserved"] = actual
            record["status"] = "completed"
            return data
        except BaseException as exc:
            record["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            # Keep the full reservation after an ambiguous/partially billed request.
            raise
        finally:
            record["seconds"] = time.monotonic() - start
            async with self.lock:
                self.write()

    async def close(self):
        await self.client.aclose()
