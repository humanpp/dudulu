"""对话路由单元测试（文档 2 第 6.6 节；假 Pipeline 或假 LLM，不打真实 API）。"""

import json

from fastapi.testclient import TestClient

from helpers import (
    CONFIG_PATH,
    FakeLLM,
    auth_header,
    build_test_app,
    make_db_session_factory,
    seed_character,
    seed_user,
    temp_db_factory,
)
from orchestrator.pipeline import PipelineResult, create_pipeline

BODY = {"character_id": "char_001", "session_id": "sess-001", "message": "今天好累啊"}

FAKE_STATE = {
    "expression": {"PLE": 0.11, "ARO": 0.04, "DOM": 0.02, "INT": 0.27, "SEC": 0.39},
    "internal": {"ANX": 0.08},
    "affective": {"AFF": 15, "TRS": 25, "ATT": 10,
                  "stage_code": "S0", "stage_label": "陌生"},
}


class FakePipeline:
    """脚本化管道：复现真实 Pipeline 的 SSE 事件契约。"""

    def __init__(self, reply="听见你了。", tokens=("听", "见", "你", "了。"),
                 error=None):
        self.reply = reply
        self.tokens = tokens
        self.error = error
        self.calls: list[tuple] = []

    async def process(self, message, character_id, user_id, session_id):
        self.calls.append(("process", message, character_id, user_id, session_id))
        return PipelineResult(reply=self.reply, state=FAKE_STATE, memories=[])

    async def process_stream(self, message, character_id, user_id, session_id):
        self.calls.append(("stream", message, character_id, user_id, session_id))
        full_reply = ""
        try:
            for index, token in enumerate(self.tokens):
                if self.error and index >= 1:
                    raise self.error
                full_reply += token
                yield {"event": "token", "data": token}
        except Exception as exc:
            yield {"event": "error",
                   "data": json.dumps({"message": str(exc),
                                       "partial": full_reply},
                                      ensure_ascii=False)}
            return
        yield {"event": "done",
               "data": json.dumps({"state": FAKE_STATE, "memories_used": []},
                                  ensure_ascii=False)}


def parse_sse(text: str) -> list[tuple[str, str]]:
    """按 SSE 规范解析：事件块以空行分隔，多行 data 用 \\n 连接。"""
    events: list[tuple[str, str]] = []
    for block in text.strip().split("\n\n"):
        event_name = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        if event_name is not None:
            events.append((event_name, "\n".join(data_lines)))
    return events


def make_authenticated_app(tmp_path, pipeline=None):
    """临时库 + 已建用户 + 令牌头；pipeline 非空时覆盖管道依赖。"""
    factory = make_db_session_factory(tmp_path / "dudulu.db")
    user_id = seed_user(factory)
    app = build_test_app(factory, pipeline=pipeline)
    return app, user_id, auth_header(user_id)


class TestAuthRequired:
    def test_chat_requires_token(self, tmp_path):
        app, _, _ = make_authenticated_app(tmp_path, pipeline=FakePipeline())
        with TestClient(app) as client:
            response = client.post("/api/chat", json=BODY)
        assert response.status_code == 401

    def test_stream_requires_token(self, tmp_path):
        app, _, _ = make_authenticated_app(tmp_path, pipeline=FakePipeline())
        with TestClient(app) as client:
            response = client.post("/api/chat/stream", json=BODY)
        assert response.status_code == 401

    def test_invalid_token_rejected(self, tmp_path):
        app, _, _ = make_authenticated_app(tmp_path, pipeline=FakePipeline())
        with TestClient(app) as client:
            response = client.post(
                "/api/chat", json=BODY,
                headers={"Authorization": "Bearer not-a-token"})
        assert response.status_code == 401


class TestChatNonStream:
    def test_returns_reply_state_and_memories(self, tmp_path):
        app, _, headers = make_authenticated_app(
            tmp_path, pipeline=FakePipeline(reply="辛苦啦。"))
        with TestClient(app) as client:
            response = client.post("/api/chat", json=BODY, headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"reply", "state", "memories_used"}
        assert payload["reply"] == "辛苦啦。"
        assert payload["state"]["affective"]["stage_code"] == "S0"
        assert payload["memories_used"] == []

    def test_user_id_injected_from_jwt(self, tmp_path):
        pipeline = FakePipeline()
        app, user_id, headers = make_authenticated_app(tmp_path, pipeline=pipeline)
        with TestClient(app) as client:
            client.post("/api/chat", json=BODY, headers=headers)
        method, message, character_id, injected_user_id, session_id = \
            pipeline.calls[0]
        assert injected_user_id == user_id
        assert method == "process"
        assert message == "今天好累啊"
        assert character_id == "char_001"
        assert session_id == "sess-001"

    def test_missing_field_returns_422(self, tmp_path):
        app, _, headers = make_authenticated_app(
            tmp_path, pipeline=FakePipeline())
        with TestClient(app) as client:
            response = client.post("/api/chat",
                                   json={"character_id": "char_001"},
                                   headers=headers)
        assert response.status_code == 422


