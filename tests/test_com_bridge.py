"""UT-004: com_bridge 레지스트리 로직 테스트 (STA 스레드 mock)"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import com_bridge


def _mock_sta(pid: int) -> MagicMock:
    sta = MagicMock(spec=com_bridge.STAThread)
    sta.instance_pid = pid
    return sta


def test_get_or_create_sta_creates_new():
    """동일 pid 첫 호출 시 STAThread 생성."""
    mock_dte = MagicMock()
    with patch.object(com_bridge, "_sta_registry", {}), \
         patch("com_bridge.STAThread", return_value=_mock_sta(12345)) as MockSTA:
        sta = com_bridge.get_or_create_sta(12345, mock_dte)
        MockSTA.assert_called_once_with(12345, mock_dte)
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
