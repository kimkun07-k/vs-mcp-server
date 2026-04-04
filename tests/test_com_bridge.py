"""UT-004: com_bridge 레지스트리 로직 테스트 (STA 스레드 mock)"""
import asyncio
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from vs_mcp_server import com_bridge


def _mock_sta(pid: int) -> MagicMock:
    sta = MagicMock(spec=com_bridge.STAThread)
    sta.instance_pid = pid
    return sta


def test_get_or_create_sta_creates_new():
    """동일 pid 첫 호출 시 STAThread 생성 (pid만 전달, DTE는 STA 내부에서 획득)."""
    with patch.object(com_bridge, "_sta_registry", {}), \
         patch("vs_mcp_server.com_bridge.STAThread", return_value=_mock_sta(12345)) as MockSTA:
        sta = com_bridge.get_or_create_sta(12345)
        MockSTA.assert_called_once_with(12345)
        assert sta is not None


def test_get_or_create_sta_returns_existing():
    """동일 pid 두 번째 호출 시 동일 인스턴스 반환."""
    mock_sta = _mock_sta(99)
    registry = {99: mock_sta}
    with patch.object(com_bridge, "_sta_registry", registry):
        sta = com_bridge.get_or_create_sta(99, MagicMock())
        assert sta is mock_sta


def test_get_sta_returns_none_for_unknown():
    with patch.object(com_bridge, "_sta_registry", {}):
        assert com_bridge.get_sta(9999) is None


def test_get_sta_returns_existing():
    mock_sta = _mock_sta(77)
    with patch.object(com_bridge, "_sta_registry", {77: mock_sta}):
        assert com_bridge.get_sta(77) is mock_sta


def test_remove_sta_calls_shutdown():
    mock_sta = MagicMock(spec=com_bridge.STAThread)
    registry = {55: mock_sta}
    with patch.object(com_bridge, "_sta_registry", registry):
        com_bridge.remove_sta(55)
        mock_sta.shutdown.assert_called_once()
        assert 55 not in registry


def test_remove_sta_noop_for_unknown():
    registry = {}
    with patch.object(com_bridge, "_sta_registry", registry):
        com_bridge.remove_sta(9999)  # should not raise


def test_list_sta_pids():
    registry = {1: _mock_sta(1), 2: _mock_sta(2), 3: _mock_sta(3)}
    with patch.object(com_bridge, "_sta_registry", registry):
        pids = com_bridge.list_sta_pids()
        assert set(pids) == {1, 2, 3}


def test_list_sta_pids_empty():
    with patch.object(com_bridge, "_sta_registry", {}):
        assert com_bridge.list_sta_pids() == []


def test_command_dataclass_defaults():
    import asyncio
    import uuid

    loop = asyncio.new_event_loop()
    try:
        fut = loop.create_future()
        cmd = com_bridge.Command(
            command_id=str(uuid.uuid4()),
            name="test",
            fn=lambda: None,
            future=fut,
            session_id="s1",
            loop=loop,
        )
        assert cmd.channel == "immediate"
        assert cmd.status == "pending"
        assert cmd.started_at is None
        assert cmd.finished_at is None
    finally:
        loop.close()


# ------------------------------------------------------------------ #
# STAThread DTE 재획득 로직 테스트                                     #
# ------------------------------------------------------------------ #

def test_sta_thread_init_dte_is_none():
    """STAThread 생성 시 dte는 None (STA 내부에서 ROT로 획득)."""
    with patch("vs_mcp_server.com_bridge.STAThread._run"):
        sta = com_bridge.STAThread(12345)
        assert sta.dte is None
        assert sta.instance_pid == 12345
        assert not sta._dte_ready.is_set()


def test_sta_thread_acquire_dte_success():
    """_acquire_dte_from_rot이 ROT에서 매칭 PID를 찾으면 self.dte를 설정한다."""
    mock_dte = MagicMock()
    mock_entries = [{"moniker_name": "!VisualStudio.DTE.17.0:12345", "dte": mock_dte}]

    with patch("vs_mcp_server.com_bridge.STAThread._run"):
        sta = com_bridge.STAThread(12345)

    with patch("vs_mcp_server.com_bridge.find_vs_instances", return_value=mock_entries), \
         patch("vs_mcp_server.com_bridge.get_vs_pid", return_value=12345):
        sta._acquire_dte_from_rot()

    assert sta.dte is mock_dte
    assert sta._dte_ready.is_set()


def test_sta_thread_acquire_dte_timeout():
    """ROT에서 PID를 찾지 못하면 dte는 None이고 _dte_ready는 set된다."""
    with patch("vs_mcp_server.com_bridge.STAThread._run"):
        sta = com_bridge.STAThread(99999)

    with patch("vs_mcp_server.com_bridge.find_vs_instances", return_value=[]), \
         patch("vs_mcp_server.com_bridge.config") as mock_config:
        mock_config.timeouts = {"launch": 0.1}
        mock_config.ROT_POLL_INTERVAL = 0.05
        mock_config.VS_PROG_ID = "VisualStudio.DTE.17.0"
        sta._acquire_dte_from_rot()

    assert sta.dte is None
    assert sta._dte_ready.is_set()


