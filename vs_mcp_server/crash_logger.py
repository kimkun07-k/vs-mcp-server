"""크래시/에러 학습용 JSON 구조화 로거"""
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def _ensure_log_dir() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_event(
    command: str,
    instance_pid: Optional[int],
    session_id: Optional[str],
    result: str,
    *,
    error_type: Optional[str] = None,
    error_detail: Optional[str] = None,
    vs_state_before: Optional[str] = None,
    duration_ms: Optional[float] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """크래시/에러/타임아웃 이벤트를 JSONL 파일에 기록한다."""
    record: dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        "command": command,
        "instance_pid": instance_pid,
        "session_id": session_id,
        "result": result,
    }
    if error_type is not None:
        record["error_type"] = error_type
    if error_detail is not None:
        record["error_detail"] = error_detail
    if vs_state_before is not None:
        record["vs_state_before"] = vs_state_before
    if duration_ms is not None:
        record["duration_ms"] = round(duration_ms, 1)
    if extra:
        record.update(extra)

    _ensure_log_dir()
    try:
        with _lock:
            with config.LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("crash_logger: 파일 쓰기 실패: %s", e)


def log_com_error(
    command: str,
    instance_pid: Optional[int],
    session_id: Optional[str],
    exc: Exception,
    vs_state_before: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> None:
    """COM 호출 실패를 기록하는 편의 함수."""
    log_event(
        command=command,
        instance_pid=instance_pid,
        session_id=session_id,
        result="error",
        error_type=type(exc).__name__,
        error_detail=str(exc),
        vs_state_before=vs_state_before,
        duration_ms=duration_ms,
    )


def log_timeout(
    command: str,
    instance_pid: Optional[int],
    session_id: Optional[str],
    duration_ms: float,
    vs_state_before: Optional[str] = None,
) -> None:
    """타임아웃 이벤트를 기록하는 편의 함수."""
    log_event(
        command=command,
        instance_pid=instance_pid,
        session_id=session_id,
        result="timeout",
        error_type="TimeoutError",
        vs_state_before=vs_state_before,
        duration_ms=duration_ms,
    )


def log_evaluate(
    instance_pid: Optional[int],
    session_id: Optional[str],
    expression: str,
    result: str,
    *,
    error_type: Optional[str] = None,
    error_detail: Optional[str] = None,
    vs_state_before: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> None:
    """vs_debug_evaluate 표현식/결과를 기록하는 편의 함수."""
    log_event(
        command="vs_debug_evaluate",
        instance_pid=instance_pid,
        session_id=session_id,
        result=result,
        error_type=error_type,
        error_detail=error_detail,
        vs_state_before=vs_state_before,
        duration_ms=duration_ms,
        extra={"expression": expression},
    )
