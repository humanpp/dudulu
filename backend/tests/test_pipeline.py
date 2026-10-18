"""编排管道单元测试（文档 2 第 3.8 节 13 步；协作者用假实现，不打真实 API）。"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from helpers import (
    CONFIG_PATH,
    LIN_ZHIXIA,
    PROMPT_PATH,
    FakeCharacterManager,
    FakeLLM,
    FakeWSManager,
    SpyMemoryManager,
    SpyProactiveState,
    SpySedimenter,
    make_profile,
    seed_character,
    temp_db_factory,
)
from orchestrator.classifier import Classifier
from orchestrator.pipeline import Pipeline, create_pipeline, memory_ids
from orchestrator.post_processor import PostProcessor
from orchestrator.prompt_assembler import PromptAssembler

MESSAGE = "今天好累啊"


def build_pipeline(fake_llm: FakeLLM, profile=None, ws=None,
                   db_factory=None) -> Pipeline:
    """装配管道：真实分类器/装配器/后处理 + 假协作者 + 临时 SQLite 库。"""
    return Pipeline(
        character_manager=FakeCharacterManager(profile or make_profile()),
        memory_manager=SpyMemoryManager(),
        classifier=Classifier(fake_llm, prompt_path=PROMPT_PATH),
        prompt_assembler=PromptAssembler(),
        llm_client=fake_llm,
        sedimenter=SpySedimenter(),
        proactive_state=SpyProactiveState(),
        post_processor=PostProcessor(),
        ws_manager=ws,
        emotion_config_path=CONFIG_PATH,
        db_session_factory=db_factory or temp_db_factory(),
    )


def run_process(pipeline: Pipeline, message: str = MESSAGE):
    return asyncio.run(pipeline.process(
        message, "char_001", "user_001", "sess-001"))


def collect_stream(pipeline: Pipeline, message: str = MESSAGE) -> list[dict]:
    async def consume():
        return [event async for event in pipeline.process_stream(
            message, "char_001", "user_001", "sess-001")]
    return asyncio.run(consume())


class TestProcess:
    def test_returns_reply_and_state(self):
        fake_llm = FakeLLM(reply="辛苦啦，记得休息。")
        result = run_process(build_pipeline(fake_llm))
        assert result.reply == "辛苦啦，记得休息。"
        assert set(result.state) == {"expression", "internal", "affective"}
        assert result.state["affective"]["stage_code"] == "S0"
        assert result.state["affective"]["stage_label"] == "陌生"

    def test_emotion_engine_updated_by_event(self):
        """affectionate 事件使内部层满足感上升（引擎真实参与）。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "affectionate", "intensity": 0.6, "busy_signal": False})
        result = run_process(build_pipeline(fake_llm))
        assert result.state["internal"]["SAT"] > 0.5

    def test_intense_event_changes_affective(self):
        """I > 0.7 的剧烈事件冲击情感层（裁决：impact 公式）。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "affectionate", "intensity": 1.0, "busy_signal": False})
        result = run_process(build_pipeline(fake_llm))
        assert result.state["affective"]["AFF"] > 15

    def test_prompt_contains_all_sections_and_message(self):
        fake_llm = FakeLLM()
        run_process(build_pipeline(fake_llm))
        # prompts[0] 是分类器提示词，prompts[1] 是生成提示词
        reply_prompt = fake_llm.prompts[1]
        for section in ("[CHARACTER]", "[SPEECH STYLE]", "[EMOTION STATE]",
                        "[RELATIONSHIP]", "[MEMORIES]", "[RECENT TURNS]",
                        "[USER]"):
            assert section in reply_prompt
        assert MESSAGE in reply_prompt
        assert "测试角色" in reply_prompt

    def test_character_profile_from_manager(self):
        fake_llm = FakeLLM()
        pipeline = build_pipeline(fake_llm, profile=make_profile(**LIN_ZHIXIA))
        run_process(pipeline)
        assert "林知夏" in fake_llm.prompts[1]

    def test_stores_user_and_assistant_turns(self):
        fake_llm = FakeLLM(classifier_event={
            "label": "vulnerable", "intensity": 0.7, "busy_signal": False})
        pipeline = build_pipeline(fake_llm)
        run_process(pipeline)
        memory = pipeline.memory_manager
        turns = [turn for turn, _ in memory.stored]
        session_ids = [session_id for _, session_id in memory.stored]
        assert [turn.role for turn in turns] == ["user", "assistant"]
        assert turns[0].text == MESSAGE
        assert turns[0].emotion_tag == "vulnerable"
        assert turns[1].text == fake_llm.reply
        assert turns[0].expression_state is not None
        # 两轮都带上 session_id（供 messages 索引关联）
        assert session_ids == ["sess-001", "sess-001"]

    def test_retrieve_called_with_double_keys(self):
        fake_llm = FakeLLM()
        pipeline = build_pipeline(fake_llm)
        run_process(pipeline)
        call = pipeline.memory_manager.retrieve_calls[0]
        assert call["character_id"] == "char_001"
        assert call["user_id"] == "user_001"
        assert call["query"] == MESSAGE
        assert call["top_k"] == 5

    def test_sedimenter_called(self):
        pipeline = build_pipeline(FakeLLM())
        run_process(pipeline)
        assert pipeline.sedimenter.calls == [("char_001", "user_001")]

    def test_busy_signal_lifecycle(self):
        """5.5 步：先清空 busy_until（重新激活），命中才写 now+24h。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "withdrawn", "intensity": 0.5, "busy_signal": True})
        pipeline = build_pipeline(fake_llm)
        run_process(pipeline)
        assert pipeline.proactive_state.cleared == [("char_001", "user_001")]
        assert pipeline.proactive_state.busy == [("char_001", "user_001", 24)]

    def test_busy_signal_not_set_when_absent(self):
        pipeline = build_pipeline(FakeLLM(classifier_event={
            "label": "neutral", "intensity": 0.1, "busy_signal": False}))
        run_process(pipeline)
        assert pipeline.proactive_state.busy == []

    def test_decay_wired_with_hours_since_last(self):
        """第 4 步：衰减按 _hours_since_last 的小时数执行。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "neutral", "intensity": 0.1, "busy_signal": False})
        pipeline = build_pipeline(fake_llm)
        seen: list[tuple] = []
        original = pipeline._hours_since_last

        def spy(character_id, user_id):
            seen.append((character_id, user_id))
            return original(character_id, user_id)

        pipeline._hours_since_last = spy
        run_process(pipeline)
        assert seen == [("char_001", "user_001")]

    def test_decay_moves_state_toward_baseline(self):
        """加载偏离基线的状态 + 长间隔 → 衰减后向基线回归。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "neutral", "intensity": 0.1, "busy_signal": False})
        pipeline = build_pipeline(fake_llm)
        pipeline._load_user_state = lambda c, u: {
            "expression": {"PLE": 0.9, "ARO": 0.0, "DOM": 0.0,
                           "INT": 0.2, "SEC": 0.3}}
        pipeline._hours_since_last = lambda c, u: 48.0
        result = run_process(pipeline)
        assert result.state["expression"]["PLE"] < 0.5


