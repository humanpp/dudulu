"""会话管理单元测试（文档 2 第 6.2 节）。"""

import pytest
from fastapi.testclient import TestClient

from helpers import auth_header, build_test_app, make_db_session_factory, seed_user

CHARACTER_ID = "char_001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory)


@pytest.fixture
def user_id(factory):
    return seed_user(factory)


def create_session(client, headers, character_id=CHARACTER_ID):
    return client.post("/api/session/create",
                       json={"character_id": character_id}, headers=headers)


class TestCreateSession:
    def test_create_returns_contract_shape(self, app, user_id):
        with TestClient(app) as client:
            response = create_session(client, auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"session_id", "character_id", "started_at"}
        assert payload["character_id"] == CHARACTER_ID
        assert payload["session_id"]
        assert payload["started_at"]

    def test_reuses_existing_active_session(self, app, user_id):
        """同一角色只允许一个活跃会话：第二次 create 返回现有会话。"""
        with TestClient(app) as client:
            first = create_session(client, auth_header(user_id)).json()
            second = create_session(client, auth_header(user_id)).json()
        assert first["session_id"] == second["session_id"]

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.post("/api/session/create",
                               json={"character_id": CHARACTER_ID}
                               ).status_code == 401


class TestActiveSession:
    def test_returns_active_session(self, app, user_id):
        with TestClient(app) as client:
            created = create_session(client, auth_header(user_id)).json()
            response = client.get(
                f"/api/session/active?character_id={CHARACTER_ID}",
                headers=auth_header(user_id))
        assert response.json() == {"session_id": created["session_id"]}

    def test_returns_null_when_none(self, app, user_id):
        with TestClient(app) as client:
            response = client.get(
                f"/api/session/active?character_id={CHARACTER_ID}",
                headers=auth_header(user_id))
        assert response.json() == {"session_id": None}

    def test_null_after_end(self, app, user_id):
        with TestClient(app) as client:
            created = create_session(client, auth_header(user_id)).json()
            client.post(f"/api/session/{created['session_id']}/end",
                        headers=auth_header(user_id))
            response = client.get(
                f"/api/session/active?character_id={CHARACTER_ID}",
                headers=auth_header(user_id))
        assert response.json() == {"session_id": None}

    def test_new_session_after_end(self, app, user_id):
        """结束后的下一次 create 生成新会话。"""
        with TestClient(app) as client:
            first = create_session(client, auth_header(user_id)).json()
            client.post(f"/api/session/{first['session_id']}/end",
                        headers=auth_header(user_id))
            second = create_session(client, auth_header(user_id)).json()
        assert second["session_id"] != first["session_id"]


class TestEndSession:
    def test_end_returns_status_and_turn_count(self, app, user_id):
        with TestClient(app) as client:
            created = create_session(client, auth_header(user_id)).json()
            response = client.post(
                f"/api/session/{created['session_id']}/end",
                headers=auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"status", "turn_count"}
        assert payload["status"] == "ended"
        assert isinstance(payload["turn_count"], int)

    def test_unknown_session_404(self, app, user_id):
        with TestClient(app) as client:
            response = client.post("/api/session/no-such-id/end",
                                   headers=auth_header(user_id))
        assert response.status_code == 404

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.post("/api/session/x/end").status_code == 401


class TestUserIsolation:
    def test_other_users_sessions_invisible(self, factory):
        """用户隔离：他人会话不出现在 active / end 里（文档 2 第 7.2 节）。"""
        user_a = seed_user(factory, username="a", password="secret123")
        user_b = seed_user(factory, username="b", password="secret123")
        app = build_test_app(factory)
        with TestClient(app) as client:
            session_a = create_session(client, auth_header(user_a)).json()
            # B 看不到 A 的活跃会话
            active_b = client.get(
                f"/api/session/active?character_id={CHARACTER_ID}",
                headers=auth_header(user_b)).json()
            assert active_b["session_id"] is None
            # B 结束 A 的会话 → 404（不泄露存在性）
            end_b = client.post(
                f"/api/session/{session_a['session_id']}/end",
                headers=auth_header(user_b))
            assert end_b.status_code == 404
            # A 的会话仍然活跃
            active_a = client.get(
                f"/api/session/active?character_id={CHARACTER_ID}",
                headers=auth_header(user_a)).json()
            assert active_a["session_id"] == session_a["session_id"]
