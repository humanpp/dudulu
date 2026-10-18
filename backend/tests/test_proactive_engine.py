"""主动行为引擎单元测试（文档 2 第 3.11 节 + 文档 1 第十二章）。

覆盖：五道硬性拦截、频率门控、六条触发与优先级、eagerness 调节、
退缩机制（3 次软静默 / 5 次硬静默）、生成与推送全链路、
记忆衰减堆判定（裁决①②③）。
"""

import asyncio
import json
import time
from datetime import timedelta

import pytest

from helpers import CONFIG_PATH, make_db_session_factory, make_profile
from config.settings import Settings
from core.character.profile import CharacterProfile
from db.models import ProactiveLog, ProactiveState, User, utc_now
from db.proactive_state import ProactiveStateStore
from db.user_state import BASELINE_KEY
from memory.memory_manager import MemoryManager
from memory.schemas import ConversationTurn
from scheduler.proactive import (
    MOOD_DEVIATION_THRESHOLD,
    ProactiveEngine,
    ProactiveSignal,
)

CHARACTER_ID = "char_001"
USER_ID = "user_001"
DAY = 86400.0


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def memory_manager(tmp_path):
    return MemoryManager(str(tmp_path / "mem.db"),
                         db_session_factory=None, embedder="fake")


@pytest.fixture
def profile():
    return make_profile()


@pytest.fixture(autouse=True)
def _not_quiet_hours(monkeypatch):
    """默认放行静默时段判定（测试运行时刻可能恰在用户本地静默期内；
    is_in_quiet_hours 本身的行为由 test_timezone.py 专项覆盖）。
    """
    monkeypatch.setattr("scheduler.proactive.is_in_quiet_hours",
                        lambda db, user_id, hours: False)


class FakeWS:
    """记录 push 的假 WebSocket 管理器。"""

    def __init__(self):
        self.pushed: list[tuple[str, dict]] = []

    def push(self, user_id: str, message: dict) -> None:
        self.pushed.append((user_id, message))


def make_engine(factory, memory_manager, profile, llm=None, ws=None,
                settings=None) -> ProactiveEngine:
    return ProactiveEngine(
        character_manager=_FakeManager(profile),
        memory_manager=memory_manager,
        llm_client=llm or _FakeLLM(),
        ws_manager=ws,
        db_session_factory=factory,
        config_path=CONFIG_PATH,
        settings=settings or Settings(_env_file=None),
    )


class _FakeManager:
    """只实现 get 的假角色管理器。"""

    def __init__(self, profile):
        self._profile = profile

    def get(self, character_id):
        if character_id != CHARACTER_ID:
            from core.character.manager import CharacterNotFoundError

            raise CharacterNotFoundError(character_id)
        return self._profile


class _FakeLLM:
    """记录 prompt 的假 LLM。"""

    def __init__(self, reply="在干嘛呢，突然想起你。"):
        self.reply = reply
        self.prompts: list[str] = []

    async def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.reply

    async def generate_stream(self, prompt, **kwargs):
        yield self.reply


# ── 种子辅助 ────────────────────────────────────────────────


def seed_user(factory, proactive_enabled=1):
    with factory() as db:
        db.add(User(id=USER_ID, username="u", password_hash="x",
                    proactive_enabled=proactive_enabled))
        db.commit()


def seed_state(factory, internal=None, expression=None, baseline=None,
               stage="S1", minutes_ago=600):
    """character_user_state 行（last_interaction 控制空闲时长）。"""
    from db.models import CharacterUserState

    expression_state = dict(expression or {})
    if baseline is not None:
        expression_state[BASELINE_KEY] = dict(baseline)
    with factory() as db:
        db.add(CharacterUserState(
            character_id=CHARACTER_ID, user_id=USER_ID,
            expression_state=json.dumps(expression_state),
            internal_state=json.dumps(internal or {}),
            affective_state=json.dumps({"AFF": 15, "TRS": 25, "ATT": 10}),
            relationship_stage=stage,
            last_interaction=utc_now() - timedelta(minutes=minutes_ago)))
        db.commit()


