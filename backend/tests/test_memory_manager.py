"""MemoryManager 单元测试（Phase 5 裁决 A–E；假嵌入器，无网络无下载）。"""

import time

import pytest

from helpers import make_db_session_factory, make_memory_manager
from memory.memory_manager import (
    MEMORY_TYPE_CONVERSATION,
    MEMORY_TYPE_PROACTIVE,
    DeterministicEmbedder,
    MemoryManager,
    build_namespace,
)
from memory.schemas import ConversationTurn

CHARACTER_ID = "char_001"
USER_ID = "user_001"


def make_turn(text: str, role: str = "user", **kwargs) -> ConversationTurn:
    return ConversationTurn(
        character_id=CHARACTER_ID, user_id=USER_ID, role=role, text=text,
        **kwargs)


@pytest.fixture
def manager(tmp_path):
    return make_memory_manager(tmp_path)


class TestNamespace:
    def test_format(self):
        assert build_namespace("char_001", "user_001") == "char_001|user_001"


class TestDeterministicEmbedder:
    def test_same_text_same_vector(self):
        embedder = DeterministicEmbedder()
        assert embedder.encode("你好") == embedder.encode("你好")

    def test_different_text_different_vector(self):
        embedder = DeterministicEmbedder()
        assert embedder.encode("你好") != embedder.encode("再见")

    def test_dimension_is_384(self):
        assert len(DeterministicEmbedder().encode("x")) == 384


class TestStoreConversation:
    def test_round_trip_via_get_memory(self, manager):
        rid = manager.store_conversation(make_turn(
            "用户提到工作压力大", emotion_tag="vulnerable", intensity=0.72,
            valence=-0.4))
        assert rid
        memory = manager.get_memory(rid)
        assert memory["content"] == "用户提到工作压力大"
        assert memory["role"] == "user"
        assert memory["emotion_tag"] == "vulnerable"
        assert memory["intensity"] == 0.72
        assert memory["valence"] == -0.4
        assert memory["type"] == MEMORY_TYPE_CONVERSATION
        assert memory["is_proactive"] is False
        assert memory["character_id"] == CHARACTER_ID
        assert memory["user_id"] == USER_ID
        assert memory["timestamp"]
        assert memory["importance"] == 0.72      # _calc_importance 取强度
        assert memory["half_life_hours"] == 168  # _calc_half_life 常量

    def test_proactive_flag_maps_to_memory_type(self, manager):
        rid = manager.store_conversation(make_turn(
            "在忙吗？突然想起你。", role="assistant", is_proactive=True,
            trigger_reason="loneliness_high"))
        memory = manager.get_memory(rid)
        assert memory["type"] == MEMORY_TYPE_PROACTIVE
        assert memory["is_proactive"] is True
        assert memory["trigger_reason"] == "loneliness_high"
        assert memory["role"] == "assistant"

    def test_expression_snapshot_in_metadata(self, manager):
        rid = manager.store_conversation(make_turn(
            "今天好累", expression_state={"PLE": 0.2, "ARO": -0.1}))
        memory = manager.get_memory(rid)
        assert memory["expression_snapshot"] == {"PLE": 0.2, "ARO": -0.1}

    def test_valence_defaults_zero_when_absent(self, manager):
        rid = manager.store_conversation(make_turn("普通一句"))
        assert manager.get_memory(rid)["valence"] == 0.0

    def test_created_at_from_iso_timestamp(self, manager):
        rid = manager.store_conversation(make_turn(
            "带时间戳的记忆", timestamp="2026-10-14T10:00:00"))
        hit = manager.db.get_memory(rid)
        expected = time.mktime(time.strptime("2026-10-14T10:00:00",
                                             "%Y-%m-%dT%H:%M:%S"))
        assert hit["created_at"] == pytest.approx(expected, abs=2)

    def test_messages_index_written_with_memory_id(self, manager, tmp_path):
        from db.models import Message

        rid = manager.store_conversation(make_turn("索引测试"),
                                         session_id="sess-001")
        with manager._db_session_factory() as db:
            rows = db.execute(
                __import__("sqlalchemy").select(Message)).scalars().all()
        assert len(rows) == 1
        assert rows[0].memory_id == rid
        assert rows[0].session_id == "sess-001"
        assert rows[0].role == "user"

    def test_no_index_without_session_id(self, manager):
        manager.store_conversation(make_turn("无会话存储"))
        from sqlalchemy import select

        from db.models import Message

        with manager._db_session_factory() as db:
            assert db.execute(select(Message)).scalars().all() == []


