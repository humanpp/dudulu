"""最近消息接口（文档 2 第 6.7 节）：GET /api/messages/recent。

取数方式（文档设计说明）：messages 表按 (character_id, user_id) 过滤、
按 created_at 倒序取 limit 条后反转；正文按 memory_id 从 YantrikDB 回读
（Phase 5 起由 MemoryManager.get_memory 提供 content 与 is_proactive）。
user_id 由 JWT 注入，不接受客户端传入。limit 默认 20，上限 100。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from auth.dependencies import get_current_user
from db.models import Message, User
from db.sqlite import get_db
from memory.memory_manager import MemoryManager, memory_manager_dependency

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@router.get("/messages/recent")
def recent_messages(character_id: str,
                    limit: int = Query(default=DEFAULT_LIMIT),
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db),
                    memory_manager: MemoryManager = Depends(
                        memory_manager_dependency)) -> dict:
    """取某角色最近的 limit 条消息（倒序取后反转，正序返回）。

    响应：{messages: [{id, session_id, role, content, is_proactive, created_at}]}。
    limit 超出 [1, 100] 时钳制到边界（文档：默认 20、上限 100）。
    content / is_proactive 按 memory_id 从 YantrikDB 回读；记忆缺失时
    content 为 None、is_proactive 为 False（不阻塞索引行返回）。
    """
    limit = min(max(limit, 1), MAX_LIMIT)
    rows = db.execute(
        select(Message).where(
            Message.character_id == character_id,
            Message.user_id == current_user.id,
        ).order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
    ).scalars().all()
    rows.reverse()

    messages = []
    for row in rows:
        memory = memory_manager.get_memory(row.memory_id)
        messages.append({
            "id": row.id,
            "session_id": row.session_id,
            "role": row.role,
            "content": memory["content"] if memory else None,
            "is_proactive": bool(memory["is_proactive"]) if memory else False,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return {"messages": messages}
