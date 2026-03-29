"""실제 Visual Studio 2022와의 통합 테스트

설계 원칙:
  - com_sta fixture가 모듈 전체 CoInitialize/CoUninitialize를 단독 관리
  - 개별 테스트에서 CoInitialize/CoUninitialize 호출 금지 (pywin32 중첩 CoUninit 버그 회피)
  - DTE를 메인 스레드에서 직접 호출
  - STAThread 없이 _SyncSTA 어댑터로 툴 함수 테스트

실행 방법:
  cd C:/Dallokan/vs-mcp-server
  python -m pytest tests/test_integration_vs.py -v -s
"""
import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import vs_instance_manager as vim

TEST_FILE = str(Path(__file__).parent.parent / "config.py")


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _pid_from_moniker(moniker_name: str) -> int:
    """모니커에서 PID 추출. 예: '!VisualStudio.DTE.17.0:12345' -> 12345"""
    return int(moniker_name.rsplit(":", 1)[-1])


def _is_process_alive(pid: int) -> bool:
    """tasklist로 PID가 실제 devenv.exe인지 확인한다."""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
        capture_output=True, text=True,
    )
    return "devenv.exe" in result.stdout.lower()


class _SyncSTA:
    """asyncio 컨텍스트에서 fn()을 직접 실행하는 최소 STA 어댑터.

    STAThread 대신 사용 — 메인 스레드(= com_sta 컨텍스트, STA 초기화됨)에서
    fn()을 동기 실행. 크로스-아파트먼트 문제 없음.
    """

    def __init__(self, instance_pid: int, dte) -> None:
        self.instance_pid = instance_pid
        self.dte = dte
        self._current: Optional[dict] = None
        self._history: list = []
        self._lock = threading.Lock()

    async def submit(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        session_id: Optional[str] = None,
        channel: str = "immediate",
        loop=None,
    ) -> Any:
        return fn()


# ---------------------------------------------------------------------------
# 모듈 전체 COM STA 픽스처 (단 한 번만 CoInitialize/CoUninitialize)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def com_sta():
    """모듈 전체 CoInitialize/CoUninitialize 단독 관리.
    개별 테스트에서 절대 CoInitialize/CoUninitialize를 호출하지 않는다.
    """
    import pythoncom
    pythoncom.CoInitialize()
    yield
    pythoncom.CoUninitialize()


# ---------------------------------------------------------------------------
# VS DTE 연결 픽스처
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def vs_ctx(com_sta):
    """VS 인스턴스를 찾아 DTE+PID를 반환한다.

    com_sta가 CoInitialize를 유지하므로 추가 CoInit 불필요.
    스테일 ROT 항목(실제 프로세스 없음)은 필터링한다.
    """
    from utils.rot import find_vs_instances

    all_entries = find_vs_instances(config.VS_PROG_ID)
    entries = [
        e for e in all_entries
        if _is_process_alive(_pid_from_moniker(e["moniker_name"]))
    ]

    if not entries:
        print(f"\n[fixture] VS 미실행 -> devenv.exe 실행 중 (솔루션 없음)...")
        proc = subprocess.Popen([config.VS_DEVENV_PATH])
        print(f"[fixture] proc.pid={proc.pid}, ROT 등록 대기...")
        deadline = time.monotonic() + config.timeouts["launch"]
        while time.monotonic() < deadline:
            vim._try_lose_focus(proc.pid)
            entries_raw = find_vs_instances(config.VS_PROG_ID)
            alive = [
                e for e in entries_raw
                if _is_process_alive(_pid_from_moniker(e["moniker_name"]))
            ]
            if alive:
                entries = alive
                break
            time.sleep(config.ROT_POLL_INTERVAL)
        assert entries, f"VS 실행 후 ROT 등록 타임아웃 ({config.timeouts['launch']}초)"
        print(f"[fixture] ROT 등록 확인")

    dte = entries[0]["dte"]
    pid = _pid_from_moniker(entries[0]["moniker_name"])
    print(f"[fixture] PID={pid} (모니커: {entries[0]['moniker_name']})")

    # VS MainWindow 준비 대기
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            _ = dte.MainWindow.Caption
            break
        except Exception:
            time.sleep(1)

    yield {"dte": dte, "pid": pid}


