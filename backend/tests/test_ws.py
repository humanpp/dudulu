"""WebSocket 端点与管理器测试（文档 2 第 3.12 / 6.11 节）。"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from helpers import auth_header, build_test_app, make_db_session_factory, seed_user
from api.routes_ws import WebSocketManager


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory)


@pytest.fixture
def user_id(factory):
    return seed_user(factory)


class FakeWS:
    """记录 send_json 的假 WebSocket。"""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message):
        self.sent.append(message)

    async def close(self):
        pass


class TestAuthFlow:
    def test_auth_ok(self, app, user_id):
        token = auth_header(user_id)["Authorization"].split(" ")[1]
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.send_json({"type": "auth", "token": token})
                assert ws.receive_json() == {"type": "auth_ok"}

    def test_bad_token_rejected(self, app, user_id):
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.send_json({"type": "auth", "token": "not-a-jwt"})
                message = ws.receive_json()
                assert message["type"] == "auth_error"

    def test_non_auth_first_message_rejected(self, app, user_id):
        token = auth_header(user_id)["Authorization"].split(" ")[1]
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.send_json({"type": "hello", "token": token})
                assert ws.receive_json()["type"] == "auth_error"

    def test_expired_token_rejected(self, app, user_id):
        import jwt
        from datetime import datetime, timedelta, timezone

        from config.settings import get_settings

        settings = get_settings()
        payload = {"sub": user_id,
                   "exp": datetime.now(timezone.utc) - timedelta(hours=1)}
        expired = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.send_json({"type": "auth", "token": expired})
                assert ws.receive_json()["type"] == "auth_error"

    def test_disconnect_before_auth_is_quiet(self, app, user_id):
        """认证前断开（刷新/--reload 重启，关闭码 1012）不抛异常。

        2026-10-17 实测：receive_json 的 WebSocketDisconnect 不在
        认证阶段的 except 元组里，穿透到 ASGI 层刷 traceback。
        旧代码下本测试会因服务端异常而失败。
        """
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.close()  # 不发 auth 直接关
        # 能干净走到这里即通过（异常会从上下文管理器/门户冒出）

    def test_disconnect_right_after_auth_is_quiet(self, app, user_id):
        """认证成功后客户端立刻断开：auth_ok 发送失败也安静退出。"""
        token = auth_header(user_id)["Authorization"].split(" ")[1]
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.send_json({"type": "auth", "token": token})
                ws.close()

    def test_non_json_first_frame_rejected(self, app, user_id):
        """首帧不是 JSON：按认证失败处理并关闭，不崩。"""
        with TestClient(app) as client:
            with client.websocket_connect("/ws/events") as ws:
                ws.send_text("not-json")
                assert ws.receive_json()["type"] == "auth_error"


class TestManagerUnit:
    def test_connect_and_disconnect(self):
        manager = WebSocketManager()
        ws = FakeWS()
        manager.connect("u1", ws)
        assert manager.user_online("u1") is True
        manager.disconnect("u1", ws)
        assert manager.user_online("u1") is False
        assert "u1" not in manager.connections  # 空列表清理键

    def test_disconnect_unknown_user_is_noop(self):
        manager = WebSocketManager()
        manager.disconnect("nobody", FakeWS())  # 不抛异常即可

    def test_push_sends_to_all_connections(self):
        manager = WebSocketManager()
        first, second = FakeWS(), FakeWS()
        manager.connect("u1", first)
        manager.connect("u1", second)

        async def scenario():
            manager.push("u1", {"type": "state_update"})
            await asyncio.sleep(0)

        asyncio.run(scenario())
        assert first.sent == [{"type": "state_update"}]
        assert second.sent == [{"type": "state_update"}]

    def test_push_offline_user_is_noop(self):
        manager = WebSocketManager()
        manager.push("ghost", {"type": "state_update"})  # 不抛异常即可

    def test_push_without_running_loop_does_not_raise(self):
        """同步上下文（无事件循环）下 push 记日志后跳过，不崩调用方。"""
        manager = WebSocketManager()
        manager.connect("u1", FakeWS())
        manager.push("u1", {"type": "proactive_message"})  # 同步调用

    def test_push_isolated_by_user(self):
        manager = WebSocketManager()
        mine, theirs = FakeWS(), FakeWS()
        manager.connect("u1", mine)
        manager.connect("u2", theirs)

        async def scenario():
            manager.push("u1", {"type": "state_update"})
            await asyncio.sleep(0)

        asyncio.run(scenario())
        assert mine.sent and not theirs.sent

    def test_send_failure_does_not_break_others(self):
        manager = WebSocketManager()

        class BrokenWS(FakeWS):
            async def send_json(self, message):
                raise RuntimeError("连接已断")

        good, broken = FakeWS(), BrokenWS()
        manager.connect("u1", good)
        manager.connect("u1", broken)

        async def scenario():
            manager.push("u1", {"type": "state_update"})
            await asyncio.sleep(0)  # 让 create_task 的发送跑完

        asyncio.run(scenario())
        assert good.sent == [{"type": "state_update"}]


class _ScriptedLLM:
    """分类器返回 affectionate、生成返回固定文本的假 LLM。"""

    async def generate(self, prompt, **kwargs):
        if "事件分类器" in prompt:
            return '{"label": "affectionate", "intensity": 0.6, ' \
                   '"busy_signal": false}'
        return "听起来真棒。"

    async def generate_stream(self, prompt, **kwargs):
        yield await self.generate(prompt)


class TestEndToEndPush:
    """管道 11.5 步：对话轮次结束后向在线用户推送 state_update。

    注意：请求与 WS 必须用两个独立的 TestClient——同一个 client 在
    websocket_connect 上下文内发请求会互相阻塞（TestClient 的门户
    被 WS 占着）。WS 连接注册在进程级单例里，跨 client 可见。
    """

    def test_chat_turn_pushes_state_update(self, factory, tmp_path):
        from api.routes_ws import ws_manager
        from helpers import CONFIG_PATH, seed_character
        from orchestrator.pipeline import create_pipeline

        user_id = seed_user(factory)
        seed_character(factory)
        pipeline = create_pipeline(
            _ScriptedLLM(), CONFIG_PATH, factory,
            yantrik_path=str(tmp_path / "mem.db"), embedder="fake",
            ws_manager=ws_manager)
        app = build_test_app(factory, pipeline=pipeline)
        token = auth_header(user_id)["Authorization"].split(" ")[1]
        with TestClient(app) as ws_client:
            with ws_client.websocket_connect("/ws/events") as ws:
                ws.send_json({"type": "auth", "token": token})
                assert ws.receive_json() == {"type": "auth_ok"}
                with TestClient(app) as post_client:
                    response = post_client.post("/api/chat", json={
                        "character_id": "char_001", "session_id": "s",
                        "message": "今天很开心"},
                        headers=auth_header(user_id))
                assert response.status_code == 200
                message = ws.receive_json()
        assert message["type"] == "state_update"
        assert message["character_id"] == "char_001"
        assert "expression" in message and "internal" in message
        assert "affective" in message and "delta" in message
        # 裁决③：affective 带 stage_code / stage_label
        assert message["affective"]["stage_code"].startswith("S")
        assert message["affective"]["stage_label"]


class TestPushPayloadContract:
    def test_proactive_message_payload_shape(self):
        """推送载荷字段照文档 2 第 3.12 节（type/character_id/message/reason）。"""
        manager = WebSocketManager()
        ws = FakeWS()
        manager.connect("u1", ws)

        async def scenario():
            manager.push("u1", {
                "type": "proactive_message",
                "character_id": "char_001",
                "message": "在干嘛呢",
                "reason": "loneliness_high",
                "priority": "P1",
                "timestamp": "2026-10-15T12:00:00",
            })
            await asyncio.sleep(0)

        asyncio.run(scenario())
        payload = ws.sent[0]
        assert payload["type"] == "proactive_message"
        assert payload["character_id"] == "char_001"
        assert payload["message"] == "在干嘛呢"
        assert payload["reason"] == "loneliness_high"
