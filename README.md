# vs-mcp-server

Visual Studio 2022를 Claude Code(MCP)에서 제어하는 서버.
COM/DTE 자동화를 통해 파일 열기, 빌드, 디버거 제어, 브레이크포인트 관리 등을 MCP 도구로 제공한다.

---

## 아키텍처

```
Claude Code (MCP Client)
        │  stdio
        ▼
  server.py  ── 22개 도구 등록, 라우팅
        │
  session_manager.py  ── 세션 ↔ VS 인스턴스 바인딩
        │
  com_bridge.STAThread  ── VS 인스턴스 1개당 STA 전용 스레드
        │  immediate / long_running 채널
        ▼
  EnvDTE COM (devenv.exe)  ── out-of-process COM 서버
        │  ROT (Running Object Table)
        ▼
  Visual Studio 2022
```

**핵심 설계 원칙:**

- **STA 전용 스레드**: EnvDTE COM은 STA(Single-Threaded Apartment)에서만 안정적으로 동작. VS 인스턴스 1개당 `STAThread` 1개를 할당.
- **듀얼 채널**: `immediate`(에디터 조작 등 빠른 명령) / `long_running`(빌드 등 오래 걸리는 명령) 채널을 분리하여 빌드 중에도 즉시 채널 처리 가능.
- **ROT 탐색**: `Running Object Table`에서 `VisualStudio.DTE.17.0` 모니커를 검색하여 실행 중인 VS 인스턴스에 연결.
- **크래시 로깅**: `vs_debug_evaluate` 결과, COM 오류, 타임아웃을 `logs/vs_mcp_crash.jsonl`에 자동 기록.

---

## 사전 요구사항

- Windows 10/11
- Python 3.11+
- Visual Studio 2022 (Community / Professional / Enterprise)
- `pywin32`, `mcp` 패키지

```bash
pip install -r requirements.txt
```

---

## MCP 서버 등록

`claude_desktop_config.json` 또는 `.mcp.json`에 추가:

```json
{
  "mcpServers": {
    "vs": {
      "command": "python",
      "args": ["C:/path/to/vs-mcp-server/server.py"]
    }
  }
}
```

VS 설치 경로가 기본값(`Community`)과 다르면 환경변수로 지정:

```bash
VS_DEVENV_PATH=C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\devenv.exe
```

---

## 도구 목록

### 인스턴스 관리

| 도구 | 설명 |
|------|------|
| `vs_list_instances` | ROT에서 실행 중인 VS 인스턴스 목록(PID, 솔루션 경로) 반환 |
| `vs_connect` | PID 또는 솔루션 경로로 VS 인스턴스에 연결, 세션 바인딩 |
| `vs_launch` | `devenv.exe` 실행 후 ROT 폴링으로 자동 연결 |
| `vs_close` | VS 인스턴스 종료 (`save_all` 옵션) |

### 에디터 — Claude → 유저

| 도구 | 설명 |
|------|------|
| `vs_file_open` | 파일을 VS 에디터에 열어 유저에게 보여줌 |
| `vs_file_goto` | 특정 파일의 라인/컬럼으로 커서 이동 |
| `vs_file_highlight` | 라인 범위를 선택 상태로 강조 (`add_bookmark` 옵션) |
| `vs_file_list_open` | 열린 파일 목록과 저장 상태 반환 |

### 에디터 — 유저 → Claude

| 도구 | 설명 |
|------|------|
| `vs_file_active` | 유저가 현재 포커스 중인 파일 경로와 커서 위치(line/column) 반환 |
| `vs_file_selection` | 유저가 드래그로 선택한 텍스트와 범위 반환 |

### 빌드

| 도구 | 설명 |
|------|------|
| `vs_build_solution` | 솔루션 전체 비동기 빌드, 결과 반환 |
| `vs_build_project` | 특정 프로젝트만 빌드 |
| `vs_build_status` | 마지막 빌드 결과 및 현재 빌드 상태 조회 |
| `vs_error_list` | Error List 창의 에러/경고를 파일 경로·라인 번호와 함께 반환 |

### 디버깅

| 도구 | 설명 |
|------|------|
| `vs_debug_start` | 디버깅 시작 (`Debugger.Go`), `wait_for_break` 옵션 |
| `vs_debug_stop` | 디버깅 종료, Design 모드로 복귀 |
| `vs_debug_breakpoint` | 브레이크포인트 추가(`add`)/제거(`remove`)/목록 조회(`list`) |
| `vs_debug_step` | 스텝 실행 (`into` / `over` / `out`) |
| `vs_debug_locals` | Break 모드에서 현재 스택 프레임의 로컬 변수 반환 |
| `vs_debug_evaluate` | Break 모드에서 표현식 평가 및 결과 반환 |
| `vs_debug_callstack` | 현재 스레드의 콜스택(함수명/파일/라인) 반환 |

