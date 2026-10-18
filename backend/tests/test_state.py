"""状态查询路由测试（文档 2 第 6.8 节）。"""

import pytest
from fastapi.testclient import TestClient

from helpers import (
    auth_header,
    build_test_app,
    make_db_session_factory,
    seed_character,
    seed_user,
)

CHARACTER_ID = "char_001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory)


@pytest.fixture
def user_id(factory):
    seed_character(factory)
    return seed_user(factory)


def fetch_state(client, headers, character_id=CHARACTER_ID):
    return client.get(f"/api/state?character_id={character_id}", headers=headers)


class TestGetState:
    def test_initial_state_shape(self, app, user_id):
        """无状态行 → 初始值；结构照 6.8 节（affective 带 stage 两字段）。"""
        with TestClient(app) as client:
            response = fetch_state(client, auth_header(user_id))
        assert response.status_code == 200
        state = response.json()
        assert set(state) == {"expression", "internal", "affective",
                              "last_updated"}
        assert state["expression"] == {
            "PLE": 0.15, "ARO": 0.0, "DOM": 0.0, "INT": 0.20, "SEC": 0.30}
        assert len(state["internal"]) == 12
        affective = state["affective"]
        assert affective["AFF"] == 15
        assert affective["TRS"] == 25
        assert affective["ATT"] == 10
        assert affective["stage_code"] == "S0"
        assert affective["stage_label"] == "陌生"
        assert state["last_updated"] is None

    def test_persisted_state_with_stage_fields(self, app, factory, user_id):
        """有状态行 → 读库值 + 阶段判定（stage_code/stage_label 分开给）。"""
        from db.models import CharacterUserState

        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=user_id,
                expression_state='{"PLE": 0.5, "ARO": 0.1, "DOM": 0.0, '
                                 '"INT": 0.6, "SEC": 0.5}',
                internal_state='{"ANX": 0.2}',
                affective_state='{"AFF": 72, "TRS": 58, "ATT": 45}'))
            db.commit()
        with TestClient(app) as client:
            state = fetch_state(client, auth_header(user_id)).json()
        assert state["expression"]["PLE"] == 0.5
        assert state["affective"]["AFF"] == 72
        assert state["affective"]["stage_code"] == "S3"
        assert state["affective"]["stage_label"] == "亲密"
        assert state["last_updated"] is not None

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.get(
                f"/api/state?character_id={CHARACTER_ID}").status_code == 401

    def test_user_isolation(self, factory):
        """他人状态不可见（返回初始值而非对方数据）。"""
        from db.models import CharacterUserState

        seed_character(factory)
        user_a = seed_user(factory, username="a", password="secret123")
        user_b = seed_user(factory, username="b", password="secret123")
        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=user_a,
                affective_state='{"AFF": 90, "TRS": 80, "ATT": 70}'))
            db.commit()
        app = build_test_app(factory)
        with TestClient(app) as client:
            state = fetch_state(client, auth_header(user_b)).json()
        assert state["affective"]["AFF"] == 15  # 初始值，非 90
