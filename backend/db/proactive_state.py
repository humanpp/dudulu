"""proactive_state 表读写（文档 2 第 5.2 节；Phase 4 替换占位实现）。

退缩状态的唯一权威来源。语义：
- busy_until：用户表达"最近很忙"后的 24h 静默；用户能发消息即清空
  （文档 1 第 12.2 节 v0.7），命中 busy_signal 则重写 now + hours
- consecutive_no_reply：主动消息发出时 +1，用户回复时归零（第 3.11 节）
- silenced_until：连续 5 次无回复 → now + 24h（第 12.4 节）
- last_proactive_at：上次主动消息时间

本模块只做存储原语；退缩规则（3 次翻倍 / 5 次静默）在
scheduler/proactive.py 的 ProactiveEngine 里（策略与存储分离）。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from db.models import ProactiveState, utc_now

logger = logging.getLogger(__name__)


class ProactiveStateStore:
    """proactive_state 表的读写入口（管道 5.5 步与主动引擎的协作者）。"""

    def __init__(self, session_factory: sessionmaker[Session]):
        """输入：session 工厂；副作用：无（不发请求、不建连）。"""
        self._session_factory = session_factory

    # ── busy_until（Phase 4 起） ────────────────────────────

    def clear_busy_until(self, character_id: str, user_id: str) -> None:
        """清空 busy_until（用户重新激活）。

        行不存在时什么都不做（无需为"无退缩状态"建行）。
        """
        with self._session_factory() as db:
            row = db.get(ProactiveState, (character_id, user_id))
            if row is not None:
                row.busy_until = None
                row.updated_at = utc_now()
                db.commit()

    def set_busy_until(self, character_id: str, user_id: str,
                       hours: float) -> None:
        """写入 busy_until = now + hours（行不存在则创建）。"""
        with self._session_factory() as db:
            row = self._get_or_create(db, character_id, user_id)
            row.busy_until = utc_now() + timedelta(hours=hours)
            row.updated_at = utc_now()
            db.commit()

    # ── 退缩计数（Phase 8b） ────────────────────────────────

    def get_state(self, character_id: str,
                  user_id: str) -> dict:
        """读退缩状态快照（主动引擎的门控依据）。

        输出：{consecutive_no_reply, last_proactive_at, silenced_until,
        busy_until}；无行返回全空/零值（不建行）。
        """
        with self._session_factory() as db:
            row = db.get(ProactiveState, (character_id, user_id))
            if row is None:
                return {"consecutive_no_reply": 0, "last_proactive_at": None,
                        "silenced_until": None, "busy_until": None}
            return {
                "consecutive_no_reply": row.consecutive_no_reply or 0,
                "last_proactive_at": row.last_proactive_at,
                "silenced_until": row.silenced_until,
                "busy_until": row.busy_until,
            }

    def record_proactive_sent(self, character_id: str, user_id: str) -> int:
        """记录一次主动消息发出：last_proactive_at = now，无回复计数 +1。

        输出：递增后的 consecutive_no_reply（供调用方判断是否触发静默期）；
        副作用：写 proactive_state（行不存在则创建）。
        """
        with self._session_factory() as db:
            row = self._get_or_create(db, character_id, user_id)
            row.last_proactive_at = utc_now()
            row.consecutive_no_reply = (row.consecutive_no_reply or 0) + 1
            row.updated_at = utc_now()
            count = row.consecutive_no_reply
            db.commit()
        return count

    def register_user_reply(self, character_id: str, user_id: str) -> None:
        """用户回复：无回复计数归零、清空 silenced_until（第 3.11 节）。

        有值可清才写库（每次用户消息都会走到这里，避免无谓写）。
        busy_until 的清空是另一条链路（clear_busy_until，5.5 步）。
        """
        with self._session_factory() as db:
            row = db.get(ProactiveState, (character_id, user_id))
            if row is None:
                return
            if (row.consecutive_no_reply or 0) == 0 and row.silenced_until is None:
                return  # 无可复位状态
            row.consecutive_no_reply = 0
            row.silenced_until = None
            row.updated_at = utc_now()
            db.commit()

    def set_silenced_until(self, character_id: str, user_id: str,
                           hours: float) -> None:
        """写 silenced_until = now + hours（连续无回复达阈值时由引擎调用）。"""
        with self._session_factory() as db:
            row = self._get_or_create(db, character_id, user_id)
            row.silenced_until = utc_now() + timedelta(hours=hours)
            row.updated_at = utc_now()
            db.commit()

    @staticmethod
    def _get_or_create(db: Session, character_id: str,
                       user_id: str) -> ProactiveState:
        """取行或创建（调用方负责 commit）。"""
        row = db.get(ProactiveState, (character_id, user_id))
        if row is None:
            row = ProactiveState(character_id=character_id, user_id=user_id)
            db.add(row)
        return row
