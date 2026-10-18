"""记忆数据结构（文档 2 第 2 章 memory/schemas.py）。

ConversationTurn：管道存储一轮对话的入参（文档 2 第 3.6 节）；
SessionSummary / DailySnapshot / WeeklyTrend：沉淀记录（文档 1 第 11.1 /
11.2 / 11.3 节字段），正文进 YantrikDB，结构化字段进记忆 metadata。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


def generate_id() -> str:
    """生成 uuid 字符串（用户/角色/会话/消息等主键，文档 2 第 3.6 节使用）。"""
    return uuid.uuid4().hex


def now_iso() -> str:
    """当前本地时间的 ISO 秒级字符串（形如 2026-10-14T23:15:00，与文档示例一致）。"""
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class ConversationTurn:
    """一轮对话，MemoryManager.store_conversation 的入参（文档 2 第 3.6 节）。"""

    character_id: str
    user_id: str
    role: str                       # user / assistant
    text: str
    is_proactive: bool = False
    trigger_reason: str | None = None
    timestamp: str = field(default_factory=now_iso)
    emotion_tag: str | None = None
    intensity: float | None = None
    expression_state: dict | None = None
    # TODO: Phase 5 — valence 的产出规则文档未定义（分类器只输出
    # label/intensity/busy_signal，见裁决②），暂记 None，待文档补充
    valence: float | None = None


@dataclass
class SessionSummary:
    """会话沉淀记录（文档 1 第 11.1 节字段；不含 STG——阶段由 8.2 判定，
    不参与沉淀加权）。ending_tone 存全部五个表达层维度（文档示例只列了
    PLE/ARO/INT 三项，此处补全以便后续使用）。"""

    session_id: str
    character_id: str
    duration_minutes: int
    turn_count: int
    peak_event: dict = field(default_factory=dict)     # label/intensity/turn
    ending_tone: dict = field(default_factory=dict)    # 表达层五维
    overall_valence: float = 0.0
    key_memory: str = ""
    timestamp: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionSummary":
        """从 metadata dict 还原；副作用：无。"""
        return cls(
            session_id=data["session_id"],
            character_id=data["character_id"],
            duration_minutes=int(data.get("duration_minutes", 0)),
            turn_count=int(data.get("turn_count", 0)),
            peak_event=dict(data.get("peak_event") or {}),
            ending_tone=dict(data.get("ending_tone") or {}),
            overall_valence=float(data.get("overall_valence", 0.0)),
            key_memory=data.get("key_memory", ""),
            timestamp=data.get("timestamp") or now_iso(),
        )

    def to_dict(self) -> dict:
        """输出 metadata dict；副作用：无。"""
        return {
            "session_id": self.session_id,
            "character_id": self.character_id,
            "duration_minutes": self.duration_minutes,
            "turn_count": self.turn_count,
            "peak_event": dict(self.peak_event),
            "ending_tone": dict(self.ending_tone),
            "overall_valence": self.overall_valence,
            "key_memory": self.key_memory,
            "timestamp": self.timestamp,
        }


@dataclass
class DailySnapshot:
    """日沉淀记录（文档 1 第 11.2 节字段）。

    day_end_affective 为文档示例之外的补充字段：周趋势分析需要历史
    AFF/TRS/ATT 才能算 security/intimacy 趋势（见交付报告说明）。
    """

    date: str
    character_id: str
    sessions: list[str] = field(default_factory=list)
    day_end_expression: dict = field(default_factory=dict)
    day_end_internal: dict = field(default_factory=dict)
    day_end_affective: dict = field(default_factory=dict)
    consolidated_events: list = field(default_factory=list)
    daily_valence: float = 0.0
    trend: str = "stable"
    timestamp: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, data: dict) -> "DailySnapshot":
        """从 metadata dict 还原；副作用：无。"""
        return cls(
            date=data["date"],
            character_id=data["character_id"],
            sessions=list(data.get("sessions") or []),
            day_end_expression=dict(data.get("day_end_expression") or {}),
            day_end_internal=dict(data.get("day_end_internal") or {}),
            day_end_affective=dict(data.get("day_end_affective") or {}),
            consolidated_events=list(data.get("consolidated_events") or []),
            daily_valence=float(data.get("daily_valence", 0.0)),
            trend=data.get("trend", "stable"),
            timestamp=data.get("timestamp") or now_iso(),
        )

    def to_dict(self) -> dict:
        """输出 metadata dict；副作用：无。"""
        return {
            "date": self.date,
            "character_id": self.character_id,
            "sessions": list(self.sessions),
            "day_end_expression": dict(self.day_end_expression),
            "day_end_internal": dict(self.day_end_internal),
            "day_end_affective": dict(self.day_end_affective),
            "consolidated_events": [dict(event)
                                    for event in self.consolidated_events],
            "daily_valence": self.daily_valence,
            "trend": self.trend,
            "timestamp": self.timestamp,
        }


@dataclass
class WeeklyTrend:
    """周趋势记录（文档 1 第 11.3 节字段）。"""

    week: str
    character_id: str
    avg_valence: float = 0.0
    valence_trend: str = "stable"
    security_trend: str = "stable"
    intimacy_trend: str = "stable"
    stage_recommendation: str = ""
    notable_patterns: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, data: dict) -> "WeeklyTrend":
        """从 metadata dict 还原；副作用：无。"""
        return cls(
            week=data["week"],
            character_id=data["character_id"],
            avg_valence=float(data.get("avg_valence", 0.0)),
            valence_trend=data.get("valence_trend", "stable"),
            security_trend=data.get("security_trend", "stable"),
            intimacy_trend=data.get("intimacy_trend", "stable"),
            stage_recommendation=data.get("stage_recommendation", ""),
            notable_patterns=list(data.get("notable_patterns") or []),
            timestamp=data.get("timestamp") or now_iso(),
        )

    def to_dict(self) -> dict:
        """输出 metadata dict；副作用：无。"""
        return {
            "week": self.week,
            "character_id": self.character_id,
            "avg_valence": self.avg_valence,
            "valence_trend": self.valence_trend,
            "security_trend": self.security_trend,
            "intimacy_trend": self.intimacy_trend,
            "stage_recommendation": self.stage_recommendation,
            "notable_patterns": list(self.notable_patterns),
            "timestamp": self.timestamp,
        }
