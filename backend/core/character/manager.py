"""角色管理器（文档 2 第 3.2 节）：角色档案的生命周期。

持久化走 SQLite（characters / character_versions 两张表，文档 2 第 5.2 节）；
模板与随机生成委托 TemplateLoader / CharacterGenerator。

与文档 2 第 3.2 节签名的两处适配（已在交付报告说明）：
1. `db: SQLiteDB` → `db_session_factory: sessionmaker`（本项目的 SQLAlchemy
   会话工厂，Phase 4 落地时即为此形态）；
2. create_* 系列增加可选尾参 `owner_user_id`（文档签名未含归属参数，
   但可见性与隔离要求创建时记录 owner，路由从 JWT 传入）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from core.character.generator import CharacterGenerator
from core.character.profile import CharacterProfile
from core.character.template_loader import Template, TemplateLoader
from core.character.validator import ConsistencyIssue, validate_consistency
from db.models import Character, CharacterVersion
from db.sqlite import get_session_factory
from db.user_state import load_user_state
from memory.schemas import generate_id, now_iso

logger = logging.getLogger(__name__)

# 每角色保留的最近版本数（文档 2 第 5.3 节数据治理策略）
MAX_VERSIONS_PER_CHARACTER = 20


class CharacterNotFoundError(Exception):
    """角色不存在（或已归档）时抛出，路由层转 404。"""


class CharacterValidationError(Exception):
    """角色卡校验失败时抛出，携带问题列表，路由层转 400。"""

    def __init__(self, issues: list[ConsistencyIssue]):
        """输入：一致性问题列表；副作用：无。"""
        self.issues = issues
        super().__init__("角色档案校验失败: "
                         + "; ".join(issue.message for issue in issues))


@dataclass
class SwitchResult:
    """角色切换结果（文档 2 第 3.2 节 switch：目标角色的状态和最近会话）。"""

    character_id: str
    profile: CharacterProfile
    state: dict
    active_session: dict | None

    def to_dict(self) -> dict:
        """输出 API 用的 dict 形态；副作用：无。"""
        return {
            "character_id": self.character_id,
            "profile": self.profile.to_dict(),
            "state": self.state,
            "active_session": self.active_session,
        }


class CharacterManager:
    """职责：管理角色档案的生命周期（文档 2 第 3.2 节）。"""

    def __init__(self, db_session_factory: sessionmaker[Session],
                 template_loader: TemplateLoader,
                 generator: CharacterGenerator):
        """初始化角色管理器。

        输入：SQLite 会话工厂、模板加载器、随机生成器；副作用：无。
        """
        self.db_session_factory = db_session_factory
        self.template_loader = template_loader
        self.generator = generator

    # ── 创建 ────────────────────────────────────────────────

    def create_from_template(self, template_id: str, name: str,
                             visibility: str = "private",
                             overrides: dict | None = None,
                             owner_user_id: str | None = None
                             ) -> CharacterProfile:
        """从模板创建角色（文档 2 第 6.3 节：顶层 name/visibility 权威）。

        输入：模板 id、名字、可见性（private/public）、其余字段覆盖；
        输出：已持久化的 CharacterProfile；副作用：写 characters 表。
        """
        template = self.template_loader.load(template_id)
        profile_data = _profile_from_template(template, name, visibility,
                                              overrides, owner_user_id)
        return self._persist_new(profile_data, change_reason=None)

    def create_random(self, template_id: str, seed: int | None = None,
                       locked_fields: list | None = None,
                       visibility: str = "private",
                       owner_user_id: str | None = None
                       ) -> CharacterProfile:
        """随机生成角色并持久化（文档 2 第 3.2 节）。"""
        template = self.template_loader.load(template_id)
        profile = self.generator.generate(
            template=template, seed=seed, locked_fields=locked_fields)
        profile.visibility.is_public = visibility == "public"
        profile.visibility.owner_user_id = owner_user_id
        profile.generation_meta.created_at = now_iso()
        return self._persist_new(profile.to_dict(), change_reason=None)

    def create_manual(self, profile_data: dict,
                      owner_user_id: str | None = None) -> CharacterProfile:
        """手动创建角色（从空白逐字段填写，文档 1 第 13.3 节第三条路径）。"""
        data = dict(profile_data)
        data.setdefault("id", generate_id())
        data.setdefault("generation_meta", {})["source"] = "manual"
        data["generation_meta"].setdefault("created_at", now_iso())
        data.setdefault("visibility", {})
        if owner_user_id is not None:
            data["visibility"]["owner_user_id"] = owner_user_id
        issues = validate_consistency(CharacterProfile.from_dict(data))
        if issues:
            raise CharacterValidationError(issues)
        return self._persist_new(data, change_reason=None)

    # ── 查询 ────────────────────────────────────────────────

    def get(self, character_id: str) -> CharacterProfile:
        """按 id 读角色档案。

        输入：角色 id；输出：CharacterProfile；副作用：无。
        不存在或已归档抛 CharacterNotFoundError。
        """
        with self.db_session_factory() as db:
            row = db.get(Character, character_id)
            if row is None or row.is_archived:
                raise CharacterNotFoundError(f"角色不存在: {character_id}")
            return CharacterProfile.from_dict(json.loads(row.profile_json))

    def list_available(self, user_id: str) -> list[CharacterProfile]:
        """列出用户可用的角色（文档 2 第 3.2 节）。

        = 自己创建的 + 系统预置（owner 为 NULL）+ 其他用户公开的；
        不含已归档角色。按创建时间正序。
        """
        from sqlalchemy import or_, select

        with self.db_session_factory() as db:
            rows = db.execute(
                select(Character).where(
                    Character.is_archived == 0,
                    or_(Character.owner_user_id == user_id,
                        Character.owner_user_id.is_(None),
                        Character.is_public == 1),
                ).order_by(Character.created_at)
            ).scalars().all()
            return [CharacterProfile.from_dict(json.loads(row.profile_json))
                    for row in rows]

    def list_archived(self, user_id: str) -> list[CharacterProfile]:
        """列出该用户已归档的角色（只有自己的；供恢复入口使用）。

        归档是软删除（DELETE /character/{id}），档案仍在库里；
        没有这个查询，归档后用户就再也找不回来了（2026-10-18 实测）。
        """
        from sqlalchemy import select

        with self.db_session_factory() as db:
            rows = db.execute(
                select(Character).where(
                    Character.is_archived == 1,
                    Character.owner_user_id == user_id,
                ).order_by(Character.created_at)
            ).scalars().all()
            return [CharacterProfile.from_dict(json.loads(row.profile_json))
                    for row in rows]

    # ── 更新 ────────────────────────────────────────────────

    def update(self, character_id: str, updates: dict,
               change_reason: str | None = None) -> CharacterProfile:
        """更新角色档案，记录版本（文档 2 第 3.2 节）。

        输入：角色 id、增量字段 dict（顶层节深合并）、变更原因；
        输出：更新后的档案；副作用：写 characters + character_versions。
        """
        profile = self.get(character_id)
        merged = _deep_merge(profile.to_dict(), updates or {})
        merged["id"] = character_id  # id 不允许被更新改写
        issues = validate_consistency(CharacterProfile.from_dict(merged))
        if issues:
            raise CharacterValidationError(issues)
        with self.db_session_factory() as db:
            row = db.get(Character, character_id)
            _write_row(row, merged)
            _record_version(db, character_id, merged,
                            change_reason or "manual update")
            _trim_versions(db, character_id)
            db.commit()
        return CharacterProfile.from_dict(merged)

    # ── 切换 ────────────────────────────────────────────────

    def switch(self, character_id: str, user_id: str) -> SwitchResult:
        """切换角色，返回目标角色的状态和最近会话（文档 2 第 3.2 节）。"""
        profile = self.get(character_id)
        with self.db_session_factory() as db:
            state, _ = load_user_state(db, character_id, user_id)
            active = _active_session(db, character_id, user_id)
        return SwitchResult(character_id=character_id, profile=profile,
                            state=state, active_session=active)

    # ── 导入导出 ────────────────────────────────────────────

    def export(self, character_id: str) -> dict:
        """导出角色卡（文档 2 第 6.3 节 import 的对称结构 {"profile": ...}）。"""
        return {"profile": self.get(character_id).to_dict()}

    def import_card(self, card: dict,
                    owner_user_id: str | None = None) -> CharacterProfile:
        """从角色卡 dict 导入（分配新 id 后走手动创建）。

        输入：角色卡（{"profile": {...}} 或裸档案 dict）、导入者 user_id；
        输出：新角色档案；副作用：写 characters 表。
        校验失败抛 CharacterValidationError 且不落库（路由转 error 响应）。
        """
        profile_data = card.get("profile", card) if isinstance(card, dict) else {}
        if not isinstance(profile_data, dict) or "name" not in profile_data:
            raise CharacterValidationError([
                ConsistencyIssue("profile", "角色卡缺少 profile.name")])
        # 导入一律分配新 id（卡片里的 id 属于来源角色，直接复用会撞主键）
        profile_data = dict(profile_data)
        profile_data["id"] = generate_id()
        return self.create_manual(profile_data, owner_user_id=owner_user_id)

    def import_from_file(self, file_path: str,
                         owner_user_id: str | None = None) -> CharacterProfile:
        """从角色卡文件导入（JSON 或 YAML，文档 2 第 6.3 节）。

        输入：文件路径、导入者 user_id；输出：新角色档案；
        副作用：写 characters 表。校验失败抛 CharacterValidationError 且不落库。
        """
        path = Path(file_path)
        with open(path, encoding="utf-8") as handle:
            card = yaml.safe_load(handle) or {}
        return self.import_card(card, owner_user_id=owner_user_id)

    # ── 归档与恢复 ──────────────────────────────────────────

    def delete(self, character_id: str, archive: bool = True):
        """归档（默认）或物理删除角色（文档 2 第 3.2 节：归档而非物理删除）。"""
        with self.db_session_factory() as db:
            row = db.get(Character, character_id)
            if row is None:
                raise CharacterNotFoundError(f"角色不存在: {character_id}")
            if archive:
                row.is_archived = 1
                row.is_active = 0
            else:
                db.delete(row)
            db.commit()

    def restore(self, character_id: str) -> CharacterProfile:
        """恢复归档角色（POST /{id}/restore）。"""
        with self.db_session_factory() as db:
            row = db.get(Character, character_id)
            if row is None or not row.is_archived:
                raise CharacterNotFoundError(f"角色不存在或未归档: {character_id}")
            row.is_archived = 0
            row.is_active = 1
            db.commit()
            return CharacterProfile.from_dict(json.loads(row.profile_json))

    # ── 版本 ────────────────────────────────────────────────

    def versions(self, character_id: str) -> list[dict]:
        """版本历史（最近 20 条，文档 2 第 5.3 节 + 6.3 节响应结构）。"""
        from sqlalchemy import select

        with self.db_session_factory() as db:
            rows = db.execute(
                select(CharacterVersion)
                .where(CharacterVersion.character_id == character_id)
                .order_by(CharacterVersion.id.desc())
            ).scalars().all()
            return [{"version_id": row.id,
                     "changed_at": row.changed_at.isoformat()
                     if row.changed_at else None,
                     "change_reason": row.change_reason}
                    for row in rows]

    def rollback(self, character_id: str, version_id: int) -> CharacterProfile:
        """回滚到指定版本（文档 2 第 6.3 节）。"""
        with self.db_session_factory() as db:
            version = db.get(CharacterVersion, version_id)
            if version is None or version.character_id != character_id:
                raise CharacterNotFoundError(
                    f"版本不存在: {character_id} v{version_id}")
            profile_data = json.loads(version.profile_json)
            row = db.get(Character, character_id)
            _write_row(row, profile_data)
            _record_version(db, character_id, profile_data,
                            change_reason=f"rollback to v{version_id}")
            _trim_versions(db, character_id)
            db.commit()
        return CharacterProfile.from_dict(profile_data)

    # ── 模板导出 ────────────────────────────────────────────

    def export_as_template(self, character_id: str,
                           template_id: str) -> Path:
        """把角色导出为社区模板（文档 2 第 3.4 节 export_as_template）。

        文档将该方法放在 TemplateLoader，但其入参是 character_id（需要读库），
        故实现放于本类并委托 loader 写盘（适配说明见交付报告）。
        """
        profile = self.get(character_id)
        template = Template(
            id=template_id,
            name=profile.name,
            description=f"由角色「{profile.name}」导出的模板",
            icon="✨",
            tags=list(profile.personality.traits[:4]),
            theme={"primary": profile.theme.primary,
                   "soft": profile.theme.soft, "deep": profile.theme.deep},
            defaults={
                "gender": profile.gender,
                "personality": {
                    "big_five": profile.personality.big_five.to_dict(),
                    "traits": list(profile.personality.traits),
                    "flaws": list(profile.personality.flaws),
                },
                "speech_style": {
                    "tone": profile.speech_style.tone,
                    "pace": profile.speech_style.pace,
                    "catchphrases": list(profile.speech_style.catchphrases),
                    "emoji_usage": profile.speech_style.emoji_usage,
                },
                "emotion_overrides": {
                    "initial_internal": dict(
                        profile.emotion_overrides.initial_internal),
                    "event_sensitivity": dict(
                        profile.emotion_overrides.event_sensitivity),
                },
            },
            locked_fields=[],
        )
        return self.template_loader.save_template(template)

    # ── 内部 ────────────────────────────────────────────────

    def _persist_new(self, profile_data: dict,
                     change_reason: str | None = None) -> CharacterProfile:
        """新建角色行（不含版本记录——版本只记更新）。"""
        issues = validate_consistency(CharacterProfile.from_dict(profile_data))
        if issues:
            raise CharacterValidationError(issues)
        with self.db_session_factory() as db:
            row = Character(id=profile_data["id"])
            _write_row(row, profile_data)
            db.add(row)
            db.commit()
        return CharacterProfile.from_dict(profile_data)


# ── 档案构建辅助 ────────────────────────────────────────────

def _profile_from_template(template: Template, name: str, visibility: str,
                           overrides: dict | None,
                           owner_user_id: str | None) -> dict:
    """模板 defaults + 用户覆盖 → 完整档案 dict（文档 2 第 6.3 节语义）。

    顶层 name / visibility 是权威字段；overrides 里的同名键仅作占位。
    """
    defaults = template.defaults or {}
    personality = defaults.get("personality") or {}
    speech_style = defaults.get("speech_style") or {}
    data: dict = {
        "id": generate_id(),
        "name": name,
        "nickname": None,
        "gender": defaults.get("gender"),
        "age": None,
        "birthday": None,
        "zodiac": None,
        "appearance": {
            "body_type": defaults.get("body_type"),
            "avatar_url": None,
        },
        "background": {
            "occupation": _first_of(
                (defaults.get("background_hints") or {}).get("occupation_pool")),
            "living_situation": _first_of(
                (defaults.get("background_hints") or {}).get("living_pool")),
            "key_experiences": [],
        },
        "personality": {
            "big_five": personality.get("big_five") or {},
            "traits": list(personality.get("traits") or []),
            "flaws": list(personality.get("flaws") or []),
        },
        "speech_style": {
            "tone": speech_style.get("tone"),
            "pace": speech_style.get("pace"),
            "catchphrases": list(speech_style.get("catchphrases") or []),
            "emoji_usage": speech_style.get("emoji_usage"),
        },
        "theme": dict(template.theme),
        "relationship_defaults": {"initial_stage": "S1"},
        "emotion_overrides": _deep_merge(
            defaults.get("emotion_overrides") or {}, {}),
        "proactive_enabled": True,
        "visibility": {
            "is_public": visibility == "public",
            "owner_user_id": owner_user_id,
        },
        "generation_meta": {
            "source": "template",
            "template_id": template.id,
            "locked_fields": list(template.locked_fields),
            "created_at": now_iso(),
        },
    }
    if overrides:
        data = _deep_merge(data, overrides)
        # 顶层权威字段复位（文档 2 第 6.3 节设计说明）
        data["name"] = name
        data["visibility"] = {
            "is_public": visibility == "public",
            "owner_user_id": owner_user_id,
        }
    return data


def _first_of(pool) -> str | None:
    """取池子第一项作为模板创建的默认值（空池返回 None）。"""
    items = list(pool or [])
    return str(items[0]) if items else None


def _deep_merge(parent: dict, child: dict) -> dict:
    """深合并：child 覆盖 parent，dict 递归、列表与标量整体替换。"""
    import copy

    merged = copy.deepcopy(parent)
    for key, value in child.items():
        if (key in merged and isinstance(merged[key], dict)
                and isinstance(value, dict)):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _write_row(row: Character, profile_data: dict) -> None:
    """把档案 dict 写入 characters 行（含去冗余列同步）。"""
    profile = CharacterProfile.from_dict(profile_data)
    row.name = profile.name
    row.profile_json = json.dumps(profile.to_dict(), ensure_ascii=False)
    row.generation_meta_json = json.dumps(
        profile.generation_meta.__dict__, ensure_ascii=False, default=str)
    row.owner_user_id = profile.visibility.owner_user_id
    row.is_public = 1 if profile.visibility.is_public else 0
    row.is_active = 1
    row.is_archived = 0


def _record_version(db: Session, character_id: str, profile_data: dict,
                    change_reason: str | None) -> None:
    """写一条角色版本（文档 2 第 5.2 节 character_versions）。"""
    db.add(CharacterVersion(
        character_id=character_id,
        profile_json=json.dumps(profile_data, ensure_ascii=False),
        change_reason=change_reason,
    ))
    # autoflush=False：先刷盘，_trim_versions 的查询才能看到这条新版本
    db.flush()


def _trim_versions(db: Session, character_id: str) -> None:
    """只保留最近 MAX_VERSIONS_PER_CHARACTER 条版本（文档 2 第 5.3 节）。"""
    from sqlalchemy import select

    rows = db.execute(
        select(CharacterVersion)
        .where(CharacterVersion.character_id == character_id)
        .order_by(CharacterVersion.id.desc())
    ).scalars().all()
    for stale in rows[MAX_VERSIONS_PER_CHARACTER:]:
        db.delete(stale)


def _active_session(db: Session, character_id: str,
                    user_id: str) -> dict | None:
    """查该用户对该角色的活跃会话（switch 的"最近会话"）。"""
    from sqlalchemy import select

    from db.models import Session as ChatSession

    row = db.execute(
        select(ChatSession).where(
            ChatSession.character_id == character_id,
            ChatSession.user_id == user_id,
            ChatSession.status == "active",
        ).order_by(ChatSession.started_at.desc())
    ).scalars().first()
    if row is None:
        return None
    return {"session_id": row.id,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "turn_count": row.turn_count}


# ── FastAPI 依赖（实例缓存，避免每请求重建模板缓存） ──────────

_MANAGER_CACHE: dict[int, CharacterManager] = {}


def get_character_manager(
    db_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> CharacterManager:
    """FastAPI 依赖：按会话工厂缓存返回 CharacterManager 实例。"""
    key = id(db_factory)
    manager = _MANAGER_CACHE.get(key)
    if manager is None:
        loader = TemplateLoader()
        manager = CharacterManager(
            db_session_factory=db_factory,
            template_loader=loader,
            generator=CharacterGenerator(loader),
        )
        _MANAGER_CACHE[key] = manager
    return manager
