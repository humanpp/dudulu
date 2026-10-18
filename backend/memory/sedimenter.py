"""沉淀调度器（文档 2 第 3.10 节 + 文档 1 第十一章）。

职责：在合适时机把情绪沉淀为情感——
- 会话沉淀（11.1）：峰值 + 尾声加权（配置 peak/ending/average_weight）
- 日沉淀（11.2）：汇总当天、更新慢心境基线（11.4）、微调 AFF/TRS/ATT
- 周趋势（11.3）：趋势分析与关系阶段推荐

每个 (character_id, user_id) 对独立进行；三条沉淀的产物都以
memory_type = session_summary / daily_snapshot / weekly_trend 存入
YantrikDB（文档 2 第 5.1 节类型枚举），结构化字段进记忆 metadata。

幂等（文档 2 第 10.2 节）：写入前按 list_memories 判重——同一会话的
session_summary、同一天的 daily_snapshot、同一 ISO 周的 weekly_trend
只允许一条，重复触发直接跳过。（YantrikDB 的 record 自带
idempotency_key，但实测与 Python 侧嵌入器不兼容，故用前置判重。）

时间基准：库内存 naive UTC；日/周边界按**用户本地时区**判定
（users.timezone，缺省 Asia/Shanghai，见 db/timezone.py——Phase 8a
裁决①；Phase 7 曾用 UTC，本次对齐）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import yaml
from sqlalchemy.orm import Session, sessionmaker

from core.emotion_engine import EVENT_VALENCE
from db.models import (
    CharacterUserState,
    Message,
    Session as ChatSession,
    StateChange,
)
from db.timezone import (
    local_date_days_ago,
    local_day_start_utc,
    local_iso_week,
    user_local_date,
)
from db.user_state import save_mood_baseline
from memory.memory_manager import (
    MEMORY_TYPE_DAILY_SNAPSHOT,
    MEMORY_TYPE_SESSION_SUMMARY,
    MEMORY_TYPE_WEEKLY_TREND,
    MemoryManager,
)
from memory.schemas import DailySnapshot, SessionSummary, WeeklyTrend, now_iso

logger = logging.getLogger(__name__)

# 会话空闲判定：最后一条消息超过该时长视为会话结束（文档未定义，待调优）
SESSION_IDLE_MINUTES = 30.0
# 趋势判定阈值：|delta| 超过才算 rising/falling（待调优）
TREND_THRESHOLD = 0.05
# 周趋势「某工作日情绪偏低」模式的距离阈值（待调优）
WEEKDAY_PATTERN_GAP = 0.1
# 日沉淀合并事件的最大条数（待调优）
MAX_CONSOLIDATED_EVENTS = 5
# 情感层微调系数（文档 1 第 11.4 节"根据 consolidated_events 和
# daily_valence 微调"未给公式，以下三个为临时值，待调优）
AFF_VALENCE_SCALE = 2.0          # AFF += daily_valence × 该系数（±2 分/天）
TRS_VULNERABLE_BONUS = 0.5       # 当天有 vulnerable/reassuring 事件的 TRS 加成
ATT_SESSION_BONUS = 0.3          # 每个当天会话的 ATT 加成
# 阶段推荐接近阈值：AFF 距下一阶段下界 5 分以内且 TRS/ATT 达标（待调优）
STAGE_PROXIMITY = 5.0

WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 提升信任的事件标签（脆弱展露与被安抚）
TRUST_BUILDING_LABELS = ("vulnerable", "reassuring")


def utc_today() -> str:
    """当前 UTC 日期字符串（YYYY-MM-DD）。

    仅作测试与调试参照；沉淀判定一律用用户本地日期（见 _local_today）。
    """
    return datetime.now(timezone.utc).date().isoformat()


class Sedimenter:
    """职责：在合适时机触发会话沉淀、日沉淀、周沉淀（文档 2 第 3.10 节）。"""

    def __init__(self, config_path: str,
                 memory_manager: MemoryManager,
                 db_session_factory: sessionmaker[Session]):
        """初始化沉淀器。

        输入：数值配置路径（读 sedimentation 权重与开关、affective stages）、
            MemoryManager、SQLite 会话工厂；副作用：读配置文件。
        """
        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        sediment_cfg = config.get("sedimentation") or {}
        self.session_enabled = bool(sediment_cfg.get("session_summary", True))
        self.daily_enabled = bool(sediment_cfg.get("daily_snapshot", True))
        self.weekly_enabled = bool(sediment_cfg.get("weekly_trend", True))
        self.peak_weight = float(sediment_cfg.get("peak_weight", 0.5))
        self.ending_weight = float(sediment_cfg.get("ending_weight", 0.3))
        self.average_weight = float(sediment_cfg.get("average_weight", 0.2))
        self.stages: dict = (config.get("affective_layer") or {}).get(
            "stages") or {}
        self.memory_manager = memory_manager
        self.db_session_factory = db_session_factory

    # ── 用户本地时间辅助（裁决①：日/周边界按用户时区） ──────

    def _local_today(self, user_id: str) -> str:
        """用户本地当天日期（YYYY-MM-DD）。"""
        with self.db_session_factory() as db:
            return user_local_date(db, user_id)

    def _local_week(self, user_id: str) -> str:
        """用户本地当前 ISO 周（YYYY-Www）。"""
        with self.db_session_factory() as db:
            return local_iso_week(db, user_id)

    # ── 入口（文档 2 第 3.10 节 maybe_sediment 三分支） ──────

    def maybe_sediment(self, character_id: str, user_id: str) -> dict:
        """检查并执行到期的沉淀，返回本次实际执行的动作（测试/日志用）。

        输入：双键；输出：{"session": [session_id...], "daily": date,
            "weekly": week}（未执行的分支不出现在结果里）；副作用：见各分支。
        """
        done: dict = {}
        if self.session_enabled:
            ended = self._ended_session_ids(character_id, user_id)
            for session_id in ended:
                self._session_sediment(character_id, user_id, session_id)
            if ended:
                done["session"] = ended
        if self.daily_enabled and self._is_new_day(character_id, user_id):
            snapshot = self._daily_sediment(character_id, user_id)
            if snapshot is not None:
                done["daily"] = snapshot.date
        if self.weekly_enabled and self._is_new_week(character_id, user_id):
            trend = self._weekly_sediment(character_id, user_id)
            if trend is not None:
                done["weekly"] = trend.week
        return done

    # ── 会话沉淀（文档 1 第 11.1 节） ───────────────────────

    def _ended_session_ids(self, character_id: str,
                           user_id: str) -> list[str]:
        """找出「已结束且未沉淀」的会话 id 列表。

        判定：有轮次、无 session_summary、且（不是最近会话 或 最后一条
        消息距现在超过 SESSION_IDLE_MINUTES）。最近会话且仍活跃 → 不沉淀
        （用户可能还在聊）。
        """
        with self.db_session_factory() as db:
            sessions = db.execute(
                _select(ChatSession).where(
                    ChatSession.character_id == character_id,
                    ChatSession.user_id == user_id,
                ).order_by(ChatSession.started_at.desc())
            ).scalars().all()
            if not sessions:
                return []
            last_message_at = db.execute(
                _select(_max(Message.created_at)).where(
                    Message.character_id == character_id,
                    Message.user_id == user_id)
            ).scalar_one_or_none()
            per_session_last = {}
            for session in sessions:
                per_session_last[session.id] = db.execute(
                    _select(_max(Message.created_at)).where(
                        Message.session_id == session.id)
                ).scalar_one_or_none()
        summarized = self._summarized_session_ids(character_id, user_id)
        # 库里的时间是 naive UTC（utc_now 同基准），这里去掉 tzinfo 再比较
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        ended: list[str] = []
        for index, session in enumerate(sessions):
            if session.turn_count <= 0 or session.id in summarized:
                continue
            session_last = per_session_last.get(session.id)
            if session_last is None:
                continue  # 没有消息的会话不沉淀
            idle = (now - session_last) > timedelta(minutes=SESSION_IDLE_MINUTES)
            if idle or index > 0:
                ended.append(session.id)
        _ = last_message_at  # 保留查询结果供将来扩展（当前逻辑按会话逐个判断）
        return ended

    def _summarized_session_ids(self, character_id: str,
                                user_id: str) -> set[str]:
        """已沉淀的会话 id 集合（判重用）。"""
        records = self.memory_manager.list_sediments(
            character_id, user_id, MEMORY_TYPE_SESSION_SUMMARY)
        return {(record.get("metadata") or {}).get("session_id")
                for record in records}

    def _session_sediment(self, character_id: str, user_id: str,
                          session_id: str) -> SessionSummary:
        """会话沉淀：峰值 + 尾声加权（文档 1 第 11.1 节）。

        输入：双键 + 会话 id；输出：SessionSummary；副作用：写 YantrikDB。
        """
        with self.db_session_factory() as db:
            session = db.get(ChatSession, session_id)
            if session is None:
                raise ValueError(f"会话不存在: {session_id}")
            started_at = session.started_at
            turn_count = session.turn_count
            messages = db.execute(
                _select(Message).where(Message.session_id == session_id)
                .order_by(Message.created_at)
            ).scalars().all()
            last_at = messages[-1].created_at if messages else started_at
            changes = db.execute(
                _select(StateChange).where(
                    StateChange.character_id == character_id,
                    StateChange.user_id == user_id,
                    StateChange.changed_at >= started_at,
                    StateChange.changed_at <= last_at + timedelta(minutes=1),
                ).order_by(StateChange.id)
            ).scalars().all()

        turns = []
        for message in messages:
            memory = self.memory_manager.get_memory(message.memory_id)
            if memory is None:
                continue
            turns.append(memory)

        peak_event = self._peak_event(turns)
        ending_tone = self._ending_tone(changes, turns)
        summary = SessionSummary(
            session_id=session_id,
            character_id=character_id,
            duration_minutes=self._duration_minutes(started_at, last_at),
            turn_count=turn_count,
            peak_event=peak_event,
            ending_tone=ending_tone,
            overall_valence=self._weighted_valence(
                [self._turn_valence(turn) for turn in turns],
                float(ending_tone.get("PLE", 0.0)),
                peak_valence=self._event_valence(peak_event.get("label"))),
            key_memory=self._key_memory(turns),
        )
        self._store_summary(summary, user_id)
        logger.info("会话沉淀完成: %s (%s 轮, valence=%s)",
                    session_id, summary.turn_count, summary.overall_valence)
        return summary

    def _store_summary(self, summary: SessionSummary,
                       user_id: str) -> str:
        """会话摘要落库（importance 取峰值强度，valence 取整体效价）。"""
        peak_intensity = summary.peak_event.get("intensity")
        text = (f"会话小结：{summary.key_memory or '（无关键内容）'}"
                f"（{summary.turn_count} 轮，{summary.duration_minutes} 分钟，"
                f"整体效价 {summary.overall_valence:+.2f}）")
        return self.memory_manager.store_sediment(
            character_id=summary.character_id,
            user_id=user_id,
            memory_type=MEMORY_TYPE_SESSION_SUMMARY,
            text=text,
            metadata=summary.to_dict(),
            importance=float(peak_intensity) if peak_intensity is not None
            else 0.5,
            valence=summary.overall_valence,
            timestamp=summary.timestamp,
        )

    # ── 日沉淀（文档 1 第 11.2 / 11.4 节） ──────────────────

    def _is_new_day(self, character_id: str, user_id: str) -> bool:
        """用户本地今天是否还没沉淀过（无日快照或最新快照不是今天）。"""
        latest = self._latest_sediment(character_id, user_id,
                                       MEMORY_TYPE_DAILY_SNAPSHOT)
        if latest is None:
            return True
        return (latest.get("metadata") or {}).get("date") != self._local_today(
            user_id)

    def _daily_sediment(self, character_id: str,
                        user_id: str) -> DailySnapshot | None:
        """日沉淀：汇总当天所有会话，更新慢心境和情感层。

        输入：双键；输出：DailySnapshot（今天无互动或已沉淀时返回 None）；
        副作用：写 daily_snapshot 记忆、更新 mood_baseline、微调情感层。
        """
        today = self._local_today(user_id)
        if not self._is_new_day(character_id, user_id):
            logger.info("日沉淀幂等跳过：%s %s 今天已沉淀", character_id, today)
            return None
        with self.db_session_factory() as db:
            # 当天 = 用户本地零点起（换算为 naive UTC 与库时间比较）
            day_start = local_day_start_utc(db, user_id)
            changes = db.execute(
                _select(StateChange).where(
                    StateChange.character_id == character_id,
                    StateChange.user_id == user_id,
                    StateChange.changed_at >= day_start,
                ).order_by(StateChange.id)
            ).scalars().all()
            if not changes:
                return None  # 今天没有互动，无可沉淀
            sessions_today = db.execute(
                _select(ChatSession.id).where(
                    ChatSession.character_id == character_id,
                    ChatSession.user_id == user_id,
                    ChatSession.started_at >= day_start,
                )
            ).scalars().all()
            row = db.get(CharacterUserState, (character_id, user_id))

        last_state = _loads_json(changes[-1].state_after_json)
        day_end_expression = dict(last_state.get("expression") or {})
        day_end_internal = dict(last_state.get("internal") or {})
        day_end_affective = {dim: last_state.get("affective", {}).get(dim)
                             for dim in ("AFF", "TRS", "ATT")
                             if last_state.get("affective", {}).get(dim)
                             is not None}
        events = [(change.event_label, change.event_intensity,
                   _loads_json(change.delta_json)) for change in changes]
        valences = [self._event_valence(label)
                    for label, _, _ in events if label]
        peak_label = max(
            (event for event in events if event[0]),
            key=lambda event: float(event[1] or 0.0),
            default=(None, None, None))[0]
        daily_valence = self._weighted_valence(
            valences, float(day_end_expression.get("PLE", 0.0)),
            peak_valence=self._event_valence(peak_label))
        snapshot = DailySnapshot(
            date=today,
            character_id=character_id,
            sessions=list(sessions_today),
            day_end_expression=day_end_expression,
            day_end_internal=day_end_internal,
            day_end_affective=day_end_affective,
            consolidated_events=self._consolidated_events(events),
            daily_valence=daily_valence,
            trend=self._daily_trend(character_id, user_id, daily_valence),
        )
        self._store_snapshot(snapshot, user_id)
        self._apply_sediment_effects(character_id, user_id, snapshot, row)
        logger.info("日沉淀完成: %s %s (valence=%s, trend=%s)",
                    character_id, today, snapshot.daily_valence, snapshot.trend)
        return snapshot

    def _store_snapshot(self, snapshot: DailySnapshot,
                        user_id: str) -> str:
        """日快照落库（importance 取当天事件强度均值）。"""
        intensities = [event.get("intensity") or 0.0
                       for event in snapshot.consolidated_events]
        importance = (sum(intensities) / len(intensities)) if intensities else 0.5
        text = (f"日快照 {snapshot.date}：{len(snapshot.sessions)} 个会话，"
                f"当日效价 {snapshot.daily_valence:+.2f}，趋势 {snapshot.trend}")
        return self.memory_manager.store_sediment(
            character_id=snapshot.character_id,
            user_id=user_id,
            memory_type=MEMORY_TYPE_DAILY_SNAPSHOT,
            text=text,
            metadata=snapshot.to_dict(),
            importance=round(importance, 4),
            valence=snapshot.daily_valence,
            timestamp=snapshot.timestamp,
        )

    def _apply_sediment_effects(self, character_id: str, user_id: str,
                                snapshot: DailySnapshot,
                                row: CharacterUserState | None) -> None:
        """沉淀对后续对话的影响（文档 1 第 11.4 节）。

        - 慢心境基线更新为 day_end_expression（写 expression_state 的
          `_baseline` 子键，Phase 7 裁决的持久化形态）
        - 情感层按 consolidated_events 与 daily_valence 微调（临时公式）
        """
        if snapshot.day_end_expression:
            with self.db_session_factory() as db:
                save_mood_baseline(db, character_id, user_id,
                                   snapshot.day_end_expression)
                db.commit()
        if row is None:
            return
        labels = {event.get("label") for event in snapshot.consolidated_events}
        trust_building = bool(labels & set(TRUST_BUILDING_LABELS))
        with self.db_session_factory() as db:
            state_row = db.get(CharacterUserState, (character_id, user_id))
            if state_row is None:
                return
            affective = _loads_json(state_row.affective_state) or {}
            affective["AFF"] = _clamp(
                affective.get("AFF", 0.0)
                + snapshot.daily_valence * AFF_VALENCE_SCALE, 0.0, 100.0)
            if trust_building:
                affective["TRS"] = _clamp(
                    affective.get("TRS", 0.0) + TRS_VULNERABLE_BONUS,
                    0.0, 100.0)
            if snapshot.sessions:
                affective["ATT"] = _clamp(
                    affective.get("ATT", 0.0)
                    + ATT_SESSION_BONUS * len(snapshot.sessions), 0.0, 100.0)
            state_row.affective_state = json.dumps(affective, ensure_ascii=False)
            db.commit()

    def _daily_trend(self, character_id: str, user_id: str,
                     current_valence: float) -> str:
        """当日趋势：与上一个日快照的 daily_valence 比较（首个快照记 stable）。"""
        previous = self._previous_snapshot(character_id, user_id)
        if previous is None:
            return "stable"
        previous_valence = float(
            (previous.get("metadata") or {}).get("daily_valence", 0.0))
        return _trend_of(current_valence - previous_valence)

    def _previous_snapshot(self, character_id: str,
                           user_id: str) -> dict | None:
        """用户本地今天之前的最近一个日快照（趋势比较用）。"""
        records = self.memory_manager.list_sediments(
            character_id, user_id, MEMORY_TYPE_DAILY_SNAPSHOT)
        today = self._local_today(user_id)
        dated = [record for record in records
                 if (record.get("metadata") or {}).get("date")
                 and record["metadata"]["date"] < today]
        if not dated:
            return None
        return max(dated, key=lambda record: record["metadata"]["date"])

    # ── 周趋势（文档 1 第 11.3 节） ─────────────────────────

    def _is_new_week(self, character_id: str, user_id: str) -> bool:
        """用户本地当前 ISO 周是否还没沉淀过。"""
        latest = self._latest_sediment(character_id, user_id,
                                       MEMORY_TYPE_WEEKLY_TREND)
        if latest is None:
            return True
        return (latest.get("metadata") or {}).get("week") != self._local_week(
            user_id)

    def _weekly_sediment(self, character_id: str,
                         user_id: str) -> WeeklyTrend | None:
        """周沉淀：趋势分析，关系阶段推荐。

        输入：双键；输出：WeeklyTrend（无日快照或本周已沉淀返回 None）；
        副作用：写 weekly_trend 记忆。
        """
        week = self._local_week(user_id)
        if not self._is_new_week(character_id, user_id):
            logger.info("周沉淀幂等跳过：%s %s 本周已沉淀", character_id, week)
            return None
        snapshots = self._recent_snapshots(character_id, user_id, days=7)
        if not snapshots:
            return None
        trend = WeeklyTrend(
            week=week,
            character_id=character_id,
            avg_valence=round(_mean(
                [self._snapshot_valence(record) for record in snapshots]), 4),
            valence_trend=self._series_trend(
                [self._snapshot_valence(record) for record in snapshots]),
            security_trend=self._series_trend(
                [self._snapshot_trs(record) for record in snapshots]),
            intimacy_trend=self._series_trend(
                [self._snapshot_aff(record) for record in snapshots]),
            stage_recommendation=self._stage_recommendation(
                character_id, user_id, snapshots),
            notable_patterns=self._notable_patterns(snapshots),
        )
        self.memory_manager.store_sediment(
            character_id=character_id,
            user_id=user_id,
            memory_type=MEMORY_TYPE_WEEKLY_TREND,
            text=(f"周趋势 {week}：平均效价 {trend.avg_valence:+.2f}，"
                  f"效价{_trend_cn(trend.valence_trend)}，"
                  f"阶段建议 {trend.stage_recommendation or '维持'}"),
            metadata=trend.to_dict(),
            importance=0.6,
            valence=trend.avg_valence,
            timestamp=trend.timestamp,
        )
        logger.info("周沉淀完成: %s %s", character_id, week)
        return trend

    def _recent_snapshots(self, character_id: str, user_id: str,
                          days: int = 7) -> list[dict]:
        """最近 N 天（用户本地）的日快照（按日期升序）。"""
        with self.db_session_factory() as db:
            cutoff = local_date_days_ago(db, user_id, days)
        records = self.memory_manager.list_sediments(
            character_id, user_id, MEMORY_TYPE_DAILY_SNAPSHOT)
        recent = [record for record in records
                  if (record.get("metadata") or {}).get("date", "") >= cutoff]
        return sorted(recent,
                      key=lambda record: record["metadata"]["date"])

    def _series_trend(self, values: list[float]) -> str:
        """序列趋势：前半均值 vs 后半均值（单点记 stable）。"""
        if len(values) < 2:
            return "stable"
        middle = len(values) // 2
        first_half = values[:middle] or values[:1]
        second_half = values[middle:] or values[-1:]
        return _trend_of(_mean(second_half) - _mean(first_half))

    def _stage_recommendation(self, character_id: str, user_id: str,
                              snapshots: list[dict]) -> str:
        """关系阶段推荐：当前阶段 + AFF 接近下一阶段下界 → "Sx → Sy"。

        当前关系状态取 character_user_state 的实时 AFF/TRS/ATT（推荐面向
        "接下来会怎样"，不是历史快照）；无状态行时退回最近快照的值。
        """
        with self.db_session_factory() as db:
            row = db.get(CharacterUserState, (character_id, user_id))
        current = (row.relationship_stage if row else None) or "S1"
        if row is not None and row.affective_state:
            affective = _loads_json(row.affective_state)
            aff = float(affective.get("AFF") or 0.0)
            trs = float(affective.get("TRS") or 0.0)
            att = float(affective.get("ATT") or 0.0)
        elif snapshots:
            latest = snapshots[-1]
            aff = self._snapshot_aff(latest)
            trs = self._snapshot_trs(latest)
            att = self._snapshot_att(latest)
        else:
            aff = trs = att = 0.0
        next_stage = self._next_stage_code(current)
        if next_stage is None or current not in self.stages:
            # 最高阶段或无法识别的阶段码：不给进阶建议
            return f"维持 {current}"
        stage_cfg = self.stages.get(next_stage) or {}
        aff_range = stage_cfg.get("AFF") or [None, None]
        lower = aff_range[0]
        if lower is None:
            return f"维持 {current}"
        near_boundary = aff >= lower - STAGE_PROXIMITY
        trs_ok = trs >= stage_cfg.get("TRS_min", 0.0)
        att_ok = att >= stage_cfg.get("ATT_min", 0.0)
        if near_boundary and trs_ok and att_ok:
            return f"{current} → {next_stage}"
        return f"维持 {current}"

    def _next_stage_code(self, current: str) -> str | None:
        """当前阶段的下一阶段代码（S0→S1→...→S4，S4 之后 None）。"""
        codes = sorted(self.stages)
        if current not in codes:
            return codes[0] if codes else None
        index = codes.index(current)
        return codes[index + 1] if index + 1 < len(codes) else None

    def _notable_patterns(self, snapshots: list[dict]) -> list[str]:
        """值得注意的模式（临时规则，待调优）。

        - 某工作日的平均效价明显低于整体 → 「每周X情绪偏低」
        - 一周内多次出现脆弱展露 → 「信任在加深」
        """
        patterns: list[str] = []
        if not snapshots:
            return patterns
        by_weekday: dict[int, list[float]] = {}
        for record in snapshots:
            date_text = (record.get("metadata") or {}).get("date")
            if not date_text:
                continue
            weekday = datetime.fromisoformat(date_text).weekday()
            by_weekday.setdefault(weekday, []).append(
                self._snapshot_valence(record))
        overall = _mean([self._snapshot_valence(record)
                         for record in snapshots])
        for weekday, values in sorted(by_weekday.items()):
            if _mean(values) < overall - WEEKDAY_PATTERN_GAP:
                patterns.append(
                    f"用户每周{WEEKDAYS_CN[weekday][1]}情绪偏低，"
                    f"可能和作息节奏有关")
        disclosure_days = sum(
            1 for record in snapshots
            if any(event.get("label") in TRUST_BUILDING_LABELS
                   for event in (record.get("metadata") or {})
                   .get("consolidated_events", [])))
        if disclosure_days >= 3:
            patterns.append("用户一周内多次展露脆弱面，信任在加深")
        return patterns

    # ── 通用取数与计算 ──────────────────────────────────────

    def _latest_sediment(self, character_id: str, user_id: str,
                         memory_type: str) -> dict | None:
        """某类型最新一条沉淀记录（判重/比较用）。"""
        records = self.memory_manager.list_sediments(
            character_id, user_id, memory_type)
        if not records:
            return None
        return max(records, key=lambda record:
                   float(record.get("created_at") or 0.0))

    @staticmethod
    def _duration_minutes(started_at: datetime,
                          last_at: datetime) -> int:
        """会话时长（分钟，向上取整到分钟）。"""
        seconds = max((last_at - started_at).total_seconds(), 0.0)
        return int(round(seconds / 60.0))

    @staticmethod
    def _turn_valence(turn: dict) -> float:
        """单轮效价：分类器未产出 valence 时按事件标签映射（裁决②同源）。"""
        label = turn.get("emotion_tag")
        return EVENT_VALENCE.get(label, 0.0) if label else 0.0

    def _weighted_valence(self, valences: list[float],
                          ending_valence: float,
                          peak_valence: float | None = None) -> float:
        """加权效价：峰值 0.5 / 尾声 0.3 / 均值 0.2（文档 1 第十四章）。

        peak_valence 为峰值事件（强度最高那轮）的效价；缺省时退化为
        最极端效价。无事件数据时退化为尾声效价。权重和归一化，
        配置改动不破坏量纲。
        """
        if not valences:
            return round(ending_valence, 4)
        if peak_valence is None:
            peak_valence = valences[int(max(
                range(len(valences)),
                key=lambda index: abs(valences[index])))]
        average = _mean(valences)
        total = self.peak_weight + self.ending_weight + self.average_weight
        value = (self.peak_weight * peak_valence
                 + self.ending_weight * ending_valence
                 + self.average_weight * average) / total
        return round(value, 4)

    @staticmethod
    def _event_valence(label: str | None) -> float:
        """事件标签 → 效价（与分类器/引擎同一张映射表，裁决⑤）。"""
        return EVENT_VALENCE.get(label, 0.0) if label else 0.0

    @staticmethod
    def _peak_event(turns: list[dict]) -> dict:
        """峰值事件：强度最高的一轮 → {label, intensity, turn}。

        turn 为该轮在会话中的对数序号（第几"轮"对话，1 起）；
        无强度数据时返回空 dict。
        """
        best: dict = {}
        best_intensity = -1.0
        turn_number = 0
        for turn in turns:
            if (turn.get("role") or "") == "user":
                turn_number += 1
            intensity = turn.get("intensity")
            if intensity is not None and float(intensity) > best_intensity:
                best_intensity = float(intensity)
                best = {"label": turn.get("emotion_tag"),
                        "intensity": float(intensity),
                        "turn": turn_number}
        return best

    @staticmethod
    def _ending_tone(changes: list, turns: list[dict]) -> dict:
        """尾声语调：会话窗口内最后一次 state_after 的表达层。

        无状态变更记录时退化为最后一轮的 expression_snapshot。
        """
        if changes:
            state = _loads_json(changes[-1].state_after_json)
            expression = state.get("expression")
            if isinstance(expression, dict) and expression:
                return dict(expression)
        for turn in reversed(turns):
            snapshot = turn.get("expression_snapshot")
            if isinstance(snapshot, dict) and snapshot:
                return dict(snapshot)
        return {}

    @staticmethod
    def _key_memory(turns: list[dict]) -> str:
        """关键记忆：峰值轮的用户发言（无峰值时取第一条用户发言）。

        文档未定义选取规则，暂取峰值事件对应的用户消息（待调优）。
        """
        user_turns = [turn for turn in turns
                      if (turn.get("role") or "") == "user"]
        if not user_turns:
            return ""
        best = max(user_turns,
                   key=lambda turn: float(turn.get("intensity") or 0.0))
        return str(best.get("content") or "")

    @staticmethod
    def _consolidated_events(events: list) -> list[dict]:
        """当天事件按强度取前 N 条，impact 由该轮情感层增量格式化。

        impact 形如 "AFF +1.5, TRS +0.8"（只含非零情感层维度）。
        """
        with_intensity = [event for event in events if event[0]]
        with_intensity.sort(key=lambda event: float(event[1] or 0.0),
                            reverse=True)
        consolidated = []
        for label, intensity, delta in with_intensity[:MAX_CONSOLIDATED_EVENTS]:
            affective_delta = (delta or {}).get("affective") or {}
            parts = [f"{dim} {value:+.1f}" for dim, value
                     in sorted(affective_delta.items())
                     if abs(float(value)) >= 0.05]
            consolidated.append({
                "label": label,
                "intensity": float(intensity) if intensity is not None else None,
                "impact": ", ".join(parts) if parts else "无明显变化",
            })
        return consolidated

    # ── 日快照字段读取（周趋势用） ──────────────────────────

    @staticmethod
    def _snapshot_valence(record: dict) -> float:
        """日快照的 daily_valence。"""
        return float((record.get("metadata") or {}).get("daily_valence", 0.0))

    @staticmethod
    def _snapshot_affective(record: dict, dim: str) -> float:
        """日快照的 day_end_affective 某维度（缺失 0）。"""
        affective = (record.get("metadata") or {}).get("day_end_affective") or {}
        return float(affective.get(dim) or 0.0)

    def _snapshot_aff(self, record: dict) -> float:
        """AFF（亲密度）。"""
        return self._snapshot_affective(record, "AFF")

    def _snapshot_trs(self, record: dict) -> float:
        """TRS（安全感）。"""
        return self._snapshot_affective(record, "TRS")

    def _snapshot_att(self, record: dict) -> float:
        """ATT（依恋）。"""
        return self._snapshot_affective(record, "ATT")


# ── 模块级小工具 ────────────────────────────────────────────

def _select(entity):
    """SQLAlchemy select 的延迟导入（避免模块级依赖风格不一致）。"""
    from sqlalchemy import select

    return select(entity)


def _max(column):
    """SQLAlchemy func.max 的延迟导入。"""
    from sqlalchemy import func

    return func.max(column)


def _loads_json(text: str | None) -> dict:
    """JSON TEXT -> dict；空/损坏返回空 dict。"""
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _mean(values: list[float]) -> float:
    """均值（空列表 0.0）。"""
    return sum(values) / len(values) if values else 0.0


def _clamp(value: float, low: float, high: float) -> float:
    """钳制到 [low, high]。"""
    return max(low, min(high, value))


def _trend_of(delta: float) -> str:
    """趋势判定：超过 ±TREND_THRESHOLD 才记 rising/falling。"""
    if delta > TREND_THRESHOLD:
        return "rising"
    if delta < -TREND_THRESHOLD:
        return "falling"
    return "stable"


def _trend_cn(trend: str) -> str:
    """趋势代码 → 中文（周趋势正文用）。"""
    return {"rising": "上升", "falling": "下降"}.get(trend, "平稳")
