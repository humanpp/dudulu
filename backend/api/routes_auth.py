"""认证接口（文档 2 第 6.1 节）：注册 / 登录 / 修改密码。"""

from __future__ import annotations

import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from auth.jwt import create_token
from auth.password import hash_password, verify_password
from db.models import User
from db.sqlite import get_db
from memory.schemas import generate_id

logger = logging.getLogger(__name__)

router = APIRouter()


class RegisterRequest(BaseModel):
    """POST /api/auth/register 请求体（文档 2 第 6.1 节）。"""

    username: str = Field(min_length=1, max_length=64)
    # bcrypt 上限 72 字节，超出部分无意义，直接在校验层拒绝
    password: str = Field(min_length=1, max_length=72)


class LoginRequest(BaseModel):
    """POST /api/auth/login 请求体（文档 2 第 6.1 节）。"""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=72)


class ChangePasswordRequest(BaseModel):
    """PUT /api/auth/password 请求体（文档 2 第 6.1 节）。"""

    old_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=1, max_length=72)


def _valid_timezone(x_timezone: str | None) -> str | None:
    """校验 X-Timezone 头；无效返回 None（不落库）。"""
    if not x_timezone:
        return None
    try:
        ZoneInfo(x_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return x_timezone


@router.post("/auth/register")
def register(body: RegisterRequest,
             x_timezone: str | None = Header(default=None),
             db: Session = Depends(get_db)) -> dict:
    """注册并直接签发 token。

    响应：{user_id, token}（文档 2 第 6.1 节）。
    用户名已存在 → 409（文档未定义冲突码，按 REST 惯例取 409，见交付报告）。
    """
    existing = db.execute(
        select(User).where(User.username == body.username)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        id=generate_id(),
        username=body.username,
        password_hash=hash_password(body.password),
        timezone=_valid_timezone(x_timezone),
    )
    db.add(user)
    db.commit()
    logger.info("用户注册: user_id=%s", user.id)
    return {"user_id": user.id, "token": create_token(user.id)}


@router.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)) -> dict:
    """登录。

    响应：{user_id, token}；用户名或密码错误 → 401（不区分是哪一项）。
    """
    user = db.execute(
        select(User).where(User.username == body.username)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {"user_id": user.id, "token": create_token(user.id)}


@router.put("/auth/password")
def change_password(body: ChangePasswordRequest,
                    current_user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> dict:
    """修改密码。

    响应：{status: "ok"}（文档 2 第 6.1 节）。
    旧密码错误 → 401。旧 token 不强制失效（文档设计说明），由前端主动清除。
    """
    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="旧密码错误")
    current_user.password_hash = hash_password(body.new_password)
    db.commit()
    return {"status": "ok"}
