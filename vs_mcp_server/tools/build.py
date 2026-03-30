"""빌드 도구

vs_build_solution, vs_build_project, vs_build_status, vs_error_list
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .. import config
from .. import crash_logger
from .. import session_manager as sm

logger = logging.getLogger(__name__)


def _get_sta(session_id: str):
    return sm.get_manager().require_sta(session_id)


async def vs_build_solution(
    *,
    session_id: str,
    configuration: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    """솔루션 전체를 비동기 빌드하고 결과를 반환한다.

    SolutionBuild.Build(WaitForBuildToFinish=False)로 빌드를 시작하고,
    OnBuildDone 이벤트로 완료를 감지한다. 빌드 중에도 즉시 채널은 동작한다.

    Args:
        session_id: 세션 ID
        configuration: 빌드 구성 (예: "Debug", "Release"). None이면 현재 활성 구성 사용.
        timeout: 최대 대기 시간(초). 기본값 config.timeouts["build"]

    Returns:
        {"success": bool, "failed_projects": int, "output": str}
    """
    if timeout is None:
        timeout = config.timeouts["build"]

    sta = _get_sta(session_id)
    loop = asyncio.get_running_loop()
    done_event = asyncio.Event()
    build_result: dict = {}

    def _build():
        dte = sta.dte
        sb = dte.Solution.SolutionBuild

        if configuration:
            try:
                sb.ActiveConfiguration = configuration
            except Exception as e:
                logger.warning("빌드 구성 설정 실패: %s", e)

        # OnBuildDone 이벤트 핸들러 연결
        build_events = dte.Events.BuildEvents
        output_lines: list[str] = []

        def _on_build_done(scope, action, project_config, platform, succeeded):
            # 핸들러 누적 방지: 호출 즉시 자기 자신을 제거
            try:
                build_events.OnBuildDone -= _on_build_done
            except Exception:
                pass
            build_result["success"] = bool(succeeded)
            build_result["failed_projects"] = int(sb.LastBuildInfo)
            build_result["output"] = "\n".join(output_lines)
            loop.call_soon_threadsafe(done_event.set)

        try:
            build_events.OnBuildDone += _on_build_done
        except Exception:
            pass

        # 비동기 빌드 시작
        sb.Build(WaitForBuildToFinish=False)
        return None  # 실제 결과는 이벤트로 받음

    # STA 스레드에서 빌드 시작 (long_running 채널)
    await sta.submit(
        "vs_build_solution_start", _build,
        session_id=session_id,
        channel="long_running",
        loop=loop,
    )

    # 빌드 완료 이벤트 대기
    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pid = sta.instance_pid
        crash_logger.log_timeout(
            command="vs_build_solution",
            instance_pid=pid,
            session_id=session_id,
            duration_ms=timeout * 1000,
        )
        return {
            "success": False,
            "failed_projects": -1,
            "output": f"빌드 타임아웃 ({timeout}초). 빌드는 백그라운드에서 계속 진행 중일 수 있습니다.",
            "timed_out": True,
        }

    return build_result


async def vs_build_project(
    *,
    session_id: str,
    project_name: str,
    configuration: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    """특정 프로젝트만 빌드한다.

    Args:
        session_id: 세션 ID
        project_name: 프로젝트 고유 이름 (UniqueProjectName)
        configuration: 빌드 구성. None이면 현재 활성 구성.
        timeout: 최대 대기 시간(초)

    Returns:
        {"success": bool, "project_name": str, "failed_projects": int}
    """
    if timeout is None:
        timeout = config.timeouts["build"]

    sta = _get_sta(session_id)
    loop = asyncio.get_running_loop()
    done_event = asyncio.Event()
    build_result: dict = {}

    def _build_project():
        dte = sta.dte
        sb = dte.Solution.SolutionBuild

        active_cfg = configuration
        if active_cfg is None:
            try:
                active_cfg = sb.ActiveConfiguration.Name
            except Exception:
                active_cfg = "Debug"

        build_events = dte.Events.BuildEvents

        def _on_build_done(scope, action, project_config, platform, succeeded):
            # 핸들러 누적 방지: 호출 즉시 자기 자신을 제거
            try:
                build_events.OnBuildDone -= _on_build_done
            except Exception:
                pass
            build_result["success"] = bool(succeeded)
            build_result["project_name"] = project_name
            build_result["failed_projects"] = int(sb.LastBuildInfo)
            loop.call_soon_threadsafe(done_event.set)

        try:
            build_events.OnBuildDone += _on_build_done
        except Exception:
            pass

        sb.BuildProject(active_cfg, project_name, WaitForBuildToFinish=False)
        return None

    await sta.submit(
        "vs_build_project_start", _build_project,
        session_id=session_id,
        channel="long_running",
        loop=loop,
    )

    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        crash_logger.log_timeout(
            command="vs_build_project",
            instance_pid=sta.instance_pid,
            session_id=session_id,
            duration_ms=timeout * 1000,
        )
        return {
            "success": False,
            "project_name": project_name,
            "failed_projects": -1,
            "timed_out": True,
        }

    return build_result


async def vs_build_status(*, session_id: str) -> dict:
    """마지막 빌드 결과를 반환한다.

    Returns:
        {"last_build_failed_projects": int, "build_state": str}
    """
    sta = _get_sta(session_id)

    def _status():
        dte = sta.dte
        sb = dte.Solution.SolutionBuild

        # BuildState: vsBuildStateNotStarted=1, vsBuildStateInProgress=2, vsBuildStateDone=3
        state_map = {1: "not_started", 2: "in_progress", 3: "done"}
        try:
            state = state_map.get(int(sb.BuildState), "unknown")
        except Exception:
            state = "unknown"

        try:
            failed = int(sb.LastBuildInfo)
        except Exception:
            failed = -1

        return {
            "last_build_failed_projects": failed,
            "build_state": state,
            "build_succeeded": failed == 0,
        }

    return await sta.submit(
        "vs_build_status", _status,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_error_list(
    *,
    session_id: str,
    include_warnings: bool = True,
    include_messages: bool = False,
) -> dict:
    """Error List 창에서 에러/경고/메시지를 가져온다.

    Args:
        session_id: 세션 ID
        include_warnings: True면 경고도 포함
        include_messages: True면 메시지도 포함

    Returns:
        {"errors": [...], "warnings": [...], "messages": [...], "error_count": int}
    """
    sta = _get_sta(session_id)

    def _error_list():
        dte = sta.dte
        try:
            error_list = dte.ToolWindows.ErrorList
        except Exception as e:
            logger.warning("ErrorList 접근 실패: %s", e)
            return {"errors": [], "warnings": [], "messages": [], "error_count": 0}

        errors, warnings, messages = [], [], []

        # vsBuildErrorLevel: 1=error, 2=warning, 3=message
        try:
            for i in range(1, error_list.ErrorItems.Count + 1):
                try:
                    item = error_list.ErrorItems.Item(i)
                    entry = {
                        "description": item.Description or "",
                        "file": item.FileName or "",
                        "line": item.Line,
                        "column": item.Column,
                        "project": item.Project or "",
                        "error_code": item.ErrorNumber or "",
                    }
                    level = item.ErrorLevel
                    if level == 1:
                        errors.append(entry)
                    elif level == 2:
                        warnings.append(entry)
                    else:
                        messages.append(entry)
                except Exception:
                    continue
        except Exception as e:
            logger.warning("ErrorItems 순회 실패: %s", e)

        result = {
            "errors": errors,
            "error_count": len(errors),
        }
        if include_warnings:
            result["warnings"] = warnings
            result["warning_count"] = len(warnings)
        if include_messages:
            result["messages"] = messages
            result["message_count"] = len(messages)

        return result

    return await sta.submit(
        "vs_error_list", _error_list,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )
