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

from vs_mcp_server import config
from vs_mcp_server import vs_instance_manager as vim

TEST_FILE = str(Path(__file__).parent.parent / "vs_mcp_server" / "config.py")
DEBUG_PROGRAM_CS = str(Path(__file__).parent / "debug_target" / "Program.cs")
DEBUG_SLN = str(Path(__file__).parent / "debug_target" / "DebugTarget.sln")
ERROR_SLN = str(Path(__file__).parent / "error_target" / "ErrorTarget.sln")
BP_LINE = 5  # Console.WriteLine in Program.cs


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
    from vs_mcp_server.utils.rot import find_vs_instances

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
    from vs_mcp_server import session_manager as sm
    from vs_mcp_server import com_bridge

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
    from vs_mcp_server.utils.rot import find_vs_instances

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
    from vs_mcp_server.utils.rot import get_vs_pid

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
    from vs_mcp_server.tools.editor import vs_file_open

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
    from vs_mcp_server.tools.editor import vs_file_list_open

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
    from vs_mcp_server.tools.editor import vs_file_active

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
    from vs_mcp_server.tools.build import vs_build_status

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
    from vs_mcp_server.tools.debug import vs_debug_breakpoint

    result = asyncio.run(_acall(vs_debug_breakpoint,
                                session_id=session_ctx["session_id"], action="list"))
    assert isinstance(result["breakpoints"], list)
    print(f"\n  초기 브레이크포인트 수: {result['count']} [OK]")


def test_breakpoint_add(session_ctx):
    """브레이크포인트를 추가하면 list에서 확인된다."""
    from vs_mcp_server.tools.debug import vs_debug_breakpoint

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
    from vs_mcp_server.tools.debug import vs_debug_breakpoint

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
    from vs_mcp_server import com_bridge

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
        from vs_mcp_server.utils.rot import find_vs_instances
        from vs_mcp_server import config as cfg
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
    from vs_mcp_server.tools.debug import vs_debug_stop

    result = asyncio.run(_acall(vs_debug_stop, session_id=session_ctx["session_id"]))
    assert result["status"] == "not_debugging", f"예상치 못한 결과: {result}"
    print(f"\n  vs_debug_stop (Design모드) -> 'not_debugging' [OK]")


# ---------------------------------------------------------------------------
# IT-009: 파일 커서 이동 (vs_file_goto)
# ---------------------------------------------------------------------------

def test_file_goto_moves_cursor(session_ctx):
    """vs_file_goto가 지정한 라인으로 커서를 이동시킨다."""
    from vs_mcp_server.tools.editor import vs_file_goto, vs_file_active

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
    from vs_mcp_server.tools.editor import vs_file_highlight

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
    from vs_mcp_server.tools.editor import vs_file_selection

    result = asyncio.run(_acall(vs_file_selection, session_id=session_ctx["session_id"]))
    assert result.get("path"), f"path 없음: {result}"
    assert isinstance(result.get("start_line"), int), f"start_line이 int가 아님: {result}"
    assert isinstance(result.get("end_line"), int), f"end_line이 int가 아님: {result}"
    assert "text" in result
    print(f"\n  vs_file_selection -> lines {result['start_line']}-{result['end_line']}, "
          f"text='{result['text'][:40].strip()}' [OK]")


# ---------------------------------------------------------------------------
# IT-018: DTE 범용 명령 실행 (vs_command)
# ---------------------------------------------------------------------------

def test_vs_command_executes_successfully(session_ctx):
    """vs_command가 유효한 VS 명령을 실행하고 status=executed를 반환한다."""
    from vs_mcp_server.tools.dte import vs_command

    result = asyncio.run(_acall(vs_command,
                                session_id=session_ctx["session_id"],
                                command="Edit.LineEnd"))
    assert result["status"] == "executed", f"vs_command 실패: {result}"
    assert result["command"] == "Edit.LineEnd"
    print(f"\n  vs_command('Edit.LineEnd') -> status={result['status']} [OK]")


def test_vs_command_with_args(session_ctx):
    """vs_command에 args 인수가 정상적으로 전달된다."""
    from vs_mcp_server.tools.dte import vs_command

    result = asyncio.run(_acall(vs_command,
                                session_id=session_ctx["session_id"],
                                command="Edit.GoTo",
                                args="1"))
    assert result["status"] == "executed", f"vs_command with args 실패: {result}"
    assert result["args"] == "1"
    print(f"\n  vs_command('Edit.GoTo', '1') -> status={result['status']} [OK]")


def test_vs_command_invalid_raises(session_ctx):
    """존재하지 않는 명령을 실행하면 COM 예외가 전파된다."""
    from vs_mcp_server.tools.dte import vs_command

    with pytest.raises(Exception):
        asyncio.run(_acall(vs_command,
                            session_id=session_ctx["session_id"],
                            command="Nonexistent.FakeCommand.12345"))
    print(f"\n  vs_command('Nonexistent.FakeCommand.12345') -> 예외 전파 [OK]")


# ---------------------------------------------------------------------------
# 실제 디버깅 세션 픽스처 (IT-012~017)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def debug_vs_ctx(com_sta, vs_ctx):
    """DebugTarget.sln으로 새 VS 인스턴스를 실행하고 Debug 빌드까지 완료한다.

    vs_ctx VS(비디버그 테스트용)를 먼저 종료하여 2개 동시 실행으로 인한
    COM RPC_E_CALL_REJECTED(0x80010001) 를 방지한다.
    """
    from vs_mcp_server.utils.rot import find_vs_instances

    # 비디버그 VS 종료 (이미 완료된 테스트이므로 안전하게 Quit)
    try:
        vs_ctx["dte"].Quit()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not _is_process_alive(vs_ctx["pid"]):
                break
            time.sleep(0.5)
    except Exception:
        pass

    proc = subprocess.Popen([config.VS_DEVENV_PATH, DEBUG_SLN])
    print(f"\n[debug_vs_ctx] 새 VS 시작 proc.pid={proc.pid}")

    # ROT 등록 대기 (proc.pid 기준으로 새 인스턴스만 탐색)
    deadline = time.monotonic() + config.timeouts["launch"]
    new_dte = None
    new_pid = None
    while time.monotonic() < deadline:
        vim._try_lose_focus(proc.pid)
        for entry in find_vs_instances(config.VS_PROG_ID):
            m_pid = _pid_from_moniker(entry["moniker_name"])
            if m_pid == proc.pid and _is_process_alive(m_pid):
                new_dte = entry["dte"]
                new_pid = m_pid
                break
        if new_dte:
            break
        time.sleep(config.ROT_POLL_INTERVAL)

    assert new_dte is not None, f"새 VS ROT 등록 타임아웃 (proc.pid={proc.pid})"
    print(f"[debug_vs_ctx] ROT 등록 확인 pid={new_pid}")

    # 솔루션 로드 + MainWindow 준비 대기
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            _ = new_dte.MainWindow.Caption
            if new_dte.Solution.IsOpen:
                break
        except Exception:
            pass
        time.sleep(1)
    print(f"[debug_vs_ctx] 솔루션 로드: {new_dte.Solution.FullName}")

    # 동기 빌드 — COM 이벤트 핸들러 없이 WaitForBuildToFinish=True 사용
    print("[debug_vs_ctx] Debug 빌드 시작...")
    sb = new_dte.Solution.SolutionBuild
    sb.Build(WaitForBuildToFinish=True)
    assert int(sb.LastBuildInfo) == 0, f"DebugTarget 빌드 실패: LastBuildInfo={sb.LastBuildInfo}"
    print("[debug_vs_ctx] 빌드 완료")

    yield {"dte": new_dte, "pid": new_pid}

    # 정리: 디버거 중단 후 VS 종료
    try:
        if int(new_dte.Debugger.CurrentMode) != 1:
            new_dte.Debugger.Stop(WaitForDesignMode=True)
    except Exception:
        pass
    try:
        new_dte.Quit()
    except Exception:
        pass


@pytest.fixture(scope="module")
def debug_session_ctx(debug_vs_ctx):
    """새 VS 인스턴스에 _SyncSTA 세션을 바인딩한다."""
    from vs_mcp_server import session_manager as sm
    from vs_mcp_server import com_bridge

    dte = debug_vs_ctx["dte"]
    pid = debug_vs_ctx["pid"]
    session_id = "it-debug-01"

    sta = _SyncSTA(pid, dte)
    manager = sm.get_manager()
    manager.bind_instance(session_id, pid, dte)
    com_bridge._sta_registry[pid] = sta

    yield {"session_id": session_id, "dte": dte, "pid": pid}

    manager.unbind_instance(session_id)
    com_bridge._sta_registry.pop(pid, None)


