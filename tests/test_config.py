"""UT-001: config 모듈 테스트"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config


def test_timeout_default():
    assert config.timeouts["default"] == 30

def test_timeout_build():
    assert config.timeouts["build"] == 600

def test_timeout_debug_evaluate():
    assert config.timeouts["debug_evaluate"] == 10

def test_timeout_launch():
    assert config.timeouts["launch"] == 120

def test_log_file_under_log_dir():
    assert str(config.LOG_FILE).startswith(str(config.LOG_DIR))

def test_vs_prog_id():
    assert config.VS_PROG_ID == "VisualStudio.DTE.17.0"

def test_rot_poll_interval_positive():
    assert config.ROT_POLL_INTERVAL > 0

def test_queue_history_max_positive():
    assert config.QUEUE_HISTORY_MAX > 0

def test_queue_history_default_limit():
    assert config.QUEUE_HISTORY_DEFAULT_LIMIT == 20
