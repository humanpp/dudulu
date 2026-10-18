"""管道 SQLite 持久化测试（Phase 4 替换清单：状态读写 / 衰减 / busy_signal /
state_changes / turn_count / valence）。协作者用假实现，不打真实 API。"""

import asyncio
import json
from datetime import timedelta

import pytest

from helpers import (
    CONFIG_PATH,
    PROMPT_PATH,
    FakeCharacterManager,
    FakeLLM,
    SpyMemoryManager,
    SpySedimenter,
    make_db_session_factory,
    make_profile,
)
from orchestrator.classifier import Classifier
from orchestrator.pipeline import Pipeline
from orchestrator.post_processor import PostProcessor
from orchestrator.prompt_assembler import PromptAssembler

CHARACTER_ID = "char_001"
USER_ID = "user_001"
SESSION_ID = "sess-001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


def build_pipeline(factory, fake_llm=None) -> Pipeline:
    """真实分类器/装配器 + 假角色管理/记忆/沉淀 + 临时 SQLite。"""
    return Pipeline(
        character_manager=FakeCharacterManager(make_profile()),
        memory_manager=SpyMemoryManager(),
        classifier=Classifier(fake_llm or FakeLLM(), prompt_path=PROMPT_PATH),
        prompt_assembler=PromptAssembler(),
        llm_client=fake_llm or FakeLLM(),
        sedimenter=SpySedimenter(),
        proactive_state=_RecordingProactiveState(),
        post_processor=PostProcessor(),
        emotion_config_path=CONFIG_PATH,
        db_session_factory=factory,
    )


class _RecordingProactiveState:
    """管道协作者 spy：记录 busy_until 读写。"""

    def __init__(self):
        self.cleared: list[tuple] = []
        self.busy: list[tuple] = []
        self.replies: list[tuple] = []

    def clear_busy_until(self, character_id, user_id):
        self.cleared.append((character_id, user_id))

    def set_busy_until(self, character_id, user_id, hours):
        self.busy.append((character_id, user_id, hours))

    def register_user_reply(self, character_id, user_id):
        self.replies.append((character_id, user_id))


def run(pipeline: Pipeline, message: str = "今天好累啊"):
    return asyncio.run(pipeline.process(message, CHARACTER_ID, USER_ID,
                                        SESSION_ID))


def state_row(factory):
    from db.models import CharacterUserState

    with factory() as db:
        return db.get(CharacterUserState, (CHARACTER_ID, USER_ID))


class TestPersistAndLoad:
    def test_first_turn_creates_state_row(self, factory):
        run(build_pipeline(factory))
        row = state_row(factory)
        assert row is not None
        expression = json.loads(row.expression_state)
        assert set(expression) == {"PLE", "ARO", "DOM", "INT", "SEC"}
        assert row.relationship_stage == "S0"
        assert row.last_interaction is not None

    def test_stage_fields_stripped_from_affective_json(self, factory):
        """入库前剔除 stage_code / stage_label（只存 AFF/TRS/ATT）。"""
        run(build_pipeline(factory))
        affective = json.loads(state_row(factory).affective_state)
        assert set(affective) == {"AFF", "TRS", "ATT"}

    def test_second_turn_loads_persisted_state(self, factory):
        """状态跨轮延续：affectionate 强事件推高 AFF 后，第二轮仍读得到。"""
        strong = FakeLLM(classifier_event={
            "label": "affectionate", "intensity": 1.0, "busy_signal": False})
        first = run(build_pipeline(factory, strong))
        assert first.state["affective"]["AFF"] > 15
        second = run(build_pipeline(factory, strong))
        assert second.state["affective"]["AFF"] >= first.state["affective"]["AFF"]

    def test_hours_since_last_zero_without_history(self, factory):
        """无历史行 → 0.0（不衰减）。"""
        pipeline = build_pipeline(factory)
        assert pipeline._hours_since_last(CHARACTER_ID, USER_ID) == 0.0

    def test_hours_since_last_from_last_interaction(self, factory):
        """有 last_interaction → 按小时差衰减。"""
        from db.models import CharacterUserState, utc_now

        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                last_interaction=utc_now() - timedelta(hours=2)))
            db.commit()
        pipeline = build_pipeline(factory)
        hours = pipeline._hours_since_last(CHARACTER_ID, USER_ID)
        assert 1.9 < hours < 2.1

    def test_decay_applied_before_persist(self, factory):
        """第 4 步衰减真实生效：偏离基线的状态在长间隔后回归。"""
        from db.models import CharacterUserState, utc_now

        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                expression_state=json.dumps(
                    {"PLE": 0.9, "ARO": 0.0, "DOM": 0.0, "INT": 0.2, "SEC": 0.3}),
                last_interaction=utc_now() - timedelta(hours=48)))
            db.commit()
        result = run(build_pipeline(factory, FakeLLM(classifier_event={
            "label": "neutral", "intensity": 0.1, "busy_signal": False})))
        assert result.state["expression"]["PLE"] < 0.5


