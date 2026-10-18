"""记忆检索路由测试（文档 2 第 6.9 节；假嵌入器记忆库）。"""

import pytest
from fastapi.testclient import TestClient

from helpers import (
    auth_header,
    build_test_app,
    make_db_session_factory,
    make_memory_manager,
    seed_character,
    seed_user,
)
from memory.schemas import ConversationTurn

CHARACTER_ID = "char_001"
USER_ID = "user_001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def manager(tmp_path):
    return make_memory_manager(tmp_path)


@pytest.fixture
def app(factory, manager):
    return build_test_app(factory, memory_manager=manager)


@pytest.fixture
def user_id(factory):
    seed_character(factory)
    return seed_user(factory)


def seed_memories(manager, user_id: str):
    """给指定 user 种子两条记忆（双键 namespace 与认证用户一致）。"""
    manager.store_conversation(ConversationTurn(
        character_id=CHARACTER_ID, user_id=user_id, role="user",
        text="用户提到工作压力大，感觉快撑不住了",
        emotion_tag="vulnerable", intensity=0.72, valence=-0.4))
    manager.store_conversation(ConversationTurn(
        character_id=CHARACTER_ID, user_id=user_id, role="user",
        text="用户说今天天气不错，想出去散步",
        emotion_tag="playful", intensity=0.4, valence=0.6))


def search(client, headers, **params):
    query = {"character_id": CHARACTER_ID, "q": "工作", **params}
    return client.get("/api/memory/search", params=query, headers=headers)


class TestMemorySearch:
    def test_returns_documented_shape(self, app, manager, user_id):
        seed_memories(manager, user_id)
        with TestClient(app) as client:
            response = search(client, auth_header(user_id))
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"memories"}
        memory = payload["memories"][0]
        for field in ("id", "content", "type", "role", "is_proactive",
                      "emotion_tag", "intensity", "importance", "valence",
                      "timestamp"):
            assert field in memory

    def test_emotion_tag_filter(self, app, manager, user_id):
        seed_memories(manager, user_id)
        with TestClient(app) as client:
            memories = search(client, auth_header(user_id),
                              emotion_tag="playful").json()["memories"]
        assert memories
        assert all(item["emotion_tag"] == "playful" for item in memories)

    def test_time_range_param_accepted(self, app, manager, user_id):
        seed_memories(manager, user_id)
        with TestClient(app) as client:
            response = search(client, auth_header(user_id),
                              time_range="last_7_days")
        assert response.status_code == 200

    def test_limit_param(self, app, manager, user_id):
        seed_memories(manager, user_id)
        with TestClient(app) as client:
            memories = search(client, auth_header(user_id),
                              limit=1).json()["memories"]
        assert len(memories) == 1

    def test_requires_auth(self, app, user_id):
        with TestClient(app) as client:
            assert client.get("/api/memory/search",
                              params={"character_id": CHARACTER_ID,
                                      "q": "工作"}).status_code == 401

    def test_user_isolation(self, factory, manager):
        """他人记忆检索不到（双键隔离，文档 2 第 7.2 节）。"""
        seed_memories(manager, USER_ID)  # 记忆属于 user_001
        seed_character(factory)
        user_b = seed_user(factory, username="b", password="secret123")
        app = build_test_app(factory, memory_manager=manager)
        with TestClient(app) as client:
            response = search(client, auth_header(user_b))  # 以 user_b 检索
        assert response.status_code == 200
        assert response.json()["memories"] == []
