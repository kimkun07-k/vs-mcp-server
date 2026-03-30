"""인스턴스 관리 도구

vs_list_instances, vs_connect, vs_launch, vs_close
"""
from __future__ import annotations

import logging
from typing import Optional

from .. import com_bridge
from .. import config
from .. import session_manager as sm
from .. import vs_instance_manager as vim

logger = logging.getLogger(__name__)


def vs_list_instances() -> dict:
    """ROT에서 실행 중인 VS 2022 인스턴스 목록을 반환한다.

    Returns:
        {"instances": [{"pid": int, "solution_path": str, "connected": bool}]}
    """
    instances = vim.list_instances()
    return {"instances": instances, "count": len(instances)}


def vs_connect(
    *,
    session_id: str,
    pid: Optional[int] = None,
    solution_path: Optional[str] = None,
) -> dict:
    """지정한 PID 또는 솔루션 경로로 DTE를 획득하고 세션에 바인딩한다.

    Args:
        session_id: Claude Code 세션 식별자
        pid: VS 프로세스 PID (pid 또는 solution_path 중 하나 필수)
        solution_path: .sln 파일 경로

    Returns:
        {"session_id": str, "instance_pid": int, "solution_path": str}
    """
    if pid is None and solution_path is None:
        raise ValueError("pid 또는 solution_path 중 하나는 필수입니다.")

    import pythoncom
    pythoncom.CoInitialize()
    try:
        if pid is not None:
            dte = vim.get_dte_by_pid(pid)
            if dte is None:
                raise RuntimeError(f"PID {pid}에 해당하는 VS 인스턴스를 ROT에서 찾을 수 없습니다.")
        else:
            dte = vim.get_dte_by_solution(solution_path)
            if dte is None:
                raise RuntimeError(f"솔루션 '{solution_path}'을 연 VS 인스턴스를 ROT에서 찾을 수 없습니다.")

        from utils.rot import get_vs_pid
        actual_pid = get_vs_pid(dte) or pid or 0
        try:
            sol = dte.Solution.FullName or solution_path or ""
        except Exception:
            sol = solution_path or ""

        manager = sm.get_manager()
        manager.bind_instance(session_id, actual_pid, dte)

        return {
            "session_id": session_id,
            "instance_pid": actual_pid,
            "solution_path": sol,
            "status": "connected",
        }
    finally:
        pythoncom.CoUninitialize()


def vs_launch(
    *,
    session_id: str,
    solution_path: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    """devenv.exe를 실행하고 ROT 폴링으로 자동 연결한다.

    Args:
        session_id: Claude Code 세션 식별자
        solution_path: 열 솔루션 경로 (없으면 빈 VS 실행)
        timeout: 최대 대기 시간(초). 기본값 config.timeouts["launch"]

    Returns:
        {"session_id": str, "instance_pid": int, "solution_path": str}
    """
    result = vim.launch_vs(solution_path=solution_path, timeout=timeout)
    dte = result["dte"]
    pid = result["pid"]
    sol = result["solution_path"]

    manager = sm.get_manager()
    manager.bind_instance(session_id, pid, dte)

    return {
        "session_id": session_id,
        "instance_pid": pid,
        "solution_path": sol,
        "status": "launched",
    }


async def vs_close(
    *,
    session_id: str,
    save_all: bool = True,
) -> dict:
    """VS 인스턴스를 종료한다.

    Args:
        session_id: 종료할 인스턴스에 연결된 세션 ID
        save_all: True면 종료 전 Documents.SaveAll() 호출

    Returns:
        {"status": "closed", "instance_pid": int}
    """
    import asyncio

    manager = sm.get_manager()
    sta = manager.require_sta(session_id)
    session = manager.get_session(session_id)
    pid = session.instance_pid if session else None

    def _close():
        dte = sta.dte
        if save_all:
            try:
                dte.Documents.SaveAll()
            except Exception as e:
                logger.warning("SaveAll 실패: %s", e)
        dte.Quit()

    await sta.submit("vs_close", _close, session_id=session_id, loop=asyncio.get_running_loop())

    if pid:
        com_bridge.remove_sta(pid)
    manager.unbind_instance(session_id)
    return {"status": "closed", "instance_pid": pid}