class TestBusySignalPersistence:
    def test_real_store_writes_busy_until(self, factory):
        """5.5 步经真实 ProactiveStateStore 落 proactive_state 表。"""
        from db.models import ProactiveState
        from db.proactive_state import ProactiveStateStore

        store = ProactiveStateStore(factory)
        pipeline = build_pipeline(factory, FakeLLM(classifier_event={
            "label": "withdrawn", "intensity": 0.5, "busy_signal": True}))
        pipeline.proactive_state = store
        run(pipeline)
        with factory() as db:
            row = db.get(ProactiveState, (CHARACTER_ID, USER_ID))
        assert row is not None
        assert row.busy_until is not None

    def test_clear_then_set_semantics(self, factory):
        """先清空（重新激活）再写 24h：最终 busy_until 非空且在将来。"""
        from db.models import ProactiveState, utc_now
        from db.proactive_state import ProactiveStateStore

        store = ProactiveStateStore(factory)
        store.set_busy_until(CHARACTER_ID, USER_ID, hours=24)   # 预置一个旧值
        pipeline = build_pipeline(factory, FakeLLM(classifier_event={
            "label": "neutral", "intensity": 0.1, "busy_signal": True}))
        pipeline.proactive_state = store
        run(pipeline)
        with factory() as db:
            row = db.get(ProactiveState, (CHARACTER_ID, USER_ID))
        assert row.busy_until > utc_now()

    def test_clear_without_row_is_noop(self, factory):
        from db.models import ProactiveState
        from db.proactive_state import ProactiveStateStore

        ProactiveStateStore(factory).clear_busy_until(CHARACTER_ID, USER_ID)
        with factory() as db:
            assert db.get(ProactiveState, (CHARACTER_ID, USER_ID)) is None


class TestStateChangesLog:
    def test_one_row_per_turn(self, factory):
        pipeline = build_pipeline(factory)
        run(pipeline)
        run(pipeline)
        from db.models import StateChange

        with factory() as db:
            rows = db.query(StateChange).filter_by(
                character_id=CHARACTER_ID, user_id=USER_ID).all()
        assert len(rows) == 2
        assert rows[0].event_label == "affectionate"
        assert rows[0].event_intensity == 0.6
        assert json.loads(rows[0].delta_json)["expression"]
        assert json.loads(rows[0].state_after_json)["affective"]["AFF"] == 15

    def test_last_event_valence_feed_decay(self, factory):
        """上一轮事件的 valence 进入本轮衰减（裁决⑤）；无历史时返回 None。"""
        pipeline = build_pipeline(factory)
        assert pipeline._last_event_valence(CHARACTER_ID, USER_ID) is None
        run(pipeline)  # affectionate → valence +1.0
        assert pipeline._last_event_valence(CHARACTER_ID, USER_ID) == 1.0

    def test_valence_mapping_table(self):
        """裁决⑤临时映射表：正向 +1 / 中性 0 / 负向 -1。"""
        from core.emotion_engine import EVENT_VALENCE

        assert EVENT_VALENCE["affectionate"] == 1.0
        assert EVENT_VALENCE["reassuring"] == 1.0
        assert EVENT_VALENCE["neutral"] == 0.0
        assert EVENT_VALENCE["hostile"] == -1.0
        assert EVENT_VALENCE["withdrawn"] == -1.0


class TestSessionTurnCount:
    def test_turn_count_incremented(self, factory):
        from db.models import Session
        from memory.schemas import generate_id

        with factory() as db:
            db.add(Session(id=SESSION_ID, character_id=CHARACTER_ID,
                           user_id=USER_ID, status="active"))
            db.commit()
        run(build_pipeline(factory))
        run(build_pipeline(factory))
        with factory() as db:
            assert db.get(Session, SESSION_ID).turn_count == 2

    def test_turn_count_scoped_to_user(self, factory):
        """UPDATE 带 user_id：不会写到他人会话（用户隔离）。"""
        from db.models import Session

        with factory() as db:
            db.add(Session(id=SESSION_ID, character_id=CHARACTER_ID,
                           user_id="someone_else", status="active"))
            db.commit()
        run(build_pipeline(factory))
        with factory() as db:
            assert db.get(Session, SESSION_ID).turn_count == 0