def seed_proactive_state(factory, no_reply=0, last_proactive_minutes_ago=None,
                         silenced_hours=None, busy_hours=None):
    with factory() as db:
        row = ProactiveState(character_id=CHARACTER_ID, user_id=USER_ID,
                             consecutive_no_reply=no_reply)
        if last_proactive_minutes_ago is not None:
            row.last_proactive_at = utc_now() - timedelta(
                minutes=last_proactive_minutes_ago)
        if silenced_hours is not None:
            row.silenced_until = utc_now() + timedelta(hours=silenced_hours)
        if busy_hours is not None:
            row.busy_until = utc_now() + timedelta(hours=busy_hours)
        db.add(row)
        db.commit()


def seed_proactive_log(factory, count, minutes_ago=60):
    with factory() as db:
        for _ in range(count):
            db.add(ProactiveLog(
                character_id=CHARACTER_ID, user_id=USER_ID,
                triggered_at=utc_now() - timedelta(minutes=minutes_ago),
                reason="time_interval", message="m"))
        db.commit()


def raw_memory(memory_manager, text, importance, half_life_days, age_days,
               memory_type="conversation_turn"):
    """绕过 store_conversation 直接写一条可控 importance/half_life/年龄 的记忆。

    created_at 用 time.time()（真实 epoch）：utc_now().timestamp() 会把
    naive UTC 当作本地时间换算，引入数小时偏移，导致年龄越过半衰期。
    """
    return memory_manager.db.record(
        text=text, memory_type=memory_type, namespace=f"{CHARACTER_ID}|{USER_ID}",
        importance=importance, half_life=half_life_days * DAY,
        created_at=time.time() - age_days * DAY,
        metadata={"character_id": CHARACTER_ID, "user_id": USER_ID,
                  "timestamp": "2026-10-01T10:00:00"})


# ── 五道硬性拦截 + 频率门控 ─────────────────────────────────


