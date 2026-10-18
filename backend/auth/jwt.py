"""JWT 签发与验证（文档 2 第 7.1 节）。

有效期取 Settings.TOKEN_EXPIRE_DAYS（默认 30 天，文档 2 第 8.1 节）。
文档示例用 datetime.utcnow()，Python 3.12 已弃用，此处用等价的
timezone-aware datetime.now(timezone.utc)（PyJWT 正确转换为 exp 时间戳）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from config.settings import get_settings

ALGORITHM = "HS256"


def create_token(user_id: str) -> str:
    """签发 JWT。

    输入：user_id；输出：token 串（payload: sub=user_id, exp=now+30 天）；
    副作用：无。
    """
    settings = get_settings()
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=settings.TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str:
    """验证 JWT 并取出 user_id。

    输入：token 串；输出：user_id；副作用：无。
    令牌无效或过期时抛 jwt.PyJWTError，由调用方转 401。
    """
    settings = get_settings()
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    return payload["sub"]
