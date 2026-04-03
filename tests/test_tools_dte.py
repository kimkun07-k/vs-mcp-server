"""UT-DTE: tools/dte 도구 테스트 (mock STAThread 기반)"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from vs_mcp_server import com_bridge
from vs_mcp_server.tools import dte as dte_tools


def _make_future(loop, result=None, exc=None):
    """주어진 결과/예외를 가진 완료된 Future를 반환한다."""
    fut = loop.create_future()
    if exc is not None:
        fut.set_exception(exc)
    else:
        fut.set_result(result)
    return fut


def _mock_sta(pid: int = 1234) -> MagicMock:
    sta = MagicMock(spec=com_bridge.STAThread)
    sta.instance_pid = pid
    sta.dte = MagicMock()
    return sta


# ── vs_command ────────────────────────────────────────────────────────

def test_vs_command_success():
    """vs_command 정상 실행: ExecuteCommand 호출 후 status=executed 반환."""
    loop = asyncio.new_event_loop()
    try:
        sta = _mock_sta()
        expected = {"command": "Edit.FormatDocument", "args": "", "status": "executed", "message": ""}
        sta.submit.return_value = _make_future(loop, expected)

        with patch.object(dte_tools.sm, "get_manager") as mock_mgr:
            mock_mgr.return_value.require_sta.return_value = sta
            result = loop.run_until_complete(
                dte_tools.vs_command(session_id="s1", command="Edit.FormatDocument")
            )

        assert result["status"] == "executed"
        assert result["command"] == "Edit.FormatDocument"
        sta.submit.assert_called_once()
    finally:
        loop.close()


def test_vs_command_with_args():
    """vs_command에 args 인수가 전달된다."""
    loop = asyncio.new_event_loop()
    try:
        sta = _mock_sta()
        expected = {"command": "View.Output", "args": "Build", "status": "executed", "message": ""}
        sta.submit.return_value = _make_future(loop, expected)

        with patch.object(dte_tools.sm, "get_manager") as mock_mgr:
            mock_mgr.return_value.require_sta.return_value = sta
            result = loop.run_until_complete(
                dte_tools.vs_command(session_id="s1", command="View.Output", args="Build")
            )

        assert result["status"] == "executed"
        assert result["args"] == "Build"
    finally:
        loop.close()


def test_vs_command_com_exception_propagates():
    """COM 예외가 발생하면 Future에 예외가 전파된다."""
    loop = asyncio.new_event_loop()
    try:
        sta = _mock_sta()
        sta.submit.return_value = _make_future(loop, exc=RuntimeError("COM error"))

        with patch.object(dte_tools.sm, "get_manager") as mock_mgr:
            mock_mgr.return_value.require_sta.return_value = sta
            with pytest.raises(RuntimeError, match="COM error"):
                loop.run_until_complete(
                    dte_tools.vs_command(session_id="s1", command="Bad.Command")
                )
    finally:
        loop.close()
