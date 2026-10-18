"""FastAPI 认证依赖（文档 2 第 6.1 / 7.1 / 7.2 节）。

- Bearer Token 提取 user_id，从 users 表加载用户；
- X-Timezone 同步（文档 2 第 6.1 节 v1.4 明确）：进程内缓存
  user_id → timezone，每请求只做内存比对，不一致才写一次库并刷新缓存。
  不逐请求查库比对，也不只在登录时同步一次。
- 用户时区兜底 Asia/Shanghai（UTC+8）在读取侧处理（见 effective_timezone），
  与前端兜底是同一个值（文档 2 第 3.11 节）。
"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.jwt import verify_token
from db.models import User
from db.sqlite import get_db

logger = logging.getLogger(__name__)

# 进程内时区缓存：user_id -> timezone（随用户记录首次加载填入）
_TIMEZONE_CACHE: dict[str, str | None] = {}

DEFAULT_TIMEZONE = "Asia/Shanghai"


def effective_timezone(timezone_name: str | None) -> str:
    """用户时区的兜底读取：NULL/空 → Asia/Shanghai（UTC+8）。"""
    return timezone_name or DEFAULT_TIMEZONE


def get_current_user(
    authorization: str | None = Header(default=None),
    x_timezone: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 解析当前用户。

    输入：Authorization 头、X-Timezone 头、请求级 Session；
    输出：User ORM 对象；副作用：可能写一次 users.timezone（时区变化时）。
    令牌缺失/无效/过期、用户不存在 → 401。
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少认证令牌")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        user_id = verify_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="认证无效或已过期") from exc

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    _sync_timezone(db, user, x_timezone)
    return user


def _sync_timezone(db: Session, user: User, x_timezone: str | None) -> None:
    """X-Timezone 同步（文档 2 第 6.1 节）。

    首次见到该用户：把库中现值装入缓存；之后每请求只做内存比对，
    与请求头一致则零成本返回，不一致才写一次库并刷新缓存。
    无效时区名忽略（不写库、不报错）。
    """
    if not x_timezone:
        return
    if user.id not in _TIMEZONE_CACHE:
        _TIMEZONE_CACHE[user.id] = user.timezone
    if _TIMEZONE_CACHE[user.id] == x_timezone:
        return
    try:
        ZoneInfo(x_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.debug("忽略无效时区头: user_id=%s tz=%r", user.id, x_timezone)
        return
    user.timezone = x_timezone
    db.commit()
    _TIMEZONE_CACHE[user.id] = x_timezone
