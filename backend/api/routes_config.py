"""配置接口（文档 2 第 6.10 节）：LLM 与主动行为的全局配置读写。

权限口径（Phase 9 前置裁决）：全局配置（config 表单例键），任何已认证
用户可读写；路由层要求 JWT。LLM 覆盖存 config 表，读取时与 Settings
（环境变量/.env）合并——config 表优先，改设置在 get_pipeline 即时生效。
"""

from __future__ import annotations

import logging

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from auth.dependencies import get_current_user
from config.settings import Settings, get_settings
from db.config_store import (
    GLOBAL_CONFIG_USER,
    LLM_CONFIG_KEY,
    PROACTIVE_CONFIG_KEY,
    load_config,
    load_merged_config,
    merge_overrides,
    save_config,
)
from db.models import User
from db.sqlite import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


class LLMConfigUpdate(BaseModel):
    """PUT /api/config/llm 请求体（文档 2 第 6.10 节）。

    api_key 语义：字段缺省（不出现在请求体）= 保持原值；
    空字符串 = 清除；新值 = 替换。其余字段同理缺省保持。
    """

    mode: str | None = None
    api: dict | None = None
    local: dict | None = None


class ProactiveConfigUpdate(BaseModel):
    """PUT /api/config/proactive 请求体：部分更新，只传要改的字段。"""

    enabled: bool | None = None
    interval_hours: float | None = None
    min_gap_hours: float | None = None
    max_daily: int | None = None
    silence_threshold: int | None = None
    silence_recovery_hours: float | None = None
    quiet_hours: list | None = None


def _mask_key(api_key: str | None) -> str | None:
    """API Key 掩码（sk-…**** 形态，文档 2 第 6.10 节）；未配置返回 None。"""
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "****"
    return f"{api_key[:3]}…****"


def _load_numeric_config(path: str) -> dict:
    """读 dudulu_config.yaml（主动行为全局默认值的来源）。"""
    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except OSError as exc:
        logger.warning("读取数值配置失败: %s", exc)
        return {}


@router.get("/config/llm")
def get_llm_config(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings)) -> dict:
    """读 LLM 配置（GET 不返回明文 key，只给 has_api_key / api_key_masked）。

    Phase 10（额外问题 1）：返回「全局默认 + 当前用户覆盖」合并视图，
    不再有"一个用户改 Key 影响所有人"的问题。
    """
    overrides = load_merged_config(db, LLM_CONFIG_KEY, current_user.id)
    api = merge_overrides({
        "provider": settings.LLM_API_PROVIDER,
        "model": settings.LLM_API_MODEL,
        "base_url": settings.LLM_API_BASE_URL,
    }, overrides.get("api") or {})
    local = merge_overrides({
        "base_url": settings.LLM_LOCAL_BASE_URL,
        "model": settings.LLM_LOCAL_MODEL,
    }, overrides.get("local") or {})
    # key 的回退链：用户/全局显式设置（含空串 = 已清除）> 环境变量。
    # 不能用 `or` 兜底——那会把用户显式清除的空串复活成 env key。
    if "api_key" in api:
        api_key = api.pop("api_key") or ""
    else:
        api_key = settings.LLM_API_KEY
    return {
        "mode": overrides.get("mode", settings.LLM_MODE),
        "api": {
            "provider": api.get("provider"),
            "model": api.get("model"),
            "base_url": api.get("base_url"),
            "has_api_key": bool(api_key),
            "api_key_masked": _mask_key(api_key),
        },
        "local": local,
    }


@router.put("/config/llm")
def update_llm_config(
        body: LLMConfigUpdate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings)) -> dict:
    """更新当前用户的 LLM 覆盖（部分字段；缺省保持，api_key 空串 = 清除）。

    以「全局默认 + 本人已有覆盖」为基础合并后写回本人作用域，
    不影响其他用户（额外问题 1 的修复）。
    """
    stored = load_merged_config(db, LLM_CONFIG_KEY, current_user.id)
    update = body.model_dump(exclude_unset=True)
    for section in ("api", "local"):
        if section in update and update[section] is not None:
            merged = merge_overrides(stored.get(section) or {},
                                     update[section])
            # api_key 显式空串 = 清除：从覆盖里删掉，回落 Settings 值也为空
            if section == "api" and update[section].get("api_key") == "":
                merged.pop("api_key", None)
                merged["api_key"] = ""
            stored[section] = merged
        elif section in update:
            stored.pop(section, None)
    if "mode" in update:
        stored["mode"] = update["mode"]
    save_config(db, LLM_CONFIG_KEY, stored, user_id=current_user.id)
    db.commit()
    logger.info("LLM 配置已更新（user=%s）", current_user.id)
    return {"status": "ok"}


@router.get("/config/proactive")
def get_proactive_config(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings)) -> dict:
    """读主动行为配置（字段与文档 1 第 12.4 节参数表一一对应）。

    Phase 10：全局默认（dudulu_config.yaml + __global__ 覆盖）与当前用户
    覆盖的合并视图。
    """
    numeric = _load_numeric_config(settings.EMOTION_CONFIG_PATH)
    proactive_cfg = numeric.get("proactive") or {}
    base = {
        "enabled": bool(proactive_cfg.get("enabled", True)),
        "interval_hours": float(proactive_cfg.get("interval_hours", 4.5)),
        "min_gap_hours": float(proactive_cfg.get("min_gap_hours", 3.0)),
        "max_daily": int(proactive_cfg.get("max_daily", 3)),
        "silence_threshold": int(proactive_cfg.get("silence_threshold", 3)),
        "silence_recovery_hours": float(
            proactive_cfg.get("silence_recovery_hours", 12)),
        "quiet_hours": list(proactive_cfg.get("quiet_hours", [23, 8])),
    }
    base = merge_overrides(base, load_config(
        db, PROACTIVE_CONFIG_KEY, user_id=GLOBAL_CONFIG_USER))
    return merge_overrides(
        base, load_config(db, PROACTIVE_CONFIG_KEY, user_id=current_user.id))


@router.put("/config/proactive")
def update_proactive_config(
        body: ProactiveConfigUpdate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)) -> dict:
    """更新当前用户的主动行为覆盖（部分更新，只传要改的字段）。"""
    stored = load_merged_config(db, PROACTIVE_CONFIG_KEY, current_user.id)
    stored.update(body.model_dump(exclude_unset=True))
    save_config(db, PROACTIVE_CONFIG_KEY, stored, user_id=current_user.id)
    db.commit()
    logger.info("主动行为配置已更新（user=%s）: %s",
                current_user.id, sorted(body.model_dump(exclude_unset=True)))
    return {"status": "ok"}
