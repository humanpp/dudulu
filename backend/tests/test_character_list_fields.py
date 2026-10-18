"""角色列表视图字段（Phase 10 裁决⑦：后端是契约定义方）。

/character/list 除完整档案外，还提供角色卡（文档 3 第 6.3 节）需要的
扁平字段：occupation / stage_code / stage_label / affection。
"""

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


def create_character(client, user_id, template="gentle_artist",
                     name="列表角色"):
    response = client.post("/api/character/create/template",
                           json={"template_id": template, "name": name},
                           headers=auth_header(user_id))
    assert response.status_code == 200
    return response.json()["id"]


class TestListViewFields:
    def test_flat_fields_present(self, app, user_id):
        with TestClient(app) as client:
            create_character(client, user_id)
            characters = client.get("/api/character/list",
                                    headers=auth_header(user_id)).json()[
                "characters"]
        assert len(characters) == 1
        character = characters[0]
        # 文档 3 第 6.3 节角色卡字段
        for field in ("id", "name", "occupation", "stage_code",
                      "stage_label", "affection", "theme"):
            assert field in character, field
        assert character["occupation"]  # 来自 background.occupation
        assert character["stage_code"].startswith("S")
        assert character["stage_label"]

    def test_initial_affection_without_state(self, app, user_id):
        """无状态行：好感度走「角色关系初始值 → 配置初始值」回退链。

        gentle_artist 模板未设 relationship_defaults，故取配置的
        affective_layer.initial.AFF = 15（与 /api/state 首屏一致）。
        """
        with TestClient(app) as client:
            create_character(client, user_id, template="gentle_artist")
            characters = client.get("/api/character/list",
                                    headers=auth_header(user_id)).json()[
                "characters"]
        assert characters[0]["affection"] == 15

    def test_reflects_current_state(self, app, user_id, factory):
        """有状态行：好感度/阶段反映该用户的当前关系。"""
        import json

        from db.models import CharacterUserState

        with TestClient(app) as client:
            character_id = create_character(client, user_id)
            with factory() as db:
                db.add(CharacterUserState(
                    character_id=character_id, user_id=user_id,
                    affective_state=json.dumps(
                        {"AFF": 72, "TRS": 58, "ATT": 45})))
                db.commit()
            characters = client.get("/api/character/list",
                                    headers=auth_header(user_id)).json()[
                "characters"]
        assert characters[0]["affection"] == 72
        assert characters[0]["stage_code"] == "S3"

    def test_states_isolated_by_user(self, app, user_id, factory):
        """他人对该角色的状态不进我的列表（用户隔离）。"""
        import json

        from db.models import CharacterUserState

        other = seed_user(factory, username="other", password="secret123")
        with TestClient(app) as client:
            character_id = create_character(client, user_id)
            with factory() as db:
                db.add(CharacterUserState(
                    character_id=character_id, user_id=other,
                    affective_state=json.dumps({"AFF": 99, "TRS": 99,
                                                "ATT": 99})))
                db.commit()
            characters = client.get("/api/character/list",
                                    headers=auth_header(user_id)).json()[
                "characters"]
        assert characters[0]["affection"] == 15  # 我的初始值，不是 99
