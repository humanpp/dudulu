"""模板接口（文档 2 第 6.5 节全部端点）。

注意路由声明顺序：/template/pools 与 /template/names/{gender} 是静态路径，
必须先于 /template/{template_id} 声明，否则会被后者按 template_id 匹配。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth.dependencies import get_current_user
from core.character.manager import (
    CharacterManager,
    CharacterNotFoundError,
    get_character_manager,
)
from core.character.template_loader import (
    load_name_pool,
    load_pool,
)
from db.models import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/template/list")
def list_templates(
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """所有模板列表（内置在前，社区模板在后，文档 2 第 6.5 节）。"""
    loader = manager.template_loader
    templates = loader.list_builtin() + loader.list_community()
    return {"templates": [meta.to_dict() for meta in templates]}


@router.get("/template/pools")
def get_pools(
        current_user: User = Depends(get_current_user)) -> dict:
    """获取所有随机池（文档 2 第 6.5 节）。"""
    pools: dict = {}
    for pool_name in ("occupations", "experiences", "hairstyles",
                      "catchphrases", "rare_traits"):
        try:
            pools[pool_name] = load_pool(pool_name)
        except FileNotFoundError as exc:
            logger.warning("随机池缺失: %s", exc)
            pools[pool_name] = {}
    return {"pools": pools}


@router.get("/template/names/{gender}")
def get_name_pool(
        gender: str,
        current_user: User = Depends(get_current_user)) -> dict:
    """获取姓名池（文档 2 第 6.5 节；male/female/non_binary）。"""
    return {"gender": gender, "names": load_name_pool(gender)}


@router.post("/template/export")
def export_template(
        body: dict,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """将角色导出为模板（文档 2 第 6.5 节）。

    Body: {"character_id": ..., "template_id": ...}；
    响应：{"status": "ok", "template_id": ...}。
    """
    character_id = body.get("character_id")
    template_id = body.get("template_id")
    if not character_id or not template_id:
        raise HTTPException(status_code=422,
                            detail="缺少 character_id 或 template_id")
    try:
        path = manager.export_as_template(character_id, template_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "template_id": template_id, "path": str(path)}


@router.get("/template/{template_id}")
def get_template(
        template_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """模板详情（完整模板 dict）。"""
    try:
        template = manager.template_loader.load(template_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return template.to_dict()
