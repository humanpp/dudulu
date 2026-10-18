"""角色主题色管理（文档 1 第 3.3 节 + 文档 3 第 3.1 节）。

后端职责：校验主题色格式（十六进制三/六位）、提供 primary 的透明度推导
（前端 --accent-ghost 用 8% 透明度版本）。前端 CSS 变量名为
--accent-primary / --accent-soft / --accent-deep / --accent-ghost
（文档 3 第 3.1 节），此处仅做数值侧管理，不直接操作 DOM。
"""

from __future__ import annotations

import re

from core.character.profile import Theme

# 十六进制颜色：#RGB 或 #RRGGBB（文档 1 第 3.2 / 3.3 节示例均为 #RRGGBB）
HEX_COLOR_PATTERN = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# 悬停背景的透明度（文档 3 第 3.2 节：primary 的 8% 透明度版本）
GHOST_ALPHA = 0.08


def is_valid_hex(color: str | None) -> bool:
    """判断是否为合法十六进制颜色串；副作用：无。"""
    return bool(color) and bool(HEX_COLOR_PATTERN.match(color))


def validate_theme(theme: dict | None) -> list[str]:
    """校验主题色三值，返回问题列表（空 = 通过）。

    输入：theme dict（primary/soft/deep，可缺省）；输出：问题描述列表；
    副作用：无。
    """
    issues: list[str] = []
    data = theme or {}
    for key in ("primary", "soft", "deep"):
        value = data.get(key)
        if value is None:
            continue  # 缺省走 Theme 默认值，不算问题
        if not is_valid_hex(value):
            issues.append(f"theme.{key} 不是合法十六进制颜色: {value!r}")
    return issues


def hex_to_rgba(color: str, alpha: float = GHOST_ALPHA) -> str:
    """十六进制 → rgba() 串（供 --accent-ghost 使用）。

    输入：#RGB/#RRGGBB 与透明度；输出：rgba(r, g, b, a)；副作用：无。
    非法输入抛 ValueError（调用方为后端自身推导，不走用户输入）。
    """
    if not is_valid_hex(color):
        raise ValueError(f"非法十六进制颜色: {color!r}")
    digits = color[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    red, green, blue = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def theme_css_variables(theme: Theme) -> dict[str, str]:
    """主题色 → 文档 3 第 3.1 节的 CSS 变量映射（供前端/调试核对）。

    输入：Theme；输出：{--accent-primary: ..., ...}；副作用：无。
    """
    return {
        "--accent-primary": theme.primary,
        "--accent-soft": theme.soft,
        "--accent-deep": theme.deep,
        "--accent-ghost": hex_to_rgba(theme.primary),
    }