### 큐 관리

| 도구 | 설명 |
|------|------|
| `vs_queue_status` | 현재 실행 중인 명령과 대기 큐 목록 반환 |
| `vs_queue_cancel` | 대기 큐의 명령을 `command_id`로 취소 |
| `vs_queue_history` | 최근 N건의 명령 이력(완료/실패/취소) 반환 |

---

## 사용 예시

```
# VS 연결
vs_connect(session_id="s1", solution_path="C:/MyProject/MyProject.sln")

# 파일 열고 특정 라인으로 이동
vs_file_open(session_id="s1", path="C:/MyProject/src/main.cpp")
vs_file_goto(session_id="s1", path="C:/MyProject/src/main.cpp", line=42)

# 브레이크포인트 추가 후 디버깅 시작
vs_debug_breakpoint(session_id="s1", action="add", file="src/main.cpp", line=42)
vs_debug_start(session_id="s1", wait_for_break=True)

# Break 모드에서 변수 평가
vs_debug_evaluate(session_id="s1", expression="myVariable.Value")
vs_debug_locals(session_id="s1")
vs_debug_callstack(session_id="s1")
```

---

## 테스트

### 단위 테스트 (mock 기반)

```bash
python -m pytest tests/ -v --ignore=tests/test_integration_vs.py
```

### 통합 테스트 (실제 VS 2022 필요)

VS 2022가 실행 중인 상태에서:

```bash
python -m pytest tests/test_integration_vs.py -v -s
```

20개 테스트가 실제 VS 인스턴스에 대해 실행된다. VS가 미실행이면 자동으로 실행 후 ROT 등록을 대기한다.

**통합 테스트 항목:**

| 그룹 | 테스트 | 검증 내용 |
|------|--------|----------|
| IT-001 | ROT 감지 (3개) | VS 인스턴스 감지, PID 검증, `get_vs_pid()` 정확성 |
| IT-002 | DTE 속성 (3개) | MainWindow, Solution, Version COM 속성 접근 |
| IT-003 | 파일 열기 (1개) | `vs_file_open` 후 `ActiveDocument` 변경 확인 |
| IT-004 | 파일 목록/활성 (2개) | `vs_file_list_open`, `vs_file_active` round-trip |
| IT-005 | 빌드 상태 (1개) | `vs_build_status` 반환값 검증 |
| IT-006 | 브레이크포인트 (3개) | add/list/remove CRUD 확인 |
| IT-007 | 디버거 모드 (2개) | Design 모드 확인, `vs_debug_stop` |
| IT-008 | STAThread 큐 (2개) | 실제 큐 메커니즘 및 이력 기록 확인 |
| IT-009 | 커서 이동 (1개) | `vs_file_goto` 후 `vs_file_active` round-trip |
| IT-010 | 하이라이트 (1개) | `vs_file_highlight` 범위 선택 |
| IT-011 | 선택 텍스트 (1개) | `vs_file_selection` 반환값 |

---

## 설정

`config.py`에서 조정 가능:

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `timeouts["build"]` | 600초 | 빌드 최대 대기 시간 |
| `timeouts["launch"]` | 120초 | VS 실행 후 ROT 등록 대기 |
| `timeouts["debug_evaluate"]` | 10초 | 표현식 평가 타임아웃 |
| `VS_DEVENV_PATH` | Community 경로 | devenv.exe 경로 (환경변수로 재정의 가능) |
| `QUEUE_HISTORY_MAX` | 100 | 명령 이력 최대 보존 건수 |

---

## 알려진 제약사항

- **Windows 전용**: COM/DTE는 Windows에서만 동작한다.
- **VS 2022 전용**: `VisualStudio.DTE.17.0` 모니커만 지원한다.
- **STAThread cross-apartment**: `STAThread` 내에서 DTE를 사용할 때는 ROT에서 직접 DTE를 재획득해야 한다. 메인 스레드의 DTE 포인터를 STAThread에 전달하면 `RPC_E_WRONG_THREAD` 발생.
- **Break 모드 전용 기능**: `vs_debug_step`, `vs_debug_locals`, `vs_debug_evaluate`, `vs_debug_callstack`은 디버거가 Break 모드일 때만 동작한다.
