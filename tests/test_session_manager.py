"""UT-003: session_manager 모듈 테스트"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from vs_mcp_server import com_bridge
from vs_mcp_server import session_manager as sm
from vs_mcp_server.session_manager import SessionManager, Session


def _fresh_manager() -> SessionManager:
    return SessionManager()


def test_create_session_returns_session():
    mgr = _fresh_manager()
    sess = mgr.create_session("sess-a")
    assert isinstance(sess, Session)
    assert sess.session_id == "sess-a"


def test_create_session_idempotent():
    mgr = _fresh_manager()
    s1 = mgr.create_session("sess-b")
    s2 = mgr.create_session("sess-b")
    assert s1 is s2


def test_get_session_returns_none_for_unknown():
    mgr = _fresh_manager()
    assert mgr.get_session("nonexistent") is None


def test_get_session_returns_created():
    mgr = _fresh_manager()
    mgr.create_session("sess-c")
    sess = mgr.get_session("sess-c")
    assert sess is not None
    assert sess.session_id == "sess-c"


def test_remove_session_returns_true():
    mgr = _fresh_manager()
    mgr.create_session("sess-d")
    result = mgr.remove_session("sess-d")
    assert result is True


def test_remove_session_returns_false_for_unknown():
    mgr = _fresh_manager()
    assert mgr.remove_session("ghost") is False


def test_remove_session_actually_removes():
    mgr = _fresh_manager()
    mgr.create_session("sess-e")
    mgr.remove_session("sess-e")
    assert mgr.get_session("sess-e") is None


def test_list_sessions():
    mgr = _fresh_manager()
    mgr.create_session("a")
    mgr.create_session("b")
    sessions = mgr.list_sessions()
    ids = [s["session_id"] for s in sessions]
    assert "a" in ids
    assert "b" in ids


def test_get_or_create_session():
    mgr = _fresh_manager()
    s1 = mgr.get_or_create_session("new-sess")
    s2 = mgr.get_or_create_session("new-sess")
    assert s1 is s2


def test_require_sta_raises_when_no_instance():
    mgr = _fresh_manager()
    mgr.create_session("unbound")
    try:
        mgr.require_sta("unbound")
        assert False, "RuntimeError should have been raised"
    except RuntimeError as e:
        assert "unbound" in str(e) or "vs_connect" in str(e)


def test_session_no_pid_by_default():
    mgr = _fresh_manager()
    sess = mgr.create_session("fresh")
    assert sess.instance_pid is None


def test_unbind_instance():
    mgr = _fresh_manager()
    mock_dte = MagicMock()
    with patch.object(com_bridge, "get_or_create_sta", return_value=MagicMock()), \
         patch.object(com_bridge, "get_sta", return_value=MagicMock()):
        mgr.bind_instance("sess-f", 9999, mock_dte)
        assert mgr.get_session("sess-f").instance_pid == 9999
        mgr.unbind_instance("sess-f")
        assert mgr.get_session("sess-f").instance_pid is None


def test_get_manager_singleton():
    m1 = sm.get_manager()
    m2 = sm.get_manager()
    assert m1 is m2
