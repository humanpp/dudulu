"""角色路由单元测试（文档 2 第 6.3 节全部端点 + 6.4 节预览随机）。"""

import pytest
from fastapi.testclient import TestClient

from helpers import (
    auth_header,
    build_test_app,
    make_db_session_factory,
    seed_character,
    seed_user,
)

TEMPLATE_ID = "gentle_artist"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory)


@pytest.fixture
def user_id(factory):
    return seed_user(factory)


@pytest.fixture
def other_user_id(factory):
    return seed_user(factory, username="other", password="secret123")


def create_template_character(client, headers, name="小知", **kwargs):
    body = {"template_id": TEMPLATE_ID, "name": name, **kwargs}
    return client.post("/api/character/create/template", json=body,
                       headers=headers)


class TestListAndGet:
    def test_list_returns_created(self, app, user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            response = client.get("/api/character/list",
                                  headers=auth_header(user_id))
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()["characters"]]
        assert created["id"] in ids

    def test_get_detail(self, app, user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            response = client.get(
                f"/api/character/{created['id']}",
                headers=auth_header(user_id))
        assert response.status_code == 200
        assert response.json()["name"] == "小知"
        assert response.json()["theme"]["primary"] == "#C4A882"

    def test_get_others_private_returns_404(self, app, factory, user_id,
                                            other_user_id):
        """他人私有角色 404（隔离，不泄露存在性）。"""
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            response = client.get(f"/api/character/{created['id']}",
                                  headers=auth_header(other_user_id))
        assert response.status_code == 404

    def test_get_missing_returns_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.get("/api/character/no-such",
                                  headers=auth_header(user_id))
        assert response.status_code == 404

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.get("/api/character/list").status_code == 401


class TestCreateTemplate:
    def test_creates_with_owner(self, app, user_id):
        with TestClient(app) as client:
            response = create_template_character(client, auth_header(user_id))
        assert response.status_code == 200
        profile = response.json()
        assert profile["name"] == "小知"
        assert profile["visibility"]["owner_user_id"] == user_id
        assert profile["visibility"]["is_public"] is False

    def test_visibility_public(self, app, user_id, other_user_id):
        """公开角色对其他用户可见。"""
        with TestClient(app) as client:
            created = create_template_character(
                client, auth_header(user_id), visibility="public").json()
            response = client.get(f"/api/character/{created['id']}",
                                  headers=auth_header(other_user_id))
        assert response.status_code == 200

    def test_top_level_authoritative(self, app, user_id):
        """顶层 name/visibility 权威，overrides 同名键仅占位。"""
        with TestClient(app) as client:
            response = client.post("/api/character/create/template", json={
                "template_id": TEMPLATE_ID,
                "name": "权威名",
                "visibility": "public",
                "overrides": {"name": "占位名", "nickname": "昵称"},
            }, headers=auth_header(user_id))
        profile = response.json()
        assert profile["name"] == "权威名"
        assert profile["visibility"]["is_public"] is True
        assert profile["nickname"] == "昵称"

    def test_unknown_template_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/create/template", json={
                "template_id": "no_such", "name": "x"},
                headers=auth_header(user_id))
        assert response.status_code == 404

    def test_missing_name_422(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/create/template", json={
                "template_id": TEMPLATE_ID}, headers=auth_header(user_id))
        assert response.status_code == 422


class TestCreateRandom:
    def test_creates_with_seed(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/create/random", json={
                "template_id": TEMPLATE_ID, "seed": 7},
                headers=auth_header(user_id))
        assert response.status_code == 200
        profile = response.json()
        assert profile["generation_meta"]["random_seed"] == 7
        assert profile["generation_meta"]["source"] == "random"

    def test_locked_fields(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/create/random", json={
                "template_id": TEMPLATE_ID, "seed": 3,
                "locked_fields": ["gender"]},
                headers=auth_header(user_id))
        assert response.status_code == 200


class TestCreateManual:
    def test_creates(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/create/manual", json={
                "profile": {"name": "手建", "age": 24, "gender": "female"}},
                headers=auth_header(user_id))
        assert response.status_code == 200
        assert response.json()["name"] == "手建"

    def test_validation_error_400_with_issues(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/create/manual", json={
                "profile": {"name": "", "age": 19,
                            "background": {"occupation": "大学讲师"}}},
                headers=auth_header(user_id))
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["status"] == "error"
        assert detail["issues"]


