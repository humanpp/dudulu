"""状态查询接口（文档 2 第 6.8 节）：GET /api/state。

返回结构：三层状态 + affective 里的 stage_code / stage_label（后端不拼串，
展示形式由前端组合）+ last_updated。阶段判定复用情感引擎
（determine_stage），与管道内计算同源。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from config.settings import Settings, get_settings
from core.character.manager import (
    CharacterManager,
    CharacterNotFoundError,
    get_character_manager,
)
from core.emotion_engine import EmotionEngine
from db.models import User
from db.sqlite import get_db
from db.user_state import load_user_state

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/state")
def get_state(character_id: str,
              current_user: User = Depends(get_current_user),
              db: Session = Depends(get_db),
              manager: CharacterManager = Depends(get_character_manager),
              settings: Settings = Depends(get_settings)) -> dict:
    """查询某角色的三层情感状态（user_id 由 JWT 注入）。

    响应（文档 2 第 6.8 节）：
        {expression, internal, affective: {AFF, TRS, ATT, stage_code,
         stage_label}, last_updated}
    无状态行时返回初始值（角色档案初始值 + 配置初始值，与管道首轮一致）。
    角色不存在 → 404。
    """
    try:
        profile = manager.get(character_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    user_state, updated_at = load_user_state(db, character_id, current_user.id)
    # 阶段判定复用情感引擎（determine_stage），与管道内计算同源
    engine = EmotionEngine(settings.EMOTION_CONFIG_PATH, profile, user_state)
    state = engine.get_full_state()
    state["last_updated"] = updated_at.isoformat() if updated_at else None
    return state
