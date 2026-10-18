"""最近消息单元测试（文档 2 第 6.7 节；Phase 5 起正文按 memory_id 回读）。"""

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
OTHER_CHARACTER = "char_002"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def manager(tmp_path):
    return make_memory_manager(tmp_path)


@pytest.fixture
def app(factory, manager):
    """带假嵌入器记忆库的应用（避免连真实 data/yantrik 库）。"""
    return build_test_app(factory, memory_manager=manager)


@pytest.fixture
def user_id(factory):
    seed_character(factory)
    return seed_user(factory)


def seed_messages(factory, user_id: str, character_id: str, count: int,
                  session_id: str = "sess-001") -> list[str]:
    """直接插 count 条消息行（无 memory_id），返回 id 列表（按插入顺序）。"""
    from db.models import Message
    from memory.schemas import generate_id

    ids = []
    with factory() as db:
        for index in range(count):
            message_id = generate_id()
            db.add(Message(id=message_id, session_id=session_id,
                           character_id=character_id, user_id=user_id,
                           role="user" if index % 2 == 0 else "assistant"))
            ids.append(message_id)
        db.commit()
    return ids


def seed_messages_with_memory(manager, factory, user_id: str, count: int,
                              session_id: str = "sess-001") -> None:
    """经 MemoryManager 存储 count 条消息（带 memory_id，正文在 YantrikDB）。"""
    for index in range(count):
        manager.store_conversation(ConversationTurn(
            character_id=CHARACTER_ID, user_id=user_id,
            role="user" if index % 2 == 0 else "assistant",
            text=f"第 {index + 1} 条消息内容"), session_id=session_id)


def fetch(client, headers, character_id=CHARACTER_ID, limit=None):
    url = f"/api/messages/recent?character_id={character_id}"
    if limit is not None:
        url += f"&limit={limit}"
    return client.get(url, headers=headers)


class TestRecentMessages:
    def test_shape_and_order(self, app, factory, user_id):
        """正序返回（最早在前）；字段符合 6.7 节响应结构。"""
        seed_messages(factory, user_id, CHARACTER_ID, count=3)
        with TestClient(app) as client:
            response = fetch(client, auth_header(user_id))
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert len(messages) == 3
        for message in messages:
            assert set(message) == {"id", "session_id", "role", "content",
                                    "is_proactive", "created_at"}

    def test_content_read_back_from_memory(self, factory, manager, user_id):
        """Phase 5：正文按 memory_id 从 YantrikDB 回读。"""
        seed_messages_with_memory(manager, factory, user_id, count=2)
        app = build_test_app(factory, memory_manager=manager)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id)).json()["messages"]
        assert [message["content"] for message in messages] == [
            "第 1 条消息内容", "第 2 条消息内容"]
        assert all(message["is_proactive"] is False for message in messages)

    def test_proactive_flag_read_back(self, factory, manager, user_id):
        """主动消息的 is_proactive 从 YantrikDB 回读为 True。"""
        manager.store_conversation(ConversationTurn(
            character_id=CHARACTER_ID, user_id=user_id, role="assistant",
            text="在忙吗？突然想起你。", is_proactive=True,
            trigger_reason="loneliness_high"), session_id="sess-001")
        app = build_test_app(factory, memory_manager=manager)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id)).json()["messages"]
        assert messages[0]["is_proactive"] is True
        assert messages[0]["content"] == "在忙吗？突然想起你。"

    def test_missing_memory_keeps_null_content(self, app, factory, user_id):
        """索引行有但记忆缺失（极端情况）：content=None，不阻塞返回。"""
        seed_messages(factory, user_id, CHARACTER_ID, count=1)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id)).json()["messages"]
        assert messages[0]["content"] is None
        assert messages[0]["is_proactive"] is False

    def test_limit_respected(self, app, factory, user_id):
        seed_messages(factory, user_id, CHARACTER_ID, count=5)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id), limit=2).json()["messages"]
        assert len(messages) == 2

    def test_limit_default_20(self, app, factory, user_id):
        seed_messages(factory, user_id, CHARACTER_ID, count=25)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id)).json()["messages"]
        assert len(messages) == 20

    def test_limit_capped_at_100(self, app, factory, user_id):
        seed_messages(factory, user_id, CHARACTER_ID, count=3)
        with TestClient(app) as client:
            response = fetch(client, auth_header(user_id), limit=500)
        assert response.status_code == 200
        assert len(response.json()["messages"]) == 3

    def test_empty_when_no_messages(self, app, user_id):
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id)).json()["messages"]
        assert messages == []

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            assert client.get(
                f"/api/messages/recent?character_id={CHARACTER_ID}"
            ).status_code == 401


class TestIsolation:
    def test_other_character_invisible(self, app, factory, user_id):
        seed_messages(factory, user_id, OTHER_CHARACTER, count=2)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_id)).json()["messages"]
        assert messages == []

    def test_other_user_invisible(self, factory):
        """用户隔离：他人消息检索不到（文档 2 第 7.2 节）。"""
        user_a = seed_user(factory, username="a", password="secret123")
        user_b = seed_user(factory, username="b", password="secret123")
        seed_messages(factory, user_a, CHARACTER_ID, count=3)
        app = build_test_app(factory)
        with TestClient(app) as client:
            messages = fetch(client, auth_header(user_b)).json()["messages"]
        assert messages == []