def test_sta_thread_acquire_dte_skips_wrong_pid():
    """ROT에 다른 PID의 인스턴스만 있으면 매칭하지 않는다."""
    mock_dte_other = MagicMock()
    mock_entries = [{"moniker_name": "!VisualStudio.DTE.17.0:99", "dte": mock_dte_other}]

    with patch("vs_mcp_server.com_bridge.STAThread._run"):
        sta = com_bridge.STAThread(12345)

    with patch("vs_mcp_server.com_bridge.find_vs_instances", return_value=mock_entries), \
         patch("vs_mcp_server.com_bridge.get_vs_pid", return_value=99), \
         patch("vs_mcp_server.com_bridge.config") as mock_config:
        mock_config.timeouts = {"launch": 0.1}
        mock_config.ROT_POLL_INTERVAL = 0.05
        mock_config.VS_PROG_ID = "VisualStudio.DTE.17.0"
        sta._acquire_dte_from_rot()

    assert sta.dte is None


def test_get_or_create_sta_ignores_dte_param():
    """get_or_create_sta에 dte를 전달해도 무시되고 pid만 사용된다 (하위 호환)."""
    mock_dte = MagicMock()
    with patch.object(com_bridge, "_sta_registry", {}), \
         patch("vs_mcp_server.com_bridge.STAThread", return_value=_mock_sta(42)) as MockSTA:
        sta = com_bridge.get_or_create_sta(42, mock_dte)
        MockSTA.assert_called_once_with(42)
        assert sta is not None


# ------------------------------------------------------------------ #
# Race condition 재현 테스트: DTE 획득 전 command 실행                  #
# ------------------------------------------------------------------ #

def _make_cmd(loop, fn, name="test_cmd"):
    """테스트용 Command 객체를 생성한다."""
    fut = loop.create_future()
    return com_bridge.Command(
        command_id=str(uuid.uuid4()),
        name=name,
        fn=fn,
        future=fut,
        session_id="test",
        loop=loop,
    )


def test_race_condition_execute_waits_for_dte_ready():
    """_execute()가 _dte_ready 이벤트를 기다린 후 명령을 실행한다.

    재현 시나리오:
      1. STAThread 생성 직후 dte=None, _dte_ready 미설정
      2. 다른 스레드에서 _execute() 호출 — _dte_ready.wait()에서 블록됨
      3. 0.2초 후 dte를 설정하고 _dte_ready를 set
      4. _execute()가 블록 해제되고 command가 유효한 dte로 실행됨

    이 테스트는 _execute()에 _dte_ready.wait() 가드가 없으면 실패한다:
    command가 즉시 실행되어 sta.dte가 None인 상태에서 AttributeError 발생.
    """
    with patch("vs_mcp_server.com_bridge.STAThread._run"):
        sta = com_bridge.STAThread(12345)

    assert sta.dte is None
    assert not sta._dte_ready.is_set()

    loop = asyncio.new_event_loop()
    accessed_dte_values = []

    def _fn():
        # command 실행 시점의 sta.dte를 기록
        accessed_dte_values.append(sta.dte)
        sta.dte.ItemOperations.OpenFile("test.cpp")  # DTE 속성 접근
        return {"status": "ok"}

    cmd = _make_cmd(loop, _fn)

    # _execute를 별도 스레드에서 실행 (블록될 것)
    exec_done = threading.Event()

    def _run_execute():
        sta._execute(cmd)
        exec_done.set()

    t = threading.Thread(target=_run_execute, daemon=True)
    t.start()

    # 0.1초 후에도 _execute가 블록 상태인지 확인
    time.sleep(0.1)
    assert not exec_done.is_set(), (
        "_execute()가 _dte_ready를 기다리지 않고 즉시 실행됨 — race condition 발생"
    )

    # DTE 획득 시뮬레이션
    mock_dte = MagicMock()
    sta.dte = mock_dte
    sta._dte_ready.set()

    # _execute가 완료될 때까지 대기
    exec_done.wait(timeout=5)
    assert exec_done.is_set(), "_execute()가 5초 내에 완료되지 않음"

    # command가 유효한 DTE로 실행되었는지 검증
    assert len(accessed_dte_values) == 1
    assert accessed_dte_values[0] is mock_dte
    mock_dte.ItemOperations.OpenFile.assert_called_once_with("test.cpp")
    assert cmd.status == "done"

    loop.close()


def test_race_condition_execute_rejects_when_dte_acquisition_failed():
    """DTE 획득이 타임아웃되면 (dte=None, _dte_ready=set) command를 거부한다."""
    with patch("vs_mcp_server.com_bridge.STAThread._run"):
        sta = com_bridge.STAThread(99999)

    # DTE 획득 실패 시뮬레이션: dte는 None이지만 _dte_ready는 set
    sta._dte_ready.set()
    assert sta.dte is None

    loop = asyncio.new_event_loop()
    was_called = []

    def _fn():
        was_called.append(True)
        return {"status": "ok"}

    cmd = _make_cmd(loop, _fn)
    sta._execute(cmd)

    # command fn이 호출되지 않았어야 함
    assert len(was_called) == 0, "dte가 None인데 command가 실행됨"
    # future에 RuntimeError가 설정됨
    assert cmd.status == "pending"  # running으로 변하지 않음

    loop.close()
