"""UT-006: tools/queue 도구 테스트 (mock STAThread 기반)"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from vs_mcp_server import com_bridge
from vs_mcp_server.tools import queue as queue_tools


def _mock_sta(pid: int, current=None, queued=None, history=None) -> MagicMock:
    sta = MagicMock(spec=com_bridge.STAThread)
    sta.instance_pid = pid
    sta.get_current.return_value = current
    sta.get_queue_snapshot.return_value = queued or []
    sta.get_history.return_value = history or []
    sta.cancel_queued.return_value = False
    return sta


def test_vs_queue_status_all_instances():
    """instance_pid=None이면 전체 인스턴스 반환."""
    mock_sta1 = _mock_sta(100, current={"command_id": "abc", "name": "vs_file_open"})
    mock_sta2 = _mock_sta(200)

    with patch.object(com_bridge, "list_sta_pids", return_value=[100, 200]), \
         patch.object(com_bridge, "get_sta", side_effect=lambda p: {100: mock_sta1, 200: mock_sta2}[p]):
        result = queue_tools.vs_queue_status()

    assert result["total_instances"] == 2
    pids = [i["instance_pid"] for i in result["instances"]]
    assert 100 in pids
    assert 200 in pids


def test_vs_queue_status_specific_instance():
    """instance_pid 지정 시 해당 인스턴스만 반환."""
    mock_sta = _mock_sta(300, queued=[{"command_id": "x1", "name": "vs_build_solution"}])

    with patch.object(com_bridge, "list_sta_pids", return_value=[300]), \
         patch.object(com_bridge, "get_sta", return_value=mock_sta):
        result = queue_tools.vs_queue_status(instance_pid=300)

    assert result["total_instances"] == 1
    inst = result["instances"][0]
    assert inst["instance_pid"] == 300
    assert inst["queued_count"] == 1


def test_vs_queue_cancel_already_running():
    """실행 중인 명령 취소 시 'already_running' 반환."""
    running_cmd = {"command_id": "running-id", "name": "vs_build_solution"}
    mock_sta = _mock_sta(400, current=running_cmd)

    with patch.object(com_bridge, "list_sta_pids", return_value=[400]), \
         patch.object(com_bridge, "get_sta", return_value=mock_sta):
        result = queue_tools.vs_queue_cancel(command_id="running-id")

    assert result["status"] == "already_running"
    assert result["command_id"] == "running-id"


def test_vs_queue_cancel_success():
    """대기 중인 명령 취소 성공."""
    mock_sta = _mock_sta(500)
    mock_sta.cancel_queued.return_value = True

    with patch.object(com_bridge, "list_sta_pids", return_value=[500]), \
         patch.object(com_bridge, "get_sta", return_value=mock_sta):
        result = queue_tools.vs_queue_cancel(command_id="queued-id")

    assert result["status"] == "cancelled"
    mock_sta.cancel_queued.assert_called_once_with("queued-id")


def test_vs_queue_cancel_not_found():
    """존재하지 않는 command_id 취소 시 'not_found' 반환."""
    mock_sta = _mock_sta(600)
    mock_sta.cancel_queued.return_value = False

    with patch.object(com_bridge, "list_sta_pids", return_value=[600]), \
         patch.object(com_bridge, "get_sta", return_value=mock_sta):
        result = queue_tools.vs_queue_cancel(command_id="ghost-id")

    assert result["status"] == "not_found"


def test_vs_queue_history_default_limit():
    """history 기본 limit=20 적용."""
    history_data = [{"command_id": f"c{i}", "name": "cmd", "status": "done"} for i in range(5)]
    mock_sta = _mock_sta(700, history=history_data)

    with patch.object(com_bridge, "list_sta_pids", return_value=[700]), \
         patch.object(com_bridge, "get_sta", return_value=mock_sta):
        result = queue_tools.vs_queue_history()

    mock_sta.get_history.assert_called_once_with(limit=20)
    assert result["instances"][0]["count"] == 5


def test_vs_queue_history_custom_limit():
    """history custom limit 전달."""
    mock_sta = _mock_sta(800, history=[])

    with patch.object(com_bridge, "list_sta_pids", return_value=[800]), \
         patch.object(com_bridge, "get_sta", return_value=mock_sta):
        queue_tools.vs_queue_history(limit=5)

    mock_sta.get_history.assert_called_once_with(limit=5)


def test_vs_queue_status_empty():
    """STA 인스턴스 없을 때 빈 결과."""
    with patch.object(com_bridge, "list_sta_pids", return_value=[]):
        result = queue_tools.vs_queue_status()
    assert result["total_instances"] == 0
    assert result["instances"] == []