class TestUpdateDeleteRestore:
    def test_update_and_versions(self, app, user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            response = client.put(f"/api/character/{created['id']}",
                                  json={"updates": {"nickname": "新昵称"}},
                                  headers=auth_header(user_id))
            assert response.status_code == 200
            assert response.json()["nickname"] == "新昵称"
            versions = client.get(
                f"/api/character/{created['id']}/versions",
                headers=auth_header(user_id)).json()["versions"]
            assert len(versions) == 1

    def test_others_update_403(self, app, user_id, other_user_id):
        """他人角色写操作 403。"""
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            response = client.put(f"/api/character/{created['id']}",
                                  json={"updates": {"nickname": "x"}},
                                  headers=auth_header(other_user_id))
        assert response.status_code == 403

    def test_delete_and_restore(self, app, user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            deleted = client.delete(f"/api/character/{created['id']}",
                                    headers=auth_header(user_id))
            assert deleted.json()["status"] == "archived"
            # 归档后 get 404
            assert client.get(
                f"/api/character/{created['id']}",
                headers=auth_header(user_id)).status_code == 404
            restored = client.post(
                f"/api/character/{created['id']}/restore",
                headers=auth_header(user_id))
            assert restored.status_code == 200
            assert restored.json()["name"] == "小知"

    def test_rollback(self, app, user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            client.put(f"/api/character/{created['id']}",
                       json={"updates": {"nickname": "第一版"}},
                       headers=auth_header(user_id))
            client.put(f"/api/character/{created['id']}",
                       json={"updates": {"nickname": "第二版"}},
                       headers=auth_header(user_id))
            versions = client.get(
                f"/api/character/{created['id']}/versions",
                headers=auth_header(user_id)).json()["versions"]
            first_version = versions[-1]["version_id"]
            rolled = client.post(
                f"/api/character/{created['id']}/rollback/{first_version}",
                headers=auth_header(user_id))
        assert rolled.status_code == 200
        assert rolled.json()["nickname"] == "第一版"


class TestSwitch:
    def test_switch_returns_state_and_session(self, app, user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            session = client.post("/api/session/create", json={
                "character_id": created["id"]},
                headers=auth_header(user_id)).json()
            response = client.post(
                f"/api/character/{created['id']}/switch",
                headers=auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert payload["character_id"] == created["id"]
        assert payload["profile"]["name"] == "小知"
        assert payload["active_session"]["session_id"] == session["session_id"]
        assert "expression" not in payload["state"]  # 无状态行为空 dict

    def test_switch_missing_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/no-such/switch",
                                   headers=auth_header(user_id))
        assert response.status_code == 404


class TestExportImport:
    def test_export_and_import_round_trip(self, app, user_id, other_user_id):
        with TestClient(app) as client:
            created = create_template_character(client,
                                                auth_header(user_id)).json()
            card = client.get(f"/api/character/{created['id']}/export",
                              headers=auth_header(user_id)).json()
            imported = client.post("/api/character/import", json=card,
                                   headers=auth_header(other_user_id)).json()
        assert imported["status"] == "ok"
        assert imported["profile"]["name"] == "小知"
        assert imported["profile"]["id"] != created["id"]
        assert imported["profile"]["visibility"]["owner_user_id"] == \
            other_user_id

    def test_import_invalid_returns_error_body(self, app, user_id):
        """校验失败：{status: error, issues: [...]} 且不落库（文档 2 第 6.3 节）。"""
        with TestClient(app) as client:
            response = client.post("/api/character/import", json={
                "profile": {"name": ""}}, headers=auth_header(user_id))
            assert response.status_code == 200
            assert response.json()["status"] == "error"
            assert response.json()["issues"]
            listed = client.get("/api/character/list",
                                headers=auth_header(user_id)).json()
            assert listed["characters"] == []


class TestPreviewEndpoints:
    def test_random_field(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/preview/random-field",
                                   json={"template_id": TEMPLATE_ID,
                                         "field": "age",
                                         "current_profile": {}},
                                   headers=auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert payload["field"] == "age"
        assert isinstance(payload["value"], int)

    def test_random_field_unsupported_422(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/preview/random-field",
                                   json={"template_id": TEMPLATE_ID,
                                         "field": "theme",
                                         "current_profile": {}},
                                   headers=auth_header(user_id))
        assert response.status_code == 422

    def test_random_all(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/character/preview/random-all",
                                   json={"template_id": TEMPLATE_ID,
                                         "locked_fields": ["name"],
                                         "current_profile":
                                             {"name": "保留名"}},
                                   headers=auth_header(user_id))
            assert response.status_code == 200
            profile = response.json()["profile"]
            assert profile["name"] == "保留名"
            # 不落库：预览角色不在角色列表中
            listed = client.get("/api/character/list",
                                headers=auth_header(user_id)).json()
            assert profile["id"] not in [item["id"] for item in
                                         listed["characters"]]

    def test_validate(self, app, user_id):
        with TestClient(app) as client:
            ok = client.post("/api/character/preview/validate",
                             json={"profile": {"id": "c", "name": "ok"}},
                             headers=auth_header(user_id)).json()
            bad = client.post("/api/character/preview/validate",
                              json={"profile": {"id": "c", "name": "",
                                                "age": 19,
                                                "background": {
                                                    "occupation":
                                                        "大学讲师"}}},
                              headers=auth_header(user_id)).json()
        assert ok == {"valid": True, "issues": []}
        assert bad["valid"] is False

    def test_archived_list_and_restore(self, app, user_id):
        """归档后能通过 ?archived=true 找回并恢复（2026-10-18 实测缺口）。"""
        with TestClient(app) as client:
            created = client.post("/api/character/create/template",
                                  json={"template_id": "gentle_artist",
                                        "name": "要归档的"},
                                  headers=auth_header(user_id)).json()
            character_id = created["id"]
            client.delete(f"/api/character/{character_id}",
                          headers=auth_header(user_id))

            # 正常列表里没有
            normal = client.get("/api/character/list",
                                headers=auth_header(user_id)).json()[
                "characters"]
            assert all(item["id"] != character_id for item in normal)

            # 归档列表里有
            archived = client.get("/api/character/list?archived=true",
                                  headers=auth_header(user_id)).json()[
                "characters"]
            assert [item["id"] for item in archived] == [character_id]
            assert archived[0]["name"] == "要归档的"

            # 恢复后回到正常列表
            response = client.post(f"/api/character/{character_id}/restore",
                                   headers=auth_header(user_id))
            assert response.status_code == 200
            normal = client.get("/api/character/list",
                                headers=auth_header(user_id)).json()[
                "characters"]
            assert any(item["id"] == character_id for item in normal)
            archived = client.get("/api/character/list?archived=true",
                                  headers=auth_header(user_id)).json()[
                "characters"]
            assert archived == []

    def test_archived_list_isolated_by_user(self, app, user_id, factory):
        """归档列表只含自己的角色（他人归档不可见）。"""
        other = seed_user(factory, username="other", password="secret123")
        with TestClient(app) as client:
            mine = client.post("/api/character/create/template",
                               json={"template_id": "gentle_artist",
                                     "name": "我的"},
                               headers=auth_header(user_id)).json()
            theirs = client.post("/api/character/create/template",
                                 json={"template_id": "gentle_artist",
                                       "name": "他的"},
                                 headers=auth_header(other)).json()
            client.delete(f"/api/character/{mine['id']}",
                          headers=auth_header(user_id))
            client.delete(f"/api/character/{theirs['id']}",
                          headers=auth_header(other))
            archived = client.get("/api/character/list?archived=true",
                                  headers=auth_header(user_id)).json()[
                "characters"]
        assert [item["id"] for item in archived] == [mine["id"]]

    def test_three_creation_paths(self, app, user_id):
        """三条创建路径（文档 1 第 13.3 节）：模板 / 随机 / 空白手填。

        前端向导的三个入口分别落在这三个端点上。
        """
        with TestClient(app) as client:
            # ① 模板
            from_template = client.post(
                "/api/character/create/template",
                json={"template_id": "gentle_artist", "name": "模板角色"},
                headers=auth_header(user_id)).json()
            # ② 随机（固定种子 → 可复现）
            random_one = client.post(
                "/api/character/create/random",
                json={"template_id": "energetic_coach", "seed": 42},
                headers=auth_header(user_id)).json()
            # ③ 空白手填：背景故事自由编写
            manual = client.post(
                "/api/character/create/manual",
                json={"profile": {
                    "name": "手写角色", "gender": "female", "age": 27,
                    "background": {"occupation": "独立开发者",
                                   "current_state": "刚辞职，正在做一个人的项目，"
                                                    "有点慌但也很期待。"},
                    "personality": {"traits": ["固执", "浪漫"]},
                }},
                headers=auth_header(user_id)).json()
        assert from_template["name"] == "模板角色"
        assert random_one["generation_meta"]["source"] == "random"
        assert random_one["generation_meta"]["random_seed"] == 42
        assert manual["background"]["occupation"] == "独立开发者"
        assert "有点慌" in manual["background"]["current_state"]
        assert manual["personality"]["traits"] == ["固执", "浪漫"]

    def test_validate_partial_profile_from_wizard(self, app, user_id):
        """向导实时校验的前端同款 payload（部分档案，无 id）→ 200 不 500。

        2026-10-17 实测事故：前端每敲一次键就调这个端点，500 刷屏。
        """
        with TestClient(app) as client:
            response = client.post("/api/character/preview/validate", json={
                "profile": {"name": "", "gender": "female", "age": 22,
                            "appearance": {"body_type": "匀称"},
                            "personality": {"big_five": {}},
                            "visibility": {"is_public": False}}},
                headers=auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert payload["valid"] is False  # 名字为空 → 拦住「下一步」
        assert any(issue["field"] == "name" for issue in payload["issues"])
