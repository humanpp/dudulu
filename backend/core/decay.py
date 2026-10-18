"""衰减函数：文档 1 第 9.2 节定义的指数衰减，表达层与情感层共用。"""

from __future__ import annotations

import math


def decay(current: float, baseline: float, half_life_hours: float,
          hours_elapsed: float, valence: float) -> float:
    """按半衰期把 current 向 baseline 指数回归（文档 1 第 9.2 节）。

    输入：
        current: 当前值
        baseline: 衰减目标基线
        half_life_hours: 半衰期（小时）
        hours_elapsed: 经过的小数小时数
        valence: 当前情感效价（-1.0 ~ 1.0），正效价减慢衰减、负效价加快衰减
    输出：
        衰减后的新值
    副作用：无（纯函数）
    """
    decay_rate = 0.693 / half_life_hours
    adjusted_rate = decay_rate * (1.0 - 0.3 * valence)
    return baseline + (current - baseline) * math.exp(-adjusted_rate * hours_elapsed)