class TestRetrieve:
    def _seed(self, manager):
        manager.store_conversation(make_turn(
            "用户提到工作压力大，感觉快撑不住了",
            emotion_tag="vulnerable", intensity=0.72, valence=-0.4))
        manager.store_conversation(make_turn(
            "用户说今天天气不错，想出去散步",
            emotion_tag="playful", intensity=0.4, valence=0.6))
        manager.store_conversation(make_turn(
            "用户问 AI 昨晚做了什么", emotion_tag="curious", intensity=0.3))

    def test_returns_top_k(self, manager):
        self._seed(manager)
        results = manager.retrieve("工作", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": 0.0}}, top_k=2)
        assert len(results) == 2

    def test_double_key_isolation(self, manager):
        self._seed(manager)
        # 换 user：检索不到
        assert manager.retrieve("工作", CHARACTER_ID, "other_user",
                                {"expression": {"PLE": 0.0}}) == []
        # 换 character：检索不到
        assert manager.retrieve("工作", "char_002", USER_ID,
                                {"expression": {"PLE": 0.0}}) == []

    def test_emotion_tag_post_filter(self, manager):
        self._seed(manager)
        results = manager.retrieve("用户", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": 0.0}},
                                   emotion_tag="vulnerable", top_k=5)
        assert results
        assert all(item["emotion_tag"] == "vulnerable" for item in results)

    def test_time_range_excludes_old(self, manager):
        manager.store_conversation(make_turn(
            "很久以前的记忆", timestamp="2020-01-01T10:00:00"))
        manager.store_conversation(make_turn("今天的记忆"))
        results = manager.retrieve("记忆", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": 0.0}},
                                   time_range="last_7_days", top_k=5)
        assert all(item["content"] != "很久以前的记忆" for item in results)

    def test_time_range_all_keeps_old(self, manager):
        manager.store_conversation(make_turn(
            "很久以前的记忆", timestamp="2020-01-01T10:00:00"))
        results = manager.retrieve("记忆", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": 0.0}},
                                   time_range="all", top_k=5)
        assert any(item["content"] == "很久以前的记忆" for item in results)

    def test_unknown_time_range_treated_as_all(self, manager):
        manager.store_conversation(make_turn("某条记忆"))
        results = manager.retrieve("记忆", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": 0.0}},
                                   time_range="bogus", top_k=5)
        assert len(results) == 1

    def test_pool_enlarged_when_emotion_tag_filter(self, manager):
        """裁决②：emotion_tag 后过滤是有损操作，候选池放大到 max(top_k×10, 100)。"""
        calls: list[int] = []
        original_recall = manager.db.recall  # 先捕获，避免代理自递归

        def spy_recall(**kwargs):
            calls.append(kwargs["top_k"])
            return original_recall(**kwargs)

        manager.db = _DbProxy(manager.db, recall=spy_recall)
        manager.store_conversation(make_turn("带标签的记忆",
                                             emotion_tag="vulnerable"))
        manager.retrieve("记忆", CHARACTER_ID, USER_ID,
                         {"expression": {"PLE": 0.0}},
                         emotion_tag="vulnerable", top_k=5)
        assert calls == [100]  # max(5×10, 100)

    def test_pool_normal_without_emotion_tag(self, manager):
        """无过滤时候选池为 max(top_k×3, 20)。"""
        calls: list[int] = []
        original_recall = manager.db.recall

        def spy_recall(**kwargs):
            calls.append(kwargs["top_k"])
            return original_recall(**kwargs)

        manager.db = _DbProxy(manager.db, recall=spy_recall)
        manager.store_conversation(make_turn("普通记忆"))
        manager.retrieve("记忆", CHARACTER_ID, USER_ID,
                         {"expression": {"PLE": 0.0}}, top_k=5)
        assert calls == [20]

    def test_result_field_names(self, manager):
        self._seed(manager)
        results = manager.retrieve("工作", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": 0.0}})
        expected = {"id", "character_id", "user_id", "content", "type",
                    "role", "is_proactive", "trigger_reason", "timestamp",
                    "emotion_tag", "intensity", "valence", "importance",
                    "half_life_hours", "expression_snapshot"}
        assert set(results[0]) == expected


class TestRerank:
    def test_weights_applied_to_db_factors(self, manager):
        """0.4 相似 + 0.3 情感 + 0.2 近因 + 0.1 重要（裁决 B）。"""
        hit = {"scores": {"similarity": 0.8, "recency": 0.5,
                          "importance": 0.6},
               "valence": -0.2}
        current_state = {"expression": {"PLE": 0.2}}
        # emotion_match = 1 - |valence - PLE| / 2 = 1 - 0.2 = 0.8
        expected = (0.4 * 0.8 + 0.3 * 0.8 + 0.2 * 0.5 + 0.1 * 0.6)
        assert manager._rerank_score(hit, current_state) == \
            pytest.approx(expected)

    def test_fallback_to_db_score_without_scores(self, manager):
        """scores 缺失 → 直接用 DB 综合分（裁决 B 回退路径）。"""
        hit = {"score": 0.42}
        assert manager._rerank_score(hit, {"expression": {}}) == 0.42

    def test_valence_multiplier_and_graph_not_used(self, manager):
        """文档契约外的两个因子不参与（裁决 B）。"""
        base = {"scores": {"similarity": 0.5, "recency": 0.5,
                           "importance": 0.5}, "valence": 0.0}
        noisy = {"scores": {"similarity": 0.5, "recency": 0.5,
                            "importance": 0.5, "valence_multiplier": 1.9,
                            "graph_proximity": 0.9}, "valence": 0.0}
        assert (manager._rerank_score(base, {"expression": {}})
                == manager._rerank_score(noisy, {"expression": {}}))

    def test_emotion_match_formula(self, manager):
        memory = {"valence": -0.5}
        assert manager._emotion_match(memory, {"expression": {"PLE": -0.5}}) \
            == 1.0
        assert manager._emotion_match(memory, {"expression": {"PLE": 0.5}}) \
            == 0.5
        # 无 PLE 时按 0 处理
        assert manager._emotion_match(memory, {}) == pytest.approx(0.75)


class TestParseTimeRange:
    def test_none_and_all(self, manager):
        assert manager._parse_time_range(None) is None
        assert manager._parse_time_range("all") is None

    def test_last_7_days_window(self, manager):
        start, end = manager._parse_time_range("last_7_days")
        assert end - start == pytest.approx(7 * 86400, abs=2)

    def test_last_30_days_window(self, manager):
        start, end = manager._parse_time_range("last_30_days")
        assert end - start == pytest.approx(30 * 86400, abs=2)


class TestGetMemory:
    def test_missing_rid_returns_none(self, manager):
        assert manager.get_memory("") is None
        assert manager.get_memory("no-such-rid") is None


class TestCalcHelpers:
    def test_importance_from_intensity(self, manager):
        assert manager._calc_importance(make_turn("x", intensity=0.7)) == 0.7
        assert manager._calc_importance(make_turn("x")) == 0.5

    def test_half_life_constant(self, manager):
        assert manager._calc_half_life(make_turn("x")) == 168.0

    def test_to_unix_valid_and_invalid(self, manager):
        assert manager._to_unix("2026-10-14T10:00:00") > 0
        # 非法字符串回退为当前时间（不抛）
        assert manager._to_unix("not-a-timestamp") > 0


class _DbProxy:
    """YantrikDB（Rust 扩展类型，属性只读）的委托代理：可覆盖指定方法。"""

    def __init__(self, inner, **overrides):
        self._inner = inner
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._inner, name)


class TestDefensiveBranches:
    def test_degraded_dimension_warning(self, tmp_path, caplog):
        """裁决 C 防线 1：维度 ≤64 → WARNING（离线降级态）。"""
        from memory.memory_manager import MemoryManager

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        manager = MemoryManager(str(tmp_path / "mem.db"),
                                db_session_factory=factory, embedder="fake")
        manager.db = _DbProxy(manager.db, embed=lambda text: [0.0] * 64)
        with caplog.at_level("WARNING"):
            manager._warn_if_degraded()
        assert "离线降级嵌入模型" in caplog.text

    def test_dimension_probe_failure_does_not_block(self, tmp_path, caplog):
        """探测失败只告警不抛（启动不阻塞）。"""
        from memory.memory_manager import MemoryManager

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        manager = MemoryManager(str(tmp_path / "mem.db"),
                                db_session_factory=factory, embedder="fake")

        def exploding_embed(text):
            raise RuntimeError("probe failed")

        manager.db = _DbProxy(manager.db, embed=exploding_embed)
        with caplog.at_level("WARNING"):
            manager._warn_if_degraded()
        assert "维度探测失败" in caplog.text

    def test_get_memory_swallows_db_errors(self, manager):
        """get_memory 遇库错误按缺失处理（不向上抛）。"""

        def exploding_get(rid):
            raise RuntimeError("db corrupted")

        manager.db = _DbProxy(manager.db, get_memory=exploding_get)
        assert manager.get_memory("any-rid") is None


class TestEmbedderModes:
    def test_unknown_mode_raises(self, tmp_path):
        factory = make_db_session_factory(tmp_path / "dudulu.db")
        with pytest.raises(ValueError):
            MemoryManager(str(tmp_path / "mem.db"),
                          db_session_factory=factory, embedder="bogus")

    def test_fake_mode_logs_and_works(self, tmp_path, caplog):
        with caplog.at_level("INFO"):
            manager = make_memory_manager(tmp_path, embedder="fake")
        assert "假嵌入器" in caplog.text
        rid = manager.store_conversation(make_turn("模式测试"))
        assert manager.get_memory(rid)["content"] == "模式测试"

    def test_manager_cache_keyed_by_path(self, tmp_path):
        """同路径同工厂同模式 → 同一实例（避免每请求重开库）。"""
        from memory.memory_manager import get_memory_manager

        factory = make_db_session_factory(tmp_path / "dudulu.db")
        path = str(tmp_path / "cached.db")
        first = get_memory_manager(path, db_session_factory=factory,
                                   embedder="fake")
        second = get_memory_manager(path, db_session_factory=factory,
                                    embedder="fake")
        assert first is second


@pytest.mark.integration
class TestDefaultEmbedderSmoke:
    """集成冒烟：with_default 捆绑嵌入模型（模型需已缓存；默认跳过）。"""

    def test_store_and_recall_with_bundled_model(self, tmp_path):
        manager = make_memory_manager(tmp_path, embedder="default")
        rid = manager.store_conversation(make_turn(
            "用户提到工作压力大，感觉快撑不住了",
            emotion_tag="vulnerable", intensity=0.72, valence=-0.4))
        results = manager.retrieve("工作压力", CHARACTER_ID, USER_ID,
                                   {"expression": {"PLE": -0.2}}, top_k=3)
        assert results
        assert results[0]["content"] == "用户提到工作压力大，感觉快撑不住了"
        assert manager.get_memory(rid)["content"]
