"""角色管理器单元测试（文档 2 第 3.2 节全套方法）。"""

import json

import pytest

from helpers import make_db_session_factory, seed_character, seed_user
from core.character.manager import (
    CharacterManager,
    CharacterNotFoundError,
    CharacterValidationError,
)
from core.character.generator import CharacterGenerator
from core.character.template_loader import TemplateLoader

TEMPLATE_ID = "gentle_artist"
USER_A = "user_a"
USER_B = "user_b"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def manager(factory):
    loader = TemplateLoader()
    return CharacterManager(factory, loader, CharacterGenerator(loader))


class TestCreateFromTemplate:
    def test_creates_persisted_profile(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "小知",
                                               owner_user_id=USER_A)
        assert profile.id
        assert profile.name == "小知"
        assert profile.theme.primary == "#C4A882"
        assert profile.generation_meta.source == "template"
        assert profile.generation_meta.template_id == TEMPLATE_ID
        assert profile.visibility.owner_user_id == USER_A
        assert profile.visibility.is_public is False
        # 已落库：get 能读回
        assert manager.get(profile.id).name == "小知"

    def test_top_level_name_and_visibility_authoritative(self, manager):
        """文档 2 第 6.3 节：顶层 name/visibility 优先于 overrides 同名键。"""
        profile = manager.create_from_template(
            TEMPLATE_ID, "权威名", visibility="public",
            overrides={"name": "占位名", "visibility": {"is_public": False},
                       "nickname": "昵称"},
            owner_user_id=USER_A)
        assert profile.name == "权威名"
        assert profile.visibility.is_public is True
        assert profile.nickname == "昵称"  # overrides 的其余字段仍生效

    def test_unknown_template_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.create_from_template("no_such", "x")


class TestCreateRandom:
    def test_persisted_with_seed(self, manager):
        profile = manager.create_random(TEMPLATE_ID, seed=99,
                                        owner_user_id=USER_A)
        assert manager.get(profile.id).generation_meta.random_seed == 99
        assert profile.generation_meta.source == "random"

    def test_visibility_public(self, manager):
        profile = manager.create_random(TEMPLATE_ID, seed=1,
                                        visibility="public",
                                        owner_user_id=USER_A)
        assert profile.visibility.is_public is True


class TestCreateManual:
    def test_creates_and_validates(self, manager):
        profile = manager.create_manual(
            {"name": "手建", "gender": "female", "age": 24},
            owner_user_id=USER_A)
        assert manager.get(profile.id).name == "手建"
        assert profile.generation_meta.source == "manual"

    def test_validation_failure_raises_with_issues(self, manager):
        with pytest.raises(CharacterValidationError) as exc_info:
            manager.create_manual({"name": "", "age": 19,
                                   "background": {"occupation": "大学讲师"}})
        fields = {issue.field for issue in exc_info.value.issues}
        assert "name" in fields


class TestGet:
    def test_missing_raises(self, manager):
        with pytest.raises(CharacterNotFoundError):
            manager.get("no-such-id")

    def test_archived_not_returned(self, manager, factory):
        profile = manager.create_from_template(TEMPLATE_ID, "归档者",
                                               owner_user_id=USER_A)
        manager.delete(profile.id, archive=True)
        with pytest.raises(CharacterNotFoundError):
            manager.get(profile.id)


class TestListAvailable:
    def test_own_plus_public_plus_preset(self, manager):
        mine = manager.create_from_template(TEMPLATE_ID, "我的",
                                            owner_user_id=USER_A)
        others_public = manager.create_random(
            TEMPLATE_ID, seed=2, visibility="public", owner_user_id=USER_B)
        others_private = manager.create_random(
            TEMPLATE_ID, seed=3, visibility="private", owner_user_id=USER_B)
        with manager.db_session_factory() as db:
            from db.models import Character

            preset = Character(id="char_preset", name="系统预置")
            preset.profile_json = json.dumps(
                {"id": "char_preset", "name": "系统预置"})
            preset.is_public = 1
            db.add(preset)
            db.commit()
        available = manager.list_available(USER_A)
        ids = {profile.id for profile in available}
        assert mine.id in ids
        assert others_public.id in ids          # 他人公开可见
        assert others_private.id not in ids     # 他人私有不可见
        assert "char_preset" in ids             # 系统预置（owner 为 NULL）

    def test_archived_excluded(self, manager):
        archived = manager.create_random(TEMPLATE_ID, seed=5,
                                         owner_user_id=USER_A)
        manager.delete(archived.id, archive=True)
        ids = {profile.id for profile in manager.list_available(USER_A)}
        assert archived.id not in ids


