"""会话管理接口（文档 2 第 6.2 节）：创建 / 结束 / 查询活跃会话。

同一角色在同一时间段内只允许一个活跃会话；create 遇到已有活跃会话时
返回现有会话（文档设计说明）。所有查询按 user_id 隔离（文档 2 第 7.2 节）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from db.models import Session as ChatSession
from db.models import User, utc_now
from db.sqlite import get_db
from memory.schemas import generate_id

logger = logging.getLogger(__name__)

router = APIRouter()


class SessionCreateRequest(BaseModel):
    """POST /api/session/create 请求体（文档 2 第 6.2 节）。"""

    character_id: str


def _session_payload(session: ChatSession) -> dict:
    """会话行的 API 表示（started_at 转 ISO 串）。"""
    return {
        "session_id": session.id,
        "character_id": session.character_id,
        "started_at": session.started_at.isoformat() if session.started_at else None,
    }


@router.post("/session/create")
def create_session(body: SessionCreateRequest,
                   current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    """创建（或复用）活跃会话。

    响应：{session_id, character_id, started_at}。
    """
    existing = db.execute(
        select(ChatSession).where(
            ChatSession.character_id == body.character_id,
            ChatSession.user_id == current_user.id,
            ChatSession.status == "active",
        )).scalar_one_or_none()
    if existing is not None:
        return _session_payload(existing)
    session = ChatSession(
        id=generate_id(),
        character_id=body.character_id,
        user_id=current_user.id,
        status="active",
    )
    db.add(session)
    db.commit()
    return _session_payload(session)


@router.post("/session/{session_id}/end")
def end_session(session_id: str,
                current_user: User = Depends(get_current_user),
                db: Session = Depends(get_db)) -> dict:
    """结束会话。

    响应：{status: "ended", turn_count: N}。
    会话不存在或不属于当前用户 → 404（隔离：不泄露他人会话存在性）。
    """
    session = db.get(ChatSession, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.ended_at = utc_now()
    session.status = "ended"
    db.commit()
    return {"status": "ended", "turn_count": session.turn_count}


@router.get("/session/active")
def active_session(character_id: str,
                   current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    """查询某角色的活跃会话。

    响应：{session_id: uuid | null}（文档 2 第 6.2 节）。
    """
    session = db.execute(
        select(ChatSession).where(
            ChatSession.character_id == character_id,
            ChatSession.user_id == current_user.id,
            ChatSession.status == "active",
        )).scalar_one_or_none()
    return {"session_id": session.id if session is not None else None}
