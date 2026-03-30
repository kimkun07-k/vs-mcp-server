"""큐 관리 도구

vs_queue_status, vs_queue_cancel, vs_queue_history
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .. import com_bridge
from .. import config

logger = logging.getLogger(__name__)


def vs_queue_status(
    *,
    instance_pid: Optional[int] = None,
) -> dict:
    """인스턴스별 STA 스레드의 현재 실행 중인 명령과 대기 큐를 반환한다.

    Args:
        instance_pid: 조회할 인스턴스 PID. None이면 전체 인스턴스 반환.

    Returns:
        {
            "instances": [
                {
                    "instance_pid": int,
                    "current": {...} | null,
                    "queued": [...],
                    "queued_count": int
                }
            ]
        }
    """
    pids = [instance_pid] if instance_pid is not None else com_bridge.list_sta_pids()
    result = []
    for pid in pids:
        sta = com_bridge.get_sta(pid)
        if sta is None:
            continue
        current = sta.get_current()
        queued = sta.get_queue_snapshot()
        result.append({
            "instance_pid": pid,
            "current": current,
            "queued": queued,
            "queued_count": len(queued),
        })
    return {"instances": result, "total_instances": len(result)}


def vs_queue_cancel(
    *,
    command_id: str,
    instance_pid: Optional[int] = None,
) -> dict:
    """대기 큐에서 command_id에 해당하는 명령을 취소한다.

    이미 실행 중인 명령은 취소 불가.

    Args:
        command_id: 취소할 명령의 UUID
        instance_pid: 조회할 인스턴스 PID. None이면 전체 인스턴스에서 검색.

    Returns:
        {"status": "cancelled" | "not_found" | "already_running", "command_id": str}
    """
    pids = [instance_pid] if instance_pid is not None else com_bridge.list_sta_pids()

    for pid in pids:
        sta = com_bridge.get_sta(pid)
        if sta is None:
            continue

        # 현재 실행 중인지 확인
        current = sta.get_current()
        if current and current.get("command_id") == command_id:
            return {
                "status": "already_running",
                "command_id": command_id,
                "message": "이미 실행 중인 명령은 취소할 수 없습니다.",
            }

        # 대기 큐에서 취소 시도
        if sta.cancel_queued(command_id):
            return {
                "status": "cancelled",
                "command_id": command_id,
                "instance_pid": pid,
            }

    return {
        "status": "not_found",
        "command_id": command_id,
        "message": "해당 command_id를 가진 대기 명령을 찾을 수 없습니다.",
    }


def vs_queue_history(
    *,
    instance_pid: Optional[int] = None,
    limit: int = config.QUEUE_HISTORY_DEFAULT_LIMIT,
) -> dict:
    """최근 N건의 명령 이력을 반환한다.

    Args:
        instance_pid: 조회할 인스턴스 PID. None이면 전체.
        limit: 반환할 최대 건수 (기본값 20)

    Returns:
        {
            "instances": [
                {
                    "instance_pid": int,
                    "history": [...],
                    "count": int
                }
            ]
        }
    """
    pids = [instance_pid] if instance_pid is not None else com_bridge.list_sta_pids()
    result = []
    for pid in pids:
        sta = com_bridge.get_sta(pid)
        if sta is None:
            continue
        history = sta.get_history(limit=limit)
        result.append({
            "instance_pid": pid,
            "history": history,
            "count": len(history),
        })
    return {"instances": result, "total_instances": len(result)}
