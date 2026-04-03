"""DTE 래퍼 도구 — vs_command (ExecuteCommand)"""
from __future__ import annotations

import asyncio
import logging
import time

from .. import crash_logger
from .. import session_manager as sm

logger = logging.getLogger(__name__)


def _get_sta(session_id: str):
    return sm.get_manager().require_sta(session_id)


async def vs_command(
    *,
    session_id: str,
    command: str,
    args: str = "",
) -> dict:
    """dte.ExecuteCommand()를 실행한다. 6000+ VS 명령에 접근 가능.

    Args:
        session_id: 세션 식별자
        command: VS 명령 이름 (예: "Edit.FormatDocument", "File.SaveAll")
        args: 명령 인수 문자열 (선택)

    Returns:
        {"command": str, "args": str, "status": "executed"|"error", "message": str}
    """
    sta = _get_sta(session_id)

    def _fn():
        t0 = time.monotonic()
        try:
            sta.dte.ExecuteCommand(command, args)
            duration_ms = (time.monotonic() - t0) * 1000
            crash_logger.log_event(
                command="vs_command",
                instance_pid=sta.instance_pid,
                session_id=session_id,
                result="ok",
                duration_ms=duration_ms,
                extra={"vs_command": command, "vs_args": args},
            )
            return {"command": command, "args": args, "status": "executed", "message": ""}
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            crash_logger.log_com_error(
                command="vs_command",
                instance_pid=sta.instance_pid,
                session_id=session_id,
                exc=exc,
                duration_ms=duration_ms,
            )
            raise

    loop = asyncio.get_running_loop()
    return await sta.submit("vs_command", _fn, session_id=session_id, loop=loop)

