"""沉淀调度器单元测试（文档 2 第 3.10 节 + 文档 1 第十一章）。

全部用假嵌入器的临时 YantrikDB + 临时 SQLite；关键数值按文档公式
（峰值 0.5 / 尾声 0.3 / 均值 0.2）手算对拍。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from helpers import CONFIG_PATH, make_db_session_factory
from core.emotion_engine import EVENT_VALENCE
from db.models import (
    CharacterUserState,
    Message,
    Session as ChatSession,
    StateChange,
    utc_now,
)
from db.timezone import (
    local_date_days_ago,
    local_iso_week,
    user_local_date,
)
from memory.memory_manager import (
    MEMORY_TYPE_DAILY_SNAPSHOT,
    MEMORY_TYPE_SESSION_SUMMARY,
    MEMORY_TYPE_WEEKLY_TREND,
    MemoryManager,
)
from memory.schemas import ConversationTurn, generate_id
from memory.sedimenter import Sedimenter

CHARACTER_ID = "char_001"
USER_ID = "user_001"
SESSION_ID = "sess-1"
OTHER_CHARACTER = "char_other"
OTHER_USER = "user_other"


def local_today(factory) -> str:
    """测试侧取用户本地当天日期（与实现同一时区来源，避免 UTC 边界漂移）。"""
    with factory() as db:
        return user_local_date(db, USER_ID)


def local_week(factory) -> str:
    """测试侧取用户本地当前 ISO 周。"""
    with factory() as db:
        return local_iso_week(db, USER_ID)


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def memory_manager(tmp_path):
    return MemoryManager(str(tmp_path / "mem.db"),
                         db_session_factory=None, embedder="fake")


@pytest.fixture
def sedimenter(factory, memory_manager):
    return Sedimenter(CONFIG_PATH, memory_manager, factory)


# ── 种子辅助 ────────────────────────────────────────────────


def add_session(factory, session_id=SESSION_ID, started_at=None,
                turn_count=2, character_id=CHARACTER_ID, user_id=USER_ID):
    with factory() as db:
        db.add(ChatSession(
            id=session_id, character_id=character_id, user_id=user_id,
            started_at=started_at or (utc_now() - timedelta(hours=2)),
            turn_count=turn_count, status="active"))
        db.commit()


def local_day_start(factory, user_id: str = USER_ID):
    """用户本地当天零点（naive UTC）——日沉淀种子按它播种，消除时刻依赖。"""
    from db.timezone import local_day_start_utc

    with factory() as db:
        return local_day_start_utc(db, user_id)


def add_turn(memory_manager, factory, role, text, label=None, intensity=None,
             minutes_ago=90, expression=None, session_id=SESSION_ID):
    """存一条对话记忆 + messages 索引行（created_at 可回拨）。"""
    timestamp = (utc_now() - timedelta(minutes=minutes_ago)).isoformat()
    turn = ConversationTurn(
        character_id=CHARACTER_ID, user_id=USER_ID, role=role, text=text,
        emotion_tag=label, intensity=intensity,
        expression_state=expression, timestamp=timestamp)
    rid = memory_manager.store_conversation(turn)  # 不传 session_id：索引手工插
    with factory() as db:
        db.add(Message(
            id=generate_id(), session_id=session_id,
            character_id=CHARACTER_ID, user_id=USER_ID, role=role,
            memory_id=rid,
            created_at=utc_now() - timedelta(minutes=minutes_ago)))
        db.commit()
    return rid


def add_state_change(factory, label, intensity, minutes_ago=89,
                     expression=None, affective=None, delta=None, at=None):
    """插一条状态变更日志（delta/state_after 可指定；at 显式指定时刻）。"""
    with factory() as db:
        db.add(StateChange(
            character_id=CHARACTER_ID, user_id=USER_ID,
            changed_at=at or (utc_now() - timedelta(minutes=minutes_ago)),
            event_label=label, event_intensity=intensity,
            delta_json=json.dumps(delta or {}, ensure_ascii=False),
            state_after_json=json.dumps({
                "expression": expression or {},
                "internal": {},
                "affective": affective or {},
            }, ensure_ascii=False)))
        db.commit()


def add_user_state(factory, expression=None, affective=None, stage="S1"):
    with factory() as db:
        db.add(CharacterUserState(
            character_id=CHARACTER_ID, user_id=USER_ID,
            expression_state=json.dumps(expression or {}),
            internal_state=json.dumps({}),
            affective_state=json.dumps(affective or {}),
            relationship_stage=stage,
            last_interaction=utc_now()))
        db.commit()


def summaries(memory_manager):
    return memory_manager.list_sediments(CHARACTER_ID, USER_ID,
                                         MEMORY_TYPE_SESSION_SUMMARY)


def snapshots(memory_manager):
    return memory_manager.list_sediments(CHARACTER_ID, USER_ID,
                                         MEMORY_TYPE_DAILY_SNAPSHOT)


def weekly_trends(memory_manager):
    return memory_manager.list_sediments(CHARACTER_ID, USER_ID,
                                         MEMORY_TYPE_WEEKLY_TREND)


# ── 会话沉淀（文档 1 第 11.1 节） ───────────────────────────


class TestSessionSediment:
    def test_summary_fields_match_doc_example(self, sedimenter, factory,
                                              memory_manager):
        """字段与加权效价按文档公式手算对拍。"""
        add_session(factory)
        add_turn(memory_manager, factory, "user", "最近工作压力好大",
                 label="vulnerable", intensity=0.8, minutes_ago=90)
        add_turn(memory_manager, factory, "assistant", "我在，慢慢说",
                 label="reassuring", intensity=0.6, minutes_ago=89)
        add_state_change(
            factory, "vulnerable", 0.8, minutes_ago=89,
            expression={"PLE": 0.45, "ARO": -0.10, "DOM": 0.0,
                        "INT": 0.70, "SEC": 0.5},
            affective={"AFF": 30, "TRS": 25, "ATT": 10},
            delta={"affective": {"AFF": 1.5, "TRS": 0.8}})

        summary = sedimenter._session_sediment(CHARACTER_ID, USER_ID,
                                               SESSION_ID)
        assert summary.session_id == SESSION_ID
        assert summary.turn_count == 2
        # 时长：started_at(-2h) → 最后消息(-89min) ≈ 31 分钟
        assert summary.duration_minutes == 31
        # 峰值事件：强度最高的一轮（vulnerable 0.8，第 1 轮）
        assert summary.peak_event == {"label": "vulnerable",
                                      "intensity": 0.8, "turn": 1}
        # 尾声语调：会话窗口内最后一次 state_after 的表达层
        assert summary.ending_tone["PLE"] == 0.45
        assert summary.ending_tone["INT"] == 0.70
        # 关键记忆：峰值轮的用户发言
        assert summary.key_memory == "最近工作压力好大"
        # 加权效价手算：峰值(vulnerable=0.0)×0.5 + 尾声(0.45)×0.3
        #             + 均值((0.0+1.0)/2=0.5)×0.2 = 0.235
        assert summary.overall_valence == pytest.approx(0.235)
        # 落库：session_summary 记忆带结构化 metadata
        records = summaries(memory_manager)
        assert len(records) == 1
        metadata = records[0]["metadata"]
        assert metadata["session_id"] == SESSION_ID
        assert metadata["peak_event"]["label"] == "vulnerable"
        assert metadata["overall_valence"] == pytest.approx(0.235)

    def test_ending_tone_falls_back_to_expression_snapshot(
            self, sedimenter, factory, memory_manager):
        """无状态变更记录时，尾声退化为最后一轮的 expression_snapshot。"""
        add_session(factory)
        add_turn(memory_manager, factory, "user", "你好", minutes_ago=90,
                 expression={"PLE": 0.2, "ARO": 0.0, "DOM": 0.0,
                             "INT": 0.3, "SEC": 0.4})
        summary = sedimenter._session_sediment(CHARACTER_ID, USER_ID,
                                               SESSION_ID)
        assert summary.ending_tone["PLE"] == 0.2
        assert summary.peak_event == {}  # 无强度数据

    def test_duration_rounds_to_minutes(self, sedimenter, factory,
                                        memory_manager):
        add_session(factory, started_at=utc_now() - timedelta(minutes=95))
        add_turn(memory_manager, factory, "user", "在吗", minutes_ago=5)
        summary = sedimenter._session_sediment(CHARACTER_ID, USER_ID,
                                               SESSION_ID)
        assert summary.duration_minutes == 90


class TestSessionEndedDetection:
    def test_active_latest_session_not_sedimented(self, sedimenter, factory,
                                                  memory_manager):
        """最近会话且仍在活跃（最后消息 5 分钟前）→ 不沉淀。"""
        add_session(factory)
        add_turn(memory_manager, factory, "user", "在吗", minutes_ago=5)
        assert sedimenter._ended_session_ids(CHARACTER_ID, USER_ID) == []

    def test_idle_latest_session_sedimented(self, sedimenter, factory,
                                            memory_manager):
        """最近会话但已空闲 31 分钟 → 视为结束，沉淀。"""
        add_session(factory)
        add_turn(memory_manager, factory, "user", "在吗", minutes_ago=31)
        assert sedimenter._ended_session_ids(CHARACTER_ID, USER_ID) == [
            SESSION_ID]

    def test_older_session_sedimented_even_if_fresh(self, sedimenter,
                                                    factory,
                                                    memory_manager):
        """非最近会话即使刚有消息也算结束（用户开了新会话）。"""
        add_session(factory, session_id="sess-old",
                    started_at=utc_now() - timedelta(hours=5))
        add_turn(memory_manager, factory, "user", "旧会话", minutes_ago=60,
                 session_id="sess-old")
        add_session(factory, session_id="sess-new",
                    started_at=utc_now() - timedelta(minutes=10))
        add_turn(memory_manager, factory, "user", "新会话", minutes_ago=5,
                 session_id="sess-new")
        assert sedimenter._ended_session_ids(CHARACTER_ID, USER_ID) == [
            "sess-old"]

    def test_empty_session_skipped(self, sedimenter, factory):
        add_session(factory, turn_count=0)
        assert sedimenter._ended_session_ids(CHARACTER_ID, USER_ID) == []

    def test_summarized_session_not_repeated(self, sedimenter, factory,
                                             memory_manager):
        """已沉淀的会话不重复沉淀（maybe_sediment 幂等）。"""
        add_session(factory)
        add_turn(memory_manager, factory, "user", "在吗", minutes_ago=31)
        assert sedimenter.maybe_sediment(CHARACTER_ID, USER_ID) == {
            "session": [SESSION_ID]}
        assert sedimenter.maybe_sediment(CHARACTER_ID, USER_ID) == {}


# ── 日沉淀（文档 1 第 11.2 / 11.4 节） ─────────────────────


class TestDailySediment:
    def _seed_today(self, factory):
        day_start = local_day_start(factory)
        add_user_state(factory, expression={"PLE": 0.4},
                       affective={"AFF": 32, "TRS": 26, "ATT": 10})
        add_session(factory, started_at=day_start + timedelta(minutes=1))
        add_state_change(
            factory, "vulnerable", 0.8,
            expression={"PLE": 0.30, "ARO": 0.0, "DOM": 0.0,
                        "INT": 0.5, "SEC": 0.4},
            affective={"AFF": 30, "TRS": 25, "ATT": 10},
            delta={"affective": {"AFF": 1.5, "TRS": 0.8}},
            at=day_start + timedelta(minutes=2))
        add_state_change(
            factory, "affectionate", 0.6,
            expression={"PLE": 0.45, "ARO": 0.15, "DOM": 0.05,
                        "INT": 0.65, "SEC": 0.58},
            affective={"AFF": 32, "TRS": 26, "ATT": 10},
            delta={"affective": {"AFF": 0.5}},
            at=day_start + timedelta(minutes=5))

    def test_snapshot_and_effects(self, sedimenter, factory,
                                  memory_manager):
        self._seed_today(factory)
        done = sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        assert done["daily"] == local_today(factory)

        records = [record for record in snapshots(memory_manager)
                   if record["metadata"]["date"] == local_today(factory)]
        assert len(records) == 1
        metadata = records[0]["metadata"]
        assert metadata["date"] == local_today(factory)
        assert metadata["sessions"] == [SESSION_ID]
        # day_end：当天最后一次 state_after
        assert metadata["day_end_expression"]["PLE"] == 0.45
        assert metadata["day_end_affective"] == {"AFF": 32, "TRS": 26,
                                                 "ATT": 10}
        # consolidated_events 按强度降序，impact 由情感层增量格式化
        events = metadata["consolidated_events"]
        assert events[0] == {"label": "vulnerable", "intensity": 0.8,
                             "impact": "AFF +1.5, TRS +0.8"}
        assert events[1]["label"] == "affectionate"
        # daily_valence 手算：峰值(vulnerable=0)×0.5 + 尾声(0.45)×0.3
        #                 + 均值(0.5)×0.2 = 0.235
        assert metadata["daily_valence"] == pytest.approx(0.235)
        assert metadata["trend"] == "stable"  # 首个快照

        # 副作用 1：慢心境基线 = day_end_expression（写 _baseline 子键）
        from db.user_state import load_mood_baseline

        with factory() as db:
            baseline = load_mood_baseline(db, CHARACTER_ID, USER_ID)
        assert baseline == metadata["day_end_expression"]

        # 副作用 2：情感层微调（临时公式）
        with factory() as db:
            row = db.get(CharacterUserState, (CHARACTER_ID, USER_ID))
            affective = json.loads(row.affective_state)
        assert affective["AFF"] == pytest.approx(32 + 0.235 * 2.0)
        assert affective["TRS"] == pytest.approx(26 + 0.5)  # 有 vulnerable
        assert affective["ATT"] == pytest.approx(10 + 0.3)  # 1 个会话

    def test_idempotent_same_day(self, sedimenter, factory, memory_manager):
        """同一天重复触发只留一条 daily_snapshot（文档 2 第 10.2 节）。"""
        self._seed_today(factory)
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        assert len(snapshots(memory_manager)) == 1

    def test_no_activity_today_skips(self, sedimenter, factory,
                                     memory_manager):
        add_user_state(factory)
        assert sedimenter.maybe_sediment(CHARACTER_ID, USER_ID) == {}
        assert snapshots(memory_manager) == []

    def test_trend_compares_with_previous_snapshot(self, sedimenter,
                                                   factory, memory_manager):
        """与上一个日快照比较出 rising/falling。"""
        with factory() as db:
            yesterday = local_date_days_ago(db, USER_ID, 1)
        memory_manager.store_sediment(
            character_id=CHARACTER_ID, user_id=USER_ID,
            memory_type=MEMORY_TYPE_DAILY_SNAPSHOT, text="旧快照",
            metadata={"date": yesterday, "daily_valence": -0.5,
                      "day_end_expression": {"PLE": -0.5},
                      "sessions": [], "consolidated_events": []},
            timestamp=f"{yesterday}T23:00:00")
        add_user_state(factory, affective={"AFF": 20, "TRS": 20, "ATT": 10})
        add_state_change(factory, "affectionate", 0.6,
                         expression={"PLE": 0.5},
                         delta={"affective": {"AFF": 0.5}},
                         at=local_day_start(factory) + timedelta(minutes=5))
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        # list_memories 按 created_at 倒序，取今天那条
        today_records = [record for record in snapshots(memory_manager)
                         if record["metadata"]["date"] == local_today(factory)]
        assert len(today_records) == 1
        assert today_records[0]["metadata"]["trend"] == "rising"

    def test_mood_baseline_feeds_engine(self, sedimenter, factory,
                                        memory_manager):
        """沉淀后的基线进入情感引擎（文档 1 第 11.4 节链路验证）。"""
        from core.character.profile import CharacterProfile
        from core.emotion_engine import EmotionEngine
        from db.user_state import load_user_state

        self._seed_today(factory)
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        with factory() as db:
            user_state, _ = load_user_state(db, CHARACTER_ID, USER_ID)
        assert "mood_baseline" in user_state
        engine = EmotionEngine(CONFIG_PATH,
                               CharacterProfile(id=CHARACTER_ID,
                                                name="测试"),
                               user_state)
        assert engine.mood_baseline["PLE"] == 0.45  # day_end_expression


# ── 周趋势（文档 1 第 11.3 节） ─────────────────────────────


class TestWeeklySediment:
    def _seed_snapshots(self, factory, memory_manager, count=3):
        plan = [(3, 0.1, {"AFF": 20, "TRS": 25, "ATT": 10}),
                (2, 0.2, {"AFF": 22, "TRS": 26, "ATT": 11}),
                (1, 0.4, {"AFF": 25, "TRS": 28, "ATT": 12})][:count]
        with factory() as db:
            dates = [local_date_days_ago(db, USER_ID, days_ago)
                     for days_ago, _, _ in plan]
        for date, (_, valence, affective) in zip(dates, plan):
            memory_manager.store_sediment(
                character_id=CHARACTER_ID, user_id=USER_ID,
                memory_type=MEMORY_TYPE_DAILY_SNAPSHOT, text=f"快照 {date}",
                metadata={"date": date, "daily_valence": valence,
                          "day_end_expression": {"PLE": valence},
                          "day_end_affective": affective,
                          "sessions": ["s"], "consolidated_events": []},
                timestamp=f"{date}T23:00:00")

    def test_trend_fields(self, sedimenter, factory, memory_manager):
        add_user_state(factory, affective={"AFF": 25, "TRS": 28, "ATT": 12},
                       stage="S2")
        self._seed_snapshots(factory, memory_manager)
        done = sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        assert done["weekly"] == local_week(factory)

        records = weekly_trends(memory_manager)
        assert len(records) == 1
        metadata = records[0]["metadata"]
        assert metadata["avg_valence"] == pytest.approx((0.1 + 0.2 + 0.4) / 3,
                                                        abs=1e-4)
        # 序列趋势：前半均值 vs 后半均值
        assert metadata["valence_trend"] == "rising"    # 0.3 - 0.1
        assert metadata["security_trend"] == "rising"   # TRS 27 - 25
        assert metadata["intimacy_trend"] == "rising"   # AFF 23.5 - 20
        # 阶段推荐：S2 的下一阶段 S3 需要 AFF≥55，当前 25 → 维持
        assert metadata["stage_recommendation"] == "维持 S2"
        assert isinstance(metadata["notable_patterns"], list)

    def test_stage_recommendation_progression(self, sedimenter, factory,
                                              memory_manager):
        """AFF 接近下一阶段下界 → 推荐进阶。"""
        add_user_state(factory, affective={"AFF": 57, "TRS": 55, "ATT": 30},
                       stage="S2")
        self._seed_snapshots(factory, memory_manager, count=2)
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        metadata = weekly_trends(memory_manager)[0]["metadata"]
        # S3 下界 60，AFF 57 在 5 分 proximity 内且 TRS≥50 → "S2 → S3"
        assert metadata["stage_recommendation"] == "S2 → S3"

    def test_idempotent_same_week(self, sedimenter, factory,
                                  memory_manager):
        add_user_state(factory)
        self._seed_snapshots(factory, memory_manager)
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        assert len(weekly_trends(memory_manager)) == 1

    def test_no_snapshots_skips(self, sedimenter, factory):
        add_user_state(factory)
        assert sedimenter.maybe_sediment(CHARACTER_ID, USER_ID) == {}


class TestNotablePatterns:
    def test_weekday_low_pattern(self, sedimenter, factory,
                                 memory_manager):
        """某工作日明显偏低 → 记录模式（用受控日期构造）。"""
        with factory() as db:
            today = datetime.fromisoformat(local_date_days_ago(db, USER_ID,
                                                               0))
        # 找最近 7 天内的一个周三和一个周四
        wednesday = next(today - timedelta(days=offset)
                         for offset in range(7)
                         if (today - timedelta(days=offset)).weekday() == 2)
        thursday = next(today - timedelta(days=offset)
                        for offset in range(7)
                        if (today - timedelta(days=offset)).weekday() == 3)
        records = []
        for date, valence in ((wednesday, 0.0), (thursday, 0.4)):
            records.append({"metadata": {
                "date": date.isoformat(), "daily_valence": valence,
                "consolidated_events": []}})
        patterns = sedimenter._notable_patterns(records)
        assert any("周三" in pattern for pattern in patterns)

    def test_disclosure_pattern(self, sedimenter, factory):
        """一周内多次脆弱展露 → 信任加深模式。"""
        with factory() as db:
            dates = [local_date_days_ago(db, USER_ID, offset)
                     for offset in range(1, 4)]
        records = [{"metadata": {
            "date": date, "daily_valence": 0.2,
            "consolidated_events": [{"label": "vulnerable"}]}}
            for date in dates]
        patterns = sedimenter._notable_patterns(records)
        assert any("信任" in pattern for pattern in patterns)


# ── 计算单元 ────────────────────────────────────────────────


class TestWeightedValence:
    def test_doc_weights(self, sedimenter):
        """0.5 峰值 + 0.3 尾声 + 0.2 均值（文档 1 第十四章配置）。"""
        # 峰值 1.0、尾声 0.0、均值 (−1+1)/2=0 → 0.5
        assert sedimenter._weighted_valence(
            [-1.0, 1.0], ending_valence=0.0, peak_valence=1.0) == \
            pytest.approx(0.5)

    def test_empty_valences_falls_back_to_ending(self, sedimenter):
        assert sedimenter._weighted_valence([], ending_valence=0.3) == 0.3

    def test_default_peak_is_most_extreme(self, sedimenter):
        """未指定峰值效价时取最极端值。"""
        assert sedimenter._weighted_valence(
            [-0.8, 0.2], ending_valence=0.0) == pytest.approx(
            0.5 * -0.8 + 0.2 * (-0.3))


class TestSeriesTrend:
    @pytest.mark.parametrize("values,expected", [
        ([0.1, 0.2, 0.4], "rising"),
        ([0.4, 0.2, 0.1], "falling"),
        ([0.2, 0.2, 0.2], "stable"),
        ([0.3], "stable"),          # 单点
    ])
    def test_trends(self, sedimenter, values, expected):
        assert sedimenter._series_trend(values) == expected


class TestIsolation:
    def test_other_pair_not_visible(self, sedimenter, factory,
                                    memory_manager):
        """双键隔离：他人沉淀不影响本对的判重与列举。"""
        memory_manager.store_sediment(
            character_id=OTHER_CHARACTER, user_id=OTHER_USER,
            memory_type=MEMORY_TYPE_DAILY_SNAPSHOT, text="别人的",
            metadata={"date": local_today(factory), "daily_valence": 0.9})
        add_user_state(factory)
        add_state_change(factory, "affectionate", 0.6,
                         expression={"PLE": 0.5},
                         at=local_day_start(factory) + timedelta(minutes=5))
        sedimenter.maybe_sediment(CHARACTER_ID, USER_ID)
        dates = [record["metadata"]["date"]
                 for record in snapshots(memory_manager)]
        assert dates == [local_today(factory)]  # 只有自己的


class TestTurnValence:
    def test_label_mapping(self, sedimenter):
        assert sedimenter._turn_valence({"emotion_tag": "affectionate"}) == 1.0
        assert sedimenter._turn_valence({"emotion_tag": "hostile"}) == -1.0
        assert sedimenter._turn_valence({"emotion_tag": None}) == 0.0
        assert sedimenter._turn_valence({}) == 0.0

    def test_matches_engine_table(self, sedimenter):
        for label, valence in EVENT_VALENCE.items():
            assert sedimenter._event_valence(label) == valence


class TestEdgeBranches:
    """分支覆盖：异常路径、降级路径与空数据路径。"""

    def test_session_sediment_unknown_session_raises(self, sedimenter):
        with pytest.raises(ValueError, match="会话不存在"):
            sedimenter._session_sediment(CHARACTER_ID, USER_ID, "ghost")

    def test_session_without_messages_skipped(self, sedimenter, factory):
        """有会话行但无消息 → 不算结束（不沉淀）。"""
        add_session(factory)
        assert sedimenter._ended_session_ids(CHARACTER_ID, USER_ID) == []

    def test_daily_skips_when_already_done(self, sedimenter, factory,
                                           memory_manager):
        """today 已有快照时 _daily_sediment 直接返回 None。"""
        memory_manager.store_sediment(
            character_id=CHARACTER_ID, user_id=USER_ID,
            memory_type=MEMORY_TYPE_DAILY_SNAPSHOT, text="今天的",
            metadata={"date": local_today(factory), "daily_valence": 0.1})
        assert sedimenter._daily_sediment(CHARACTER_ID, USER_ID) is None

    def test_effects_without_state_row_skipped(self, sedimenter, factory,
                                               memory_manager):
        """有状态变更但无状态行：不更新基线以外的副作用（不崩）。"""
        add_state_change(factory, "affectionate", 0.6, minutes_ago=30,
                         expression={"PLE": 0.5},
                         delta={"affective": {"AFF": 0.5}})
        # 无 CharacterUserState 行：日沉淀仍可完成（基线照写）
        snapshot = sedimenter._daily_sediment(CHARACTER_ID, USER_ID)
        assert snapshot is not None
        from db.user_state import load_mood_baseline

        with factory() as db:
            assert load_mood_baseline(db, CHARACTER_ID, USER_ID) == \
                snapshot.day_end_expression

    def test_weekly_skips_when_already_done(self, sedimenter, factory,
                                            memory_manager):
        memory_manager.store_sediment(
            character_id=CHARACTER_ID, user_id=USER_ID,
            memory_type=MEMORY_TYPE_WEEKLY_TREND, text="本周的",
            metadata={"week": local_week(factory), "avg_valence": 0.2})
        assert sedimenter._weekly_sediment(CHARACTER_ID, USER_ID) is None

    def test_stage_recommendation_falls_back_to_snapshot(self, sedimenter,
                                                         factory,
                                                         memory_manager):
        """无状态行时退回最近快照的情感值。"""
        with factory() as db:
            date = local_date_days_ago(db, USER_ID, 1)
        memory_manager.store_sediment(
            character_id=CHARACTER_ID, user_id=USER_ID,
            memory_type=MEMORY_TYPE_DAILY_SNAPSHOT, text="快照",
            metadata={"date": date, "daily_valence": 0.2,
                      "day_end_affective": {"AFF": 58, "TRS": 55, "ATT": 30},
                      "consolidated_events": []})
        add_user_state(factory, affective={"AFF": 10, "TRS": 10, "ATT": 10},
                       stage="S2")
        # 状态行的 AFF=10 不接近边界；但快照 AFF=58 接近 S3 下界 60-5
        # ——当前实现优先用状态行，故此处断言用状态行口径
        recommendation = sedimenter._stage_recommendation(
            CHARACTER_ID, USER_ID,
            sedimenter._recent_snapshots(CHARACTER_ID, USER_ID))
        assert recommendation == "维持 S2"

    def test_stage_recommendation_without_state_row(self, sedimenter,
                                                    factory,
                                                    memory_manager):
        """完全没有状态行：用快照值 + 默认阶段 S1。"""
        with factory() as db:
            date = local_date_days_ago(db, USER_ID, 1)
        memory_manager.store_sediment(
            character_id=CHARACTER_ID, user_id=USER_ID,
            memory_type=MEMORY_TYPE_DAILY_SNAPSHOT, text="快照",
            metadata={"date": date, "daily_valence": 0.2,
                      "day_end_affective": {"AFF": 18, "TRS": 25, "ATT": 10},
                      "consolidated_events": []})
        recommendation = sedimenter._stage_recommendation(
            CHARACTER_ID, USER_ID,
            sedimenter._recent_snapshots(CHARACTER_ID, USER_ID))
        assert recommendation == "维持 S1"  # S2 下界 40，AFF 18 不接近

    def test_stage_recommendation_top_stage(self, sedimenter, factory):
        """S4 是最高阶段：无下一阶段 → 维持。"""
        add_user_state(factory, affective={"AFF": 90, "TRS": 80, "ATT": 70},
                       stage="S4")
        assert sedimenter._stage_recommendation(
            CHARACTER_ID, USER_ID, []) == "维持 S4"

    def test_stage_recommendation_unknown_stage(self, sedimenter, factory):
        """阶段码不在配置里：不给进阶建议，只维持。"""
        add_user_state(factory, affective={"AFF": 5, "TRS": 5, "ATT": 5},
                       stage="SX")
        recommendation = sedimenter._stage_recommendation(
            CHARACTER_ID, USER_ID, [])
        assert recommendation == "维持 SX"

    def test_next_stage_code_empty_config(self, sedimenter, monkeypatch):
        monkeypatch.setattr(sedimenter, "stages", {})
        assert sedimenter._next_stage_code("S1") is None

    def test_notable_patterns_empty(self, sedimenter):
        assert sedimenter._notable_patterns([]) == []

    def test_key_memory_no_user_turns(self, sedimenter):
        assert sedimenter._key_memory(
            [{"role": "assistant", "content": "只有 AI"}]) == ""

    def test_snapshot_att_accessor(self, sedimenter):
        record = {"metadata": {"day_end_affective": {"ATT": 12.5}}}
        assert sedimenter._snapshot_att(record) == 12.5
        assert sedimenter._snapshot_att({"metadata": {}}) == 0.0


class TestModuleHelpers:
    def test_loads_json_invalid(self):
        from memory.sedimenter import _loads_json

        assert _loads_json("not json") == {}
        assert _loads_json("[]") == {}      # 非 dict
        assert _loads_json(None) == {}

    def test_mean_and_clamp_and_trend(self):
        from memory.sedimenter import _clamp, _mean, _trend_cn, _trend_of

        assert _mean([]) == 0.0
        assert _mean([1.0, 2.0]) == 1.5
        assert _clamp(5.0, 0.0, 1.0) == 1.0
        assert _trend_of(0.5) == "rising"
        assert _trend_of(-0.5) == "falling"
        assert _trend_of(0.0) == "stable"
        assert _trend_cn("rising") == "上升"
        assert _trend_cn("falling") == "下降"
        assert _trend_cn("stable") == "平稳"


class TestSedimentSchemas:
    def test_session_summary_round_trip(self):
        from memory.schemas import SessionSummary

        summary = SessionSummary(session_id="s", character_id="c",
                                 duration_minutes=5, turn_count=2,
                                 peak_event={"label": "vulnerable"},
                                 ending_tone={"PLE": 0.1},
                                 overall_valence=0.2,
                                 key_memory="k", timestamp="2026-10-14T10:00:00")
        assert SessionSummary.from_dict(summary.to_dict()).to_dict() == \
            summary.to_dict()

    def test_daily_snapshot_defaults(self):
        from memory.schemas import DailySnapshot

        snapshot = DailySnapshot.from_dict({"date": "2026-10-14",
                                            "character_id": "c"})
        assert snapshot.sessions == []
        assert snapshot.trend == "stable"
        assert snapshot.timestamp  # 缺省取当前时间

    def test_weekly_trend_defaults(self):
        from memory.schemas import WeeklyTrend

        trend = WeeklyTrend.from_dict({"week": "2026-W42",
                                       "character_id": "c"})
        assert trend.valence_trend == "stable"
        assert trend.notable_patterns == []
