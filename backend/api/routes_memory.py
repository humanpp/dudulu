"""记忆检索接口（文档 2 第 6.9 节）：GET /api/memory/search。

调 MemoryManager.retrieve（双键过滤 + 四因子重排）。emotion_match 需要
当前情感状态，故先按 /api/state 同款方式加载（引擎算阶段只是顺带，
这里只用 expression 参与效价匹配）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, sessionmaker

from auth.dependencies import get_current_user
from config.settings import Settings, get_settings
from fastapi import HTTPException
from core.character.manager import (
    CharacterManager,
    CharacterNotFoundError,
    get_character_manager,
)
from core.emotion_engine import EmotionEngine
from db.models import User
from db.sqlite import get_db
from db.user_state import load_user_state
from memory.memory_manager import MemoryManager, memory_manager_dependency

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_LIMIT = 10
MAX_LIMIT = 100


@router.get("/memory/search")
def search_memory(character_id: str,
                  q: str,
                  emotion_tag: str | None = Query(default=None),
                  time_range: str | None = Query(default=None),
                  limit: int = Query(default=DEFAULT_LIMIT),
                  current_user: User = Depends(get_current_user),
                  db: Session = Depends(get_db),
                  memory_manager: MemoryManager = Depends(
                      memory_manager_dependency),
                  character_manager: CharacterManager = Depends(
                      get_character_manager),
                  settings: Settings = Depends(get_settings)) -> dict:
    """混合检索某角色的记忆（文档 2 第 6.9 节）。

    参数：q（查询词，必填）、emotion_tag（情感标签过滤）、
        time_range（last_7_days / last_30_days / all）、limit（默认 10）。
    响应：{memories: [...]}，字段为文档 2 第 5.1 节记忆条目结构
        （id/content/type/role/is_proactive/emotion_tag/intensity/
         importance/valence/timestamp 等）。
    角色不存在 → 404。
    """
    limit = min(max(limit, 1), MAX_LIMIT)
    try:
        profile = character_manager.get(character_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    user_state, _ = load_user_state(db, character_id, current_user.id)
    engine = EmotionEngine(settings.EMOTION_CONFIG_PATH, profile, user_state)
    current_state = engine.get_full_state()

    memories = memory_manager.retrieve(
        query=q,
        character_id=character_id,
        user_id=current_user.id,
        current_state=current_state,
        emotion_tag=emotion_tag,
        time_range=time_range,
        top_k=limit,
    )
    return {"memories": memories}
