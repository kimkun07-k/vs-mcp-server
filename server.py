"""VS 2022 MCP Server — 진입점

mcp Python SDK를 사용하여 모든 도구를 등록하고 서버를 실행한다.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

import config  # noqa: E402

# 로그 디렉토리 생성
config.LOG_DIR.mkdir(parents=True, exist_ok=True)

from mcp.server import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402
from mcp import types  # noqa: E402

import session_manager as sm  # noqa: E402
from tools import instance as instance_tools  # noqa: E402
from tools import editor as editor_tools  # noqa: E402
from tools import build as build_tools  # noqa: E402
from tools import debug as debug_tools  # noqa: E402
from tools import queue as queue_tools  # noqa: E402

app = Server("vs-mcp-server")


# ------------------------------------------------------------------ #
# 헬퍼                                                                #
# ------------------------------------------------------------------ #

def _session(args: dict) -> str:
    """args에서 session_id를 추출한다. 없으면 "default" 사용."""
    return args.get("session_id") or "default"


# ------------------------------------------------------------------ #
# Tool 목록 정의                                                      #
# ------------------------------------------------------------------ #

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # ── 인스턴스 관리 ────────────────────────────────────────────
        types.Tool(
            name="vs_list_instances",
            description="ROT에서 실행 중인 VS 2022 인스턴스 목록(PID, 솔루션 경로)을 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="vs_connect",
            description="지정한 PID 또는 솔루션 경로로 VS 인스턴스에 연결하고 세션을 바인딩한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "클라이언트 세션 식별자"},
                    "pid": {"type": "integer", "description": "VS 프로세스 PID"},
                    "solution_path": {"type": "string", "description": ".sln 파일 절대 경로"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_launch",
            description="devenv.exe를 실행하고 ROT 폴링으로 자동 연결한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "solution_path": {"type": "string", "description": "열 .sln 파일 경로 (없으면 빈 VS 실행)"},
                    "timeout": {"type": "number", "description": "최대 대기 시간(초)"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_close",
            description="VS 인스턴스를 종료한다. save_all=true면 저장 후 종료.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "save_all": {"type": "boolean", "default": True},
                },
                "required": ["session_id"],
            },
        ),

        # ── 에디터 — Claude → 유저 ──────────────────────────────────
        types.Tool(
            name="vs_file_open",
            description="유저에게 특정 파일을 VS 에디터에 열어 보여준다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "path": {"type": "string", "description": "열 파일의 절대 경로"},
                },
                "required": ["session_id", "path"],
            },
        ),
        types.Tool(
            name="vs_file_goto",
            description="특정 파일의 특정 라인으로 스크롤+커서를 이동시켜 유저의 시선을 유도한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "path": {"type": "string"},
                    "line": {"type": "integer", "description": "이동할 라인 번호 (1-based)"},
                    "column": {"type": "integer", "default": 1},
                },
                "required": ["session_id", "path", "line"],
            },
        ),
        types.Tool(
            name="vs_file_highlight",
            description="지정한 라인 범위를 선택 상태로 만들어 유저에게 시각적으로 강조한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "start_column": {"type": "integer", "default": 1},
                    "end_column": {"type": "integer"},
                    "add_bookmark": {"type": "boolean", "default": False},
                },
                "required": ["session_id", "path", "start_line", "end_line"],
            },
        ),
        types.Tool(
            name="vs_file_list_open",
            description="현재 VS에 열려 있는 파일 목록과 저장 상태를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),

        # ── 에디터 — 유저 → Claude ──────────────────────────────────
        types.Tool(
            name="vs_file_active",
            description="유저가 현재 포커스하고 있는 파일 경로와 커서 위치를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_file_selection",
            description="유저가 현재 드래그로 선택한 텍스트와 위치를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),

        # ── 빌드 ────────────────────────────────────────────────────
        types.Tool(
            name="vs_build_solution",
            description="솔루션 전체를 비동기 빌드하고 성공/실패 결과를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "configuration": {"type": "string", "description": "빌드 구성 (예: Debug, Release)"},
                    "timeout": {"type": "number", "description": "최대 대기 시간(초), 기본 600"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_build_project",
            description="특정 프로젝트만 빌드하고 결과를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "project_name": {"type": "string", "description": "프로젝트 UniqueProjectName"},
                    "configuration": {"type": "string"},
                    "timeout": {"type": "number"},
                },
                "required": ["session_id", "project_name"],
            },
        ),
        types.Tool(
            name="vs_build_status",
            description="마지막 빌드 결과와 현재 빌드 상태를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_error_list",
            description="Error List 창에서 에러/경고를 파일 경로, 라인 번호와 함께 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "include_warnings": {"type": "boolean", "default": True},
                    "include_messages": {"type": "boolean", "default": False},
                },
                "required": ["session_id"],
            },
        ),

        # ── 디버깅 ──────────────────────────────────────────────────
        types.Tool(
            name="vs_debug_start",
            description="디버깅을 시작한다 (Debugger.Go). 이미 실행 중이면 현재 상태 반환.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "wait_for_break": {"type": "boolean", "default": False},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_debug_stop",
            description="디버깅 세션을 종료하고 Design 모드로 복귀한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_debug_breakpoint",
            description="브레이크포인트를 추가(add)/제거(remove)/목록 조회(list)한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "action": {"type": "string", "enum": ["add", "remove", "list"]},
                    "file": {"type": "string", "description": "파일 경로 (add/remove 시 필수)"},
                    "line": {"type": "integer", "description": "라인 번호 (add/remove 시 필수)"},
                },
                "required": ["session_id", "action"],
            },
        ),
        types.Tool(
            name="vs_debug_step",
            description="스텝 실행(into/over/out) 후 현재 파일 경로와 라인 번호를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "step_type": {"type": "string", "enum": ["into", "over", "out"], "default": "over"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_debug_locals",
            description="Break 모드에서 현재 스택 프레임의 로컬 변수(이름/타입/값)를 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="vs_debug_evaluate",
            description="Break 모드에서 표현식을 평가하고 결과를 반환한다. 크래시 학습용 로깅 수행.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "expression": {"type": "string", "description": "평가할 표현식"},
                    "timeout": {"type": "number", "description": "평가 타임아웃(초), 기본 10"},
                },
                "required": ["session_id", "expression"],
            },
        ),
        types.Tool(
            name="vs_debug_callstack",
            description="현재 스레드의 콜스택(함수명/파일/라인)을 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),

        # ── 큐 관리 ────────────────────────────────────────────────
        types.Tool(
            name="vs_queue_status",
            description="인스턴스별 현재 실행 중인 명령과 대기 큐 목록을 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance_pid": {"type": "integer", "description": "특정 인스턴스 PID (없으면 전체)"},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="vs_queue_cancel",
            description="대기 큐에서 command_id에 해당하는 명령을 취소한다. 실행 중인 명령은 취소 불가.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command_id": {"type": "string", "description": "취소할 명령의 UUID"},
                    "instance_pid": {"type": "integer"},
                },
                "required": ["command_id"],
            },
        ),
        types.Tool(
            name="vs_queue_history",
            description="최근 N건의 완료/실패/취소된 명령 이력을 반환한다.",
            inputSchema={
                "type": "object",
                "properties": {
                    "instance_pid": {"type": "integer"},
                    "limit": {"type": "integer", "default": 20, "description": "반환할 최대 건수"},
                },
                "required": [],
            },
        ),
    ]


# ------------------------------------------------------------------ #
# Tool 실행 라우터                                                    #
# ------------------------------------------------------------------ #

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    import json

    try:
        result = await _dispatch(name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]
    except Exception as exc:
        logger.exception("Tool 실행 오류: %s", name)
        error_result = {
            "error": type(exc).__name__,
            "message": str(exc),
            "tool": name,
        }
        return [types.TextContent(type="text", text=json.dumps(error_result, ensure_ascii=False, indent=2))]


async def _dispatch(name: str, args: dict):
    sid = _session(args)

    # 인스턴스 관리
    if name == "vs_list_instances":
        return instance_tools.vs_list_instances()
    if name == "vs_connect":
        return instance_tools.vs_connect(
            session_id=sid,
            pid=args.get("pid"),
            solution_path=args.get("solution_path"),
        )
    if name == "vs_launch":
        return instance_tools.vs_launch(
            session_id=sid,
            solution_path=args.get("solution_path"),
            timeout=args.get("timeout"),
        )
    if name == "vs_close":
        return await instance_tools.vs_close(
            session_id=sid,
            save_all=args.get("save_all", True),
        )

    # 에디터
    if name == "vs_file_open":
        return await editor_tools.vs_file_open(session_id=sid, path=args["path"])
    if name == "vs_file_goto":
        return await editor_tools.vs_file_goto(
            session_id=sid,
            path=args["path"],
            line=args["line"],
            column=args.get("column", 1),
        )
    if name == "vs_file_highlight":
        return await editor_tools.vs_file_highlight(
            session_id=sid,
            path=args["path"],
            start_line=args["start_line"],
            end_line=args["end_line"],
            start_column=args.get("start_column", 1),
            end_column=args.get("end_column"),
            add_bookmark=args.get("add_bookmark", False),
        )
    if name == "vs_file_list_open":
        return await editor_tools.vs_file_list_open(session_id=sid)
    if name == "vs_file_active":
        return await editor_tools.vs_file_active(session_id=sid)
    if name == "vs_file_selection":
        return await editor_tools.vs_file_selection(session_id=sid)

    # 빌드
    if name == "vs_build_solution":
        return await build_tools.vs_build_solution(
            session_id=sid,
            configuration=args.get("configuration"),
            timeout=args.get("timeout"),
        )
    if name == "vs_build_project":
        return await build_tools.vs_build_project(
            session_id=sid,
            project_name=args["project_name"],
            configuration=args.get("configuration"),
            timeout=args.get("timeout"),
        )
    if name == "vs_build_status":
        return await build_tools.vs_build_status(session_id=sid)
    if name == "vs_error_list":
        return await build_tools.vs_error_list(
            session_id=sid,
            include_warnings=args.get("include_warnings", True),
            include_messages=args.get("include_messages", False),
        )

    # 디버깅
    if name == "vs_debug_start":
        return await debug_tools.vs_debug_start(
            session_id=sid,
            wait_for_break=args.get("wait_for_break", False),
        )
    if name == "vs_debug_stop":
        return await debug_tools.vs_debug_stop(session_id=sid)
    if name == "vs_debug_breakpoint":
        return await debug_tools.vs_debug_breakpoint(
            session_id=sid,
            action=args["action"],
            file=args.get("file"),
            line=args.get("line"),
        )
    if name == "vs_debug_step":
        return await debug_tools.vs_debug_step(
            session_id=sid,
            step_type=args.get("step_type", "over"),
        )
    if name == "vs_debug_locals":
        return await debug_tools.vs_debug_locals(session_id=sid)
    if name == "vs_debug_evaluate":
        return await debug_tools.vs_debug_evaluate(
            session_id=sid,
            expression=args["expression"],
            timeout=args.get("timeout"),
        )
    if name == "vs_debug_callstack":
        return await debug_tools.vs_debug_callstack(session_id=sid)

    # 큐
    if name == "vs_queue_status":
        return queue_tools.vs_queue_status(instance_pid=args.get("instance_pid"))
    if name == "vs_queue_cancel":
        return queue_tools.vs_queue_cancel(
            command_id=args["command_id"],
            instance_pid=args.get("instance_pid"),
        )
    if name == "vs_queue_history":
        return queue_tools.vs_queue_history(
            instance_pid=args.get("instance_pid"),
            limit=args.get("limit", config.QUEUE_HISTORY_DEFAULT_LIMIT),
        )

    raise ValueError(f"알 수 없는 도구: {name}")


# ------------------------------------------------------------------ #
# 실행                                                                #
# ------------------------------------------------------------------ #

async def main():
    logger.info("VS 2022 MCP Server 시작")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
