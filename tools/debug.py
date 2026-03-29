"""디버깅 도구

vs_debug_start, vs_debug_stop, vs_debug_breakpoint, vs_debug_step,
vs_debug_locals, vs_debug_evaluate, vs_debug_callstack
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

import config
import crash_logger
import session_manager as sm

logger = logging.getLogger(__name__)

# dbgDebugMode constants
DBG_MODE_DESIGN = 1   # dbgDesignMode
DBG_MODE_BREAK = 2    # dbgBreakMode
DBG_MODE_RUN = 3      # dbgRunMode


def _get_sta(session_id: str):
    return sm.get_manager().require_sta(session_id)


def _mode_name(mode_int: int) -> str:
    return {DBG_MODE_DESIGN: "design", DBG_MODE_BREAK: "break", DBG_MODE_RUN: "run"}.get(mode_int, "unknown")


# ------------------------------------------------------------------ #
# 시작 / 종료                                                         #
# ------------------------------------------------------------------ #

async def vs_debug_start(
    *,
    session_id: str,
    wait_for_break: bool = False,
) -> dict:
    """디버깅을 시작한다. 이미 실행 중이면 현재 상태를 반환한다.

    Args:
        session_id: 세션 ID
        wait_for_break: True면 Break 모드 진입 시까지 대기 (기본값 False)

    Returns:
        {"mode": str, "status": str}
    """
    sta = _get_sta(session_id)

    def _start():
        dte = sta.dte
        debugger = dte.Debugger
        current_mode = int(debugger.CurrentMode)
        if current_mode == DBG_MODE_RUN:
            return {"mode": "run", "status": "already_running"}
        debugger.Go(WaitForBreakOrEnd=wait_for_break)
        return {"mode": _mode_name(int(debugger.CurrentMode)), "status": "started"}

    return await sta.submit(
        "vs_debug_start", _start,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_debug_stop(*, session_id: str) -> dict:
    """디버깅 세션을 종료하고 Design 모드로 복귀한다.

    Returns:
        {"mode": "design", "status": "stopped"}
    """
    sta = _get_sta(session_id)

    def _stop():
        dte = sta.dte
        debugger = dte.Debugger
        if int(debugger.CurrentMode) == DBG_MODE_DESIGN:
            return {"mode": "design", "status": "not_debugging"}
        debugger.Stop(WaitForDesignMode=True)
        return {"mode": "design", "status": "stopped"}

    return await sta.submit(
        "vs_debug_stop", _stop,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


# ------------------------------------------------------------------ #
# 브레이크포인트                                                      #
# ------------------------------------------------------------------ #

async def vs_debug_breakpoint(
    *,
    session_id: str,
    action: Literal["add", "remove", "list"],
    file: Optional[str] = None,
    line: Optional[int] = None,
) -> dict:
    """브레이크포인트를 추가/제거/목록 조회한다.

    Args:
        session_id: 세션 ID
        action: "add" | "remove" | "list"
        file: 파일 경로 (add/remove 시 필수)
        line: 라인 번호 (add/remove 시 필수)

    Returns:
        add: {"status": "added", "file": str, "line": int}
        remove: {"status": "removed" | "not_found", "file": str, "line": int}
        list: {"breakpoints": [{"file": str, "line": int, "enabled": bool}]}
    """
    sta = _get_sta(session_id)

    def _bp():
        debugger = sta.dte.Debugger
        bps = debugger.Breakpoints

        if action == "list":
            result = []
            for i in range(1, bps.Count + 1):
                try:
                    bp = bps.Item(i)
                    result.append({
                        "file": bp.File or "",
                        "line": bp.FileLine,
                        "column": bp.FileColumn,
                        "enabled": bp.Enabled,
                        "condition": bp.Condition or "",
                    })
                except Exception:
                    continue
            return {"breakpoints": result, "count": len(result)}

        if file is None or line is None:
            raise ValueError("file과 line은 add/remove 시 필수입니다.")

        file_norm = file.lower().replace("\\", "/")

        if action == "add":
            bps.Add(File=file, Line=line, Column=1)
            return {"status": "added", "file": file, "line": line}

        if action == "remove":
            for i in range(bps.Count, 0, -1):
                try:
                    bp = bps.Item(i)
                    if (
                        bp.File.lower().replace("\\", "/") == file_norm
                        and bp.FileLine == line
                    ):
                        bp.Delete()
                        return {"status": "removed", "file": file, "line": line}
                except Exception:
                    continue
            return {"status": "not_found", "file": file, "line": line}

        raise ValueError(f"알 수 없는 action: {action}")

    return await sta.submit(
        f"vs_debug_breakpoint_{action}", _bp,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


# ------------------------------------------------------------------ #
# 스텝 실행                                                           #
# ------------------------------------------------------------------ #

async def vs_debug_step(
    *,
    session_id: str,
    step_type: Literal["into", "over", "out"] = "over",
) -> dict:
    """스텝 실행 후 현재 위치를 반환한다.

    Args:
        session_id: 세션 ID
        step_type: "into" | "over" | "out"

    Returns:
        {"file": str, "line": int, "function": str, "mode": str}
    """
    sta = _get_sta(session_id)

    def _step():
        debugger = sta.dte.Debugger
        if int(debugger.CurrentMode) != DBG_MODE_BREAK:
            raise RuntimeError("스텝 실행은 Break 모드에서만 가능합니다.")

        if step_type == "into":
            debugger.StepInto(WaitForBreakOrEnd=True)
        elif step_type == "over":
            debugger.StepOver(WaitForBreakOrEnd=True)
        elif step_type == "out":
            debugger.StepOut(WaitForBreakOrEnd=True)
        else:
            raise ValueError(f"알 수 없는 step_type: {step_type}")

        frame = debugger.CurrentStackFrame
        try:
            return {
                "file": frame.FileName or "",
                "line": frame.LineNumber,
                "function": frame.FunctionName or "",
                "mode": _mode_name(int(debugger.CurrentMode)),
            }
        except Exception:
            return {"file": "", "line": -1, "function": "", "mode": _mode_name(int(debugger.CurrentMode))}

    return await sta.submit(
        f"vs_debug_step_{step_type}", _step,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


# ------------------------------------------------------------------ #
# 로컬 변수                                                           #
# ------------------------------------------------------------------ #

async def vs_debug_locals(*, session_id: str) -> dict:
    """Break 모드에서 현재 스택 프레임의 로컬 변수를 반환한다.

    Returns:
        {"locals": [{"name": str, "type": str, "value": str}]}
    """
    sta = _get_sta(session_id)

    def _locals():
        debugger = sta.dte.Debugger
        if int(debugger.CurrentMode) != DBG_MODE_BREAK:
            raise RuntimeError("로컬 변수 조회는 Break 모드에서만 가능합니다.")

        frame = debugger.CurrentStackFrame
        result = []
        try:
            for expr in frame.Locals:
                try:
                    result.append({
                        "name": expr.Name or "",
                        "type": expr.Type or "",
                        "value": expr.Value or "",
                        "is_valid": bool(expr.IsValidValue),
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Locals 순회 실패: %s", e)

        return {"locals": result, "count": len(result)}

    return await sta.submit(
        "vs_debug_locals", _locals,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


# ------------------------------------------------------------------ #
# 표현식 평가                                                         #
# ------------------------------------------------------------------ #

async def vs_debug_evaluate(
    *,
    session_id: str,
    expression: str,
    timeout: Optional[float] = None,
) -> dict:
    """Break 모드에서 표현식을 평가하고 결과를 반환한다. 결과는 crash_logger에 기록된다.

    Args:
        session_id: 세션 ID
        expression: 평가할 표현식 (예: "myObj.Value", "x + y")
        timeout: 평가 타임아웃(초). 기본값 config.timeouts["debug_evaluate"]

    Returns:
        {"expression": str, "value": str, "type": str, "is_valid": bool}
    """
    if timeout is None:
        timeout = config.timeouts["debug_evaluate"]

    sta = _get_sta(session_id)

    def _evaluate():
        debugger = sta.dte.Debugger
        vs_state = _mode_name(int(debugger.CurrentMode))
        t0 = time.monotonic()

        try:
            if int(debugger.CurrentMode) != DBG_MODE_BREAK:
                raise RuntimeError("표현식 평가는 Break 모드에서만 가능합니다.")

            expr_result = debugger.GetExpression(expression, UseAutoExpandRules=False, Timeout=int(timeout * 1000))
            duration_ms = (time.monotonic() - t0) * 1000

            is_valid = bool(expr_result.IsValidValue)
            value = str(expr_result.Value) if expr_result.Value else ""
            type_str = str(expr_result.Type) if expr_result.Type else ""

            crash_logger.log_evaluate(
                instance_pid=sta.instance_pid,
                session_id=session_id,
                expression=expression,
                result="success" if is_valid else "invalid",
                vs_state_before=vs_state,
                duration_ms=duration_ms,
            )

            return {
                "expression": expression,
                "value": value,
                "type": type_str,
                "is_valid": is_valid,
            }
        except Exception as exc:
            duration_ms = (time.monotonic() - t0) * 1000
            crash_logger.log_evaluate(
                instance_pid=sta.instance_pid,
                session_id=session_id,
                expression=expression,
                result="error",
                error_type=type(exc).__name__,
                error_detail=str(exc),
                vs_state_before=vs_state,
                duration_ms=duration_ms,
            )
            raise

    return await sta.submit(
        "vs_debug_evaluate", _evaluate,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


# ------------------------------------------------------------------ #
# 콜스택                                                              #
# ------------------------------------------------------------------ #

async def vs_debug_callstack(*, session_id: str) -> dict:
    """현재 스레드의 콜스택을 반환한다.

    Returns:
        {"frames": [{"function": str, "file": str, "line": int}]}
    """
    sta = _get_sta(session_id)

    def _callstack():
        debugger = sta.dte.Debugger
        if int(debugger.CurrentMode) != DBG_MODE_BREAK:
            raise RuntimeError("콜스택 조회는 Break 모드에서만 가능합니다.")

        frames = []
        try:
            for frame in debugger.CurrentThread.StackFrames:
                try:
                    frames.append({
                        "function": frame.FunctionName or "",
                        "file": frame.FileName or "",
                        "line": frame.LineNumber,
                        "module": frame.Module or "",
                        "language": frame.Language or "",
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning("StackFrames 순회 실패: %s", e)

        return {"frames": frames, "depth": len(frames)}

    return await sta.submit(
        "vs_debug_callstack", _callstack,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )
