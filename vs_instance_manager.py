"""VS Instance Manager — ROT 탐색, DTE 획득, devenv.exe 실행

COM 호출은 반드시 STA 스레드에서 수행해야 하므로,
이 모듈의 함수들은 com_bridge.STAThread.submit()을 통해 호출하거나
전용 STA 스레드 내부에서만 호출해야 한다.

단, list_instances() / launch_vs() 처럼 아직 STA 스레드가 없는 시점에
호출해야 하는 함수들은 임시 STA 컨텍스트를 자체적으로 초기화한다.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

import config
import crash_logger
from utils.rot import find_vs_instances, get_vs_pid

logger = logging.getLogger(__name__)

def list_instances() -> list[dict]:
    """ROT에서 실행 중인 VS 2022 인스턴스 목록을 반환한다.

    Returns:
        list of dicts: pid, solution_path, connected, moniker_name
    """
    import pythoncom
    pythoncom.CoInitialize()
    try:
        entries = find_vs_instances(config.VS_PROG_ID)
        results = []
        for entry in entries:
            dte = entry["dte"]
            pid = get_vs_pid(dte)
            try:
                solution_path = dte.Solution.FullName or ""
            except Exception:
                solution_path = ""
            results.append({
                "pid": pid,
                "solution_path": solution_path,
                "moniker_name": entry["moniker_name"],
                "connected": True,
            })
        return results
    finally:
        pythoncom.CoUninitialize()

# DTE 획득                                                            #

def get_dte_by_pid(pid: int):
    """ROT에서 PID로 DTE 객체를 반환한다. 없으면 None."""
    import pythoncom
    entries = find_vs_instances(config.VS_PROG_ID)
    for entry in entries:
        dte = entry["dte"]
        if get_vs_pid(dte) == pid:
            return dte
    return None

def get_dte_by_solution(solution_path: str):
    """ROT에서 솔루션 경로로 DTE 객체를 반환한다. 없으면 None."""
    solution_path_norm = solution_path.lower().replace("\\", "/")
    entries = find_vs_instances(config.VS_PROG_ID)
    for entry in entries:
        dte = entry["dte"]
        try:
            sol = dte.Solution.FullName or ""
        except Exception:
            sol = ""
        if sol.lower().replace("\\", "/") == solution_path_norm:
            return dte
    return None

# VS 실행                                                             #

def launch_vs(solution_path: Optional[str] = None, timeout: Optional[float] = None) -> dict:
    """devenv.exe를 실행하고 ROT 폴링으로 DTE를 획득한다.

    Args:
        solution_path: 열 솔루션 경로 (없으면 빈 VS 실행)
        timeout: ROT 등록 대기 타임아웃(초). 기본값 config.timeouts["launch"]

    Returns:
        dict: pid, solution_path, dte
    """
    import pythoncom

    if timeout is None:
        timeout = config.timeouts["launch"]

    cmd = [config.VS_DEVENV_PATH]
    if solution_path:
        cmd.append(solution_path)

    logger.info("VS 실행: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd)
    logger.info("VS 프로세스 시작 PID=%d, ROT 등록 대기 중...", proc.pid)

    # ROT에 DTE가 등록될 때까지 폴링
    # VS는 시작 직후 ROT에 즉시 등록되지 않음 — 포커스를 잃은 후 등록됨
    # SetForegroundWindow 트릭으로 강제 등록 유발
    pythoncom.CoInitialize()
    try:
        deadline = time.monotonic() + timeout
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            _try_lose_focus(proc.pid)
            entries = find_vs_instances(config.VS_PROG_ID)
            for entry in entries:
                dte = entry["dte"]
                pid = get_vs_pid(dte)
                if pid == proc.pid:
                    logger.info("ROT 등록 확인 (attempt=%d, pid=%d)", attempt, pid)
                    try:
                        sol = dte.Solution.FullName or solution_path or ""
                    except Exception:
                        sol = solution_path or ""
                    return {"pid": pid, "solution_path": sol, "dte": dte}
            time.sleep(config.ROT_POLL_INTERVAL)

        # 폴링 실패 — 새로 뜬 인스턴스 중 솔루션이 일치하는 것 검색
        if solution_path:
            dte = get_dte_by_solution(solution_path)
            if dte:
                pid = get_vs_pid(dte) or proc.pid
                return {"pid": pid, "solution_path": solution_path, "dte": dte}

        raise TimeoutError(
            f"VS 실행 후 ROT 등록 타임아웃 ({timeout}초). proc.pid={proc.pid}"
        )
    except TimeoutError:
        crash_logger.log_timeout(
            command="vs_launch",
            instance_pid=proc.pid,
            session_id=None,
            duration_ms=timeout * 1000,
        )
        raise
    finally:
        pythoncom.CoUninitialize()

def _try_lose_focus(pid: int) -> None:
    """VS 창을 잠깐 다른 창으로 포커스 이동시켜 ROT 등록을 유발한다."""
    try:
        import win32gui
        import win32process

        def _cb(hwnd, results):
            try:
                _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                if wpid == pid and win32gui.IsWindowVisible(hwnd):
                    results.append(hwnd)
            except Exception:
                pass

        hwnds: list[int] = []
        win32gui.EnumWindows(_cb, hwnds)
        if hwnds:
            # 현재 포그라운드 저장 → VS로 포커스 → 원래로 복귀
            current = win32gui.GetForegroundWindow()
            win32gui.SetForegroundWindow(hwnds[0])
            time.sleep(0.1)
            if current:
                win32gui.SetForegroundWindow(current)
    except Exception:
        pass