class TestProcessStream:
    def test_tokens_then_done_with_real_state(self):
        fake_llm = FakeLLM(tokens=("听", "见", "你", "了。"))
        events = collect_stream(build_pipeline(fake_llm))
        names = [event["event"] for event in events]
        assert names == ["token", "token", "token", "token", "done"]
        assert "".join(event["data"] for event in events[:-1]) == "听见你了。"
        payload = json.loads(events[-1]["data"])
        assert set(payload) == {"state", "memories_used"}
        assert payload["state"]["affective"]["stage_code"] == "S0"
        assert payload["memories_used"] == []

    def test_error_event_replaces_done_with_partial(self):
        """中途失败：error 替代 done，带 partial，已吐 token 不回滚。"""
        fake_llm = FakeLLM(tokens=("听", "见"),
                           stream_error=RuntimeError("upstream timeout"))
        events = collect_stream(build_pipeline(fake_llm))
        names = [event["event"] for event in events]
        assert names == ["token", "error"]
        payload = json.loads(events[-1]["data"])
        assert payload["message"] == "upstream timeout"
        assert payload["partial"] == "听"

    def test_storage_skipped_when_stream_fails(self):
        """流式失败时直接 return，不走存储与沉淀（文档 3.8 节第 9 步语义）。"""
        fake_llm = FakeLLM(stream_error=RuntimeError("boom"))
        pipeline = build_pipeline(fake_llm)
        collect_stream(pipeline)
        assert pipeline.memory_manager.stored == []
        assert pipeline.sedimenter.calls == []

    def test_storage_runs_when_stream_succeeds(self):
        pipeline = build_pipeline(FakeLLM())
        collect_stream(pipeline)
        assert len(pipeline.memory_manager.stored) == 2
        assert pipeline.sedimenter.calls == [("char_001", "user_001")]

    def test_busy_signal_lifecycle_in_stream(self):
        """流式路径同样执行 5.5 步的 busy_signal 落库。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "withdrawn", "intensity": 0.5, "busy_signal": True})
        pipeline = build_pipeline(fake_llm)
        collect_stream(pipeline)
        assert pipeline.proactive_state.cleared == [("char_001", "user_001")]
        assert pipeline.proactive_state.busy == [("char_001", "user_001", 24)]


class TestStateUpdatePush:
    def test_push_payload_shape_and_real_delta(self):
        """11.5 步：完整三层 + delta；delta 为真实差（裁决⑤），只含数值维度。"""
        fake_llm = FakeLLM(classifier_event={
            "label": "affectionate", "intensity": 1.0, "busy_signal": False})
        ws = FakeWSManager()
        run_process(build_pipeline(fake_llm, ws=ws))
        assert len(ws.pushed) == 1
        user_id, payload = ws.pushed[0]
        assert user_id == "user_001"
        assert payload["type"] == "state_update"
        assert payload["character_id"] == "char_001"
        assert set(payload) == {"type", "character_id", "expression",
                                "internal", "affective", "delta"}
        # affective 含 stage 字段（文档 2 第 6.8 节契约）；
        # I=1.0 的剧烈事件把 AFF 从 15 推到 20.5，阶段升为 S1
        assert payload["affective"]["stage_code"] == "S1"
        assert payload["affective"]["stage_label"] == "认识"
        # delta = new - old：old 为事件前快照（初始 AFF 15、PLE 0.15）
        assert payload["delta"]["affective"]["AFF"] == pytest.approx(
            payload["affective"]["AFF"] - 15.0)
        assert payload["delta"]["expression"]["PLE"] == pytest.approx(
            payload["expression"]["PLE"] - 0.15)
        assert "stage_code" not in payload["delta"]["affective"]
        assert set(payload["delta"]["internal"]) == {
            "ANX", "LON", "CUR", "ANT", "GUI", "PRO", "DEP", "PAT",
            "VUL", "SAT", "JEA", "TRU"}

    def test_no_push_without_ws_manager(self):
        """Phase 8 前 ws_manager 为 None，11.5 步静默跳过。"""
        result = run_process(build_pipeline(FakeLLM()))
        assert result.reply  # 不报错即通过

    def test_stream_pushes_before_done(self):
        fake_llm = FakeLLM()
        ws = FakeWSManager()
        collect_stream(build_pipeline(fake_llm, ws=ws))
        assert len(ws.pushed) == 1


class TestCreatePipeline:
    def test_wires_real_and_placeholder_collaborators(self):
        fake_llm = FakeLLM()
        factory = temp_db_factory()
        seed_character(factory)  # Phase 6 起角色管理器要求角色真实存在
        pipeline = create_pipeline(fake_llm, emotion_config_path=CONFIG_PATH,
                                   db_session_factory=factory,
                                   yantrik_path=str(Path(tempfile.mkdtemp())
                                                    / "mem.db"),
                                   embedder="fake")
        assert isinstance(pipeline.classifier, Classifier)
        assert isinstance(pipeline.prompt_assembler, PromptAssembler)
        assert isinstance(pipeline.post_processor, PostProcessor)
        assert pipeline.ws_manager is None
        assert pipeline.emotion_config_path == CONFIG_PATH
        # Phase 7 起全部协作者均为真实实现（ws_manager 待 Phase 8）
        from core.character.manager import CharacterManager
        from db.proactive_state import ProactiveStateStore
        from memory.memory_manager import MemoryManager
        from memory.sedimenter import Sedimenter

        assert isinstance(pipeline.proactive_state, ProactiveStateStore)
        assert isinstance(pipeline.memory_manager, MemoryManager)
        assert isinstance(pipeline.character_manager, CharacterManager)
        assert isinstance(pipeline.sedimenter, Sedimenter)
        # 全链路可跑通（真实沉淀器：无到期沉淀时为无操作）
        run_process(pipeline)

    def test_default_emotion_config_path(self):
        pipeline = create_pipeline(
            FakeLLM(), "config/dudulu_config.yaml", temp_db_factory(),
            yantrik_path=str(Path(tempfile.mkdtemp()) / "mem.db"),
            embedder="fake")
        assert pipeline.emotion_config_path == "config/dudulu_config.yaml"


class TestMemoryIds:
    def test_extracts_ids(self):
        assert memory_ids([{"id": "m1"}, {"id": "m2"}]) == ["m1", "m2"]

    def test_empty(self):
        assert memory_ids([]) == []