class TestChatStream:
    def test_tokens_then_done(self, tmp_path):
        app, _, headers = make_authenticated_app(
            tmp_path, pipeline=FakePipeline(tokens=("听", "起来", "今天")))
        with TestClient(app) as client:
            response = client.post("/api/chat/stream", json=BODY, headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(response.text)
        assert [name for name, _ in events] == ["token", "token", "token", "done"]
        assert "".join(data for name, data in events if name == "token") == "听起来今天"
        payload = json.loads(events[-1][1])
        assert payload["state"]["affective"]["stage_code"] == "S0"
        assert payload["state"]["affective"]["stage_label"] == "陌生"

    def test_error_event_with_partial(self, tmp_path):
        app, _, headers = make_authenticated_app(
            tmp_path,
            pipeline=FakePipeline(tokens=("听", "起来"),
                                  error=RuntimeError("upstream timeout")))
        with TestClient(app) as client:
            response = client.post("/api/chat/stream", json=BODY, headers=headers)
        events = parse_sse(response.text)
        assert [name for name, _ in events] == ["token", "error"]
        payload = json.loads(events[-1][1])
        assert payload["message"] == "upstream timeout"
        assert payload["partial"] == "听"

    def test_missing_field_returns_422(self, tmp_path):
        app, _, headers = make_authenticated_app(
            tmp_path, pipeline=FakePipeline())
        with TestClient(app) as client:
            response = client.post("/api/chat/stream",
                                   json={"session_id": "s"}, headers=headers)
        assert response.status_code == 422


class TestChatEndToEndWithRealPipeline:
    """真实管道 + 临时 SQLite + 临时 YantrikDB（假嵌入器）+ 假 LLM。"""

    def _pipeline(self, tmp_path, fake_llm):
        factory = make_db_session_factory(tmp_path / "dudulu.db")
        seed_character(factory)  # Phase 6 起管道要求角色真实存在
        pipeline = create_pipeline(fake_llm, CONFIG_PATH, factory,
                                   yantrik_path=str(tmp_path / "mem.db"),
                                   embedder="fake")
        return factory, pipeline

    def test_stream_persists_state_and_session(self, tmp_path):
        factory, pipeline = self._pipeline(tmp_path, FakeLLM(tokens=("嗯", "我在")))
        user_id = seed_user(factory)
        app = build_test_app(factory, pipeline=pipeline)
        with TestClient(app) as client:
            response = client.post("/api/chat/stream", json=BODY,
                                   headers=auth_header(user_id))
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert [name for name, _ in events] == ["token", "token", "done"]
        done = json.loads(events[-1][1])
        assert done["state"]["affective"]["stage_code"] == "S0"

        # 状态落库：character_user_state 一行，stage 字段已剔除
        from sqlalchemy import select

        from db.models import CharacterUserState, Message, StateChange

        with factory() as db:
            row = db.get(CharacterUserState, ("char_001", user_id))
            assert row is not None
            affective = json.loads(row.affective_state)
            assert set(affective) == {"AFF", "TRS", "ATT"}
            assert row.relationship_stage == "S0"
            assert row.last_interaction is not None
            # state_changes 落一行
            changes = db.execute(select(StateChange).where(
                StateChange.character_id == "char_001",
                StateChange.user_id == user_id)).scalars().all()
            assert len(changes) == 1
            assert changes[0].event_label == "affectionate"
            # messages 索引两行，memory_id 关联 YantrikDB
            rows = db.execute(select(Message)).scalars().all()
            assert len(rows) == 2
            assert all(row.memory_id for row in rows)

    def test_second_turn_loads_persisted_state(self, tmp_path):
        """第二轮对话能读到第一轮落库的状态（affectionate 使 AFF 上升后保持）。"""
        factory, pipeline = self._pipeline(tmp_path, FakeLLM(
            classifier_event={
                "label": "affectionate", "intensity": 1.0,
                "busy_signal": False}))
        user_id = seed_user(factory)
        app = build_test_app(factory, pipeline=pipeline)
        with TestClient(app) as client:
            first = client.post("/api/chat", json=BODY,
                                headers=auth_header(user_id)).json()
            second = client.post("/api/chat", json=BODY,
                                 headers=auth_header(user_id)).json()
        assert first["state"]["affective"]["AFF"] > 15
        # 第二轮从落库状态起步（无衰减），AFF 不低于第一轮结束值
        assert second["state"]["affective"]["AFF"] >= first["state"]["affective"]["AFF"]

    def test_recent_turns_feed_second_prompt(self, tmp_path):
        """第二轮的 [RECENT TURNS] 含第一轮的 YantrikDB 正文。"""
        factory, pipeline = self._pipeline(tmp_path, FakeLLM())
        user_id = seed_user(factory)
        app = build_test_app(factory, pipeline=pipeline)
        with TestClient(app) as client:
            client.post("/api/chat", json=BODY, headers=auth_header(user_id))
            client.post("/api/chat", json={**BODY, "message": "第二轮啦"},
                        headers=auth_header(user_id))
        # 分类器 prompt 列表：[分类, 生成, 分类, 生成]；第二个生成 prompt
        # 应包含第一轮用户消息（来自 YantrikDB 正文回读）
        assert BODY["message"] in pipeline.classifier.llm_client.prompts[3]
        assert "user: 今天好累啊" in pipeline.classifier.llm_client.prompts[3]


class TestGetLlmClient:
    def test_builds_from_settings(self, tmp_path):
        from config.settings import Settings

        from api.routes_chat import get_llm_client
        from db.models import User
        from helpers import make_db_session_factory, seed_user
        from llm.base import LLMClient

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        user_id = seed_user(factory)
        with factory() as db:
            user = db.get(User, user_id)
            client = get_llm_client(
                Settings(_env_file=None, LLM_API_KEY="sk-test"), db, user)
        assert isinstance(client, LLMClient)

    def test_config_override_wins(self, tmp_path):
        """config 表的用户覆盖优先于 Settings（设置页 PUT 即时生效）。"""
        from config.settings import Settings
        from db.config_store import LLM_CONFIG_KEY, save_config

        from api.routes_chat import get_llm_client
        from db.models import User
        from helpers import make_db_session_factory, seed_user
        from llm.api_client import APIClient

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        user_id = seed_user(factory)
        with factory() as db:
            save_config(db, LLM_CONFIG_KEY, {
                "mode": "api",
                "api": {"provider": "override-provider",
                        "model": "override-model",
                        "api_key": "sk-override"}}, user_id=user_id)
            db.commit()
            user = db.get(User, user_id)
            client = get_llm_client(
                Settings(_env_file=None, LLM_API_KEY="sk-test"), db, user)
        assert isinstance(client, APIClient)
        assert client.model == "override-model"
        assert client.provider == "override-provider"

    def test_other_users_override_not_leaked(self, tmp_path):
        """他人的用户覆盖不进我的客户端（多用户隔离）。"""
        from config.settings import Settings
        from db.config_store import LLM_CONFIG_KEY, save_config

        from api.routes_chat import get_llm_client
        from db.models import User
        from helpers import make_db_session_factory, seed_user
        from llm.api_client import APIClient

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        mine = seed_user(factory, username="mine")
        theirs = seed_user(factory, username="theirs")
        with factory() as db:
            save_config(db, LLM_CONFIG_KEY,
                        {"api": {"model": "their-model"}}, user_id=theirs)
            db.commit()
            user = db.get(User, mine)
            client = get_llm_client(
                Settings(_env_file=None, LLM_API_KEY="sk-test"), db, user)
        assert isinstance(client, APIClient)
        assert client.model == "gpt-4o"  # Settings 默认，未读到他人覆盖


class TestGetPipeline:
    def test_builds_pipeline_from_dependencies(self, tmp_path):
        """get_pipeline 直调：LLM 客户端 + Settings + 会话工厂 → 可运行管道。"""
        from config.settings import Settings

        from api.routes_chat import get_pipeline

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        pipeline = get_pipeline(
            FakeLLM(),
            Settings(_env_file=None, LLM_API_KEY="sk-test",
                     EMOTION_CONFIG_PATH=CONFIG_PATH,
                     YANTRIK_PATH=str(tmp_path / "mem.db"),
                     YANTRIK_EMBEDDER="fake"),
            factory)
        assert pipeline.emotion_config_path == CONFIG_PATH
        assert pipeline.db_session_factory is factory
        # Phase 8b：ws_manager 为 api.routes_ws 的进程级单例（11.5 步推送目标）
        from api.routes_ws import ws_manager

        assert pipeline.ws_manager is ws_manager
