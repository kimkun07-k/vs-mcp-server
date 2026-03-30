"""UT-002: crash_logger 모듈 테스트"""
import json
from pathlib import Path
from unittest.mock import patch

from vs_mcp_server import config
from vs_mcp_server import crash_logger


def _read_last_log(log_file: Path) -> dict:
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1])


def test_log_event_writes_jsonl(tmp_path):
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        crash_logger.log_event(
            command="test_cmd",
            instance_pid=1234,
            session_id="sess-1",
            result="success",
        )
    record = _read_last_log(tmp_path / "test.jsonl")
    assert record["command"] == "test_cmd"
    assert record["instance_pid"] == 1234
    assert record["session_id"] == "sess-1"
    assert record["result"] == "success"
    assert "timestamp" in record


def test_log_event_optional_fields(tmp_path):
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        crash_logger.log_event(
            command="test_cmd",
            instance_pid=None,
            session_id=None,
            result="error",
            error_type="COMException",
            error_detail="RPC unavailable",
            duration_ms=1500.5,
        )
    record = _read_last_log(tmp_path / "test.jsonl")
    assert record["error_type"] == "COMException"
    assert record["error_detail"] == "RPC unavailable"
    assert record["duration_ms"] == 1500.5


def test_log_com_error(tmp_path):
    exc = RuntimeError("COM connection failed")
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        crash_logger.log_com_error(
            command="vs_file_open",
            instance_pid=999,
            session_id="s1",
            exc=exc,
            duration_ms=200.0,
        )
    record = _read_last_log(tmp_path / "test.jsonl")
    assert record["command"] == "vs_file_open"
    assert record["result"] == "error"
    assert record["error_type"] == "RuntimeError"
    assert "COM connection failed" in record["error_detail"]


def test_log_timeout(tmp_path):
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        crash_logger.log_timeout(
            command="vs_build_solution",
            instance_pid=111,
            session_id="s2",
            duration_ms=600000.0,
        )
    record = _read_last_log(tmp_path / "test.jsonl")
    assert record["result"] == "timeout"
    assert record["error_type"] == "TimeoutError"
    assert record["duration_ms"] == 600000.0


def test_log_evaluate_success(tmp_path):
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        crash_logger.log_evaluate(
            instance_pid=42,
            session_id="s3",
            expression="x + 1",
            result="success",
            duration_ms=50.0,
        )
    record = _read_last_log(tmp_path / "test.jsonl")
    assert record["command"] == "vs_debug_evaluate"
    assert record["expression"] == "x + 1"
    assert record["result"] == "success"


def test_log_evaluate_error(tmp_path):
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        crash_logger.log_evaluate(
            instance_pid=42,
            session_id="s3",
            expression="bad.expression()",
            result="error",
            error_type="COMException",
            error_detail="Invalid expression",
        )
    record = _read_last_log(tmp_path / "test.jsonl")
    assert record["result"] == "error"
    assert record["error_type"] == "COMException"


def test_multiple_log_entries(tmp_path):
    with patch.object(config, "LOG_DIR", tmp_path), \
         patch.object(config, "LOG_FILE", tmp_path / "test.jsonl"):
        for i in range(3):
            crash_logger.log_event(
                command=f"cmd_{i}",
                instance_pid=i,
                session_id=f"s{i}",
                result="success",
            )
    lines = (tmp_path / "test.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["command"] == f"cmd_{i}"
