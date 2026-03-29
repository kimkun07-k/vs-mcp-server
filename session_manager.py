"""Session Manager — 클라이언트 세션 ↔ VS 인스턴스 매핑

in-memory 단일 프로세스 상태 관리.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import com_bridge

logger = logging.getLogger(__name__)


@dataclass
class Session:
    session_id: str
    instance_pid: Optional[int] = None
    created_at: float = field(default_factory=time.monotonic)

    @property
    def sta(self) -> Optional[com_bridge.STAThread]:
        if self.instance_pid is None:
            return None
        return com_bridge.get_sta(self.instance_pid)


class SessionManager:
    """클라이언트 세션 ↔ VS 인스턴스 매핑을 관리한다."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # CRUD                                                                 #
    # ------------------------------------------------------------------ #

    def create_session(self, session_id: str) -> Session:
        """새 세션을 생성하고 반환한다. 이미 존재하면 기존 세션을 반환한다."""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(session_id=session_id)
                logger.info("세션 생성: %s", session_id)
            return self._sessions[session_id]

    def get_session(self, session_id: str) -> Optional[Session]:
        """세션을 조회한다. 없으면 None."""
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create_session(self, session_id: str) -> Session:
        """세션을 조회하거나 없으면 생성한다."""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = Session(session_id=session_id)
                logger.info("세션 자동 생성: %s", session_id)
            return self._sessions[session_id]

    def remove_session(self, session_id: str) -> bool:
        """세션을 삭제한다. 삭제됐으면 True."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info("세션 삭제: %s", session_id)
                return True
            return False

    def bind_instance(self, session_id: str, instance_pid: int, dte) -> None:
        """세션에 VS 인스턴스를 바인딩하고 STA 스레드를 생성/확보한다."""
        session = self.get_or_create_session(session_id)
        com_bridge.get_or_create_sta(instance_pid, dte)
        with self._lock:
            session.instance_pid = instance_pid
        logger.info("세션 %s → 인스턴스 PID %d 바인딩", session_id, instance_pid)

    def unbind_instance(self, session_id: str) -> None:
        """세션에서 VS 인스턴스 바인딩을 해제한다."""
        session = self.get_session(session_id)
        if session:
            with self._lock:
                session.instance_pid = None

    # ------------------------------------------------------------------ #
    # 조회                                                                 #
    # ------------------------------------------------------------------ #

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "instance_pid": s.instance_pid,
                    "connected": s.instance_pid is not None and com_bridge.get_sta(s.instance_pid) is not None,
                }
                for s in self._sessions.values()
            ]

    def get_sta_for_session(self, session_id: str) -> Optional[com_bridge.STAThread]:
        """세션에 바인딩된 STA 스레드를 반환한다. 없으면 None."""
        session = self.get_session(session_id)
        if session is None or session.instance_pid is None:
            return None
        return com_bridge.get_sta(session.instance_pid)

    def require_sta(self, session_id: str) -> com_bridge.STAThread:
        """STA 스레드를 반환하거나, 없으면 RuntimeError를 발생시킨다."""
        sta = self.get_sta_for_session(session_id)
        if sta is None:
            raise RuntimeError(
                f"세션 '{session_id}'에 VS 인스턴스가 연결되지 않았습니다. "
                "vs_connect 또는 vs_launch를 먼저 호출하세요."
            )
        return sta


# 전역 싱글턴
_manager: Optional[SessionManager] = None
_manager_lock = threading.Lock()


def get_manager() -> SessionManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SessionManager()
        return _manager