@pytest.fixture(scope="module")
def session_ctx(vs_ctx):
    """_SyncSTA를 session_manager에 등록하고 session_id를 반환한다."""
    import session_manager as sm
    import com_bridge

    dte = vs_ctx["dte"]
    pid = vs_ctx["pid"]
    session_id = "it-direct-01"

    sta = _SyncSTA(pid, dte)
    manager = sm.get_manager()
    manager.bind_instance(session_id, pid, dte)
    com_bridge._sta_registry[pid] = sta

    yield {"session_id": session_id, "dte": dte, "pid": pid, "sta": sta}

    manager.unbind_instance(session_id)
    com_bridge._sta_registry.pop(pid, None)


# ---------------------------------------------------------------------------
# IT-001: VS 실행 및 인스턴스 감지
# ---------------------------------------------------------------------------

def test_rot_finds_vs_instance(vs_ctx):
    """ROT에서 VS 인스턴스를 찾아 PID와 모니커를 반환한다."""
    from utils.rot import find_vs_instances

    # com_sta 컨텍스트 내에서 직접 호출 — 추가 CoInitialize 없음
    entries = find_vs_instances(config.VS_PROG_ID)
    alive = [e for e in entries if _is_process_alive(_pid_from_moniker(e["moniker_name"]))]

    assert len(alive) >= 1, "ROT에서 살아있는 VS 인스턴스를 찾을 수 없음"
    pid = _pid_from_moniker(alive[0]["moniker_name"])
    assert pid > 0
    assert "VisualStudio" in alive[0]["moniker_name"]
    print(f"\n  moniker={alive[0]['moniker_name']}, pid={pid} [OK]")


def test_vs_pid_matches_tasklist(vs_ctx):
    """모니커에서 추출한 PID가 실제 devenv.exe 프로세스다."""
    pid = vs_ctx["pid"]
    assert _is_process_alive(pid), (
        f"PID {pid}가 tasklist에서 devenv.exe가 아님"
    )
    print(f"\n  tasklist PID {pid} -> devenv.exe [OK]")


def test_get_vs_pid_returns_correct_pid(vs_ctx):
    """get_vs_pid()가 모니커에서 추출한 PID와 동일한 값을 반환한다."""
    from utils.rot import get_vs_pid

    dte = vs_ctx["dte"]
    reported = get_vs_pid(dte)
    real_pid = vs_ctx["pid"]

    print(f"\n  get_vs_pid={reported}, 모니커 PID={real_pid}")
    assert reported is not None, "get_vs_pid()가 None을 반환함"
    assert reported == real_pid, (
        f"get_vs_pid()={reported}가 모니커 PID={real_pid}와 불일치"
    )
    assert reported != 1033, "get_vs_pid()가 LocaleID(1033)를 반환 — 버그 미수정"
    print(f"  get_vs_pid -> {reported} [OK]")


# ---------------------------------------------------------------------------
# IT-002: DTE 기본 속성 접근
# ---------------------------------------------------------------------------

def test_dte_main_window_accessible(vs_ctx):
    """dte.MainWindow.Caption에 접근 가능하다."""
    caption = vs_ctx["dte"].MainWindow.Caption
    assert isinstance(caption, str) and len(caption) > 0
    print(f"\n  MainWindow.Caption='{caption}' [OK]")


def test_dte_solution_fullname(vs_ctx):
    """dte.Solution.FullName이 문자열을 반환한다."""
    sol = vs_ctx["dte"].Solution.FullName or ""
    assert isinstance(sol, str)
    print(f"\n  Solution.FullName='{sol}' [OK]")


