"""Phase 6 补充边界测试：加载器容错、校验容错、管理器边角、路由 404 分支。"""

import pytest
from fastapi.testclient import TestClient

from helpers import auth_header, build_test_app, make_db_session_factory, seed_user
from core.character.template_loader import TemplateLoader, load_occupation_rules
from core.character.validator import validate_consistency


class TestTemplateLoaderFaults:
    def test_yaml_without_template_id_raises(self, tmp_path):
        (tmp_path / "bad.yaml").write_text("template:\n  name: 无 id\n",
                                           encoding="utf-8")
        loader = TemplateLoader(tmp_path)
        with pytest.raises(ValueError, match="template.id"):
            loader.load("bad")

    def test_builtin_skips_unloadable(self, tmp_path, monkeypatch):
        """list 时单个模板加载失败只跳过并告警，不影响其余。"""
        (tmp_path / "good.yaml").write_text(
            "template:\n  id: good\n  name: 好模板\n", encoding="utf-8")
        (tmp_path / "bad.yaml").write_text("template:\n  name: 无 id\n",
                                           encoding="utf-8")
        config_path = tmp_path / "cfg.yaml"
        config_path.write_text(
            "templates:\n  builtin: [good, bad]\n", encoding="utf-8")
        monkeypatch.setattr(
            "core.character.template_loader.CONFIG_PATH", str(config_path))
        loader = TemplateLoader(tmp_path)
        metas = loader.list_builtin()
        assert [meta.id for meta in metas] == ["good"]

    def test_missing_dir_returns_empty(self, tmp_path):
        loader = TemplateLoader(tmp_path / "no_such_dir")
        assert loader._template_files() == []
        assert loader.list_builtin() == []

    def test_builtin_ids_fallback_when_config_unreadable(self, tmp_path,
                                                         monkeypatch):
        """配置不可读 → 退回目录扫描（含告警分支）。"""
        (tmp_path / "scan_me.yaml").write_text(
            "template:\n  id: scan_me\n  name: 扫描模板\n", encoding="utf-8")
        loader = TemplateLoader(tmp_path)
        monkeypatch.setattr(
            "core.character.template_loader.CONFIG_PATH",
            str(tmp_path / "no_config.yaml"))
        assert [meta.id for meta in loader.list_builtin()] == ["scan_me"]

    def test_name_pool_missing_file_returns_empty(self, tmp_path):
        from core.character.template_loader import load_name_pool

        assert load_name_pool("male", names_dir=tmp_path) == []

    def test_pool_missing_raises(self, tmp_path):
        from core.character.template_loader import load_pool

        with pytest.raises(FileNotFoundError):
            load_pool("no_such_pool", pools_dir=tmp_path)


class TestValidatorFaultTolerance:
    def test_occupation_rules_load_failure_does_not_crash(self, monkeypatch):
        """职业规则读取失败时按无规则处理（校验不崩）。"""
        import core.character.template_loader as loader_module

        def exploding_rules(pools_dir=None):
            raise OSError("boom")

        monkeypatch.setattr(loader_module, "load_occupation_rules",
                            exploding_rules)
        from core.character.profile import CharacterProfile

        profile = CharacterProfile.from_dict(
            {"id": "c", "name": "n", "age": 19,
             "background": {"occupation": "大学讲师"}})
        assert validate_consistency(profile) == []


class TestManagerEdgeCases:
    def test_delete_missing_raises(self, tmp_path):
        from core.character.generator import CharacterGenerator
        from core.character.manager import (
            CharacterManager,
            CharacterNotFoundError,
        )

        loader = TemplateLoader()
        manager = CharacterManager(make_db_session_factory(
            tmp_path / "d.db"), loader, CharacterGenerator(loader))
        with pytest.raises(CharacterNotFoundError):
            manager.delete("no-such")

    def test_physical_delete_removes_row(self, tmp_path):
        from core.character.generator import CharacterGenerator
        from core.character.manager import CharacterManager

        factory = make_db_session_factory(tmp_path / "d.db")
        loader = TemplateLoader()
        manager = CharacterManager(factory, loader, CharacterGenerator(loader))
        profile = manager.create_from_template("gentle_artist", "物理删")
        manager.delete(profile.id, archive=False)
        with factory() as db:
            from db.models import Character

            assert db.get(Character, profile.id) is None

    def test_import_card_without_profile_key_raises(self, tmp_path):
        from core.character.generator import CharacterGenerator
        from core.character.manager import (
            CharacterManager,
            CharacterValidationError,
        )

        loader = TemplateLoader()
        manager = CharacterManager(make_db_session_factory(
            tmp_path / "d.db"), loader, CharacterGenerator(loader))
        with pytest.raises(CharacterValidationError):
            manager.import_card({"no_name": True})


class TestRoute404Branches:
    @pytest.fixture
    def factory(self, tmp_path):
        return make_db_session_factory(tmp_path / "dudulu.db")

    @pytest.fixture
    def app(self, factory):
        return build_test_app(factory)

    @pytest.fixture
    def user_id(self, factory):
        return seed_user(factory)

    def test_chat_unknown_character_404(self, app, user_id, tmp_path):
        from helpers import CONFIG_PATH, FakeLLM
        from orchestrator.pipeline import create_pipeline

        factory = make_db_session_factory(tmp_path / "pipeline.db")
        seeded = seed_user(factory)  # 用户要种子在管道自己的库里
        pipeline = create_pipeline(
            FakeLLM(), CONFIG_PATH, factory,
            yantrik_path=str(tmp_path / "mem.db"), embedder="fake")
        app = build_test_app(factory, pipeline=pipeline)
        with TestClient(app) as client:
            response = client.post("/api/chat", json={
                "character_id": "ghost", "session_id": "s",
                "message": "hi"}, headers=auth_header(seeded))
        assert response.status_code == 404

    def test_state_unknown_character_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/state?character_id=ghost",
                                  headers=auth_header(user_id))
        assert response.status_code == 404

    def test_memory_search_unknown_character_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.get(
                "/api/memory/search?character_id=ghost&q=x",
                headers=auth_header(user_id))
        assert response.status_code == 404

    def test_template_export_unknown_character_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/template/export", json={
                "character_id": "ghost", "template_id": "t"},
                headers=auth_header(user_id))
        assert response.status_code == 404
