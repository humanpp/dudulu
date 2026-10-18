"""表达层逻辑：5 维定义、范围钳制与耦合约束软钳制（文档 1 第六章）。"""

from __future__ import annotations

# 维度符号 -> 中文标签（文档 1 第 6.1 节）
EXPRESSION_LABELS: dict[str, str] = {
    "PLE": "愉悦度",
    "ARO": "唤醒度",
    "DOM": "支配感",
    "INT": "亲密感",
    "SEC": "安全感",
}

# 维度取值范围（文档 1 第 6.1 节）
EXPRESSION_RANGES: dict[str, tuple[float, float]] = {
    "PLE": (-1.0, 1.0),
    "ARO": (-1.0, 1.0),
    "DOM": (-1.0, 1.0),
    "INT": (0.0, 1.0),
    "SEC": (0.0, 1.0),
}

EXPRESSION_DIMENSIONS: tuple[str, ...] = tuple(EXPRESSION_LABELS)

# 耦合约束拉回比例：越界部分每轮拉回 30%（Phase 1 裁决）
PULL_BACK_RATIO: float = 0.3


def clamp_expression(expression: dict[str, float]) -> dict[str, float]:
    """把表达层各维钳制到文档 1 第 6.1 节的范围。

    输入：表达层状态 dict（可缺维，缺维按 0.0 处理）；输出：新 dict；副作用：无。
    """
    return {
        dim: min(max(float(expression.get(dim, 0.0)), low), high)
        for dim, (low, high) in EXPRESSION_RANGES.items()
    }


def soft_clamp(expression: dict[str, float]) -> dict[str, float]:
    """文档 1 第 6.4 节的耦合约束（软钳制）。

    条件命中时，把越界维度向约束边界拉回 PULL_BACK_RATIO（30%），而不是直接截断。
    输入：表达层状态 dict（五维齐全）；输出：钳制后的新 dict；副作用：无。
    """
    result = dict(expression)
    pleasure, arousal, dominance = result["PLE"], result["ARO"], result["DOM"]
    intimacy, security = result["INT"], result["SEC"]

    # 若 PLE < -0.3 且 ARO > 0.5，则 DOM 不应同时 > 0.3
    if pleasure < -0.3 and arousal > 0.5 and dominance > 0.3:
        result["DOM"] = dominance - (dominance - 0.3) * PULL_BACK_RATIO
    # 若 SEC < 0.2，则 INT 不应 > 0.7
    if security < 0.2 and intimacy > 0.7:
        result["INT"] = intimacy - (intimacy - 0.7) * PULL_BACK_RATIO
    # 若 INT < 0.2，则 PLE 的绝对值不应 > 0.8
    if intimacy < 0.2 and abs(pleasure) > 0.8:
        boundary = 0.8 if pleasure > 0 else -0.8
        result["PLE"] = pleasure - (pleasure - boundary) * PULL_BACK_RATIO
    return result