def test_dte_version(vs_ctx):
    """dte.Version이 '17.' 로 시작한다 (VS 2022)."""
    version = vs_ctx["dte"].Version
    assert version.startswith("17."), f"VS 버전이 17.x가 아님: {version}"
    print(f"\n  DTE.Version='{version}' [OK]")


# ---------------------------------------------------------------------------
# IT-003: 파일 열기 (vs_file_open)
# ---------------------------------------------------------------------------

def test_file_open_config_py(session_ctx):
    """vs_file_open이 config.py를 VS에서 실제로 연다."""
    from tools.editor import vs_file_open

    result = asyncio.run(_acall(vs_file_open, session_id=session_ctx["session_id"], path=TEST_FILE))
    assert result["status"] == "opened", f"파일 열기 실패: {result}"
    assert result["path"] == TEST_FILE

    try:
        active = session_ctx["dte"].ActiveDocument.FullName or ""
    except Exception:
        active = ""

    assert active.lower().replace("\\", "/").endswith("config.py"), (
        f"ActiveDocument가 config.py가 아님: '{active}'"
    )
    print(f"\n  vs_file_open -> ActiveDocument='{active}' [OK]")


# ---------------------------------------------------------------------------
# IT-004: 열린 파일 목록 및 활성 파일
# ---------------------------------------------------------------------------

def test_file_list_open_contains_config(session_ctx):
    """vs_file_list_open이 열린 파일 목록을 반환하고 config.py를 포함한다."""
    from tools.editor import vs_file_list_open

    result = asyncio.run(_acall(vs_file_list_open, session_id=session_ctx["session_id"]))
    assert "files" in result
    assert result["count"] >= 1

    paths = [f["path"].lower().replace("\\", "/") for f in result["files"]]
    assert any("config.py" in p for p in paths), (
        f"config.py가 열린 파일 목록에 없음: {paths}"
    )
    print(f"\n  열린 파일 {result['count']}개, config.py 포함 [OK]")


def test_file_active_returns_position(session_ctx):
    """vs_file_active가 현재 파일 경로와 커서 위치를 반환한다."""
    from tools.editor import vs_file_active

    result = asyncio.run(_acall(vs_file_active, session_id=session_ctx["session_id"]))
    assert result.get("path"), f"활성 파일 path 없음: {result}"
    assert isinstance(result.get("line"), int)
    assert isinstance(result.get("column"), int)
    print(f"\n  vs_file_active -> {result['path']}:{result['line']}:{result['column']} [OK]")


# ---------------------------------------------------------------------------
# IT-005: 빌드 상태 조회
# ---------------------------------------------------------------------------

def test_build_status_returns_valid_state(session_ctx):
    """vs_build_status가 유효한 build_state와 last_build_failed_projects를 반환한다."""
    from tools.build import vs_build_status

    result = asyncio.run(_acall(vs_build_status, session_id=session_ctx["session_id"]))
    assert result["build_state"] in ("not_started", "in_progress", "done"), (
        f"예상치 못한 build_state: {result['build_state']}"
    )
    assert isinstance(result["last_build_failed_projects"], int)
    print(f"\n  build_state={result['build_state']}, "
          f"last_failed={result['last_build_failed_projects']} [OK]")


# ---------------------------------------------------------------------------
# IT-006: 브레이크포인트 CRUD
# ---------------------------------------------------------------------------

def test_breakpoint_list(session_ctx):
    """vs_debug_breakpoint list가 breakpoints 목록을 반환한다."""
    from tools.debug import vs_debug_breakpoint

    result = asyncio.run(_acall(vs_debug_breakpoint,
                                session_id=session_ctx["session_id"], action="list"))
    assert isinstance(result["breakpoints"], list)
    print(f"\n  초기 브레이크포인트 수: {result['count']} [OK]")


