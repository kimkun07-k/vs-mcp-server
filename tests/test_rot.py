"""UT-005: utils/rot 모듈 테스트 (mock 기반)"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    """find_vs_instances가 prog_id 미포함 모니커를 제외하고 일치하는 것만 반환."""
    mock_obj_vs = MagicMock()
    mock_obj_other = MagicMock()
    mock_entries = [
        ("!VisualStudio.DTE.17.0:1111", mock_obj_vs),
        ("!SomeOtherApp:2222", mock_obj_other),
    ]

    mock_dte = MagicMock()
    mock_pythoncom = MagicMock()
    mock_wcc = MagicMock()
    mock_wcc.Dispatch.return_value = mock_dte
    # win32com.client.Dispatch 접근 경로 양쪽 모두 패치
    mock_win32com = MagicMock()
    mock_win32com.client = mock_wcc

    with patch("utils.rot.iter_rot_monikers", return_value=mock_entries), \
         patch.dict("sys.modules", {
             "pythoncom": mock_pythoncom,
             "win32com": mock_win32com,
             "win32com.client": mock_wcc,
         }):
        results = rot.find_vs_instances("VisualStudio.DTE.17.0")

    # "SomeOtherApp" 항목은 반드시 제외
    moniker_names = [r["moniker_name"] for r in results]
    assert all("SomeOtherApp" not in n for n in moniker_names)
    # VisualStudio 항목은 포함 (Dispatch 성공 시)
    assert len(results) == 1
    assert "VisualStudio" in results[0]["moniker_name"]


def test_find_vs_instances_empty_rot():
    """ROT가 비어 있을 때 빈 리스트 반환."""
    with patch("utils.rot.iter_rot_monikers", return_value=[]):
        results = rot.find_vs_instances("VisualStudio.DTE.17.0")
    assert results == []


def test_get_vs_pid_via_window():
    """get_vs_pid가 win32process.GetWindowThreadProcessId로 PID 9876 반환."""
    mock_dte = MagicMock()
    # LocaleID를 문자열로 설정 → int() 변환 실패 → 두 번째 경로(win32)로 진행
    mock_dte.LocaleID = "invalid"
    mock_dte.MainWindow.HWnd = 12345

    mock_win32process = MagicMock()
    mock_win32process.GetWindowThreadProcessId.return_value = (0, 9876)

    with patch.dict("sys.modules", {
        "win32process": mock_win32process,
        "win32gui": MagicMock(),
    }):
        pid = rot.get_vs_pid(mock_dte)

    assert pid == 9876


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