class TestTurnStorage:
    def test_valence_flows_into_user_turn(self, factory):
        """裁决⑤：user 轮的 valence 随 ConversationTurn 交给记忆层（Phase 5 入库）。"""
        pipeline = build_pipeline(factory, FakeLLM(classifier_event={
            "label": "hostile", "intensity": 0.9, "busy_signal": False}))
        run(pipeline)
        turns = [turn for turn, _ in pipeline.memory_manager.stored]
        assert turns[0].role == "user"
        assert turns[0].valence == -1.0
        assert turns[1].role == "assistant"


class TestMemoryIntegration:
    """管道 + 真实 MemoryManager（假嵌入器）：存储、索引、正文回读。"""

    def _pipeline(self, factory, tmp_path, fake_llm=None):
        from memory.memory_manager import MemoryManager

        memory_manager = MemoryManager(
            str(tmp_path / "dudulu_memory.db"),
            db_session_factory=factory, embedder="fake")
        pipeline = build_pipeline(factory, fake_llm or FakeLLM())
        pipeline.memory_manager = memory_manager
        return pipeline

    def test_turns_stored_with_messages_index(self, factory, tmp_path):
        from sqlalchemy import select

        from db.models import Message

        pipeline = self._pipeline(factory, tmp_path)
        run(pipeline)
        run(pipeline)
        with factory() as db:
            rows = db.execute(select(Message)).scalars().all()
        # 两轮 × user+assistant，memory_id 全部非空
        assert len(rows) == 4
        assert all(row.memory_id for row in rows)

    def test_recent_turns_reads_back_from_memory(self, factory, tmp_path):
        """第 8 步：messages 索引 + YantrikDB 正文回读（文档 2 第 6.7 节）。"""
        pipeline = self._pipeline(factory, tmp_path)
        run(pipeline, "第一轮消息")
        run(pipeline, "第二轮消息")
        turns = pipeline._get_recent_turns(CHARACTER_ID, USER_ID, n=10)
        assert [turn.role for turn in turns] == [
            "user", "assistant", "user", "assistant"]
        assert turns[0].text == "第一轮消息"
        assert turns[1].text == "听见你了。"
        assert turns[2].text == "第二轮消息"

    def test_recent_turns_isolated_by_user(self, factory, tmp_path):
        pipeline = self._pipeline(factory, tmp_path)
        run(pipeline)
        assert pipeline._get_recent_turns(CHARACTER_ID, "other_user") == []

    def test_recent_turns_skips_missing_memory(self, factory, tmp_path):
        """索引行有但记忆缺失：跳过该行，不阻塞装配。"""
        from db.models import Message
        from memory.schemas import generate_id

        pipeline = self._pipeline(factory, tmp_path)
        with factory() as db:
            db.add(Message(id=generate_id(), session_id=SESSION_ID,
                           character_id=CHARACTER_ID, user_id=USER_ID,
                           role="user", memory_id="no-such-rid"))
            db.commit()
        assert pipeline._get_recent_turns(CHARACTER_ID, USER_ID) == []

    def test_prompt_includes_recent_turns_on_second_turn(self, factory, tmp_path):
        """第二轮 Prompt 的 [RECENT TURNS] 含第一轮内容。"""
        fake_llm = FakeLLM()
        pipeline = self._pipeline(factory, tmp_path, fake_llm)
        run(pipeline, "第一轮消息")
        run(pipeline, "第二轮消息")
        # prompts: [分类器, 生成, 分类器, 生成]
        assert "第一轮消息" in fake_llm.prompts[3]
        assert "user: 第一轮消息" in fake_llm.prompts[3]

    def test_retrieve_called_with_real_manager(self, factory, tmp_path):
        """第 7 步检索经真实 MemoryManager（双键 + 四因子）。"""
        pipeline = self._pipeline(factory, tmp_path)
        run(pipeline)
        memory_manager = pipeline.memory_manager
        results = memory_manager.retrieve("今天好累", CHARACTER_ID, USER_ID,
                                          {"expression": {"PLE": 0.0}},
                                          top_k=5)
        assert any(item["content"] == "今天好累啊" for item in results)
        # 换 user 检索不到（双键隔离）
        assert memory_manager.retrieve(
            "今天好累", CHARACTER_ID, "other_user",
            {"expression": {"PLE": 0.0}}) == []