def test_breakpoint_add(session_ctx):
    """브레이크포인트를 추가하면 list에서 확인된다."""
    from tools.debug import vs_debug_breakpoint

    sid = session_ctx["session_id"]
    bp_line = 5

    add_result = asyncio.run(_acall(vs_debug_breakpoint,
                                    session_id=sid, action="add",
                                    file=TEST_FILE, line=bp_line))
    assert add_result["status"] == "added", f"add 실패: {add_result}"

    list_result = asyncio.run(_acall(vs_debug_breakpoint, session_id=sid, action="list"))
    found = any(
        bp["file"].lower().replace("\\", "/").endswith("config.py") and bp["line"] == bp_line
        for bp in list_result["breakpoints"]
    )
    assert found, f"추가한 bp가 목록에 없음: {list_result['breakpoints']}"
    print(f"\n  bp 추가 config.py:{bp_line} -> 목록 확인 [OK]")


def test_breakpoint_remove(session_ctx):
    """브레이크포인트 제거 후 목록에서 사라진다."""
    from tools.debug import vs_debug_breakpoint

    sid = session_ctx["session_id"]
    bp_line = 5

    remove_result = asyncio.run(_acall(vs_debug_breakpoint,
                                       session_id=sid, action="remove",
                                       file=TEST_FILE, line=bp_line))
    assert remove_result["status"] in ("removed", "not_found"), (
        f"예상치 못한 remove 결과: {remove_result}"
    )

    if remove_result["status"] == "removed":
        list_result = asyncio.run(_acall(vs_debug_breakpoint, session_id=sid, action="list"))
        still = any(
            bp["file"].lower().replace("\\", "/").endswith("config.py") and bp["line"] == bp_line
            for bp in list_result["breakpoints"]
        )
        assert not still, "제거 후에도 bp가 목록에 남아있음"
        print(f"\n  bp 제거 config.py:{bp_line} -> 삭제 확인 [OK]")
    else:
        print(f"\n  bp가 이미 없음 (not_found) [OK]")


# ---------------------------------------------------------------------------
# IT-008: 실제 STAThread 큐 경로
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sta_thread_ctx(vs_ctx):
    """실제 com_bridge.STAThread를 생성하고 DTE를 큐로 호출한다."""
    import com_bridge

    pid = vs_ctx["pid"]
    dte = vs_ctx["dte"]
    sta = com_bridge.STAThread(pid, dte)

    yield {"sta": sta, "pid": pid, "dte": dte}

    sta.shutdown()


def test_sta_thread_queue_executes_fn(sta_thread_ctx):
    """STAThread 큐 경로: STAThread 내부에서 ROT DTE 재획득 후 Version을 읽어온다.

    메인 스레드의 DTE 포인터는 해당 STA apartment에 귀속되므로 STAThread에서
    직접 사용할 수 없다 (RPC_E_WRONG_THREAD). STAThread 자체 STA 내에서
    find_vs_instances()로 새 프록시를 획득하는 것이 올바른 패턴이다.
    """
    sta = sta_thread_ctx["sta"]

    def _get_version_in_sta():
        """STAThread 내 STA에서 ROT DTE를 획득하여 Version을 반환한다."""
        from utils.rot import find_vs_instances
        import config as cfg
        entries = find_vs_instances(cfg.VS_PROG_ID)
        if not entries:
            raise RuntimeError("STAThread 내부에서 VS ROT 항목 없음")
        return entries[0]["dte"].Version

    async def _run():
        loop = asyncio.get_running_loop()
        fut = sta.submit("test_version_rot", _get_version_in_sta, loop=loop)
        return await asyncio.wait_for(fut, timeout=10.0)

    version = asyncio.run(_run())
    assert isinstance(version, str)
    assert version.startswith("17."), f"버전이 17.x가 아님: {version}"
    print(f"\n  STAThread 큐 (ROT 재획득) -> dte.Version='{version}' [OK]")


def test_sta_thread_history_recorded(sta_thread_ctx):
    """STAThread가 명령 이력을 기록한다."""
    sta = sta_thread_ctx["sta"]
    history = sta.get_history()
    assert len(history) >= 1, "이력이 없음 (test_sta_thread_queue_executes_fn 선행 필요)"
    assert history[-1]["status"] == "done", f"마지막 명령 상태가 done이 아님: {history[-1]}"
    assert history[-1]["name"] == "test_version_rot"
    print(f"\n  STAThread 이력 {len(history)}건, 마지막={history[-1]['name']} [OK]")


