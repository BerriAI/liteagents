import asyncio
import logging
import sys

from dotenv import load_dotenv

from liteagents.project import run_crons

from .channels import load_channel
from .core import ROOT, load_config, run_turn
from .crons import CRONS


async def serve(channel: str) -> None:
    crons = asyncio.create_task(run_crons(CRONS, run_turn))
    try:
        await load_channel(channel)()
    finally:
        crons.cancel()


def main(channel: str | None = None) -> None:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(serve(channel or load_config().channel))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
