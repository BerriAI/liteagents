"""Side-effect markers shared by native-loop and worker-crash tests."""

import asyncio

from liteagents import Tool


class FixtureTool(Tool):
    def __init__(self, name, marker):
        self.name = name
        self.marker = marker
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}

    def mark(self, text):
        with self.marker.open("a") as stream:
            stream.write(text + "\n")

    async def execute(self, input):
        if self.name == "lookup":
            self.mark("lookup")
            return "USD 12"
        self.mark("slow-start")
        await asyncio.sleep(6)
        self.mark("slow-done")
        return "Validated USD 12"
