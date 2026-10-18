"""YantrikDB 封装（文档 2 第 3.6 节；真实 API 契约以 Phase 5 裁决 A–E 为准）。

真实库为 PyPI `yantrikdb`（0.23.x），与文档 2 第 3.6/5.1 节示例的虚构 API
（insert/search/semantic_score）不同，实际契约：

- 存储：`db.record(text, memory_type=, importance=, valence=, half_life=,
  namespace=, source=, emotional_state=, metadata=, created_at=)` → rid
- 检索：`db.recall(query=, namespace=, time_window=, memory_type=, top_k=)`
  → dict 列表，每项含 `score`（DB 综合分）与 `scores` 因子分解
  （similarity / decay / recency / importance / graph_proximity /
  valence_multiplier / contributions）
- 嵌入器：`YantrikDB.with_default(path)`（捆绑模型，维度 256；离线降级 64，
  维度在库创建时永久锁定）或 `set_embedder(带 encode() 方法的对象)`

映射（裁决 A）：
- namespace = f"{character_id}|{user_id}" 双键隔离（精确匹配）
- memory_type = conversation_turn / proactive_message / session_summary /
  daily_snapshot / weekly_trend（is_proactive → proactive_message）
- source = role（user / assistant，实测可原样存取）
- content→text、half_life_hours×3600→half_life、emotion_tag→emotional_state、
  importance/valence 同名；trigger_reason/expression_snapshot/intensity/
  timestamp(ISO)/character_id/user_id 进 metadata；created_at 传 unix 浮点

重排（裁决 B）：文档权重 0.4/0.3/0.2/0.1 不变，落到 DB 因子分解上——
similarity/recency/importance 取 scores，emotion_match 由本类自算；
valence_multiplier 与 graph_proximity 不参与；scores 缺失时回退 DB 综合分。
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from fastapi import Depends
from sqlalchemy.orm import sessionmaker
from yantrikdb import YantrikDB

from config.settings import Settings, get_settings
from db.models import Message, utc_now
from db.sqlite import get_session_factory
from memory.schemas import ConversationTurn, generate_id, now_iso

logger = logging.getLogger(__name__)

# 双键分隔符：两个 ID 均为 [a-zA-Z0-9_]，不含 "|"，无歧义（裁决 D）
NAMESPACE_SEPARATOR = "|"

# 记忆分类（裁决 A：memory_type 是一等公民过滤器，记忆浏览器 UI 按它筛选）
MEMORY_TYPE_CONVERSATION = "conversation_turn"
MEMORY_TYPE_PROACTIVE = "proactive_message"
MEMORY_TYPE_SESSION_SUMMARY = "session_summary"
MEMORY_TYPE_DAILY_SNAPSHOT = "daily_snapshot"
MEMORY_TYPE_WEEKLY_TREND = "weekly_trend"

# 四因子重排权重（文档 2 第 3.6 节，固定不许调）
WEIGHT_SIMILARITY = 0.4
WEIGHT_EMOTION = 0.3
WEIGHT_RECENCY = 0.2
WEIGHT_IMPORTANCE = 0.1

# 默认记忆半衰期（小时）：文档 2 第 5.1 节示例值 168；公式未定义，暂常量
DEFAULT_HALF_LIFE_HOURS = 168.0

# ── 记忆衰减堆判定阈值（文档 1 第 12.2/12.3 节 + 裁决①②③，进第十六章） ──
# "重要"：importance ≥ 0.7（与 event_labels 强度体系一致：vulnerable/conflict
# 基准 0.7 是"值得记住"的分界）
DECAY_HEAP_IMPORTANCE_MIN = 0.7
# "即将"条件一：DB 的 decay 因子 < 0.3（当前强度已经很低）
DECAY_HEAP_DECAY_MAX = 0.3
# "即将"条件二：0 < 半衰期剩余 < 72h（剩下多少时间；避免 half_life 很长
# 的记忆被过早提起——decay 低但还有大半年才忘的记忆提起它是打扰）
DECAY_HEAP_REMAINING_HOURS = 72.0
# 候选池规模（裁决③：recall 取 50 条后应用层两级过滤）
DECAY_HEAP_POOL_SIZE = 50
# 衰减堆不考虑的记忆类型（沉淀产物不是"可引用的记忆"）
DECAY_HEAP_EXCLUDED_TYPES = frozenset({
    MEMORY_TYPE_SESSION_SUMMARY, MEMORY_TYPE_DAILY_SNAPSHOT,
    MEMORY_TYPE_WEEKLY_TREND})

# 离线降级嵌入器的维度（potion-base-2M）：检测到即告警（裁决 C 防线 1）
DEGRADED_EMBED_DIM = 64

# 假嵌入器维度：普通 YantrikDB(path) 新建库的期望维度（实测 384）
FAKE_EMBED_DIM = 384


def build_namespace(character_id: str, user_id: str) -> str:
    """双键 namespace：f"{character_id}|{user_id}"（裁决 D）。"""
    return f"{character_id}{NAMESPACE_SEPARATOR}{user_id}"


class DeterministicEmbedder:
    """确定性假嵌入器（YANTRIK_EMBEDDER="fake"，仅开发调试与单测）。

    同一文本永远得到同一向量（md5 种子，跨进程稳定），不同文本得到不同
    向量；无语义能力（相似度不代表语义），生产必须用 default 模式。
    维度固定为 FAKE_EMBED_DIM，匹配普通构造库的期望维度。
    """

    def encode(self, text: str) -> list[float]:
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16) % (2**32)
        rng = np.random.default_rng(seed)
        return rng.random(FAKE_EMBED_DIM).astype("float32").tolist()


class MemoryManager:
    """职责：管理 YantrikDB 中的所有记忆，按 (character_id, user_id) 双键隔离
    （文档 2 第 3.6 节；真实 API 见模块 docstring）。"""

    def __init__(self, db_path: str,
                 db_session_factory: sessionmaker | None = None,
                 embedder: str | None = None):
        """初始化记忆管理器。

        输入：
            db_path: YantrikDB 文件路径（Settings.YANTRIK_PATH）
            db_session_factory: SQLite 会话工厂（写 messages 索引行用；
                None 时不写索引）
            embedder: "default"（with_default 捆绑模型）/ "fake"（确定性假嵌入）；
                None 时取 Settings.YANTRIK_EMBEDDER
        副作用：打开/创建记忆库文件；default 模式首次运行需联网下载嵌入模型。
        """
        self.db_path = db_path
        self._db_session_factory = db_session_factory
        # YantrikDB 不会自动创建父目录（data/yantrik/ 需存在）
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        mode = embedder or get_settings().YANTRIK_EMBEDDER
        if mode == "fake":
            self.db = YantrikDB(db_path)
            self.db.set_embedder(DeterministicEmbedder())
            logger.info("MemoryManager 使用假嵌入器（仅调试）: %s", db_path)
        elif mode == "default":
            self.db = YantrikDB.with_default(db_path)
            self._warn_if_degraded()
            logger.info("MemoryManager 使用捆绑嵌入模型: %s", db_path)
        else:
            raise ValueError(f"未知嵌入器模式: {mode}")

    def _warn_if_degraded(self) -> None:
        """裁决 C 防线 1：检测到离线降级维度（64）时打 WARNING。"""
        try:
            dimension = len(self.db.embed("dimension probe"))
        except Exception as exc:  # 探测失败不阻塞启动
            logger.warning("嵌入器维度探测失败: %s", exc)
            return
        if dimension <= DEGRADED_EMBED_DIM:
            logger.warning(
                "当前使用离线降级嵌入模型（维度 %d），语义检索质量下降；"
                "建议联网后删除记忆库文件重建（维度创建时永久锁定）: %s",
                dimension, self.db_path)

    # ── 存储 ────────────────────────────────────────────────

    def store_conversation(self, turn: ConversationTurn,
                           session_id: str | None = None) -> str:
        """存储一轮对话，附带双键和情感标记（文档 2 第 3.6 节）。

        输入：ConversationTurn；session_id 非空时同时写 messages 索引行
            （memory_id 关联 YantrikDB rid）；
        输出：YantrikDB rid；副作用：写记忆库 + SQLite messages 表。
        """
        metadata: dict = {
            "character_id": turn.character_id,
            "user_id": turn.user_id,
            "timestamp": turn.timestamp,
        }
        if turn.trigger_reason is not None:
            metadata["trigger_reason"] = turn.trigger_reason
        if turn.expression_state is not None:
            metadata["expression_snapshot"] = turn.expression_state
        if turn.intensity is not None:
            metadata["intensity"] = turn.intensity

        rid = self.db.record(
            text=turn.text,
            memory_type=(MEMORY_TYPE_PROACTIVE if turn.is_proactive
                         else MEMORY_TYPE_CONVERSATION),
            importance=self._calc_importance(turn),
            valence=turn.valence if turn.valence is not None else 0.0,
            half_life=self._calc_half_life(turn) * 3600.0,  # 小时 → 秒
            namespace=build_namespace(turn.character_id, turn.user_id),
            source=turn.role,
            emotional_state=turn.emotion_tag,
            metadata=metadata,
            created_at=self._to_unix(turn.timestamp),
        )
        if session_id is not None and self._db_session_factory is not None:
            self._write_message_index(session_id, turn, rid)
        return rid

    def _write_message_index(self, session_id: str, turn: ConversationTurn,
                             rid: str) -> None:
        """messages 索引行（正文在 YantrikDB，memory_id 关联，文档 2 第 6.7 节）。

        created_at 显式取当前时间（微秒精度）：CURRENT_TIMESTAMP 只有秒级，
        同一轮的两条消息会同秒，倒序取数时顺序不稳定。
        """
        with self._db_session_factory() as db:
            db.add(Message(
                id=generate_id(),
                session_id=session_id,
                character_id=turn.character_id,
                user_id=turn.user_id,
                role=turn.role,
                memory_id=rid,
                created_at=utc_now(),
            ))
            db.commit()

    @staticmethod
    def _to_unix(iso_timestamp: str) -> float:
        """ISO 秒级字符串 → unix 浮点（record 的 created_at 只收实数）。"""
        try:
            return datetime.fromisoformat(iso_timestamp).timestamp()
        except ValueError:
            return time.time()

    def _calc_importance(self, turn: ConversationTurn) -> float:
        """记忆重要性。

        文档未定义公式，暂取事件强度（无强度时 0.5），标记待调优。
        """
        return turn.intensity if turn.intensity is not None else 0.5

    def _calc_half_life(self, turn: ConversationTurn) -> float:
        """记忆半衰期（小时）。

        文档未定义公式，暂取常量 168（文档 2 第 5.1 节示例值），标记待调优。
        """
        return DEFAULT_HALF_LIFE_HOURS

    # ── 检索 ────────────────────────────────────────────────

    def retrieve(self, query: str, character_id: str, user_id: str,
                 current_state: dict,
                 emotion_tag: str = None,
                 time_range: str = None,
                 top_k: int = 5) -> list:
        """混合检索（强制双键过滤，文档 2 第 3.6 节）。

        - 语义相似度 / 时间近因性 / 关系重要性：取 DB 的 scores 因子分解
        - 情感匹配度：本类 _emotion_match 自算
        - 衰减门控：DB 的 decay 因子（不进文档权重，仅 DB 内部用）

        输入：query、双键、当前完整状态、可选情感标签/时间范围/top_k；
        输出：重排后的记忆 dict 列表（字段见 _normalize）；副作用：无。
        """
        time_window = self._parse_time_range(time_range)
        # DB 先按相关性取候选池（放大 3 倍且至少 20 条），四因子重排后取 top_k；
        # emotion_tag 是应用层后过滤（recall 不支持 emotional_state 过滤），
        # 有损操作需更大候选池才能保证过滤后仍可能达到 top_k（裁决②）
        if emotion_tag:
            pool_size = max(top_k * 10, 100)
        else:
            pool_size = max(top_k * 3, 20)
        hits = self.db.recall(
            query=query,
            namespace=build_namespace(character_id, user_id),
            time_window=time_window,
            top_k=pool_size,
        )
        # recall 不支持 emotional_state 过滤（实测），在此后过滤
        if emotion_tag:
            hits = [hit for hit in hits
                    if hit.get("emotional_state") == emotion_tag]

        scored = [(self._rerank_score(hit, current_state), hit)
                  for hit in hits]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._normalize(hit) for _, hit in scored[:top_k]]

    def _rerank_score(self, hit: dict, current_state: dict) -> float:
        """四因子重排（裁决 B）：0.4 相似 + 0.3 情感 + 0.2 近因 + 0.1 重要。

        similarity/recency/importance 取 DB 的 scores 分解；scores 缺失时
        回退为 DB 综合分（裁决 B 的回退路径）。
        """
        scores = hit.get("scores") or {}
        if not scores:
            return float(hit.get("score") or 0.0)
        return (WEIGHT_SIMILARITY * scores.get("similarity", 0.0)
                + WEIGHT_EMOTION * self._emotion_match(hit, current_state)
                + WEIGHT_RECENCY * scores.get("recency", 0.0)
                + WEIGHT_IMPORTANCE * scores.get("importance", 0.0))

    def _emotion_match(self, memory: dict, current_state: dict) -> float:
        """记忆情感色调与当前心境的吻合度（文档 2 第 3.6 节 _emotion_match）。

        文档未定义公式：暂按记忆 valence 与当前表达层 PLE 的吻合度计算
        （两者同域 [-1, 1]，吻合度 = 1 - |差|/2），标记待调优；
        Phase 7 沉淀器接入后可改用内部层状态（如 ANX 偏高时偏向负面记忆）。
        """
        pleasure = float(current_state.get("expression", {}).get("PLE", 0.0))
        valence = float(memory.get("valence") or 0.0)
        return 1.0 - abs(valence - pleasure) / 2.0

    def _parse_time_range(self, time_range: str | None) -> tuple | None:
        """time_range → recall 的 (start_ts, end_ts) 绝对时间窗（unix 秒）。

        "last_7_days" / "last_30_days" / "all" / None；未知值按 all 处理并告警。
        """
        if not time_range or time_range == "all":
            return None
        days = {"last_7_days": 7, "last_30_days": 30}.get(time_range)
        if days is None:
            logger.warning("未知 time_range，按 all 处理: %r", time_range)
            return None
        now = time.time()
        return (now - days * 86400.0, now)

    # ── 沉淀记录存储（Phase 7 沉淀器用） ─────────────────────

    def store_sediment(self, *, character_id: str, user_id: str,
                       memory_type: str, text: str, metadata: dict,
                       importance: float = 0.5, valence: float = 0.0,
                       half_life_hours: float | None = None,
                       timestamp: str | None = None) -> str:
        """存储一条沉淀记录（session_summary / daily_snapshot / weekly_trend）。

        输入：双键、记忆类型、正文、结构化 metadata（文档 1 第十一章字段）、
            重要性/效价/半衰期（小时，None 取默认）/ISO 时间戳；
        输出：YantrikDB rid；副作用：写记忆库。
        不写 messages 索引（沉淀不是对话消息，文档 2 第 6.7 节范围外）。
        """
        metadata = dict(metadata)
        metadata.setdefault("character_id", character_id)
        metadata.setdefault("user_id", user_id)
        metadata.setdefault("timestamp", timestamp or now_iso())
        return self.db.record(
            text=text,
            memory_type=memory_type,
            importance=importance,
            valence=valence,
            half_life=(half_life_hours if half_life_hours is not None
                       else DEFAULT_HALF_LIFE_HOURS) * 3600.0,
            namespace=build_namespace(character_id, user_id),
            source="system",
            metadata=metadata,
            created_at=self._to_unix(metadata["timestamp"]),
        )

    def list_sediments(self, character_id: str, user_id: str,
                       memory_type: str, limit: int = 100) -> list[dict]:
        """按类型列举某双键下的沉淀记录（判重与汇总用，Phase 7）。

        输入：双键、记忆类型、上限；输出：原始记忆 dict 列表
            （含 rid/metadata/created_at 等，按 created_at 升序）；
        副作用：无。基于 YantrikDB list_memories（命名空间精确隔离）。
        """
        result = self.db.list_memories(
            memory_type=memory_type,
            namespace=build_namespace(character_id, user_id),
            limit=limit,
        )
        return list(result.get("memories") or [])

    def find_forgetting_important(
            self, character_id: str, user_id: str,
            top_k: int = DECAY_HEAP_POOL_SIZE) -> list[dict]:
        """找"即将遗忘的重要记忆"（文档 1 第 12.2 节衰减堆触发 + 裁决①②③）。

        两级过滤都在应用层（不依赖 DB）：
        1. importance ≥ 0.7（"重要"）
        2. scores.decay < 0.3 且 0 < 半衰期剩余 < 72h（"即将"）
        输出按 decay 升序（最接近遗忘的在前，供 P0 消息引用）。

        实现注记（API 缺口与副作用，已报交付报告）：
        1. recall 的 hit 不带 half_life，list_memories 不带 scores.decay，
           故按 rid 关联合并两次查询；每次 tick 每对只调一次。
        2. 实测 recall 默认会把命中记忆的 half_life ×1.2（DB 的检索强化/
           间隔重复效应）——"检查"不该有副作用，故此处必须传
           skip_reinforce=True；管道检索（retrieve）是真实检索，保留默认
           强化行为。
        3. recall 的 decay 因子随年龄衰减但不受 half_life 影响，
           "半衰期剩余"单取 list_memories 的 half_life - (now - created_at)。
        """
        namespace = build_namespace(character_id, user_id)
        try:
            hits = self.db.recall(query="重要的事", namespace=namespace,
                                  top_k=top_k, skip_reinforce=True)
        except Exception as exc:  # 检索失败不阻塞心跳
            logger.warning("衰减堆检索失败 %s/%s: %s", character_id,
                           user_id, exc)
            return []
        decay_by_rid = {hit.get("rid"): (hit.get("scores") or {}).get("decay")
                        for hit in hits}
        records = self.db.list_memories(namespace=namespace, limit=top_k)
        now = time.time()
        candidates = []
        for record in records.get("memories") or []:
            rid = record.get("rid")
            if record.get("type") in DECAY_HEAP_EXCLUDED_TYPES:
                continue
            importance = record.get("importance")
            if importance is None or float(importance) < DECAY_HEAP_IMPORTANCE_MIN:
                continue
            decay = decay_by_rid.get(rid)
            if decay is None or float(decay) >= DECAY_HEAP_DECAY_MAX:
                continue
            half_life = record.get("half_life") or 0.0
            created_at = record.get("created_at") or now
            remaining_hours = (float(half_life) - (now - float(created_at))) \
                / 3600.0
            if not (0.0 < remaining_hours < DECAY_HEAP_REMAINING_HOURS):
                continue
            memory = self._normalize(record)
            memory["decay_factor"] = float(decay)
            memory["remaining_hours"] = round(remaining_hours, 2)
            candidates.append(memory)
        candidates.sort(key=lambda item: item["decay_factor"])
        return candidates

    # ── 单条回读 ────────────────────────────────────────────

    def get_memory(self, rid: str) -> dict | None:
        """按 rid 回读单条记忆（/api/messages/recent 正文回读用）。

        输入：YantrikDB rid；输出：归一化记忆 dict，不存在返回 None；副作用：无。
        """
        if not rid:
            return None
        try:
            hit = self.db.get_memory(rid)
        except Exception as exc:  # rid 不存在/损坏：按缺失处理
            logger.debug("记忆回读失败 rid=%s: %s", rid, exc)
            return None
        return self._normalize(hit) if hit else None

    def _normalize(self, hit: dict) -> dict:
        """YantrikDB hit dict → 文档 2 第 5.1 节记忆条目字段名。"""
        metadata = hit.get("metadata") or {}
        memory_type = hit.get("type") or MEMORY_TYPE_CONVERSATION
        return {
            "id": hit.get("rid"),
            "character_id": metadata.get("character_id"),
            "user_id": metadata.get("user_id"),
            "content": hit.get("text"),
            "type": memory_type,
            "role": hit.get("source"),
            "is_proactive": memory_type == MEMORY_TYPE_PROACTIVE,
            "trigger_reason": metadata.get("trigger_reason"),
            "timestamp": metadata.get("timestamp"),
            "emotion_tag": hit.get("emotional_state"),
            "intensity": metadata.get("intensity"),
            "valence": hit.get("valence"),
            "importance": hit.get("importance"),
            "half_life_hours": (hit.get("half_life") or 0.0) / 3600.0,
            "expression_snapshot": metadata.get("expression_snapshot"),
        }


# ── FastAPI 依赖（带实例缓存，避免每请求重开记忆库） ──────────

_MANAGER_CACHE: dict[tuple, MemoryManager] = {}


def get_memory_manager(db_path: str,
                       db_session_factory: sessionmaker | None = None,
                       embedder: str | None = None) -> MemoryManager:
    """按 (db_path, 工厂, 模式) 缓存返回 MemoryManager 实例。

    with_default 重开虽仅 ~0.3s（模型已缓存），缓存可避免每请求重开库文件；
    测试用不同临时路径天然区分。副作用：首次调用打开/创建记忆库。
    """
    key = (db_path, db_session_factory, embedder or get_settings().YANTRIK_EMBEDDER)
    manager = _MANAGER_CACHE.get(key)
    if manager is None:
        manager = MemoryManager(db_path, db_session_factory=db_session_factory,
                                embedder=embedder)
        _MANAGER_CACHE[key] = manager
    return manager


def memory_manager_dependency(
    db_factory: sessionmaker = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> MemoryManager:
    """FastAPI 依赖：按配置提供 MemoryManager（测试可覆盖为假嵌入器实例）。"""
    return get_memory_manager(settings.YANTRIK_PATH,
                              db_session_factory=db_factory)