# ---------------------------------------------------------------------------
# IT-012: 브레이크포인트 설정 + 디버거 시작 → Break 모드 진입 확인
# ---------------------------------------------------------------------------

def test_debug_breakpoint_hit(debug_session_ctx):
    """Program.cs:5에 BP 추가 후 디버거 시작, 실제로 Break 모드에 진입한다."""
    from vs_mcp_server.tools.debug import vs_debug_breakpoint, vs_debug_start

    sid = debug_session_ctx["session_id"]
    dte = debug_session_ctx["dte"]

    # 기존 브레이크포인트 전체 제거 (클린 상태)
    bps = dte.Debugger.Breakpoints
    for i in range(bps.Count, 0, -1):
        try:
            bps.Item(i).Delete()
        except Exception:
            pass

    # Program.cs:BP_LINE 브레이크포인트 추가
    add_result = asyncio.run(_acall(vs_debug_breakpoint,
                                    session_id=sid, action="add",
                                    file=DEBUG_PROGRAM_CS, line=BP_LINE))
    assert add_result["status"] == "added", f"bp 추가 실패: {add_result}"
    print(f"\n  bp 추가 Program.cs:{BP_LINE} [OK]")

    # 디버거 시작 — wait_for_break=False (blocking 없이 즉시 반환)
    start_result = asyncio.run(_acall(vs_debug_start, session_id=sid, wait_for_break=False))
    assert start_result["status"] in ("started", "already_running"), f"시작 실패: {start_result}"
    print(f"  디버거 시작 -> mode={start_result['mode']}")

    # Break 모드 진입 폴링 (최대 30초)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if int(dte.Debugger.CurrentMode) == 2:
            break
        time.sleep(0.5)

    assert int(dte.Debugger.CurrentMode) == 2, (
        "Break 모드 진입 실패 — 브레이크포인트에 걸리지 않음 (30초 타임아웃)"
    )
    print(f"  Break 모드 진입 확인 [OK]")


# ---------------------------------------------------------------------------
# IT-013: Break 모드에서 로컬 변수 조회 (vs_debug_locals)
# ---------------------------------------------------------------------------

def test_debug_locals_in_break(debug_session_ctx):
    """Break 모드에서 vs_debug_locals가 x=42, y=50, msg를 반환한다."""
    from vs_mcp_server.tools.debug import vs_debug_locals

    result = asyncio.run(_acall(vs_debug_locals, session_id=debug_session_ctx["session_id"]))
    assert result["count"] > 0, f"로컬 변수가 없음: {result}"

    names = {v["name"] for v in result["locals"]}
    assert "x" in names, f"'x' 없음: {names}"
    assert "y" in names, f"'y' 없음: {names}"
    assert "msg" in names, f"'msg' 없음: {names}"

    x_var = next(v for v in result["locals"] if v["name"] == "x")
    y_var = next(v for v in result["locals"] if v["name"] == "y")
    assert x_var["value"] == "42", f"x != 42: {x_var['value']}"
    assert y_var["value"] == "50", f"y != 50: {y_var['value']}"
    print(f"\n  로컬 변수 {result['count']}개: x={x_var['value']}, y={y_var['value']} [OK]")


# ---------------------------------------------------------------------------
# IT-014: Break 모드에서 표현식 평가 (vs_debug_evaluate)
# ---------------------------------------------------------------------------

def test_debug_evaluate_in_break(debug_session_ctx):
    """Break 모드에서 표현식 평가가 올바른 값을 반환한다."""
    from vs_mcp_server.tools.debug import vs_debug_evaluate

    sid = debug_session_ctx["session_id"]

    result_sum = asyncio.run(_acall(vs_debug_evaluate, session_id=sid, expression="x + y"))
    assert result_sum["is_valid"], f"x+y 평가 실패: {result_sum}"
    assert result_sum["value"] == "92", f"x+y != 92: {result_sum['value']}"
    print(f"\n  evaluate('x + y') = {result_sum['value']} [OK]")

    result_msg = asyncio.run(_acall(vs_debug_evaluate, session_id=sid, expression="msg"))
    assert result_msg["is_valid"], f"msg 평가 실패: {result_msg}"
    assert "hello from debugger" in result_msg["value"], (
        f"msg 값 불일치: {result_msg['value']}"
    )
    print(f"  evaluate('msg') = {result_msg['value']} [OK]")


