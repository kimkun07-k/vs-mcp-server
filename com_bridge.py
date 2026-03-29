"""COM Bridge — STA 스레드 관리 + 명령 큐 + 비동기 실행

EnvDTE COM 객체는 STA(Single-Threaded Apartment)에서만 안정적으로 동작하므로,
VS 인스턴스 1개당 전용 STA 스레드 1개를 할당한다.

즉시 채널 (immediate): 에디터 조작 등 빠른 명령
장시간 채널 (long_running): 빌드 등 오래 걸리는 명령 — 빌드 중에도 즉시 채널은 처리 가능
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import config
import crash_logger

logger = logging.getLogger(__name__)


@dataclass
class Command:
    """큐에 넣는 단일 명령 단위."""
    command_id: str
    name: str
    fn: Callable[[], Any]
    future: asyncio.Future
    session_id: Optional[str]
    loop: asyncio.AbstractEventLoop
    channel: str = "immediate"          # "immediate" | "long_running"
    enqueued_at: float = field(default_factory=time.monotonic)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    status: str = "pending"             # pending | running | done | error | cancelled


class STAThread:
    """VS 인스턴스 1개에 대응하는 STA 전용 스레드.

    asyncio Future를 통해 결과를 반환하므로, MCP 핸들러에서 await 가능하다.
    """

    def __init__(self, instance_pid: int, dte) -> None:
        self.instance_pid = instance_pid
        self.dte = dte
        self._immediate_q: queue.Queue[Command | None] = queue.Queue()
        self._long_q: queue.Queue[Command | None] = queue.Queue()
        self._history: list[Command] = []
        self._current: Optional[Command] = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name=f"STA-{instance_pid}", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def submit(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        session_id: Optional[str] = None,
        channel: str = "immediate",
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> asyncio.Future:
        """명령을 큐에 제출하고 asyncio.Future를 반환한다."""
        if loop is None:
            loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        cmd = Command(
            command_id=str(uuid.uuid4()),
            name=name,
            fn=fn,
            future=fut,
            session_id=session_id,
            loop=loop,
            channel=channel,
        )
        q = self._long_q if channel == "long_running" else self._immediate_q
        q.put(cmd)
        logger.debug("submit %s [%s] pid=%d", name, cmd.command_id[:8], self.instance_pid)
        return fut

    def get_current(self) -> Optional[dict]:
        """현재 실행 중인 명령 정보를 반환한다."""
        with self._lock:
            if self._current is None:
                return None
            cmd = self._current
            return {
                "command_id": cmd.command_id,
                "name": cmd.name,
                "session_id": cmd.session_id,
                "channel": cmd.channel,
                "elapsed_ms": round((time.monotonic() - (cmd.started_at or cmd.enqueued_at)) * 1000, 1),
            }

    def get_queue_snapshot(self) -> list[dict]:
        """대기 중인 명령 목록(스냅샷)을 반환한다."""
        items = []
        for q in (self._immediate_q, self._long_q):
            # queue.Queue는 내부 deque를 직접 순회할 수 있다.
            with q.mutex:
                for cmd in list(q.queue):
                    if cmd is None:
                        continue
                    items.append({
                        "command_id": cmd.command_id,
                        "name": cmd.name,
                        "session_id": cmd.session_id,
                        "channel": cmd.channel,
                        "wait_ms": round((time.monotonic() - cmd.enqueued_at) * 1000, 1),
                    })
        return items

    def cancel_queued(self, command_id: str) -> bool:
        """대기 큐에서 command_id에 해당하는 명령을 취소한다.

        이미 실행 중인 명령은 취소 불가. True: 취소 성공, False: 찾을 수 없음.
        """
        for q in (self._immediate_q, self._long_q):
            with q.mutex:
                for cmd in list(q.queue):
                    if cmd is not None and cmd.command_id == command_id:
                        q.queue.remove(cmd)
                        cmd.status = "cancelled"
                        self._resolve_future(cmd, None, cancelled=True)
                        self._add_history(cmd)
                        return True
        return False

    def get_history(self, limit: int = config.QUEUE_HISTORY_DEFAULT_LIMIT) -> list[dict]:
        """최근 N건의 명령 이력을 반환한다."""
        with self._lock:
            recent = self._history[-limit:]
        return [
            {
                "command_id": c.command_id,
                "name": c.name,
                "session_id": c.session_id,
                "channel": c.channel,
                "status": c.status,
                "duration_ms": round(
                    ((c.finished_at or c.started_at or c.enqueued_at) - (c.started_at or c.enqueued_at)) * 1000, 1
                ) if c.started_at else None,
            }
            for c in recent
        ]

    def shutdown(self) -> None:
        """STA 스레드를 종료한다."""
        self._immediate_q.put(None)
        self._long_q.put(None)
        self._thread.join(timeout=5)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        """STA 스레드 메인 루프."""
        import pythoncom
        pythoncom.CoInitialize()
        try:
            while True:
                # 즉시 채널 우선 처리
                cmd = self._try_get(self._immediate_q)
                if cmd is None:
                    cmd = self._try_get(self._long_q, timeout=0.05)
                if cmd is None:
                    continue
                self._execute(cmd)
        except _ShutdownSignal:
            pass
        finally:
            pythoncom.CoUninitialize()

    def _try_get(self, q: queue.Queue, timeout: float = 0.0) -> Optional[Command]:
        try:
            cmd = q.get(timeout=timeout)
            if cmd is None:
                raise _ShutdownSignal
            return cmd
        except queue.Empty:
            return None

    def _execute(self, cmd: Command) -> None:
        cmd.started_at = time.monotonic()
        cmd.status = "running"
        with self._lock:
            self._current = cmd
        t0 = time.monotonic()
        try:
            result = cmd.fn()
            cmd.status = "done"
            self._resolve_future(cmd, result)
        except Exception as exc:
            cmd.status = "error"
            duration_ms = (time.monotonic() - t0) * 1000
            crash_logger.log_com_error(
                command=cmd.name,
                instance_pid=self.instance_pid,
                session_id=cmd.session_id,
                exc=exc,
                duration_ms=duration_ms,
            )
            self._reject_future(cmd, exc)
        finally:
            cmd.finished_at = time.monotonic()
            with self._lock:
                self._current = None
                self._add_history(cmd)

    def _resolve_future(self, cmd: Command, result: Any, cancelled: bool = False) -> None:
        def _set():
            if cmd.future.done():
                return
            if cancelled:
                cmd.future.cancel()
            else:
                cmd.future.set_result(result)
        cmd.loop.call_soon_threadsafe(_set)

    def _reject_future(self, cmd: Command, exc: Exception) -> None:
        def _set():
            if not cmd.future.done():
                cmd.future.set_exception(exc)
        cmd.loop.call_soon_threadsafe(_set)

    def _add_history(self, cmd: Command) -> None:
        self._history.append(cmd)
        if len(self._history) > config.QUEUE_HISTORY_MAX:
            self._history = self._history[-config.QUEUE_HISTORY_MAX:]


class _ShutdownSignal(Exception):
    pass


# ------------------------------------------------------------------ #
# 전역 STA 스레드 레지스트리                                          #
# ------------------------------------------------------------------ #

_sta_registry: dict[int, STAThread] = {}   # pid → STAThread
_registry_lock = threading.Lock()


def get_or_create_sta(instance_pid: int, dte) -> STAThread:
    """pid에 해당하는 STAThread를 반환하거나, 없으면 새로 생성한다."""
    with _registry_lock:
        if instance_pid not in _sta_registry:
            _sta_registry[instance_pid] = STAThread(instance_pid, dte)
        return _sta_registry[instance_pid]


def get_sta(instance_pid: int) -> Optional[STAThread]:
    """pid에 해당하는 STAThread를 반환한다. 없으면 None."""
    with _registry_lock:
        return _sta_registry.get(instance_pid)


def remove_sta(instance_pid: int) -> None:
    """pid에 해당하는 STAThread를 종료하고 레지스트리에서 제거한다."""
    with _registry_lock:
        sta = _sta_registry.pop(instance_pid, None)
    if sta:
        sta.shutdown()


def list_sta_pids() -> list[int]:
    with _registry_lock:
        return list(_sta_registry.keys())
