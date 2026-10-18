"""角色接口（文档 2 第 6.3 节全部端点 + 第 6.4 节预览随机）。

预览随机三个端点不落库（文档 2 第 6.4 节设计说明）。
用户隔离：list/get 等均以 JWT 注入的 user_id 为基准（文档 2 第 7.2 节）；
越权访问他人私有角色返回 404（不泄露存在性）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from config.settings import Settings, get_settings
from core.affective_layer import determine_stage
from core.character.generator import PreviewGenerator
from core.character.manager import (
    CharacterManager,
    CharacterNotFoundError,
    CharacterValidationError,
    get_character_manager,
)
from db.models import User
from db.sqlite import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ── 请求体 ──────────────────────────────────────────────────


class TemplateCreateRequest(BaseModel):
    """POST /api/character/create/template 请求体（文档 2 第 6.3 节）。"""

    template_id: str
    name: str
    visibility: str = "private"
    overrides: dict | None = None


class RandomCreateRequest(BaseModel):
    """POST /api/character/create/random 请求体。"""

    template_id: str
    seed: int | None = None
    locked_fields: list[str] | None = None
    visibility: str = "private"


class ManualCreateRequest(BaseModel):
    """POST /api/character/create/manual 请求体（与 import 同构）。"""

    profile: dict


class UpdateRequest(BaseModel):
    """PUT /api/character/{id} 请求体：增量字段 dict（顶层节深合并）。"""

    updates: dict


class ImportRequest(BaseModel):
    """POST /api/character/import 请求体：角色卡 JSON（文档 2 第 6.3 节）。"""

    profile: dict


class RandomFieldRequest(BaseModel):
    """POST /api/character/preview/random-field 请求体（文档 2 第 6.4 节）。"""

    template_id: str
    field: str
    current_profile: dict = {}


class RandomAllRequest(BaseModel):
    """POST /api/character/preview/random-all 请求体（文档 2 第 6.4 节）。"""

    template_id: str
    locked_fields: list[str] = []
    current_profile: dict = {}


class ValidateRequest(BaseModel):
    """POST /api/character/preview/validate 请求体（文档 2 第 6.4 节）。"""

    profile: dict


class TemplateExportRequest(BaseModel):
    """POST /api/character/template-export 请求体（角色导出为模板）。"""

    character_id: str
    template_id: str


# ── 查询 ────────────────────────────────────────────────────


@router.get("/character/list")
def list_characters(
        archived: bool = Query(default=False,
                               description="true = 只看自己已归档的角色"),
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db),
        settings: Settings = Depends(get_settings)) -> dict:
    """当前用户可用的角色列表（自己创建的 + 系统预置 + 他人公开的）。

    `archived=true` 时返回**该用户已归档**的角色（恢复入口用），
    默认只返回未归档的。

    列表视图字段（文档 3 第 6.3 节角色卡）：完整档案 + 扁平的
    `occupation` + 该用户对该角色的当前关系字段（`stage_code` /
    `stage_label` / `affection`）。无状态行时用角色关系初始值
    （与 /api/state 的首屏一致）。后端是契约定义方（Phase 10 裁决⑦）。
    """
    profiles = (manager.list_archived(current_user.id) if archived
                else manager.list_available(current_user.id))
    states = _load_user_states(db, current_user.id, [p.id for p in profiles])
    stages_config = _load_stages_config(settings.EMOTION_CONFIG_PATH)
    characters = []
    for profile in profiles:
        data = profile.to_dict()
        data["occupation"] = profile.background.occupation
        affective = states.get(profile.id) or _initial_affective(
            profile, _load_affective_initial(settings.EMOTION_CONFIG_PATH))
        stage_code, stage_label = determine_stage(affective, stages_config)
        data["stage_code"] = stage_code
        data["stage_label"] = stage_label
        data["affection"] = affective.get("AFF", 0)
        characters.append(data)
    return {"characters": characters}


def _load_user_states(db: Session, user_id: str,
                      character_ids: list[str]) -> dict[str, dict]:
    """批量读该用户对若干角色的 affective 状态（无行/无 JSON 返回空）。"""
    import json

    from sqlalchemy import select

    from db.models import CharacterUserState

    if not character_ids:
        return {}
    rows = db.execute(
        select(CharacterUserState).where(
            CharacterUserState.user_id == user_id,
            CharacterUserState.character_id.in_(character_ids))
    ).scalars().all()
    states: dict[str, dict] = {}
    for row in rows:
        if row.affective_state:
            try:
                states[row.character_id] = json.loads(row.affective_state)
            except (TypeError, ValueError):
                continue
    return states


def _load_affective_initial(config_path: str) -> dict:
    """读 affective_layer.initial（关系初始值的最终回退，与引擎一致）。"""
    import yaml

    try:
        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        return (config.get("affective_layer") or {}).get("initial") or {}
    except OSError:
        return {}


def _initial_affective(profile, config_initial: dict) -> dict:
    """无状态行时的阶段判定输入。

    回退链与引擎一致：角色关系初始值 → 配置 affective 初始值 → 0。
    """
    relationship = profile.relationship_defaults
    return {
        "AFF": float(relationship.initial_affection
                     if relationship.initial_affection is not None
                     else config_initial.get("AFF", 0)),
        "TRS": float(relationship.initial_trust
                     if relationship.initial_trust is not None
                     else config_initial.get("TRS", 0)),
        "ATT": float(relationship.initial_attachment
                     if relationship.initial_attachment is not None
                     else config_initial.get("ATT", 0)),
    }


def _load_stages_config(config_path: str) -> dict:
    """读 affective_layer.stages（阶段判定用）。"""
    import yaml

    try:
        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        return (config.get("affective_layer") or {}).get("stages") or {}
    except OSError:
        return {}


@router.get("/character/{character_id}")
def get_character(
        character_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """角色详情。他人私有角色返回 404（隔离，不泄露存在性）。"""
    profile = _get_visible_character(manager, db, character_id, current_user.id)
    return profile.to_dict()


# ── 创建 ────────────────────────────────────────────────────


@router.post("/character/create/template")
def create_from_template(
        body: TemplateCreateRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """从模板创建角色（顶层 name/visibility 权威，overrides 承载其余改动）。"""
    try:
        profile = manager.create_from_template(
            body.template_id, body.name, visibility=body.visibility,
            overrides=body.overrides, owner_user_id=current_user.id)
    except CharacterValidationError as exc:
        raise _validation_error(exc) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return profile.to_dict()


@router.post("/character/create/random")
def create_random(
        body: RandomCreateRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """随机创建角色（持久化）。"""
    try:
        profile = manager.create_random(
            body.template_id, seed=body.seed, locked_fields=body.locked_fields,
            visibility=body.visibility, owner_user_id=current_user.id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return profile.to_dict()


@router.post("/character/create/manual")
def create_manual(
        body: ManualCreateRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """手动创建角色（逐字段填写）。"""
    try:
        profile = manager.create_manual(body.profile,
                                        owner_user_id=current_user.id)
    except CharacterValidationError as exc:
        raise _validation_error(exc) from exc
    return profile.to_dict()


# ── 更新 / 归档 / 恢复 ──────────────────────────────────────


@router.put("/character/{character_id}")
def update_character(
        character_id: str,
        body: UpdateRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """更新角色档案并记录版本。"""
    _require_owner(manager, db, character_id, current_user.id)
    try:
        profile = manager.update(character_id, body.updates)
    except CharacterValidationError as exc:
        raise _validation_error(exc) from exc
    return profile.to_dict()


@router.delete("/character/{character_id}")
def delete_character(
        character_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """归档角色（默认软删除，文档 2 第 3.2 节）。"""
    _require_owner(manager, db, character_id, current_user.id)
    manager.delete(character_id, archive=True)
    return {"status": "archived"}


@router.post("/character/{character_id}/restore")
def restore_character(
        character_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """恢复归档角色。"""
    _require_owner(manager, db, character_id, current_user.id,
                   allow_archived=True)
    try:
        profile = manager.restore(character_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return profile.to_dict()


# ── 切换 ────────────────────────────────────────────────────


@router.post("/character/{character_id}/switch")
def switch_character(
        character_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """切换角色：返回目标角色的状态和最近会话（文档 2 第 3.2 节）。"""
    try:
        result = manager.switch(character_id, current_user.id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.to_dict()


# ── 导入导出 ────────────────────────────────────────────────


@router.get("/character/{character_id}/export")
def export_character(
        character_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """导出角色卡（{"profile": ...}，与 import 对称）。"""
    _get_visible_character(manager, db, character_id, current_user.id)
    return manager.export(character_id)


@router.post("/character/import")
def import_character(
        body: ImportRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """导入角色卡；校验失败返回 {"status": "error", "issues": [...]} 且不落库。"""
    try:
        profile = manager.import_card(body.profile,
                                      owner_user_id=current_user.id)
    except CharacterValidationError as exc:
        return {"status": "error",
                "issues": [issue.to_dict() for issue in exc.issues]}
    return {"status": "ok", "profile": profile.to_dict()}


# ── 版本 ────────────────────────────────────────────────────


@router.get("/character/{character_id}/versions")
def list_versions(
        character_id: str,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """版本历史（最近 20 条，文档 2 第 6.3 节响应结构）。"""
    _require_owner(manager, db, character_id, current_user.id)
    return {"versions": manager.versions(character_id)}


@router.post("/character/{character_id}/rollback/{version_id}")
def rollback_character(
        character_id: str,
        version_id: int,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """回滚到指定版本。"""
    _require_owner(manager, db, character_id, current_user.id)
    try:
        profile = manager.rollback(character_id, version_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return profile.to_dict()


@router.post("/character/template-export")
def export_as_template(
        body: TemplateExportRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager),
        db: Session = Depends(get_db)) -> dict:
    """将角色导出为社区模板（POST /api/template/export 的角色侧入口）。"""
    _require_owner(manager, db, body.character_id, current_user.id)
    try:
        path = manager.export_as_template(body.character_id, body.template_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "template_id": body.template_id, "path": str(path)}


# ── 预览随机（不落库，文档 2 第 6.4 节） ────────────────────


@router.post("/character/preview/random-field")
def preview_random_field(
        body: RandomFieldRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """随机单个字段（前端 🎲 按钮），不写库。"""
    preview = PreviewGenerator(manager.generator)
    try:
        return preview.randomize_field(body.template_id, body.field,
                                       body.current_profile)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/character/preview/random-all")
def preview_random_all(
        body: RandomAllRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """随机全部未锁定字段（前端"随机全部"按钮），不写库。"""
    preview = PreviewGenerator(manager.generator)
    try:
        return preview.randomize_all(body.template_id, body.locked_fields,
                                     body.current_profile)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/character/preview/validate")
def preview_validate(
        body: ValidateRequest,
        current_user: User = Depends(get_current_user),
        manager: CharacterManager = Depends(get_character_manager)) -> dict:
    """实时校验档案一致性，不写库。"""
    preview = PreviewGenerator(manager.generator)
    return preview.validate(body.profile)


# ── 隔离辅助 ────────────────────────────────────────────────


def _get_visible_character(manager: CharacterManager, db: Session,
                           character_id: str, user_id: str):
    """读角色并要求当前用户可见（自己/公开/系统预置），否则 404。"""
    from sqlalchemy import select

    from db.models import Character

    row = db.execute(
        select(Character).where(Character.id == character_id,
                                Character.is_archived == 0)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    if (row.owner_user_id not in (None, user_id) and not row.is_public):
        raise HTTPException(status_code=404, detail="角色不存在")
    try:
        return manager.get(character_id)
    except CharacterNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _require_owner(manager: CharacterManager, db: Session,
                   character_id: str, user_id: str,
                   allow_archived: bool = False) -> None:
    """写操作要求当前用户是角色 owner（系统预置角色不可改）。

    allow_archived=True 时允许已归档角色（restore 用）。
    """
    from sqlalchemy import select

    from db.models import Character

    row = db.execute(
        select(Character).where(Character.id == character_id)
    ).scalar_one_or_none()
    if row is None or (row.is_archived and not allow_archived):
        raise HTTPException(status_code=404, detail="角色不存在")
    if row.owner_user_id != user_id:
        raise HTTPException(status_code=403, detail="无权修改该角色")


def _validation_error(exc: CharacterValidationError) -> HTTPException:
    """校验失败 → 400 + issues（文档 2 第 6.3 节 import 的同款结构）。"""
    return HTTPException(status_code=400, detail={
        "status": "error",
        "issues": [issue.to_dict() for issue in exc.issues],
    })
