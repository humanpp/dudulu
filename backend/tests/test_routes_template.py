"""模板路由单元测试（文档 2 第 6.5 节全部端点）。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from helpers import auth_header, build_test_app, make_db_session_factory, seed_user


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory)


@pytest.fixture
def user_id(factory):
    return seed_user(factory)


class TestTemplateList:
    def test_returns_eight_builtin(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/template/list",
                                  headers=auth_header(user_id))
        assert response.status_code == 200
        templates = response.json()["templates"]
        assert len(templates) == 8
        assert templates[0]["id"] == "gentle_artist"
        for item in templates:
            assert set(item) == {"id", "name", "description", "icon", "tags",
                                 "theme", "defaults"}

    def test_items_carry_theme_and_defaults(self, app, user_id):
        """向导契约（文档 3 第 9.7 节）：列表项带 theme/defaults，
        选中模板后直接初始化档案，不用再打详情接口。"""
        with TestClient(app) as client:
            templates = client.get("/api/template/list",
                                   headers=auth_header(user_id)).json()[
                "templates"]
        artist = next(t for t in templates if t["id"] == "gentle_artist")
        assert set(artist["theme"]) == {"primary", "soft", "deep"}
        # 模板 defaults 的真实结构（age_range/gender/body_type/personality/
        # speech_style/emotion_overrides/background_hints）
        assert artist["defaults"]["age_range"] == [22, 28]
        assert artist["defaults"]["gender"] == "female"
        assert artist["defaults"]["personality"]["big_five"]["openness"] == 0.82
        assert artist["defaults"]["speech_style"]["catchphrases"]

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.get("/api/template/list").status_code == 401


class TestTemplateDetail:
    def test_returns_full_template(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/template/gentle_artist",
                                  headers=auth_header(user_id))
        assert response.status_code == 200
        template = response.json()
        assert template["id"] == "gentle_artist"
        assert template["theme"]["primary"] == "#C4A882"
        assert template["defaults"]["personality"]["big_five"]["openness"] == 0.82
        assert template["locked_fields"]

    def test_unknown_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/template/no_such",
                                  headers=auth_header(user_id))
        assert response.status_code == 404


class TestTemplateExport:
    def test_export_character_as_template(self, app, user_id, tmp_path):
        """导出写真实模板目录（依赖用默认 loader），用后必须清理避免污染。"""
        exported = Path(
            __import__("core.character.template_loader", fromlist=["x"])
            .DEFAULT_TEMPLATE_DIR) / "exported_tpl.yaml"
        try:
            with TestClient(app) as client:
                created = client.post("/api/character/create/template", json={
                    "template_id": "gentle_artist", "name": "模板源"},
                    headers=auth_header(user_id)).json()
                response = client.post("/api/template/export", json={
                    "character_id": created["id"],
                    "template_id": "exported_tpl"},
                    headers=auth_header(user_id))
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
                # 导出后出现在模板列表（社区模板）
                listed = client.get("/api/template/list",
                                    headers=auth_header(user_id)).json()
                assert any(item["id"] == "exported_tpl"
                           for item in listed["templates"])
        finally:
            exported.unlink(missing_ok=True)

    def test_missing_params_422(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/template/export", json={},
                                   headers=auth_header(user_id))
        assert response.status_code == 422


class TestNamesAndPools:
    def test_name_pool_by_gender(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/template/names/female",
                                  headers=auth_header(user_id))
        assert response.status_code == 200
        names = response.json()["names"]
        assert len(names) >= 50
        assert "林知夏" in names

    def test_pools_returns_all_five(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/template/pools",
                                  headers=auth_header(user_id))
        assert response.status_code == 200
        pools = response.json()["pools"]
        assert set(pools) == {"occupations", "experiences", "hairstyles",
                              "catchphrases", "rare_traits"}
        assert pools["hairstyles"]["female"]
