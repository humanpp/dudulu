"""用户-角色状态的读取辅助（character_user_state 表，文档 2 第 5.2 节）。

路由层（/api/state、/api/memory/search）与管道共用：读出的 JSON 交给
情感引擎做阶段判定等计算，本模块只做存储读取，不碰 core（保持分层）。

mood_baseline（慢心境基线，文档 1 第 11.4 节）的持久化形态（Phase 7 裁决）：
存在 expression_state JSON 的 `_baseline` 子键里（不改 DDL），读取时提取到
user_state 顶层 `mood_baseline` 键——情感引擎的构造函数契约读顶层键，
而 expression 维度 dict 保持干净（只含 PLE/ARO/DOM/INT/SEC）。
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from db.models import CharacterUserState

# expression_state JSON 内的慢心境基线子键（Phase 7 裁决）
BASELINE_KEY = "_baseline"


def load_user_state(db: Session, character_id: str,
                    user_id: str) -> tuple[dict, datetime | None]:
    """读 (character_id, user_id) 的当前状态。

    输入：请求级 Session、双键；输出：(user_state dict, updated_at)。
    user_state 形如 {"expression": {...}, "internal": {...}, "affective": {...}}；
    若存有慢心境基线，附带顶层键 "mood_baseline"（引擎第 3 步消费）。
    affective 只含 AFF/TRS/ATT，stage 不入库；无行返回 ({}, None)。
    """
    row = db.get(CharacterUserState, (character_id, user_id))
    if row is None:
        return {}, None
    expression = _loads(row.expression_state)
    state: dict = {
        "expression": {dim: value for dim, value in expression.items()
                       if dim != BASELINE_KEY},
        "internal": _loads(row.internal_state),
        "affective": _loads(row.affective_state),
    }
    baseline = expression.get(BASELINE_KEY)
    if isinstance(baseline, dict) and baseline:
        state["mood_baseline"] = baseline
    return state, row.updated_at


def save_mood_baseline(db: Session, character_id: str, user_id: str,
                       baseline: dict) -> None:
    """写慢心境基线（日沉淀用，文档 1 第 11.4 节：基线更新为 day_end_expression）。

    输入：请求级 Session、双键、表达层五维 dict；输出：无；
    副作用：UPSERT character_user_state，把基线并入 expression_state 的
    `_baseline` 子键（其余状态列不动）。调用方负责 commit。
    """
    from db.models import utc_now

    row = db.get(CharacterUserState, (character_id, user_id))
    if row is None:
        row = CharacterUserState(character_id=character_id, user_id=user_id)
        db.add(row)
    expression = _loads(row.expression_state)
    expression[BASELINE_KEY] = {dim: float(value)
                                for dim, value in baseline.items()}
    row.expression_state = json.dumps(expression, ensure_ascii=False)
    row.updated_at = utc_now()


def load_mood_baseline(db: Session, character_id: str,
                       user_id: str) -> dict:
    """单独读慢心境基线（空 dict = 未沉淀过，用角色初始值）。

    输入：请求级 Session、双键；输出：表达层五维 dict；副作用：无。
    """
    row = db.get(CharacterUserState, (character_id, user_id))
    if row is None:
        return {}
    baseline = _loads(row.expression_state).get(BASELINE_KEY)
    return dict(baseline) if isinstance(baseline, dict) else {}


def _loads(text: str | None) -> dict:
    """JSON TEXT 列 -> dict；空值返回空 dict。"""
    return json.loads(text) if text else {}
