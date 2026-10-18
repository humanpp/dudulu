"""主动行为引擎（文档 2 第 3.11 节 + 文档 1 第十二章）。

职责：周期性审视每个活跃的 (character_id, user_id) 对，由确定性引擎
决定"是否"和"何时"，LLM 只决定"说什么"（文档 1 第 12.1 节）。

流程：tick() → _should_evaluate（五道硬性拦截 + 频率门控）→
_evaluate（六条触发条件 + 优先级）→ _generate_and_push
（生成 → 存记忆 → proactive_log → WebSocket 推送）。

时区：quiet_hours 与"今天几次"都按用户本地时区（db/timezone.py，
Phase 8a）。库内存 naive UTC。tick 为 async（LLM 调用与 WS 推送
都是异步原语；APScheduler 的 AsyncIOScheduler 直接承载）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import yaml
from sqlalchemy import func, or_, select
from sqlalchemy.orm import sessionmaker

from config.settings import Settings, get_settings
from core.character.manager import CharacterManager
from core.emotion_engine import EmotionEngine
from db.models import (
    CharacterUserState,
    Message,
    ProactiveLog,
    User,
    utc_now,
)
from db.proactive_state import ProactiveStateStore
from db.timezone import is_in_quiet_hours, local_day_start_utc
from llm.base import LLMClient
from llm.factory import create_llm_client, llm_config_from_settings
from memory.memory_manager import MemoryManager
from memory.schemas import ConversationTurn, now_iso

logger = logging.getLogger(__name__)

# 用户正在对话中的判定：距上次消息小于该分钟数不主动（文档 1 第 12.2 节）
ACTIVE_CONVERSATION_MINUTES = 10.0
# 孤独感触发的对话间隔门槛（文档 1 第 12.2 节；角色覆盖时按 min(4h, interval)）
LONELINESS_GAP_HOURS = 4.0
# 慢心境偏离阈值（文档 1 第 12.2 节：当前慢心境与基线偏离 > 0.3）
MOOD_DEVIATION_THRESHOLD = 0.3
# 连续无回复达 5 次 → 静默 24h（文档 1 第 12.4 节）
HARD_SILENCE_HOURS = 24.0
# 主动消息的记忆情感标签（文档 1 第 12.6 节：单独标签，不在九类事件标签内）
PROACTIVE_EMOTION_TAG = "proactive"
# 期待感关键词（文档 1 第 12.2 节"用户上次提到'下次再聊'之类"——
# 文档未定义检测方式，暂用关键词扫描最近用户消息，待文档补 classifier 字段）
ANTICIPATION_KEYWORDS = ("下次再聊", "回头聊", "晚点聊", "明天聊", "下次聊",
                         "改天聊", "有空聊", "回头再说", "晚点再说",
                         "明天再说", "下次再约")
# 主动消息的最近用户消息扫描窗口（期待感判定用）
RECENT_USER_MESSAGES = 10

# 触发原因代码（文档 1 第 12.2 节六条）
REASON_LONELINESS = "loneliness_high"
REASON_ANTICIPATION = "anticipation_high"
REASON_DECAY_HEAP = "decay_heap"
REASON_MOOD_DEVIATION = "mood_deviation"
REASON_TIME_INTERVAL = "time_interval"

# 触发原因 → 提示词用中文说明
_REASON_TEXT = {
    REASON_LONELINESS: "你感到有些孤独，想主动联系对方",
    REASON_ANTICIPATION: "对方上次说改天再聊，你记得这个约定",
    REASON_DECAY_HEAP: "有一件重要的往事即将被遗忘，你想先提起它",
    REASON_MOOD_DEVIATION: "你最近心境低落或波动，想说说自己的状态",
    REASON_TIME_INTERVAL: "有一阵子没联系了，想轻描淡写地问候",
}


@dataclass
class ProactiveSignal:
    """一次评估的结果（文档 2 第 3.11 节 _evaluate 的返回）。"""

    should_act: bool
    reason: str = ""                  # 触发原因代码
    priority: str = ""                # P0 ~ P4（文档 1 第 12.3 节）
    memory_quote: str = ""            # P0 引用的记忆正文（裁决④）
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """输出日志/调试用 dict；副作用：无。"""
        return {"should_act": self.should_act, "reason": self.reason,
                "priority": self.priority, "memory_quote": self.memory_quote,
                "details": dict(self.details)}


class ProactiveEngine:
    """职责：周期性审视 (character, user) 对，决定是否主动发起对话。"""

    def __init__(self, character_manager: CharacterManager,
                 memory_manager: MemoryManager,
                 llm_client: LLMClient,
                 ws_manager,
                 db_session_factory: sessionmaker,
                 config_path: str,
                 prompt_path: str = "config/prompts/proactive.txt",
                 settings: Settings | None = None):
        """初始化主动引擎。

        输入：角色管理器、记忆管理器、LLM 客户端、WebSocket 管理器
            （可为 None——独立调度进程无 WS 连接时推送为空操作）、
            SQLite 会话工厂、数值配置路径、提示词路径、Settings；
        副作用：读取数值配置与提示词模板。
        """
        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        self.proactive_cfg = config.get("proactive") or {}
        self.stages = (config.get("affective_layer") or {}).get("stages") or {}
        self.character_manager = character_manager
        self.memory_manager = memory_manager
        self.llm_client = llm_client
        self.ws_manager = ws_manager
        self.db_session_factory = db_session_factory
        self.settings = settings or get_settings()
        self.prompt_template = _read_prompt(prompt_path)
        self.proactive_state = ProactiveStateStore(db_session_factory)

    # ── 心跳入口（文档 2 第 3.11 节 tick） ──────────────────

    async def tick(self) -> dict:
        """每次心跳执行一次，遍历所有活跃的 (character, user) 对。

        输出：{"pairs": N, "sent": M, "skipped": K}（统计用）；
        副作用：可能生成并推送主动消息、写 proactive_state / proactive_log。
        """
        pairs = self._list_active_pairs()
        sent = 0
        skipped = 0
        for character_id, user_id in pairs:
            blocked = self._should_evaluate(character_id, user_id)
            if blocked is not None:
                skipped += 1
                logger.debug("跳过 %s/%s: %s", character_id, user_id, blocked)
                continue
            signal = self._evaluate(character_id, user_id)
            if not signal.should_act:
                continue
            await self._generate_and_push(character_id, user_id, signal)
            sent += 1
        return {"pairs": len(pairs), "sent": sent, "skipped": skipped}

    def _list_active_pairs(self) -> list[tuple[str, str]]:
        """近期有互动的双键（复用 Phase 7 的 ACTIVE_WINDOW_DAYS）。"""
        from scheduler.jobs import ACTIVE_WINDOW_DAYS

        cutoff = utc_now() - timedelta(days=ACTIVE_WINDOW_DAYS)
        with self.db_session_factory() as db:
            rows = db.execute(
                select(CharacterUserState.character_id,
                       CharacterUserState.user_id).where(
                    or_(CharacterUserState.last_interaction.is_(None),
                        CharacterUserState.last_interaction >= cutoff))
            ).all()
        return [(character_id, user_id) for character_id, user_id in rows]

    # ── 五道硬性拦截 + 频率门控（文档 2 第 3.11 节） ─────────

    def _should_evaluate(self, character_id: str,
                         user_id: str) -> str | None:
        """返回 None = 放行；返回字符串 = 拦截原因（调试/统计用）。

        五道硬性拦截（任一命中即跳过）：
        1. 用户级开关 users.proactive_enabled = 0
        2. 角色级开关 profile.proactive_enabled = false
        3. 当前时刻落在用户本地 quiet_hours 内
        4. proactive_state.silenced_until 未到期（退缩静默期）
        5. proactive_state.busy_until 未到期（用户表示"最近很忙"）

        频率门控（非硬性拦截，但同样跳过）：
        - 距上次主动消息 < 有效最小间隔 / 审视间隔未到
        - 距上次用户消息 < 10 分钟（用户正在对话中）
        - 软静默期内（连续无回复达阈值且未过 silence_recovery_hours）
        - 今日主动次数已达有效上限
        """
        # 1. 用户级开关
        with self.db_session_factory() as db:
            user = db.get(User, user_id)
            if user is not None and not user.proactive_enabled:
                return "user_disabled"
        # 2. 角色级开关
        try:
            profile = self.character_manager.get(character_id)
        except Exception as exc:  # 角色不存在/已归档：跳过该对
            logger.debug("角色不可用 %s: %s", character_id, exc)
            return "character_unavailable"
        if not profile.proactive_enabled:
            return "character_disabled"
        # 3. 静默时段（用户本地时区）
        with self.db_session_factory() as db:
            if is_in_quiet_hours(
                    db, user_id,
                    tuple(self.proactive_cfg.get("quiet_hours", (23, 8)))):
                return "quiet_hours"
        # 4 / 5. 退缩静默期与忙碌期
        state = self.proactive_state.get_state(character_id, user_id)
        now = utc_now()
        if state["silenced_until"] is not None and state["silenced_until"] > now:
            return "silenced"
        if state["busy_until"] is not None and state["busy_until"] > now:
            return "busy"

        params = self._resolve_params(profile)
        last_interaction = self._last_interaction_at(character_id, user_id)
        if last_interaction is not None:
            idle_minutes = (now - last_interaction).total_seconds() / 60.0
            if idle_minutes < ACTIVE_CONVERSATION_MINUTES:
                return "user_in_conversation"  # 用户正在对话中

        last_proactive = state["last_proactive_at"]
        if last_proactive is not None:
            since_proactive = (now - last_proactive).total_seconds() / 3600.0
            if since_proactive < params["min_gap_hours"]:
                return "min_gap"
            # 软静默：连续无回复达阈值且未过恢复期（裁决④）
            if (state["consecutive_no_reply"] >= params["silence_threshold"]
                    and since_proactive < params["silence_recovery_hours"]):
                return "soft_silence"
            if since_proactive < params["interval_hours"]:
                return "interval_not_reached"
        # 每日上限（用户本地"今天"）
        if self._proactive_count_today(character_id, user_id) >= \
                params["max_daily"]:
            return "max_daily"
        return None

    # ── 触发评估（文档 1 第 12.2 / 12.3 节） ────────────────

    def _evaluate(self, character_id: str,
                  user_id: str) -> ProactiveSignal:
        """评估六条触发条件，按优先级排序（文档 1 第 12.3 节）。"""
        try:
            profile = self.character_manager.get(character_id)
        except Exception as exc:
            logger.debug("角色不可用 %s: %s", character_id, exc)
            return ProactiveSignal(should_act=False)
        params = self._resolve_params(profile)
        engine, hours_since_last = self._build_engine(profile, character_id,
                                                      user_id)
        full_state = engine.get_full_state()
        stage_code = full_state["affective"].get("stage_code") or "S1"
        stage_rank = self._stage_rank(stage_code)
        # 无交互记录视为"很久以前"（inf 参与比较；入 details 时转 None）
        idle_hours = hours_since_last if hours_since_last is not None \
            else float("inf")
        idle_detail = None if idle_hours == float("inf") \
            else round(idle_hours, 2)

        # P0：记忆衰减堆（裁决①②③④）
        forgetting = self.memory_manager.find_forgetting_important(
            character_id, user_id)
        if forgetting:
            top = forgetting[0]
            return ProactiveSignal(
                should_act=True, reason=REASON_DECAY_HEAP, priority="P0",
                memory_quote=top.get("content") or "",
                details={"memory_id": top.get("id"),
                         "decay_factor": top.get("decay_factor"),
                         "remaining_hours": top.get("remaining_hours")})

        internal = full_state["internal"]
        # P1：孤独感高 + 关系阶段 S2+（阶段不足降为 P3）
        if (internal.get("LON", 0.0) > 0.7
                and idle_hours > min(LONELINESS_GAP_HOURS,
                                     params["interval_hours"])):
            priority = "P1" if stage_rank >= 2 else "P3"
            return ProactiveSignal(
                should_act=True, reason=REASON_LONELINESS, priority=priority,
                details={"LON": internal.get("LON"),
                         "idle_hours": idle_detail, "stage": stage_code})

        # P2：期待感高 + 用户上次提到"下次再聊"
        if internal.get("ANT", 0.0) > 0.6 and self._has_anticipation_hint(
                character_id, user_id):
            return ProactiveSignal(
                should_act=True, reason=REASON_ANTICIPATION, priority="P2",
                details={"ANT": internal.get("ANT")})

        # P3：慢心境偏离 + 时间间隔足够
        deviation = self._mood_deviation(engine)
        if (deviation > MOOD_DEVIATION_THRESHOLD
                and idle_hours > params["interval_hours"]):
            return ProactiveSignal(
                should_act=True, reason=REASON_MOOD_DEVIATION, priority="P3",
                details={"deviation": round(deviation, 4),
                         "stage": stage_code})

        # P4：仅时间间隔触发
        if idle_hours > params["interval_hours"]:
            return ProactiveSignal(
                should_act=True, reason=REASON_TIME_INTERVAL, priority="P4",
                details={"idle_hours": idle_detail})

        return ProactiveSignal(should_act=False)

    # ── 生成与推送（文档 2 第 3.11 节 _generate_and_push） ──

    async def _generate_and_push(self, character_id: str, user_id: str,
                                 signal: ProactiveSignal) -> str:
        """生成主动消息，存记忆，写日志，WebSocket 推送。

        输出：消息正文；副作用：一次 LLM 调用 + 写 YantrikDB /
        proactive_log / proactive_state + WS 推送。
        """
        profile = self.character_manager.get(character_id)
        engine, _ = self._build_engine(profile, character_id, user_id)
        full_state = engine.get_full_state()
        prompt = self._build_prompt(profile, signal, full_state)
        message = (await self.llm_client.generate(prompt)).strip()
        # 存记忆（文档 1 第 12.6 节：is_proactive / trigger_reason /
        # emotion_tag=proactive / expression_snapshot）
        self.memory_manager.store_conversation(ConversationTurn(
            character_id=character_id, user_id=user_id, role="assistant",
            text=message, is_proactive=True,
            trigger_reason=signal.reason,
            emotion_tag=PROACTIVE_EMOTION_TAG,
            expression_state=full_state["expression"],
            timestamp=now_iso(),
        ))
        # proactive_log（统计与调优）
        with self.db_session_factory() as db:
            db.add(ProactiveLog(
                character_id=character_id, user_id=user_id,
                triggered_at=utc_now(), reason=signal.reason,
                message=message))
            db.commit()
        # 退缩计数 + 连续 5 次无回复 → 静默 24h（文档 1 第 12.4 节）
        count = self.proactive_state.record_proactive_sent(character_id,
                                                           user_id)
        if count >= 5:
            self.proactive_state.set_silenced_until(character_id, user_id,
                                                    HARD_SILENCE_HOURS)
            logger.info("连续 %d 次无回复，进入 24h 静默期: %s/%s",
                        count, character_id, user_id)
        # WebSocket 推送（无连接的调度进程退化为空操作）
        if self.ws_manager is not None:
            self.ws_manager.push(user_id, {
                "type": "proactive_message",
                "character_id": character_id,
                "message": message,
                "reason": signal.reason,
                "priority": signal.priority,
                "timestamp": now_iso(),
            })
        logger.info("主动消息已发送: %s/%s (%s/%s)",
                    character_id, user_id, signal.priority, signal.reason)
        return message

    # ── 提示词装配 ──────────────────────────────────────────

    def _build_prompt(self, profile, signal: ProactiveSignal,
                      full_state: dict) -> str:
        """按 proactive.txt 的 __PLACEHOLDER__ 约定装配提示词。"""
        style = profile.speech_style
        style_text = f"{style.tone or '自然口语'}；{style.pace or '不急不缓'}"
        if style.catchphrases:
            style_text += f"；口头禅：{'、'.join(style.catchphrases)}"
        internal = full_state["internal"]
        internal_text = (f"孤独感 {internal.get('LON', 0):.2f}，"
                         f"期待感 {internal.get('ANT', 0):.2f}，"
                         f"愧疚感 {internal.get('GUI', 0):.2f}")
        stage_code = full_state["affective"].get("stage_code") or "S1"
        stage_label = (self.stages.get(stage_code) or {}).get("label",
                                                              stage_code)
        memory_quote = ""
        if signal.memory_quote:
            memory_quote = (f"你想自然提起的一件重要的事："
                            f"「{signal.memory_quote}」")
        reason_text = _REASON_TEXT.get(signal.reason, signal.reason)
        return (self.prompt_template
                .replace("__CHARACTER_NAME__", profile.name)
                .replace("__SPEECH_STYLE__", style_text)
                .replace("__STAGE_LABEL__", f"{stage_label}（{stage_code}）")
                .replace("__INTERNAL_STATE__", internal_text)
                .replace("__TRIGGER_REASON__", reason_text)
                .replace("__MEMORY_QUOTE__", memory_quote))

    # ── 参数解析（文档 1 第 12.7 节 eagerness 调节） ─────────

    def _resolve_params(self, profile) -> dict:
        """角色覆盖 + 全局配置 + eagerness → 有效频率参数。

        eagerness > 1 更主动（间隔变短、上限变高），< 1 更克制；
        连续无回复达 silence_threshold 时的间隔翻倍在软静默门控里
        由 silence_recovery_hours 窗口兜住（裁决④），此处只做基础换算。
        """
        global_cfg = self.proactive_cfg
        role = profile.emotion_overrides.proactive
        interval = role.interval_hours if role.interval_hours is not None \
            else float(global_cfg.get("interval_hours", 4.5))
        min_gap = role.min_gap_hours if role.min_gap_hours is not None \
            else float(global_cfg.get("min_gap_hours", 3.0))
        max_daily = role.max_daily if role.max_daily is not None \
            else int(global_cfg.get("max_daily", 3))
        silence_threshold = role.silence_threshold \
            if role.silence_threshold is not None \
            else int(global_cfg.get("silence_threshold", 3))
        silence_recovery = role.silence_recovery_hours \
            if role.silence_recovery_hours is not None \
            else float(global_cfg.get("silence_recovery_hours", 12))
        eagerness = role.eagerness if role.eagerness is not None \
            else float(global_cfg.get("default_eagerness", 1.0))
        eagerness = max(eagerness, 0.1)  # 防御性下限，避免除零/负间隔
        return {
            "interval_hours": interval / eagerness,
            "min_gap_hours": min_gap / eagerness,
            "max_daily": max(1, round(max_daily * eagerness)),
            "silence_threshold": silence_threshold,
            "silence_recovery_hours": silence_recovery,
            "eagerness": eagerness,
        }

    # ── 状态取数 ────────────────────────────────────────────

    def _build_engine(self, profile, character_id: str,
                      user_id: str) -> tuple[EmotionEngine, float | None]:
        """装配情感引擎并施加自上次交互以来的衰减（与管道同口径）。"""
        from db.user_state import load_user_state

        with self.db_session_factory() as db:
            user_state, _ = load_user_state(db, character_id, user_id)
            row = db.get(CharacterUserState, (character_id, user_id))
        last_interaction = row.last_interaction if row else None
        hours_since_last = None
        if last_interaction is not None:
            hours_since_last = max(
                (utc_now() - last_interaction).total_seconds() / 3600.0, 0.0)
        engine = EmotionEngine(self.settings.EMOTION_CONFIG_PATH, profile,
                               user_state)
        if hours_since_last:
            engine.apply_decay(hours_since_last)
        return engine, hours_since_last

    def _last_interaction_at(self, character_id: str,
                             user_id: str) -> datetime | None:
        """该对最后一次用户交互时间（character_user_state.last_interaction）。"""
        with self.db_session_factory() as db:
            row = db.get(CharacterUserState, (character_id, user_id))
            return row.last_interaction if row else None

    def _proactive_count_today(self, character_id: str,
                               user_id: str) -> int:
        """用户本地"今天"已发出的主动消息数（proactive_log）。"""
        with self.db_session_factory() as db:
            day_start = local_day_start_utc(db, user_id)
            count = db.execute(
                select(func.count()).select_from(ProactiveLog).where(
                    ProactiveLog.character_id == character_id,
                    ProactiveLog.user_id == user_id,
                    ProactiveLog.triggered_at >= day_start)
            ).scalar_one()
        return int(count)

    def _has_anticipation_hint(self, character_id: str,
                               user_id: str) -> bool:
        """最近用户消息里是否有"下次再聊"类关键词（临时检测方式）。"""
        with self.db_session_factory() as db:
            rows = db.execute(
                select(Message).where(
                    Message.character_id == character_id,
                    Message.user_id == user_id,
                    Message.role == "user")
                .order_by(Message.created_at.desc())
                .limit(RECENT_USER_MESSAGES)
            ).scalars().all()
        for row in rows:
            memory = self.memory_manager.get_memory(row.memory_id)
            if memory and memory.get("content"):
                if any(keyword in memory["content"]
                       for keyword in ANTICIPATION_KEYWORDS):
                    return True
        return False

    @staticmethod
    def _mood_deviation(engine: EmotionEngine) -> float:
        """当前慢心境与基线的平均绝对偏离（五维均值；文档未定义公式，待调优）。"""
        baseline = engine.mood_baseline or {}
        if not baseline:
            return 0.0
        current = engine.state.expression
        deviations = [abs(current.get(dim, 0.0) - baseline.get(dim, 0.0))
                      for dim in baseline]
        return sum(deviations) / len(deviations) if deviations else 0.0

    def _stage_rank(self, stage_code: str) -> int:
        """阶段码 → 序号（S0=0 … S4=4；未知码按 -1）。"""
        codes = sorted(self.stages)
        return codes.index(stage_code) if stage_code in codes else -1


def _read_prompt(prompt_path: str) -> str:
    """读提示词模板文件（与 Classifier 同一约定：__PLACEHOLDER__ 占位）。"""
    from pathlib import Path

    return Path(prompt_path).read_text(encoding="utf-8")


def build_proactive_engine(
        db_session_factory: sessionmaker,
        settings: Settings | None = None,
        ws_manager=None,
        embedder: str | None = None) -> ProactiveEngine:
    """装配主动引擎（Web 进程与调度进程共用同一装配方式）。

    ws_manager 传 None 时推送退化为空操作（独立调度进程无 WS 连接；
    消息仍落库，前端下次拉取可见——见交付报告的进程模型说明）。
    """
    settings = settings or get_settings()
    from core.character.manager import get_character_manager
    from memory.memory_manager import get_memory_manager

    return ProactiveEngine(
        character_manager=get_character_manager(db_session_factory),
        memory_manager=get_memory_manager(
            settings.YANTRIK_PATH, db_session_factory=db_session_factory,
            embedder=embedder),
        llm_client=create_llm_client(llm_config_from_settings(settings)),
        ws_manager=ws_manager,
        db_session_factory=db_session_factory,
        config_path=settings.EMOTION_CONFIG_PATH,
        settings=settings,
    )
