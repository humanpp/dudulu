"""编排管道（文档 2 第 3.8 节）：一次完整对话的处理流程。

步骤（与文档编号一致）：1 加载角色 → 2 加载状态 → 3 初始化引擎 → 4 时间衰减 →
5 事件分类 → 5.5 busy_signal 落库 → 6 情感更新 → 7 记忆检索 → 8 Prompt 装配 →
9 LLM 生成 → 10 后处理 → 11 存储 → 11.5 state_update 推送 → 12 沉淀检查 →
（流式）13 done 事件。

Phase 4 起：用户状态、距上次交互、busy_signal、state_changes 日志、
sessions.turn_count 均落 SQLite；记忆检索/存储与最近对话正文仍是占位
（Phase 5 YantrikDB），_get_recent_turns 留待 Phase 5（强依赖正文回读）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from core.affective_layer import AFFECTIVE_DIMENSIONS
from core.character.profile import CharacterProfile
from core.emotion_engine import (
    EVENT_VALENCE,
    EmotionEngine,
    StateDelta,
)
from core.expression_layer import EXPRESSION_DIMENSIONS
from core.internal_layer import INTERNAL_DIMENSIONS
from llm.base import LLMClient
from memory.schemas import ConversationTurn, now_iso
from orchestrator.classifier import Classifier
from orchestrator.post_processor import PostProcessor
from orchestrator.prompt_assembler import PromptAssembler

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """一次对话的处理结果（文档 2 第 3.8 节）。"""

    reply: str
    state: dict
    memories: list = field(default_factory=list)


def memory_ids(memories: list) -> list:
    """从检索结果取记忆 id 列表（API 的 memories_used 字段）。

    TODO: Phase 5 — 记忆条目结构落地后确认取 id 的方式（当前按 dict 取）。
    """
    return [memory["id"] for memory in memories]


def _existing_baseline(expression_state_json: str | None) -> dict:
    """从已存的 expression_state JSON 里取 `_baseline` 子键（无则空 dict）。"""
    if not expression_state_json:
        return {}
    try:
        stored = json.loads(expression_state_json)
    except (TypeError, ValueError):
        return {}
    baseline = stored.get("_baseline")
    return dict(baseline) if isinstance(baseline, dict) else {}


class Pipeline:
    """一次完整对话的处理管道（文档 2 第 3.8 节）。"""

    def __init__(self, character_manager, memory_manager, classifier,
                 prompt_assembler, llm_client, sedimenter, proactive_state,
                 post_processor, ws_manager=None,
                 emotion_config_path: str = "config/dudulu_config.yaml",
                 db_session_factory: sessionmaker[Session] = None):
        """初始化管道。

        输入：各协作者（见各自模块 docstring）；
            ws_manager：WebSocket 管理器，None 时跳过 11.5 步推送
                （TODO: Phase 8 由 routes_ws.py 注入）；
            emotion_config_path：数值配置路径（文档 2 第 3.8 节第 3 步的字面值）；
            db_session_factory：SQLite 会话工厂（必需；用户状态、proactive_state、
                state_changes、sessions 的读写都经它开短会话）。
        副作用：无（不发请求）。
        """
        self.character_manager = character_manager
        self.memory_manager = memory_manager
        self.classifier = classifier
        self.prompt_assembler = prompt_assembler
        self.llm_client = llm_client
        self.sedimenter = sedimenter
        self.proactive_state = proactive_state
        self.post_processor = post_processor
        self.ws_manager = ws_manager
        self.emotion_config_path = emotion_config_path
        self.db_session_factory = db_session_factory

    # ── 非流式 ──────────────────────────────────────────────

    async def process(self, user_message: str, character_id: str,
                      user_id: str, session_id: str) -> PipelineResult:
        """处理一次对话（非流式，文档 2 第 3.8 节）。

        输入：用户消息、角色 id、用户 id、会话 id；
        输出：PipelineResult（reply / state / memories）；
        副作用：分类与生成各一次 LLM 调用；状态与记忆落库。
        """
        profile = self.character_manager.get(character_id)                      # 1
        user_state = self._load_user_state(character_id, user_id)               # 2
        engine = EmotionEngine(self.emotion_config_path, profile, user_state)   # 3
        engine.apply_decay(hours_elapsed=self._hours_since_last(                # 4
            character_id, user_id),
            valence=self._last_event_valence(character_id, user_id))
        # 事件更新前的状态快照：11.5 步推送 delta 的真实差基准（裁决⑤）
        state_before_event = engine.get_full_state()
        event = await self.classifier.classify(                                 # 5
            user_message, engine.get_full_state(), character_profile=profile)
        self.proactive_state.clear_busy_until(character_id, user_id)            # 5.5
        self.proactive_state.register_user_reply(character_id, user_id)         # 5.5
        if event.busy_signal:
            self.proactive_state.set_busy_until(character_id, user_id, hours=24)
        delta = engine.apply_event(event)                                       # 6

        new_state = engine.get_full_state()                                     # 7
        memories = self.memory_manager.retrieve(
            query=user_message, character_id=character_id, user_id=user_id,
            current_state=new_state, top_k=5)

        prompt = self.prompt_assembler.build(                                   # 8
            character=profile,
            expression_state=new_state["expression"],
            affective_state=new_state["affective"],
            memories=memories,
            recent_turns=self._get_recent_turns(character_id, user_id, n=10),
            user_message=user_message,
        )

        reply = await self.llm_client.generate(prompt)                          # 9
        reply = self.post_processor.process(reply)                              # 10
        self._store_turns(character_id, user_id, session_id,                    # 11
                          user_message, reply, event, new_state, delta)
        self._persist_user_state(character_id, user_id, new_state)
        self._push_state_update(user_id, character_id, new_state,               # 11.5
                                state_before=state_before_event)
        self.sedimenter.maybe_sediment(character_id, user_id)                   # 12
        return PipelineResult(reply=reply, state=new_state, memories=memories)

    # ── 流式（SSE） ─────────────────────────────────────────

    async def process_stream(self, user_message: str, character_id: str,
                             user_id: str, session_id: str) -> AsyncIterator:
        """处理一次对话（流式，文档 2 第 3.8 节）。

        前 8 步与非流式相同，第 9 步改为流式生成。
        Yields：SSE 事件 dict（{"event": ..., "data": ...}），
            顺序为若干 token，最后 done；中途失败以 error 替代 done。
        """
        profile = self.character_manager.get(character_id)                      # 1
        user_state = self._load_user_state(character_id, user_id)               # 2
        engine = EmotionEngine(self.emotion_config_path, profile, user_state)   # 3
        engine.apply_decay(hours_elapsed=self._hours_since_last(                # 4
            character_id, user_id),
            valence=self._last_event_valence(character_id, user_id))
        # 事件更新前的状态快照：11.5 步推送 delta 的真实差基准（裁决⑤）
        state_before_event = engine.get_full_state()
        event = await self.classifier.classify(                                 # 5
            user_message, engine.get_full_state(), character_profile=profile)
        self.proactive_state.clear_busy_until(character_id, user_id)            # 5.5
        self.proactive_state.register_user_reply(character_id, user_id)         # 5.5
        if event.busy_signal:
            self.proactive_state.set_busy_until(character_id, user_id, hours=24)
        delta = engine.apply_event(event)                                       # 6

        new_state = engine.get_full_state()                                     # 7
        memories = self.memory_manager.retrieve(
            query=user_message, character_id=character_id, user_id=user_id,
            current_state=new_state, top_k=5)

        prompt = self.prompt_assembler.build(                                   # 8
            character=profile,
            expression_state=new_state["expression"],
            affective_state=new_state["affective"],
            memories=memories,
            recent_turns=self._get_recent_turns(character_id, user_id, n=10),
            user_message=user_message,
        )

        full_reply = ""                                                         # 9
        try:
            async for token in self.llm_client.generate_stream(prompt):
                full_reply += token
                yield {"event": "token", "data": token}
        except Exception as exc:
            # 已吐出去的 token 不回滚：前端保留已显示的文字，并给「重新生成」
            logger.exception("流式生成失败（character_id=%s）", character_id)
            yield {"event": "error",
                   "data": json.dumps({"message": str(exc), "partial": full_reply},
                                      ensure_ascii=False)}
            return

        reply = self.post_processor.process(full_reply)                         # 10
        self._store_turns(character_id, user_id, session_id,                    # 11
                          user_message, reply, event, new_state, delta)
        self._persist_user_state(character_id, user_id, new_state)
        self._push_state_update(user_id, character_id, new_state,               # 11.5
                                state_before=state_before_event)
        self.sedimenter.maybe_sediment(character_id, user_id)                   # 12

        yield {"event": "done",                                                 # 13
               "data": json.dumps(
                   {"state": new_state,
                    "memories_used": memory_ids(memories)},
                   ensure_ascii=False)}

    # ── 内部步骤 ────────────────────────────────────────────

    def _store_turns(self, character_id: str, user_id: str, session_id: str,
                     user_message: str, reply: str, event, new_state: dict,
                     delta: StateDelta) -> None:
        """第 11 步：存储本轮对话与派生记录。

        - user / assistant 两轮对话交 MemoryManager（Phase 5 起落 YantrikDB；
          user 轮带事件 valence，裁决⑤），同时写 messages 索引行
          （memory_id 关联 YantrikDB rid，文档 2 第 6.7 节）；
        - state_changes 写一行变更日志（文档 2 第 5.2 节"每轮至少 1 行"，
          写入方文档未明确，取管道——唯一同时持有 delta 与 state_after 的地方）；
        - sessions.turn_count +1（文档未定义写入方，见交付报告）。
        """
        expression_state = new_state["expression"]
        self.memory_manager.store_conversation(ConversationTurn(
            character_id=character_id, user_id=user_id, role="user",
            text=user_message, emotion_tag=event.label,
            intensity=event.intensity, expression_state=expression_state,
            valence=event.valence, timestamp=now_iso(),
        ), session_id=session_id)
        self.memory_manager.store_conversation(ConversationTurn(
            character_id=character_id, user_id=user_id, role="assistant",
            text=reply, expression_state=expression_state,
            timestamp=now_iso(),
        ), session_id=session_id)
        self._record_state_change(character_id, user_id, event, new_state, delta)
        self._bump_session_turn(session_id, user_id)

    def _record_state_change(self, character_id: str, user_id: str, event,
                             new_state: dict, delta: StateDelta) -> None:
        """state_changes 落一行（文档 2 第 5.2 节）。"""
        from db.models import StateChange, utc_now

        with self.db_session_factory() as db:
            db.add(StateChange(
                character_id=character_id,
                user_id=user_id,
                changed_at=utc_now(),
                event_label=event.label,
                event_intensity=event.intensity,
                delta_json=json.dumps(delta.to_dict(), ensure_ascii=False),
                state_after_json=json.dumps(new_state, ensure_ascii=False),
            ))
            db.commit()

    def _bump_session_turn(self, session_id: str, user_id: str) -> None:
        """活跃会话的 turn_count +1（每轮 = user+assistant 一对）。

        写操作带 user_id 条件，保证不会写到他人会话（用户隔离）。
        会话不存在或已结束时更新 0 行，静默跳过。
        """
        from db.models import Session as ChatSession

        with self.db_session_factory() as db:
            db.execute(
                update(ChatSession)
                .where(ChatSession.id == session_id,
                       ChatSession.user_id == user_id,
                       ChatSession.status == "active")
                .values(turn_count=ChatSession.turn_count + 1))
            db.commit()

    def _push_state_update(self, user_id: str, character_id: str,
                           new_state: dict, state_before: dict) -> None:
        """第 11.5 步：推送 state_update（裁决③）。

        载荷为完整三层状态 + delta；delta 按裁决⑤取 new_state - state_before
        的真实差（不用 StateDelta 的计算值），仅覆盖数值维度，不含 stage 文本。
        state_before 为本次事件更新前的引擎状态快照（衰减后、apply_event 前）。
        ws_manager 为 None（Phase 8 前）时跳过。
        """
        if self.ws_manager is None:
            return  # TODO: Phase 8 — 注入 WebSocketManager 后启用推送
        delta = {
            "expression": {
                dim: new_state["expression"][dim] - state_before["expression"][dim]
                for dim in EXPRESSION_DIMENSIONS},
            "internal": {
                dim: new_state["internal"][dim] - state_before["internal"][dim]
                for dim in INTERNAL_DIMENSIONS},
            "affective": {
                dim: new_state["affective"][dim] - state_before["affective"][dim]
                for dim in AFFECTIVE_DIMENSIONS},
        }
        self.ws_manager.push(user_id, {
            "type": "state_update",
            "character_id": character_id,
            "expression": new_state["expression"],
            "internal": new_state["internal"],
            "affective": new_state["affective"],   # 含 stage_code / stage_label
            "delta": delta,
        })

    # ── SQLite 持久化（Phase 4 替换占位） ───────────────────

    def _load_user_state(self, character_id: str, user_id: str) -> dict:
        """第 2 步：从 character_user_state 读该用户对该角色的当前状态。

        无行时返回空 dict（引擎用初始值起步）。
        TODO: Phase 7 — mood_baseline 等基线字段的持久化形态。
        """
        from db.user_state import load_user_state

        with self.db_session_factory() as db:
            state, _ = load_user_state(db, character_id, user_id)
        return state

    def _hours_since_last(self, character_id: str, user_id: str) -> float:
        """第 4 步：距上次交互的小时数（last_interaction，naive UTC）。

        无行或无 last_interaction 时返回 0.0（不衰减）。
        """
        from db.models import CharacterUserState, utc_now

        with self.db_session_factory() as db:
            row = db.get(CharacterUserState, (character_id, user_id))
            if row is None or row.last_interaction is None:
                return 0.0
            elapsed = (utc_now() - row.last_interaction).total_seconds() / 3600.0
            return max(elapsed, 0.0)

    def _last_event_valence(self, character_id: str, user_id: str) -> float | None:
        """上一轮事件的 valence（裁决⑤：衰减函数按事件效价变速）。

        取 state_changes 最新一行的 event_label 映射；无历史返回 None
        （引擎回退用当前 PLE 推导）。冷启动即此路径。
        """
        from db.models import StateChange

        with self.db_session_factory() as db:
            label = db.execute(
                select(StateChange.event_label)
                .where(StateChange.character_id == character_id,
                       StateChange.user_id == user_id)
                .order_by(StateChange.id.desc()).limit(1)
            ).scalar_one_or_none()
        if label is None:
            return None
        return EVENT_VALENCE.get(label)

    def _persist_user_state(self, character_id: str, user_id: str,
                            new_state: dict) -> None:
        """第 11 步：写 character_user_state（UPSERT）。

        入库前剔除 affective 里的 stage_code / stage_label（判定值不存储），
        stage_code 单独写 relationship_stage 列（文档 2 第 5.2 节该列的用途）。
        expression_state 里的 `_baseline` 子键（慢心境基线，Phase 7 日沉淀
        写入）必须原样保留——每轮都整体重写 expression，不保留会被抹掉。
        """
        from db.models import CharacterUserState, utc_now
        from db.user_state import BASELINE_KEY

        affective = {dim: new_state["affective"][dim]
                     for dim in AFFECTIVE_DIMENSIONS}
        with self.db_session_factory() as db:
            row = db.get(CharacterUserState, (character_id, user_id))
            if row is None:
                row = CharacterUserState(character_id=character_id, user_id=user_id)
                db.add(row)
            expression = dict(new_state["expression"])
            baseline = _existing_baseline(row.expression_state)
            if baseline:
                expression[BASELINE_KEY] = baseline
            row.expression_state = json.dumps(expression, ensure_ascii=False)
            row.internal_state = json.dumps(new_state["internal"],
                                            ensure_ascii=False)
            row.affective_state = json.dumps(affective, ensure_ascii=False)
            row.relationship_stage = new_state["affective"].get("stage_code")
            row.last_interaction = utc_now()
            row.updated_at = utc_now()
            db.commit()

    # ── Phase 5 起的真实取数 ────────────────────────────────

    def _get_recent_turns(self, character_id: str, user_id: str,
                          n: int = 10) -> list:
        """第 8 步：取最近 n 轮对话（文档 2 第 6.7 节取数方式）。

        messages 表按 (character_id, user_id) 过滤、created_at 倒序取 n 条
        后反转，正文按 memory_id 从 YantrikDB 回读（MemoryManager.get_memory）。
        """
        from db.models import Message

        with self.db_session_factory() as db:
            rows = db.execute(
                select(Message).where(
                    Message.character_id == character_id,
                    Message.user_id == user_id,
                ).order_by(Message.created_at.desc(), Message.id.desc())
                .limit(n)
            ).scalars().all()
        rows.reverse()

        turns: list[ConversationTurn] = []
        for row in rows:
            memory = self.memory_manager.get_memory(row.memory_id)
            if memory is None:
                continue  # 记忆缺失（极端情况）：跳过该行，不阻塞装配
            turns.append(ConversationTurn(
                character_id=character_id,
                user_id=user_id,
                role=memory["role"] or row.role,
                text=memory["content"] or "",
                timestamp=memory["timestamp"] or now_iso(),
            ))
        return turns


def create_pipeline(llm_client: LLMClient,
                    emotion_config_path: str,
                    db_session_factory: sessionmaker[Session],
                    yantrik_path: str = "data/yantrik/dudulu_memory.db",
                    embedder: str | None = None,
                    ws_manager=None) -> Pipeline:
    """Phase 8 装配：分类器 / 装配器 / 后处理 / 角色管理 / MemoryManager /
    Sedimenter / proactive_state / WebSocketManager 全部为真实实现。

    输入：LLM 客户端、数值配置路径、SQLite 会话工厂、YantrikDB 路径、
        嵌入器模式（None 时取 Settings.YANTRIK_EMBEDDER）、
        ws_manager（None = 不推送状态变更，测试默认；Web 进程传
        api.routes_ws 的单例）；
    输出：可运行的 Pipeline；副作用：读取分类器提示词模板、打开记忆库
        （default 模式首次运行需联网下载嵌入模型）。
    """
    from config.settings import get_settings
    from db.proactive_state import ProactiveStateStore
    from core.character.manager import get_character_manager
    from memory.memory_manager import get_memory_manager
    from scheduler.jobs import build_sedimenter

    memory_manager = get_memory_manager(
        yantrik_path, db_session_factory=db_session_factory, embedder=embedder)
    return Pipeline(
        character_manager=get_character_manager(db_session_factory),
        memory_manager=memory_manager,
        sedimenter=build_sedimenter(db_session_factory, memory_manager,
                                    get_settings()),
        classifier=Classifier(llm_client),
        prompt_assembler=PromptAssembler(),
        llm_client=llm_client,
        proactive_state=ProactiveStateStore(db_session_factory),
        post_processor=PostProcessor(),
        ws_manager=ws_manager,
        emotion_config_path=emotion_config_path,
        db_session_factory=db_session_factory,
    )
