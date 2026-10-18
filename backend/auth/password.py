"""bcrypt 密码哈希（文档 2 第 7.1 节 + 第 9 章依赖清单，不用 passlib）。"""

from __future__ import annotations

import bcrypt

# bcrypt 算法上限 72 字节；API 层在密码入参上限制长度（见 routes_auth），
# 这里再兜底截断，避免库直接抛错。
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    """生成密码哈希。

    输入：明文密码；输出：bcrypt 哈希串（utf-8）；副作用：无。
    """
    password_bytes = password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与哈希是否匹配。

    输入：明文密码、存储的哈希串；输出：布尔；副作用：无。
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:MAX_PASSWORD_BYTES],
                              password_hash.encode("utf-8"))
    except ValueError:
        # 哈希串损坏等情况：按校验失败处理，不向上抛
        return False