# ---------------------------------------------------------------------------
# IT-007: 디버거 모드 확인
# ---------------------------------------------------------------------------

def test_debugger_mode_design(vs_ctx):
    """Design 모드에서 dte.Debugger.CurrentMode == 1."""
    mode = int(vs_ctx["dte"].Debugger.CurrentMode)
    assert mode == 1, f"디버거 모드가 Design(1)이 아님: {mode}"
    print(f"\n  Debugger.CurrentMode={mode} (Design) [OK]")


def test_debug_stop_not_debugging(session_ctx):
    """Design 모드에서 vs_debug_stop() -> 'not_debugging'."""
    from tools.debug import vs_debug_stop

    result = asyncio.run(_acall(vs_debug_stop, session_id=session_ctx["session_id"]))
    assert result["status"] == "not_debugging", f"예상치 못한 결과: {result}"
    print(f"\n  vs_debug_stop (Design모드) -> 'not_debugging' [OK]")


# ---------------------------------------------------------------------------
# IT-009: 파일 커서 이동 (vs_file_goto)
# ---------------------------------------------------------------------------

def test_file_goto_moves_cursor(session_ctx):
    """vs_file_goto가 지정한 라인으로 커서를 이동시킨다."""
    from tools.editor import vs_file_goto, vs_file_active

    target_line = 3
    result = asyncio.run(_acall(vs_file_goto,
                                session_id=session_ctx["session_id"],
                                path=TEST_FILE, line=target_line, column=1))
    assert result["status"] == "moved", f"goto 실패: {result}"
    assert result["line"] == target_line
    assert result["column"] == 1

    active = asyncio.run(_acall(vs_file_active, session_id=session_ctx["session_id"]))
    assert active["line"] == target_line, (
        f"커서가 {target_line}번 라인이 아님: {active['line']}"
    )
    print(f"\n  vs_file_goto -> line={active['line']}, column={active['column']} [OK]")


# ---------------------------------------------------------------------------
# IT-010: 범위 하이라이트 (vs_file_highlight)
# ---------------------------------------------------------------------------

def test_file_highlight_selects_range(session_ctx):
    """vs_file_highlight가 지정 라인 범위를 선택 상태로 만든다."""
    from tools.editor import vs_file_highlight

    result = asyncio.run(_acall(vs_file_highlight,
                                session_id=session_ctx["session_id"],
                                path=TEST_FILE, start_line=1, end_line=2))
    assert result["status"] == "highlighted", f"highlight 실패: {result}"
    assert result["start_line"] == 1
    assert result["end_line"] == 2
    print(f"\n  vs_file_highlight -> lines 1-2 highlighted [OK]")


# ---------------------------------------------------------------------------
# IT-011: 선택 텍스트 조회 (vs_file_selection)
# ---------------------------------------------------------------------------

def test_file_selection_returns_position(session_ctx):
    """vs_file_selection이 현재 선택 위치와 텍스트를 반환한다."""
    from tools.editor import vs_file_selection

    result = asyncio.run(_acall(vs_file_selection, session_id=session_ctx["session_id"]))
    assert result.get("path"), f"path 없음: {result}"
    assert isinstance(result.get("start_line"), int), f"start_line이 int가 아님: {result}"
    assert isinstance(result.get("end_line"), int), f"end_line이 int가 아님: {result}"
    assert "text" in result
    print(f"\n  vs_file_selection -> lines {result['start_line']}-{result['end_line']}, "
          f"text='{result['text'][:40].strip()}' [OK]")


# ---------------------------------------------------------------------------
# async 호출 헬퍼
# ---------------------------------------------------------------------------

async def _acall(fn, **kwargs):
    return await fn(**kwargs)
