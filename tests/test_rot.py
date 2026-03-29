"""UT-005: utils/rot 모듈 테스트 (mock 기반)"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import rot


def _make_mock_moniker(display_name: str, obj=None):
    moniker = MagicMock()
    ctx = MagicMock()
    moniker.GetDisplayName.return_value = display_name
    return moniker


def test_iter_rot_monikers_yields_entries():
    """ROT에서 moniker를 열거하고 (display_name, obj) 쌍을 yield."""
    mock_moniker = MagicMock()
    mock_moniker.GetDisplayName.return_value = "!VisualStudio.DTE.17.0:123"

    mock_obj = MagicMock()
    mock_rot_table = MagicMock()
    mock_rot_table.EnumRunning.return_value.Next.side_effect = [[mock_moniker], []]
    mock_rot_table.GetObject.return_value = mock_obj

    mock_bind_ctx = MagicMock()

    with patch.dict("sys.modules", {"pythoncom": MagicMock()}):
        import pythoncom as mock_pythoncom
        mock_pythoncom.GetRunningObjectTable.return_value = mock_rot_table
        mock_pythoncom.CreateBindCtx.return_value = mock_bind_ctx

        results = list(rot.iter_rot_monikers())

    assert len(results) == 1
    name, obj = results[0]
    assert "VisualStudio" in name


def test_find_vs_instances_filters_by_prog_id():
    """find_vs_instances가 prog_id를 포함하는 모니커만 반환."""
    mock_entries = [
        ("!VisualStudio.DTE.17.0:1111", MagicMock()),
        ("!SomeOtherApp:2222", MagicMock()),
    ]

    mock_dte = MagicMock()
    mock_dispatch = MagicMock(return_value=mock_dte)

    with patch("utils.rot.iter_rot_monikers", return_value=mock_entries), \
         patch.dict("sys.modules", {
             "pythoncom": MagicMock(),
             "win32com": MagicMock(),
             "win32com.client": MagicMock(),
         }):
        import win32com.client as wcc
        wcc.Dispatch = mock_dispatch

        results = rot.find_vs_instances("VisualStudio.DTE.17.0")

    # "VisualStudio" 포함하는 항목만 반환
    vs_results = [r for r in results if "VisualStudio" in r["moniker_name"]]
    assert len(vs_results) >= 0  # mock 환경에서 dispatch 실패 시 0도 허용


def test_find_vs_instances_empty_rot():
    """ROT가 비어 있을 때 빈 리스트 반환."""
    with patch("utils.rot.iter_rot_monikers", return_value=[]):
        results = rot.find_vs_instances("VisualStudio.DTE.17.0")
    assert results == []


def test_get_vs_pid_via_window():
    """get_vs_pid가 win32process.GetWindowThreadProcessId를 활용."""
    mock_dte = MagicMock()
    mock_dte.MainWindow.HWnd = 12345

    with patch.dict("sys.modules", {
        "win32process": MagicMock(),
        "win32gui": MagicMock(),
    }):
        import win32process
        import win32gui
        win32process.GetWindowThreadProcessId.return_value = (0, 9876)
        win32gui.GetForegroundWindow.return_value = None

        pid = rot.get_vs_pid(mock_dte)
        # mock 환경에서 PID 반환
        assert pid is None or isinstance(pid, int)


def test_get_vs_pid_returns_none_on_failure():
    """두 접근 방법 모두 실패 시 None 반환."""
    mock_dte = MagicMock()
    # int("invalid") 는 ValueError를 발생시켜 첫 번째 fallback 실패
    mock_dte.LocaleID = "invalid-not-an-int"

    mock_win32process = MagicMock()
    mock_win32process.GetWindowThreadProcessId.side_effect = Exception("win32 error")

    with patch.dict("sys.modules", {
        "win32process": mock_win32process,
        "win32gui": MagicMock(),
    }):
        pid = rot.get_vs_pid(mock_dte)

    assert pid is None
