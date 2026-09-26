"""Local Chat Completions provider for executing cookbook notebooks without keys.

Only model decisions are scripted. Notebooks still use their real kernel, SDK,
DeepAgents/Pydantic AI loop, file tools, MCP server, and Temporal worker.
"""

import json
import sys
from contextlib import asynccontextmanager
from uuid import uuid4

from aiohttp import web


class NotebookProvider:
    def __init__(self):
        self.requests = []

    def decision(self, data):
        messages = data["messages"]
        history = json.dumps(messages)
        canonical = sorted([
            "read_file", "edit_file", "run_tests", "delegate_auditor", "save_receipt",
            "slow_check", "unstable_lookup", "lookup_order", "orders_lookup_order", "shipping_status",
        ], key=len, reverse=True)
        names = {}
        for tool in data.get("tools", []):
            native = tool["function"]["name"]
            name = next((name for name in canonical if native.endswith(name)), None)
            if name:
                names[name] = native
        calls = [call for message in messages for call in message.get("tool_calls", [])]
        plan = []
        text = "Order A123: paid, total USD 12. Tracking code COBALT-42. receipt-verified."
        if "fallback-ready" in history:
            text = "fallback-ready"
        elif not names:
            text = "READY"
        elif "run_tests" in names:
            plan = [
                ("read_file", {"path": "calculator.py"}),
                ("read_file", {"path": "test_calculator.py"}),
                ("edit_file", {"path": "calculator.py", "old": "left + right",
                               "new": "left - right"}),
                ("run_tests", {"command": [sys.executable, "-m", "unittest", "-v"]}),
            ]
            text = "Fixed subtraction and ran the tests."
        elif "delegate_auditor" in names:
            plan = [("delegate_auditor", {"prompt": "Read receipt.txt and verify order A123."})]
        elif "save_receipt" in names:
            plan = [("save_receipt", {}), ("slow_check", {})]
        elif "unstable_lookup" in names:
            plan = [("unstable_lookup", {})]
        elif "edit_file" in names:
            plan = [
                ("read_file", {"path": "status.txt"}),
                ("edit_file", {"path": "status.txt", "old": "status=pending",
                               "new": "status=approved"}),
            ]
        elif "lookup_order" in names or "orders_lookup_order" in names:
            name = "lookup_order" if "lookup_order" in names else "orders_lookup_order"
            plan = [(name, {"order_id": "A123"})]
            if "shipping_status" in names:
                plan.append(("shipping_status", {"order_id": "A123"}))
        elif "read_file" in names:
            path = "receipt.txt" if "receipt.txt" in history else "facts.txt"
            plan = [("read_file", {"path": path})]
        call = None
        if len(calls) < len(plan):
            name, arguments = plan[len(calls)]
            call = names[name], arguments
        return call, text

    async def reply(self, request):
        data = await request.json()
        self.requests.append(data)
        call, text = self.decision(data)
        identifier = "notebook-" + uuid4().hex
        message = {"role": "assistant", "content": text}
        if call:
            name, arguments = call
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-" + uuid4().hex, "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]}
        finish = "tool_calls" if call else "stop"
        response = {
            "id": identifier, "object": "chat.completion", "created": 1,
            "model": data["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        if not data.get("stream"):
            return web.json_response(response)
        delta = (
            {"tool_calls": [{"index": 0, **message["tool_calls"][0]}]}
            if call else {"content": text}
        )
        frames = [
            {**response, "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": delta, "finish_reason": None},
            ]},
            {**response, "object": "chat.completion.chunk", "choices": [
                {"index": 0, "delta": {}, "finish_reason": finish},
            ]},
        ]
        body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        return web.Response(text=body, content_type="text/event-stream")

    @asynccontextmanager
    async def running(self):
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.reply)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            self.url = f"http://127.0.0.1:{runner.addresses[0][1]}/v1"
            yield self
        finally:
            await runner.cleanup()