class TestShouldEvaluate:
    def test_user_disabled(self, factory, memory_manager, profile):
        seed_user(factory, proactive_enabled=0)
        seed_state(factory)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == \
            "user_disabled"

    def test_character_disabled(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        profile.proactive_enabled = False
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == \
            "character_disabled"

    def test_quiet_hours(self, factory, memory_manager, profile,
                         monkeypatch):
        seed_user(factory)
        seed_state(factory)
        engine = make_engine(factory, memory_manager, profile)
        monkeypatch.setattr("scheduler.proactive.is_in_quiet_hours",
                            lambda db, user_id, hours: True)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == "quiet_hours"

    def test_silenced(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        seed_proactive_state(factory, silenced_hours=12)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == "silenced"

    def test_busy(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        seed_proactive_state(factory, busy_hours=24)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == "busy"

    def test_user_in_conversation(self, factory, memory_manager, profile):
        """距上次消息 < 10 分钟：用户正在对话中。"""
        seed_user(factory)
        seed_state(factory, minutes_ago=5)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == \
            "user_in_conversation"

    def test_min_gap(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        seed_proactive_state(factory, last_proactive_minutes_ago=60)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == "min_gap"

    def test_interval_not_reached(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        seed_proactive_state(factory, last_proactive_minutes_ago=210)  # 3.5h < 4.5h
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == \
            "interval_not_reached"

    def test_soft_silence(self, factory, memory_manager, profile):
        """连续 3 次无回复且未过 12h 恢复期 → 软静默。"""
        seed_user(factory)
        seed_state(factory)
        seed_proactive_state(factory, no_reply=3,
                             last_proactive_minutes_ago=360)  # 6h < 12h
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == \
            "soft_silence"

    def test_max_daily(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        seed_proactive_log(factory, count=3)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == "max_daily"

    def test_passes_when_nothing_blocks(self, factory, memory_manager,
                                        profile):
        seed_user(factory)
        seed_state(factory)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) is None

    def test_character_unavailable(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory)
        engine = make_engine(factory, memory_manager, profile)
        assert engine._should_evaluate("ghost", USER_ID) == \
            "character_unavailable"


# ── 触发评估与优先级（文档 1 第 12.2 / 12.3 节） ─────────────


class TestEvaluateTriggers:
    def test_decay_heap_is_p0_with_memory_quote(self, factory,
                                                memory_manager, profile):
        seed_user(factory)
        seed_state(factory, minutes_ago=600)
        raw_memory(memory_manager, "你去年说想去海边", 0.8, 11, 10.5)
        engine = make_engine(factory, memory_manager, profile)
        signal = engine._evaluate(CHARACTER_ID, USER_ID)
        assert signal.should_act is True
        assert signal.reason == "decay_heap"
        assert signal.priority == "P0"
        assert signal.memory_quote == "你去年说想去海边"
        assert signal.details["remaining_hours"] < 72

    def test_loneliness_s2_is_p1(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory, internal={"LON": 0.8, "ANT": 0.1},
                   minutes_ago=300)  # 5h > min(4h, 4.5h)
        engine = make_engine(factory, memory_manager, profile)
        # 提状态到 S2（AFF 45）
        with factory() as db:
            from db.models import CharacterUserState

            row = db.get(CharacterUserState, (CHARACTER_ID, USER_ID))
            row.affective_state = json.dumps({"AFF": 45, "TRS": 25,
                                              "ATT": 10})
            db.commit()
        signal = engine._evaluate(CHARACTER_ID, USER_ID)
        assert signal.reason == "loneliness_high"
        assert signal.priority == "P1"

    def test_loneliness_below_s2_is_p3(self, factory, memory_manager,
                                       profile):
        seed_user(factory)
        seed_state(factory, internal={"LON": 0.8, "ANT": 0.1},
                   minutes_ago=300, stage="S1")
        engine = make_engine(factory, memory_manager, profile)
        signal = engine._evaluate(CHARACTER_ID, USER_ID)
        assert signal.reason == "loneliness_high"
        assert signal.priority == "P3"

    def test_anticipation_is_p2(self, factory, profile, tmp_path):
        seed_user(factory)
        seed_state(factory, internal={"LON": 0.1, "ANT": 0.7},
                   minutes_ago=300)
        # 最近用户消息含"下次再聊"（带索引的 MemoryManager）
        memory_manager = MemoryManager(str(tmp_path / "mem.db"),
                                       db_session_factory=factory,
                                       embedder="fake")
        memory_manager.store_conversation(ConversationTurn(
            character_id=CHARACTER_ID, user_id=USER_ID, role="user",
            text="今天先这样，下次再聊"), session_id="sess-1")
        engine = make_engine(factory, memory_manager, profile)
        signal = engine._evaluate(CHARACTER_ID, USER_ID)
        assert signal.reason == "anticipation_high"
        assert signal.priority == "P2"

    def test_mood_deviation_is_p3(self, factory, memory_manager, profile):
        seed_user(factory)
        # 当前表达层全面偏高、基线全 0 → 衰减后均值偏离仍 > 0.3
        seed_state(factory, internal={"LON": 0.1, "ANT": 0.1},
                   expression={"PLE": 0.9, "ARO": 0.9, "DOM": 0.9,
                              "INT": 0.9, "SEC": 0.9},
                   baseline={"PLE": 0.0, "ARO": 0.0, "DOM": 0.0,
                            "INT": 0.0, "SEC": 0.0},
                   minutes_ago=300)
        engine = make_engine(factory, memory_manager, profile)
        signal = engine._evaluate(CHARACTER_ID, USER_ID)
        assert signal.reason == "mood_deviation"
        assert signal.priority == "P3"
        assert signal.details["deviation"] > MOOD_DEVIATION_THRESHOLD

    def test_time_interval_only_is_p4(self, factory, memory_manager,
                                      profile):
        seed_user(factory)
        seed_state(factory, internal={"LON": 0.1, "ANT": 0.1},
                   minutes_ago=600)  # 10h > 4.5h
        engine = make_engine(factory, memory_manager, profile)
        signal = engine._evaluate(CHARACTER_ID, USER_ID)
        assert signal.reason == "time_interval"
        assert signal.priority == "P4"

    def test_no_trigger(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory, internal={"LON": 0.1, "ANT": 0.1},
                   minutes_ago=60)  # 1h：孤独门槛与间隔都不够
        engine = make_engine(factory, memory_manager, profile)
        assert engine._evaluate(CHARACTER_ID, USER_ID).should_act is False


# ── eagerness 与角色覆盖（文档 1 第 12.7 节） ───────────────


class TestResolveParams:
    def test_defaults(self, factory, memory_manager, profile):
        engine = make_engine(factory, memory_manager, profile)
        params = engine._resolve_params(profile)
        assert params["interval_hours"] == 4.5
        assert params["min_gap_hours"] == 3.0
        assert params["max_daily"] == 3
        assert params["eagerness"] == 1.0

    def test_eagerness_scales(self, factory, memory_manager, profile):
        profile.emotion_overrides.proactive.eagerness = 2.0
        engine = make_engine(factory, memory_manager, profile)
        params = engine._resolve_params(profile)
        assert params["interval_hours"] == 2.25
        assert params["min_gap_hours"] == 1.5
        assert params["max_daily"] == 6

    def test_low_eagerness_keeps_at_least_one_daily(self, factory,
                                                    memory_manager,
                                                    profile):
        profile.emotion_overrides.proactive.eagerness = 0.2
        engine = make_engine(factory, memory_manager, profile)
        params = engine._resolve_params(profile)
        assert params["max_daily"] == 1  # 下限保护
        assert params["interval_hours"] > 4.5

    def test_role_overrides(self, factory, memory_manager, profile):
        role = profile.emotion_overrides.proactive
        role.interval_hours = 2.0
        role.min_gap_hours = 1.0
        role.max_daily = 5
        role.silence_threshold = 2
        role.silence_recovery_hours = 6
        engine = make_engine(factory, memory_manager, profile)
        params = engine._resolve_params(profile)
        assert params["interval_hours"] == 2.0
        assert params["min_gap_hours"] == 1.0
        assert params["max_daily"] == 5
        assert params["silence_threshold"] == 2
        assert params["silence_recovery_hours"] == 6.0


# ── 生成与推送全链路（文档 2 第 3.11 节） ───────────────────


class TestGenerateAndPush:
    def test_full_chain(self, factory, memory_manager, profile):
        seed_user(factory)
        seed_state(factory, minutes_ago=600)
        ws = FakeWS()
        llm = _FakeLLM(reply="刚看到一朵云，想起你了。")
        engine = make_engine(factory, memory_manager, profile, llm=llm,
                             ws=ws)
        signal = ProactiveSignal(should_act=True, reason="time_interval",
                                 priority="P4")
        message = asyncio.run(engine._generate_and_push(
            CHARACTER_ID, USER_ID, signal))
        assert message == "刚看到一朵云，想起你了。"

        # 1. 记忆：proactive_message / assistant / trigger_reason / 标签
        records = memory_manager.db.list_memories(
            memory_type="proactive_message",
            namespace=f"{CHARACTER_ID}|{USER_ID}")["memories"]
        assert len(records) == 1
        record = records[0]
        assert record["source"] == "assistant"
        assert record["emotional_state"] == "proactive"
        assert record["metadata"]["trigger_reason"] == "time_interval"

        # 2. proactive_log
        with factory() as db:
            logs = db.query(ProactiveLog).all()
        assert len(logs) == 1
        assert logs[0].reason == "time_interval"
        assert logs[0].message == message

        # 3. proactive_state：计数 +1
        state = ProactiveStateStore(factory).get_state(CHARACTER_ID,
                                                       USER_ID)
        assert state["consecutive_no_reply"] == 1
        assert state["last_proactive_at"] is not None

        # 4. WS 推送
        assert len(ws.pushed) == 1
        user_id, payload = ws.pushed[0]
        assert user_id == USER_ID
        assert payload["type"] == "proactive_message"
        assert payload["message"] == message
        assert payload["reason"] == "time_interval"
        assert payload["priority"] == "P4"

    def test_five_no_replies_triggers_hard_silence(self, factory,
                                                   memory_manager,
                                                   profile):
        """连续 5 次无回复 → silenced_until = now + 24h。"""
        seed_user(factory)
        seed_state(factory, minutes_ago=600)
        engine = make_engine(factory, memory_manager, profile)
        signal = ProactiveSignal(should_act=True, reason="time_interval",
                                 priority="P4")
        for _ in range(5):
            asyncio.run(engine._generate_and_push(CHARACTER_ID, USER_ID,
                                                  signal))
        state = ProactiveStateStore(factory).get_state(CHARACTER_ID,
                                                       USER_ID)
        assert state["consecutive_no_reply"] == 5
        assert state["silenced_until"] is not None
        # 静默期内 _should_evaluate 拦截
        assert engine._should_evaluate(CHARACTER_ID, USER_ID) == "silenced"

    def test_user_reply_resets_counter(self, factory, memory_manager,
                                       profile):
        """用户回复归零 + 清空静默期（管道 5.5 步链路）。"""
        seed_user(factory)
        seed_state(factory, minutes_ago=600)
        store = ProactiveStateStore(factory)
        store.record_proactive_sent(CHARACTER_ID, USER_ID)
        store.record_proactive_sent(CHARACTER_ID, USER_ID)
        store.set_silenced_until(CHARACTER_ID, USER_ID, 24)
        store.register_user_reply(CHARACTER_ID, USER_ID)
        state = store.get_state(CHARACTER_ID, USER_ID)
        assert state["consecutive_no_reply"] == 0
        assert state["silenced_until"] is None

    def test_prompt_has_placeholders_substituted(self, factory,
                                                 memory_manager,
                                                 profile):
        seed_user(factory)
        seed_state(factory, minutes_ago=600)
        llm = _FakeLLM()
        engine = make_engine(factory, memory_manager, profile, llm=llm)
        signal = ProactiveSignal(should_act=True, reason="loneliness_high",
                                 priority="P1", memory_quote="你说想去海边")
        asyncio.run(engine._generate_and_push(CHARACTER_ID, USER_ID,
                                              signal))
        prompt = llm.prompts[0]
        assert profile.name in prompt
        assert "孤独感" in prompt            # 内部状态占位
        assert "有些孤独" in prompt          # 触发原因占位
        assert "你说想去海边" in prompt      # 记忆引用占位（P0/P1 引用）
        assert "__" not in prompt           # 无未替换占位

    def test_prompt_without_memory_quote(self, factory, memory_manager,
                                         profile):
        engine = make_engine(factory, memory_manager, profile)
        signal = ProactiveSignal(should_act=True, reason="time_interval",
                                 priority="P4")
        prompt = engine._build_prompt(profile, signal,
                                      {"internal": {"LON": 0.1},
                                       "affective": {"stage_code": "S1"},
                                       "expression": {}})
        assert "一件重要的事" not in prompt
        assert "__" not in prompt


# ── tick 全流程 ─────────────────────────────────────────────


class TestTick:
    def test_tick_processes_active_pairs(self, factory, memory_manager,
                                         profile):
        seed_user(factory)
        seed_state(factory, minutes_ago=600)
        ws = FakeWS()
        engine = make_engine(factory, memory_manager, profile, ws=ws)
        stats = asyncio.run(engine.tick())
        assert stats == {"pairs": 1, "sent": 1, "skipped": 0}
        assert len(ws.pushed) == 1

    def test_tick_skips_blocked_pairs(self, factory, memory_manager,
                                      profile):
        seed_user(factory, proactive_enabled=0)
        seed_state(factory, minutes_ago=600)
        engine = make_engine(factory, memory_manager, profile)
        stats = asyncio.run(engine.tick())
        assert stats == {"pairs": 1, "sent": 0, "skipped": 1}


# ── 记忆衰减堆判定（裁决①②③） ──────────────────────────────


class TestFindForgettingImportant:
    def test_hit_when_all_conditions_met(self, factory, memory_manager,
                                         profile):
        raw_memory(memory_manager, "将遗忘的重要往事", 0.8, 11, 10.5)
        engine = make_engine(factory, memory_manager, profile)
        found = engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID)
        assert len(found) == 1
        assert found[0]["content"] == "将遗忘的重要往事"
        assert found[0]["decay_factor"] < 0.3
        assert 0 < found[0]["remaining_hours"] < 72

    def test_expired_excluded(self, factory, memory_manager, profile):
        raw_memory(memory_manager, "已过期", 0.8, 11, 12)  # remaining < 0
        engine = make_engine(factory, memory_manager, profile)
        assert engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID) == []

    def test_long_remaining_excluded(self, factory, memory_manager,
                                     profile):
        raw_memory(memory_manager, "还剩很久", 0.8, 40, 10.5)  # remaining 708h
        engine = make_engine(factory, memory_manager, profile)
        assert engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID) == []

    def test_low_importance_excluded(self, factory, memory_manager,
                                     profile):
        raw_memory(memory_manager, "不重要", 0.5, 11, 10.5)
        engine = make_engine(factory, memory_manager, profile)
        assert engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID) == []

    def test_fresh_memory_excluded(self, factory, memory_manager, profile):
        raw_memory(memory_manager, "还新着", 0.9, 30, 1)  # decay 高
        engine = make_engine(factory, memory_manager, profile)
        assert engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID) == []

    def test_sorted_by_decay_ascending(self, factory, memory_manager,
                                       profile):
        raw_memory(memory_manager, "较不紧迫", 0.8, 11, 10.5)
        raw_memory(memory_manager, "更紧迫", 0.8, 11, 10.9)
        engine = make_engine(factory, memory_manager, profile)
        found = engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID)
        assert [item["content"] for item in found] == ["更紧迫", "较不紧迫"]

    def test_sediment_types_excluded(self, factory, memory_manager,
                                     profile):
        raw_memory(memory_manager, "日快照不算记忆", 0.9, 11, 10.5,
                   memory_type="daily_snapshot")
        engine = make_engine(factory, memory_manager, profile)
        assert engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID) == []

    def test_other_pair_isolated(self, factory, memory_manager, profile):
        memory_manager.db.record(
            text="别人的记忆", memory_type="conversation_turn",
            namespace="other|user", importance=0.9, half_life=11 * DAY,
            created_at=utc_now().timestamp() - 10.5 * DAY,
            metadata={"character_id": "other", "user_id": "user"})
        engine = make_engine(factory, memory_manager, profile)
        assert engine.memory_manager.find_forgetting_important(
            CHARACTER_ID, USER_ID) == []

    def test_check_does_not_reinforce_memory(self, factory, memory_manager,
                                             profile):
        """衰减堆检查不得强化记忆（recall 默认会把 half_life ×1.2）。

        这是实测到的 DB 副作用：检查类检索必须 skip_reinforce，否则
        "即将遗忘"的判定会被自己的检索动作推迟。
        """
        rid = raw_memory(memory_manager, "别强化我", 0.8, 11, 10.5)
        before = memory_manager.db.list_memories(
            namespace=f"{CHARACTER_ID}|{USER_ID}")["memories"][0]["half_life"]
        memory_manager.find_forgetting_important(CHARACTER_ID, USER_ID)
        after = memory_manager.db.list_memories(
            namespace=f"{CHARACTER_ID}|{USER_ID}")["memories"][0]["half_life"]
        assert before == after == 11 * DAY
        _ = rid