# ---------------------------------------------------------------------------
# IT-015: Break 모드에서 콜스택 조회 (vs_debug_callstack)
# ---------------------------------------------------------------------------

def test_debug_callstack_in_break(debug_session_ctx):
    """Break 모드에서 vs_debug_callstack이 최소 1개 프레임을 반환한다.

    .NET 8 관리 코드에서는 StackFrames 순회가 동작하지 않아 CurrentStackFrame
    폴백으로 1개 프레임을 반환한다. file/line은 PDB 정보 가용 여부에 따라 빈 값일 수 있다.
    """
    from vs_mcp_server.tools.debug import vs_debug_callstack

    result = asyncio.run(_acall(vs_debug_callstack, session_id=debug_session_ctx["session_id"]))
    assert result["depth"] >= 1, f"콜스택 프레임이 없음 (CurrentStackFrame 폴백도 실패): {result}"

    top = result["frames"][0]
    # function 이름은 존재해야 함 (빈 문자열이어도 오류 아님 — .NET 8 제약)
    assert isinstance(top["function"], str)
    print(f"\n  콜스택 {result['depth']}프레임, top='{top['function']}' line={top['line']} [OK]")


# ---------------------------------------------------------------------------
# IT-016: Break 모드에서 Step Over (vs_debug_step)
# ---------------------------------------------------------------------------

def test_debug_step_over_in_break(debug_session_ctx):
    """Break 모드에서 step over 후 다음 라인으로 이동한다."""
    from vs_mcp_server.tools.debug import vs_debug_step

    result = asyncio.run(_acall(vs_debug_step,
                                session_id=debug_session_ctx["session_id"],
                                step_type="over"))
    assert result["mode"] == "break", f"step 후 Break 모드 아님: {result['mode']}"
    assert result["line"] > BP_LINE, f"step 후 라인이 {BP_LINE}을 초과하지 않음: {result['line']}"
    print(f"\n  step over -> line={result['line']}, mode={result['mode']} [OK]")


# ---------------------------------------------------------------------------
# IT-017: Break 모드에서 디버거 중단 (vs_debug_stop)
# ---------------------------------------------------------------------------

def test_debug_stop_from_break(debug_session_ctx):
    """Break 모드에서 vs_debug_stop이 Design 모드로 복귀한다."""
    from vs_mcp_server.tools.debug import vs_debug_stop

    result = asyncio.run(_acall(vs_debug_stop, session_id=debug_session_ctx["session_id"]))
    assert result["status"] == "stopped", f"stop 실패: {result}"
    assert result["mode"] == "design"
    print(f"\n  vs_debug_stop -> status={result['status']}, mode={result['mode']} [OK]")


# ---------------------------------------------------------------------------
# IT-019: vs_error_list — 실제 에러/경고 반환 검증
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def error_vs_ctx(com_sta):
    """ErrorTarget.sln으로 VS를 실행하고 빌드한다 (에러/경고 발생 확인용).

    독립적 픽스처 — debug_vs_ctx와 무관하게 자체 VS 인스턴스를 사용한다.
    ErrorTarget은 의도적으로 CS0103 에러와 CS1030 경고를 포함한다.
    """
    from vs_mcp_server.utils.rot import find_vs_instances

    proc = subprocess.Popen([config.VS_DEVENV_PATH, ERROR_SLN])
    print(f"\n[error_vs_ctx] VS 시작 proc.pid={proc.pid} (ErrorTarget.sln)")

    # ROT 등록 대기
    deadline = time.monotonic() + config.timeouts["launch"]
    new_dte = None
    new_pid = None
    while time.monotonic() < deadline:
        vim._try_lose_focus(proc.pid)
        for entry in find_vs_instances(config.VS_PROG_ID):
            m_pid = _pid_from_moniker(entry["moniker_name"])
            if m_pid == proc.pid and _is_process_alive(m_pid):
                new_dte = entry["dte"]
                new_pid = m_pid
                break
        if new_dte:
            break
        time.sleep(config.ROT_POLL_INTERVAL)

    assert new_dte is not None, f"VS ROT 등록 타임아웃 (proc.pid={proc.pid})"
    print(f"[error_vs_ctx] ROT 등록 확인 pid={new_pid}")

    # 솔루션 로드 + MainWindow 준비 대기
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            _ = new_dte.MainWindow.Caption
            if new_dte.Solution.IsOpen:
                break
        except Exception:
            pass
        time.sleep(1)
    print(f"[error_vs_ctx] 솔루션 로드: {new_dte.Solution.FullName}")

    # 빌드 (실패 예상)
    print("[error_vs_ctx] 빌드 시작 (에러 예상)...")
    sb = new_dte.Solution.SolutionBuild
    sb.Build(WaitForBuildToFinish=True)
    failed = int(sb.LastBuildInfo)
    print(f"[error_vs_ctx] 빌드 완료: LastBuildInfo={failed} (1=프로젝트 실패)")
    assert failed > 0, "ErrorTarget 빌드가 성공함 — 의도적 에러가 없음"

    yield {"dte": new_dte, "pid": new_pid}

    # 정리: VS 종료
    try:
        new_dte.Quit()
    except Exception:
        pass


