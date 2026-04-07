"""에디터 도구 — Claude ↔ 유저 소통 채널

vs_file_open, vs_file_goto, vs_file_highlight, vs_file_list_open,
vs_file_active, vs_file_selection
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .. import session_manager as sm

logger = logging.getLogger(__name__)


def _get_sta(session_id: str):
    return sm.get_manager().require_sta(session_id)


async def vs_file_open(
    *,
    session_id: str,
    path: str,
) -> dict:
    """유저에게 특정 파일을 VS 에디터에 열어 보여준다.

    Args:
        session_id: 세션 ID
        path: 열 파일의 절대 경로

    Returns:
        {"path": str, "status": "opened"}
    """
    sta = _get_sta(session_id)

    def _open():
        sta.dte.ItemOperations.OpenFile(path)
        return {"path": path, "status": "opened"}

    return await sta.submit(
        "vs_file_open", _open,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_file_goto(
    *,
    session_id: str,
    path: str,
    line: int,
    column: int = 1,
) -> dict:
    """특정 파일의 특정 라인으로 스크롤+커서를 이동시킨다.

    Args:
        session_id: 세션 ID
        path: 파일 경로 (이미 열려 있어야 함. 없으면 자동으로 열림)
        line: 이동할 라인 번호 (1-based)
        column: 컬럼 번호 (1-based, 기본값 1)

    Returns:
        {"path": str, "line": int, "column": int, "status": "moved"}
    """
    sta = _get_sta(session_id)

    def _goto():
        dte = sta.dte
        # 파일이 열려 있지 않으면 먼저 열기
        doc = _find_open_document(dte, path)
        if doc is None:
            dte.ItemOperations.OpenFile(path)
            doc = dte.ActiveDocument
        else:
            doc.Activate()

        sel = doc.Selection
        sel.GotoLine(line, Select=False)
        sel.MoveToLineAndOffset(line, column, Extend=False)
        return {"path": path, "line": line, "column": column, "status": "moved"}

    return await sta.submit(
        "vs_file_goto", _goto,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_file_highlight(
    *,
    session_id: str,
    path: str,
    start_line: int,
    end_line: int,
    start_column: int = 1,
    end_column: Optional[int] = None,
    add_bookmark: bool = False,
) -> dict:
    """지정한 라인 범위를 선택(하이라이트) 상태로 만들어 유저에게 시각적으로 강조한다.

    Args:
        session_id: 세션 ID
        path: 파일 경로
        start_line: 시작 라인 (1-based)
        end_line: 끝 라인 (1-based)
        start_column: 시작 컬럼 (기본값 1)
        end_column: 끝 컬럼 (기본값: 끝 라인의 끝)
        add_bookmark: True면 북마크도 추가

    Returns:
        {"path": str, "start_line": int, "end_line": int, "status": "highlighted"}
    """
    sta = _get_sta(session_id)

    def _highlight():
        dte = sta.dte
        doc = _find_open_document(dte, path)
        if doc is None:
            dte.ItemOperations.OpenFile(path)
            doc = dte.ActiveDocument

        sel = doc.Selection
        sel.MoveToLineAndOffset(start_line, start_column, Extend=False)

        ec = end_column
        if ec is None:
            # 끝 라인의 끝 컬럼으로 이동
            sel.GotoLine(end_line, Select=False)
            sel.EndOfLine(Extend=False)
            ec = sel.CurrentColumn

        sel.MoveToLineAndOffset(start_line, start_column, Extend=False)
        sel.MoveToLineAndOffset(end_line, ec, Extend=True)

        if add_bookmark:
            try:
                dte.ExecuteCommand("Edit.ToggleBookmark")
            except Exception:
                pass

        return {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "status": "highlighted",
        }

    return await sta.submit(
        "vs_file_highlight", _highlight,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_file_list_open(*, session_id: str) -> dict:
    """현재 VS에 열려 있는 파일 목록과 저장 상태를 반환한다.

    Returns:
        {"files": [{"path": str, "saved": bool, "is_active": bool}]}
    """
    sta = _get_sta(session_id)

    def _list():
        dte = sta.dte
        active_path = ""
        try:
            if dte.ActiveDocument:
                active_path = dte.ActiveDocument.FullName or ""
        except Exception:
            pass

        files = []
        for doc in dte.Documents:
            try:
                fpath = doc.FullName or ""
                saved = doc.Saved
                files.append({
                    "path": fpath,
                    "saved": bool(saved),
                    "is_active": fpath == active_path,
                })
            except Exception:
                continue
        return {"files": files, "count": len(files)}

    return await sta.submit(
        "vs_file_list_open", _list,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_file_active(*, session_id: str) -> dict:
    """유저가 현재 포커스하고 있는 파일 정보를 반환한다.

    Returns:
        {"path": str, "line": int, "column": int} 또는 {"path": null} (열린 파일 없음)
    """
    sta = _get_sta(session_id)

    def _active():
        dte = sta.dte
        try:
            doc = dte.ActiveDocument
            if doc is None:
                return {"path": None}
            sel = doc.Selection
            return {
                "path": doc.FullName or "",
                "line": sel.CurrentLine,
                "column": sel.CurrentColumn,
            }
        except Exception as e:
            logger.debug("ActiveDocument 접근 실패: %s", e)
            return {"path": None}

    return await sta.submit(
        "vs_file_active", _active,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


async def vs_file_selection(*, session_id: str) -> dict:
    """유저가 현재 드래그로 선택한 텍스트와 위치를 반환한다.

    Returns:
        {
            "text": str,
            "start_line": int, "start_column": int,
            "end_line": int, "end_column": int,
            "path": str
        }
    """
    sta = _get_sta(session_id)

    def _selection():
        dte = sta.dte
        try:
            doc = dte.ActiveDocument
            if doc is None:
                return {"text": "", "path": None}
            sel = doc.Selection
            text = sel.Text or ""
            return {
                "path": doc.FullName or "",
                "text": text,
                "start_line": sel.TopLine,
                "start_column": sel.TopPoint.DisplayColumn,
                "end_line": sel.BottomLine,
                "end_column": sel.BottomPoint.DisplayColumn,
                "has_selection": bool(text),
            }
        except Exception as e:
            logger.debug("Selection 접근 실패: %s", e)
            return {"text": "", "path": None, "has_selection": False}

    return await sta.submit(
        "vs_file_selection", _selection,
        session_id=session_id,
        loop=asyncio.get_running_loop(),
    )


def _find_open_document(dte, path: str):
    """열려 있는 Document 중 path와 일치하는 것을 반환한다."""
    path_norm = path.lower().replace("\\", "/")
    try:
        for doc in dte.Documents:
            try:
                if doc.FullName.lower().replace("\\", "/") == path_norm:
                    return doc
            except Exception:
                continue
    except Exception:
        pass
    return None
