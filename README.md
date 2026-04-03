# vs-mcp-server

Visual Studio 2022를 Claude Code(MCP)에서 제어하는 서버.
COM/DTE 자동화를 통해 파일 열기, 빌드, 디버거 제어, 브레이크포인트 관리 등을 MCP 도구로 제공한다.

> **Windows 전용.** COM/DTE는 Windows에서만 동작한다.

---

## 빠른 시작

**1. 패키지 설치**

```bash
pip install vs-mcp-server
```

**2. MCP 서버 등록 — Claude Code CLI**

```bash
# uvx 방식 (권장, 가상환경 자동 관리)
claude mcp add --scope project vs -- uvx vs-mcp-server

# 또는 pip install 후 — 직접 실행 방식
claude mcp add --scope project vs -- vs-mcp-server
```

등록 방법(전역, Desktop, 환경변수 등)은 [설치 및 MCP 서버 등록](#설치-및-mcp-서버-등록) 섹션을 참고한다.

**3. Visual Studio 2022 실행**

VS가 이미 실행 중이면 건너뛴다. 실행하지 않았다면 `vs_launch` 도구로 자동 실행할 수 있다.

**4. Claude Code에서 VS 연결**

```python
vs_connect(session_id="s1", solution_path="C:/MyProject/MyProject.sln")
```

**5. 도구 사용 시작**

```python
vs_file_open(session_id="s1", path="C:/MyProject/src/main.cpp")
vs_debug_evaluate(session_id="s1", expression="myVar.Value")
```

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

| 항목 | 버전 / 조건 |
|------|------------|
| OS | Windows 10 / 11 (COM/DTE는 Windows 전용) |
| Python | 3.11 이상 |
| Visual Studio | 2022 Community / Professional / Enterprise |
| 패키지 | `pywin32`, `mcp` |

```bash
# Python 버전 확인
python --version
```

---

## 설치 및 MCP 서버 등록

### Claude Code CLI에 등록

Claude Code CLI는 `.mcp.json` 파일로 MCP 서버를 관리한다.

#### 방법 1 — uvx 방식 (권장)

별도 설치 없이 uvx가 자동으로 가상환경을 만들어 실행한다.

```bash
# 프로젝트별 등록
claude mcp add --scope project vs -- uvx vs-mcp-server

# 전역 등록
claude mcp add --scope user vs -- uvx vs-mcp-server
```

또는 `.mcp.json` 직접 작성:
```json
{
  "mcpServers": {
    "vs": {
      "command": "uvx",
      "args": ["vs-mcp-server"]
    }
  }
}
```

#### 방법 2 — pip install 후 직접 실행

```bash
# 설치 (git clone 불필요)
pip install vs-mcp-server
# 또는 GitHub에서 직접 설치
pip install git+https://github.com/panninghour/vs-mcp-server

# 등록
claude mcp add --scope project vs -- vs-mcp-server
```

또는 `~/.claude.json`(`%USERPROFILE%\.claude.json`)에 직접 작성:

```json
{
  "mcpServers": {
    "vs": {
      "command": "vs-mcp-server"
    }
  }
}
```

#### 방법 3 — 개발자용 (로컬 편집 가능 설치)

소스를 수정하면서 사용하려면:

```bash
git clone https://github.com/panninghour/vs-mcp-server
cd vs-mcp-server
pip install -e .
claude mcp add --scope project vs -- vs-mcp-server
```

#### 등록 확인

```bash
# 등록된 MCP 서버 목록 확인
claude mcp list
```

Claude Code 세션 안에서 서버 상태와 도구 목록을 확인하려면:

```
/mcp
```

`vs` 서버가 연결됨으로 표시되고, `vs_connect`, `vs_debug_evaluate` 등 22개 도구가 활성화되어야 한다.

---

### Claude Desktop에 등록

`claude_desktop_config.json`을 열어 `mcpServers`에 추가한다.

**파일 위치 (Windows):**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**설정 예시 (uvx 방식):**

```json
{
  "mcpServers": {
    "vs": {
      "command": "uvx",
      "args": ["vs-mcp-server"],
      "env": {
        "VS_DEVENV_PATH": "C:/Program Files/Microsoft Visual Studio/2022/Professional/Common7/IDE/devenv.exe"
      }
    }
  }
}
```

> Claude Desktop 재시작 후 반영된다.

---

### 환경변수 설정

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `VS_DEVENV_PATH` | Community 설치 경로 | `devenv.exe` 전체 경로. Professional/Enterprise 사용 시 재정의 필요. |
| `VS_MCP_LOG_DIR` | `<server.py 위치>/logs/` | 크래시 로그 저장 디렉토리 |
| `VS_MCP_LOG_LEVEL` | `INFO` | 로그 레벨 (`DEBUG` / `INFO` / `WARNING`) |

**VS 설치 경로 예시:**

```bash
# Community (기본값)
C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe

# Professional
C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\devenv.exe

# Enterprise
C:\Program Files\Microsoft Visual Studio\2022\Enterprise\Common7\IDE\devenv.exe
```

환경변수를 영구 설정하려면:

```powershell
[System.Environment]::SetEnvironmentVariable(
    "VS_DEVENV_PATH",
    "C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\devenv.exe",
    "User"
)
```

또는 `.mcp.json`의 `env` 블록으로 서버별 설정:

```json
"env": {
  "VS_DEVENV_PATH": "C:/Program Files/Microsoft Visual Studio/2022/Professional/Common7/IDE/devenv.exe"
}
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

```python
# 1. VS 인스턴스 목록 확인
vs_list_instances()
# → [{"pid": 12345, "solution": "C:/MyProject/MyProject.sln"}]

# 2. 세션 연결
vs_connect(session_id="s1", solution_path="C:/MyProject/MyProject.sln")

# 3. 파일 열기 + 특정 라인으로 이동
vs_file_open(session_id="s1", path="C:/MyProject/src/main.cpp")
vs_file_goto(session_id="s1", path="C:/MyProject/src/main.cpp", line=42)

# 4. 유저가 선택한 코드 읽기
vs_file_selection(session_id="s1")
# → {"text": "int x = foo();", "start_line": 42, "end_line": 42}

# 5. 빌드
vs_build_solution(session_id="s1", configuration="Debug")
# → {"success": true, "failed_projects": 0}

# 6. 브레이크포인트 추가 후 디버깅 시작
vs_debug_breakpoint(session_id="s1", action="add", file="C:/MyProject/src/main.cpp", line=42)
vs_debug_start(session_id="s1", wait_for_break=False)

# 7. Break 모드 진입 후 변수 검사
vs_debug_locals(session_id="s1")
vs_debug_evaluate(session_id="s1", expression="myObj.Value")
vs_debug_callstack(session_id="s1")

# 8. 스텝 실행
vs_debug_step(session_id="s1", step_type="over")

# 9. 디버깅 종료
vs_debug_stop(session_id="s1")
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

VS가 미실행이면 자동으로 실행 후 ROT 등록을 대기한다.

**통합 테스트 항목 (IT 그룹 17개, pytest 함수 26개):**

| 그룹 | ID | 검증 내용 |
|------|----|----------|
| ROT 감지 | IT-001 | VS 인스턴스 감지, PID 검증, `get_vs_pid()` 정확성, 스테일 항목 필터링 |
| DTE 속성 | IT-002 | `MainWindow`, `Solution`, `Version` COM 속성 접근 |
| 파일 열기 | IT-003 | `vs_file_open` 후 `ActiveDocument` 변경 확인 |
| 파일 목록/활성 | IT-004 | `vs_file_list_open`, `vs_file_active` round-trip |
| 빌드 상태 | IT-005 | `vs_build_status` 반환값 검증 |
| 브레이크포인트 | IT-006 | `add` / `list` / `remove` CRUD |
| 디버거 모드 | IT-007 | Design 모드 확인, `vs_debug_stop` |
| STAThread 큐 | IT-008 | 실제 큐 메커니즘 및 이력 기록 확인 |
| 커서 이동 | IT-009 | `vs_file_goto` 후 `vs_file_active` round-trip |
| 하이라이트 | IT-010 | `vs_file_highlight` 범위 선택 |
| 선택 텍스트 | IT-011 | `vs_file_selection` 반환값 |
| **디버깅 세션** | IT-012 | 새 VS 인스턴스에 `DebugTarget.sln` 로드, Debug 빌드, BP 설정 후 Break 모드 진입 확인 |
| **디버깅 세션** | IT-013 | Break 모드에서 `vs_debug_locals` → `x=42, y=50, msg` 변수 확인 |
| **디버깅 세션** | IT-014 | `vs_debug_evaluate("x + y") == "92"`, `msg`에 `"hello from debugger"` 포함 |
| **디버깅 세션** | IT-015 | `vs_debug_callstack` → `depth >= 1`, .NET 8 `CurrentStackFrame` 폴백 동작 |
| **디버깅 세션** | IT-016 | `vs_debug_step("over")` 후 `mode == "break"`, `line > BP_LINE`, `ActiveDocument` 폴백 동작 |
| **디버깅 세션** | IT-017 | `vs_debug_stop()` → `status == "stopped"`, `mode == "design"` |

---

## 설정

환경변수로 조정 가능. 개발자용 설치(`pip install -e .`)의 경우 `vs_mcp_server/config.py`에서 직접 수정할 수도 있다.

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `timeouts["build"]` | 600초 | 빌드 최대 대기 시간 |
| `timeouts["launch"]` | 120초 | VS 실행 후 ROT 등록 대기 |
| `timeouts["debug_evaluate"]` | 10초 | 표현식 평가 타임아웃 |
| `VS_DEVENV_PATH` | Community 경로 | `devenv.exe` 경로 (환경변수로 재정의 가능) |
| `QUEUE_HISTORY_MAX` | 100 | 명령 이력 최대 보존 건수 |

---

## 알려진 제약사항

- **Windows 전용**: COM/DTE는 Windows에서만 동작한다.
- **VS 2022 전용**: `VisualStudio.DTE.17.0` 모니커만 지원한다.
- **Python 3.11+**: 하위 버전에서는 동작을 보장하지 않는다.
- **STAThread cross-apartment**: `STAThread` 내에서 DTE를 사용할 때는 ROT에서 직접 DTE를 재획득해야 한다. 메인 스레드의 DTE 포인터를 STAThread에 전달하면 `RPC_E_WRONG_THREAD` 발생.
- **Break 모드 전용 기능**: `vs_debug_step`, `vs_debug_locals`, `vs_debug_evaluate`, `vs_debug_callstack`은 디버거가 Break 모드일 때만 동작한다.
- **.NET 8 관리 코드 제한**: `CurrentThread.StackFrames` COM 열거가 빈 컬렉션을 반환하는 경우가 있음. `vs_debug_callstack`은 `CurrentStackFrame` 폴백으로 처리하고, `vs_debug_step`은 `ActiveDocument.Selection` 폴백으로 처리한다.

---

## DTE 직접 접근 (Advanced)

MCP 서버를 거치지 않고 외부 Python 스크립트에서 직접 DTE COM 객체에 접근할 수 있다. VS 인스턴스는 Windows ROT(Running Object Table)에 `VisualStudio.DTE.17.0` 모니커로 등록되어 있다.

### 단일 VS 인스턴스

```python
import pythoncom
import win32com.client

pythoncom.CoInitialize()
dte = win32com.client.GetActiveObject("VisualStudio.DTE.17.0")

# 예시: 솔루션 경로 출력
print(dte.Solution.FullName)
```

> **주의**: `GetActiveObject`는 VS 인스턴스가 여러 개 실행 중일 때 어느 인스턴스를 반환할지 보장하지 않는다. 단일 인스턴스 환경에서만 사용할 것.

### 복수 VS 인스턴스 — PID로 특정 인스턴스 선택

`vs_list_instances` 도구로 PID를 확인한 뒤, ROT에서 해당 PID의 인스턴스를 직접 획득한다.

```python
import pythoncom
import win32com.client
from vs_mcp_server.utils.rot import find_vs_instances, get_vs_pid

pythoncom.CoInitialize()

target_pid = 12345  # vs_list_instances 또는 vs_connect 응답의 instance_pid

entries = find_vs_instances()
dte = next(
    e["dte"] for e in entries
    if get_vs_pid(e["dte"]) == target_pid
)

# 이후 dte 객체를 자유롭게 사용
print(dte.Solution.FullName)
```

> **참고**: `find_vs_instances()`는 ROT를 순회하여 모든 `VisualStudio.DTE.17.0` 항목을 반환한다. `get_vs_pid(dte)`는 DTE의 `MainWindow.HWnd`에서 PID를 추출한다.