@pytest.fixture(scope="module")
def error_session_ctx(error_vs_ctx):
    """에러 VS에 _SyncSTA 세션을 바인딩한다.

    bind_instance()를 호출하면 real STAThread가 생성되어
    background에서 ROT 폴링 → COM 충돌(RPC_E_CALL_REJECTED) 발생.
    _SyncSTA만 직접 등록하여 이를 방지한다.
    """
    from vs_mcp_server import session_manager as sm
    from vs_mcp_server import com_bridge

    dte = error_vs_ctx["dte"]
    pid = error_vs_ctx["pid"]
    session_id = "it-error-01"

    sta = _SyncSTA(pid, dte)
    manager = sm.get_manager()
    session = manager.get_or_create_session(session_id)
    session.instance_pid = pid
    com_bridge._sta_registry[pid] = sta

    yield {"session_id": session_id, "dte": dte, "pid": pid}

    manager.unbind_instance(session_id)
    com_bridge._sta_registry.pop(pid, None)


def test_error_list_returns_actual_errors(error_session_ctx):
    """빌드 실패 후 vs_error_list가 Build Output에서 실제 에러를 파싱한다.

    ErrorTarget의 Program.cs에는 CS0103 에러가 있으므로,
    errors 배열에 1개 이상의 항목이 있어야 한다.
    DTE1 OutputWindow의 Build pane 텍스트를 정규식으로 파싱한다.
    """
    from vs_mcp_server.tools.build import vs_error_list

    result = asyncio.run(_acall(vs_error_list,
                                session_id=error_session_ctx["session_id"],
                                include_warnings=True,
                                include_messages=True))

    assert len(result["errors"]) > 0, (
        f"vs_error_list가 빈 errors를 반환함. result={result}"
    )
    assert result["error_count"] > 0

    error_descriptions = " ".join(e["description"] for e in result["errors"])
    assert "CS0103" in error_descriptions or "undefinedVar" in error_descriptions, (
        f"예상된 CS0103 에러가 없음: {result['errors']}"
    )
    print(f"\n  errors: {result['error_count']}개")
    for e in result["errors"]:
        print(f"    [{e.get('error_code', '')}] {e['file']}:{e['line']} {e['description'][:80]}")


def test_error_list_returns_actual_warnings(error_session_ctx):
    """빌드 실패 후 vs_error_list가 Build Output에서 실제 경고를 파싱한다.

    ErrorTarget의 Program.cs에는 #warning CS1030 경고가 있으므로,
    warnings 배열에 1개 이상의 항목이 있어야 한다.
    """
    from vs_mcp_server.tools.build import vs_error_list

    result = asyncio.run(_acall(vs_error_list,
                                session_id=error_session_ctx["session_id"],
                                include_warnings=True))

    assert "warnings" in result, f"warnings 키 없음: {result.keys()}"
    assert len(result["warnings"]) > 0, (
        f"vs_error_list가 빈 warnings를 반환함. result={result}"
    )
    assert result["warning_count"] > 0

    warn_descriptions = " ".join(w["description"] for w in result["warnings"])
    assert "CS1030" in warn_descriptions or "TEST_WARNING" in warn_descriptions, (
        f"예상된 CS1030 경고가 없음: {result['warnings']}"
    )
    print(f"\n  warnings: {result['warning_count']}개")
    for w in result["warnings"]:
        print(f"    [{w.get('error_code', '')}] {w['file']}:{w['line']} {w['description'][:80]}")


# ---------------------------------------------------------------------------
# async 호출 헬퍼
# ---------------------------------------------------------------------------

async def _acall(fn, **kwargs):
    return await fn(**kwargs)
