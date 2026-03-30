"""python -m vs_mcp_server 진입점"""
import asyncio

from vs_mcp_server.server import main


def _entry() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    _entry()
