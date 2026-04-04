# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Visual Studio 2022 MCP server — Windows COM/DTE 자동화를 통해 Claude Code에서 VS를 제어하는 Python 패키지.
Windows 전용 (COM/DTE), Python 3.11+, VS 2022 (VisualStudio.DTE.17.0) 한정.

## Commands

```bash
# 개발 설치
pip install -e .

# 서버 실행
vs-mcp-server              # pip 설치 후
python -m vs_mcp_server     # 직접 실행

# 단위 테스트 (VS 불필요)
python -m pytest tests/ -v --ignore=tests/test_integration_vs.py

# 통합 테스트 (VS 2022 실행 중이어야 함)
python -m pytest tests/test_integration_vs.py -v -s

# 단일 테스트
python -m pytest tests/test_com_bridge.py::test_name -v

# 린트
ruff check
```

## Architecture

```
Claude Code (stdio) → server.py (22 MCP tools)
                        ├── session_manager.py (session ↔ VS instance 바인딩)
                        ├── com_bridge.py (STA 스레드 + dual-channel 큐)
                        │     └── vs_instance_manager.py (ROT 탐색, devenv 실행)
                        │           └── utils/rot.py (Running Object Table)
                        ├── tools/ (6 모듈: instance, editor, build, debug, queue, dte)
                        ├── crash_logger.py (JSONL 이벤트 로깅)
                        └── config.py (타임아웃, 경로, 환경변수)
```

### Core Design Decisions

- **VS 인스턴스당 전용 STA 스레드**: DTE COM 포인터는 아파트 간 전달 불가 (RPC_E_WRONG_THREAD). STAThread가 인스턴스별 COM 작업을 직렬화.
- **Dual-channel 큐**: `immediate` (에디터 등 빠른 작업) + `long_running` (빌드, 디버그). 빌드 중에도 에디터 명령 응답 가능.
- **ROT 기반 VS 탐색**: 프로세스 열거 대신 Windows Running Object Table에서 DTE 모니커 검색.
- **OnBuildDone 이벤트**: 폴링 대신 `WaitForBuildToFinish=False` + COM 이벤트 핸들러 사용.
- **STAThread.submit() → asyncio.Future**: MCP async 핸들러에서 await 가능한 패턴.

### Tool Modules (tools/)

22개 MCP 도구가 6개 모듈로 분류: instance (4), editor (6), build (4), debug (7), queue (3), dte (1).
디버그 도구들은 break-mode 전용 (step, locals, evaluate, callstack).

## Environment Variables

| 변수 | 용도 |
|------|------|
| `VS_MCP_LOG_DIR` | 크래시 로그 디렉토리 (기본: 패키지 내 logs/) |
| `VS_MCP_LOG_LEVEL` | 로그 레벨 (기본: INFO) |
| `VS_DEVENV_PATH` | devenv.exe 경로 (기본: Community 에디션) |

## Known Constraints

- .NET 8 관리 코드에서 `StackFrames` 열거가 빈 결과를 반환할 수 있음 → `CurrentStackFrame` 폴백 사용.
- 통합 테스트는 실제 VS 2022 인스턴스가 실행 중이어야 하며, `tests/debug_target/DebugTarget.sln` 프로젝트를 사용.
