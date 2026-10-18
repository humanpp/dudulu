"""全局/用户配置键值存储（SQLite config 表，文档 2 第 5.2 / 6.10 节）。

Phase 10 起配置按 user 分区（额外问题 1 的修复）：
- `__global__` 行是全局默认（管理员/部署级）
- 用户级 PUT 写当前 user_id 的覆盖行
- GET 返回「全局默认 + 当前用户覆盖」的合并视图
值为 JSON 文本。
"""

from __future__ import annotations

import copy
import json

from sqlalchemy.orm import Session

from db.models import Config, GLOBAL_CONFIG_USER, utc_now

# 配置键（用户级作用域）
LLM_CONFIG_KEY = "llm"
PROACTIVE_CONFIG_KEY = "proactive"


def load_config(db: Session, key: str,
                user_id: str | None = None) -> dict:
    """读一份配置（user_id 为空读全局默认；否则读该用户的覆盖）。"""
    row = _get_row(db, user_id, key)
    return _decode(row.value if row else None)


def load_merged_config(db: Session, key: str, user_id: str) -> dict:
    """合并视图：全局默认 + 当前用户覆盖（用户覆盖优先）。"""
    merged = load_config(db, key, user_id=GLOBAL_CONFIG_USER)
    return merge_overrides(merged, load_config(db, key, user_id=user_id))


def save_config(db: Session, key: str, value: dict,
                user_id: str | None = None) -> None:
    """写一份配置（UPSERT；user_id 缺省写全局默认。调用方负责 commit）。"""
    row = _get_row(db, user_id, key)
    if row is None:
        row = Config(user_id=user_id or GLOBAL_CONFIG_USER, key=key, value="{}")
        db.add(row)
    row.value = json.dumps(value, ensure_ascii=False)
    row.updated_at = utc_now()


def merge_overrides(base: dict, overrides: dict) -> dict:
    """深合并：overrides 覆盖 base（dict 递归，其余整体替换）。"""
    merged = copy.deepcopy(base)
    for field, value in overrides.items():
        if (field in merged and isinstance(merged[field], dict)
                and isinstance(value, dict)):
            merged[field] = merge_overrides(merged[field], value)
        else:
            merged[field] = copy.deepcopy(value)
    return merged


def _get_row(db: Session, user_id: str | None, key: str) -> Config | None:
    return db.get(Config, (user_id or GLOBAL_CONFIG_USER, key))


def _decode(text: str | None) -> dict:
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