class TestUpdate:
    def test_update_records_version(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "更新前",
                                               owner_user_id=USER_A)
        updated = manager.update(profile.id, {"nickname": "更新后"},
                                 change_reason="test")
        assert updated.nickname == "更新后"
        versions = manager.versions(profile.id)
        assert len(versions) == 1
        assert versions[0]["change_reason"] == "test"

    def test_update_merges_sections(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "深合并",
                                               owner_user_id=USER_A)
        updated = manager.update(profile.id,
                                 {"appearance": {"hair": "黑色长发"}})
        assert updated.appearance.hair == "黑色长发"
        assert updated.appearance.body_type == "slim"  # 未动节保留

    def test_update_id_immutable(self, manager):
        """id 不允许被更新改写（强制复位为路径参数）。"""
        profile = manager.create_from_template(TEMPLATE_ID, "锁 id",
                                               owner_user_id=USER_A)
        updated = manager.update(profile.id, {"id": "hacked"})
        assert updated.id == profile.id

    def test_validation_failure_no_write(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "校验",
                                               owner_user_id=USER_A)
        with pytest.raises(CharacterValidationError):
            manager.update(profile.id, {"name": ""})
        assert manager.get(profile.id).name == "校验"


class TestSwitch:
    def test_returns_state_and_session(self, manager, factory):
        profile = manager.create_from_template(TEMPLATE_ID, "切换",
                                               owner_user_id=USER_A)
        from db.models import Session as ChatSession

        with factory() as db:
            db.add(ChatSession(id="sess-1", character_id=profile.id,
                               user_id=USER_A, status="active"))
            db.commit()
        result = manager.switch(profile.id, USER_A)
        assert result.character_id == profile.id
        assert result.profile.name == "切换"
        assert result.state == {}          # 无状态行
        assert result.active_session["session_id"] == "sess-1"

    def test_no_session_returns_none(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "无会话",
                                               owner_user_id=USER_A)
        result = manager.switch(profile.id, USER_A)
        assert result.active_session is None


class TestExportImport:
    def test_export_card_structure(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "导出",
                                               owner_user_id=USER_A)
        card = manager.export(profile.id)
        assert card["profile"]["name"] == "导出"
        assert card["profile"]["id"] == profile.id

    def test_import_creates_new_character(self, manager, tmp_path):
        profile = manager.create_from_template(TEMPLATE_ID, "被导出",
                                               owner_user_id=USER_A)
        card_path = tmp_path / "card.json"
        card_path.write_text(json.dumps(manager.export(profile.id),
                                        ensure_ascii=False), encoding="utf-8")
        imported = manager.import_from_file(str(card_path),
                                            owner_user_id=USER_B)
        assert imported.name == "被导出"
        assert imported.id != profile.id       # 导入生成新 id
        assert imported.visibility.owner_user_id == USER_B

    def test_import_invalid_card_raises(self, manager, tmp_path):
        card_path = tmp_path / "bad.json"
        card_path.write_text(json.dumps({"profile": {"name": ""}}),
                             encoding="utf-8")
        with pytest.raises(CharacterValidationError):
            manager.import_from_file(str(card_path))


class TestArchiveRestore:
    def test_delete_archives(self, manager, factory):
        profile = manager.create_from_template(TEMPLATE_ID, "软删",
                                               owner_user_id=USER_A)
        manager.delete(profile.id)
        from db.models import Character

        with factory() as db:
            row = db.get(Character, profile.id)
            assert row.is_archived == 1
            assert row.is_active == 0

    def test_restore(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "恢复",
                                               owner_user_id=USER_A)
        manager.delete(profile.id)
        restored = manager.restore(profile.id)
        assert restored.name == "恢复"
        assert manager.get(profile.id).name == "恢复"

    def test_restore_non_archived_raises(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "未归档",
                                               owner_user_id=USER_A)
        with pytest.raises(CharacterNotFoundError):
            manager.restore(profile.id)


class TestVersionsRollback:
    def test_rollback_restores_profile(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "原版",
                                               owner_user_id=USER_A)
        manager.update(profile.id, {"nickname": "改过的"},
                       change_reason="first edit")
        manager.update(profile.id, {"nickname": "又改的"},
                       change_reason="second edit")
        versions = manager.versions(profile.id)
        assert len(versions) == 2
        first_version_id = versions[-1]["version_id"]  # 最早那条
        rolled = manager.rollback(profile.id, first_version_id)
        assert rolled.nickname == "改过的"
        # 回滚本身也记版本
        assert len(manager.versions(profile.id)) == 3

    def test_rollback_unknown_version_raises(self, manager):
        profile = manager.create_from_template(TEMPLATE_ID, "无版本",
                                               owner_user_id=USER_A)
        with pytest.raises(CharacterNotFoundError):
            manager.rollback(profile.id, 9999)

    def test_versions_trimmed_to_20(self, manager):
        """文档 2 第 5.3 节：每角色保留最近 20 版。"""
        profile = manager.create_from_template(TEMPLATE_ID, "多版本",
                                               owner_user_id=USER_A)
        for index in range(25):
            manager.update(profile.id, {"nickname": f"n{index}"})
        assert len(manager.versions(profile.id)) == 20


class TestExportAsTemplate:
    def test_writes_template_file(self, manager, tmp_path):
        profile = manager.create_from_template(TEMPLATE_ID, "模板源",
                                               owner_user_id=USER_A)
        loader = TemplateLoader(tmp_path)
        manager.template_loader = loader
        path = manager.export_as_template(profile.id, "my_tpl")
        assert path.is_file()
        template = loader.load("my_tpl")
        assert template.name == "模板源"
        assert template.theme["primary"] == "#C4A882"
