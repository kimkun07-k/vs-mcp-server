"""VS 2022 MCP Server 설정"""
import os
from pathlib import Path

# 타임아웃 설정 (초 단위)
timeouts = {
    "default": 30,
    "build": 600,           # 10분
    "debug_evaluate": 10,
    "launch": 120,          # VS 실행 대기
}

# 로그 설정
LOG_DIR = Path(os.environ.get("VS_MCP_LOG_DIR", Path(__file__).parent / "logs"))
LOG_FILE = LOG_DIR / "vs_mcp_crash.jsonl"
LOG_LEVEL = os.environ.get("VS_MCP_LOG_LEVEL", "INFO")

# VS 2022 설정
VS_PROG_ID = "VisualStudio.DTE.17.0"
VS_DEVENV_PATH = os.environ.get(
    "VS_DEVENV_PATH",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe",
)

# ROT 폴링 설정
ROT_POLL_INTERVAL = 0.5   # 초
ROT_POLL_MAX_ATTEMPTS = int(timeouts["launch"] / ROT_POLL_INTERVAL)

# 큐 히스토리 보존 건수
QUEUE_HISTORY_MAX = 100
QUEUE_HISTORY_DEFAULT_LIMIT = 20
