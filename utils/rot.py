"""Running Object Table (ROT) 유틸리티

COM STA 스레드에서 호출해야 한다. asyncio 이벤트 루프에서 직접 호출 금지.
"""
from __future__ import annotations

import logging
from typing import Iterator

logger = logging.getLogger(__name__)


def iter_rot_monikers() -> Iterator[tuple[str, object]]:
    """ROT에 등록된 (display_name, object) 쌍을 순회한다.

    pywin32가 설치되지 않은 환경에서는 ImportError를 발생시킨다.
    """
    import pythoncom

    rot = pythoncom.GetRunningObjectTable()
    enum = rot.EnumRunning()
    while True:
        monikers = enum.Next(1)
        if not monikers:
            break
        moniker = monikers[0]
        ctx = pythoncom.CreateBindCtx(0)
        try:
            display_name = moniker.GetDisplayName(ctx, None)
        except pythoncom.com_error:
            continue
        try:
            obj = rot.GetObject(moniker)
        except pythoncom.com_error:
            continue
        yield display_name, obj


def find_vs_instances(prog_id: str = "VisualStudio.DTE.17.0") -> list[dict]:
    """ROT에서 Visual Studio DTE 인스턴스를 찾아 목록으로 반환한다.

    Returns:
        list of dicts with keys: moniker_name, dte
    """
    import pythoncom

    results = []
    for name, obj in iter_rot_monikers():
        if prog_id.lower() not in name.lower():
            continue
        try:
            import win32com.client
            dte = win32com.client.Dispatch(obj.QueryInterface(pythoncom.IID_IDispatch))
            results.append({"moniker_name": name, "dte": dte})
        except Exception as e:
            logger.debug("ROT 객체 획득 실패 (%s): %s", name, e)
    return results


def get_vs_pid(dte) -> int | None:
    """DTE 객체로부터 VS 프로세스 PID를 반환한다."""
    try:
        import win32process
        hwnd = dte.MainWindow.HWnd
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception as e:
        logger.debug("PID 획득 실패: %s", e)
        return None
