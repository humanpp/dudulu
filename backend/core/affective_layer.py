"""情感层逻辑：AFF/TRS/ATT 范围、关系阶段判定与剧烈事件冲击（文档 1 第八章）。"""

from __future__ import annotations

from core.internal_layer import NEGATIVE_EVENT_LABELS, POSITIVE_EVENT_LABELS

# 维度符号 -> 中文标签（文档 1 第 8.1 节）
AFFECTIVE_LABELS: dict[str, str] = {
    "AFF": "好感度",
    "TRS": "信任度",
    "ATT": "依恋度",
}

# 可衰减三维；STG 由 determine_stage 判定，不参与衰减与沉淀加权（文档 1 第 8.1 节）
AFFECTIVE_DIMENSIONS: tuple[str, ...] = tuple(AFFECTIVE_LABELS)

# 情感层统一范围（文档 1 第 8.1 节）
AFFECTIVE_RANGE: tuple[float, float] = (0.0, 100.0)

# 剧烈事件阈值与冲击系数（Phase 1 裁决，文档 1 第 8.3 节的落地规则）
INTENSE_EVENT_THRESHOLD: float = 0.7
INTENSE_IMPACT_SCALE: float = 5.5


def clamp_affective(affective: dict[str, float]) -> dict[str, float]:
    """把情感层各维钳制到 [0.0, 100.0]。

    输入：情感层状态 dict（可缺维，缺维按 0.0 处理）；输出：新 dict；副作用：无。
    """
    low, high = AFFECTIVE_RANGE
    return {
        dim: min(max(float(affective.get(dim, 0.0)), low), high)
        for dim in AFFECTIVE_DIMENSIONS
    }


def determine_stage(affective: dict[str, float], stages_config: dict) -> tuple[str, str]:
    """按 AFF/TRS/ATT 判定关系阶段（文档 1 第 8.2 节）。

    先按 AFF 落基础阶段，再按 TRS/ATT 下限逐级降级：
    S3 需要 AFF ≥ 60 且 TRS ≥ 50；S4 需要 AFF ≥ 80 且 TRS ≥ 70 且 ATT ≥ 60。
    输入：情感层状态 dict、配置里的 affective_layer.stages 节；
    输出：(stage_code, stage_label)；副作用：无。
    """
    affection = float(affective.get("AFF", 0.0))
    trust = float(affective.get("TRS", 0.0))
    attachment = float(affective.get("ATT", 0.0))

    def aff_min(code: str) -> float:
        return float(stages_config[code]["AFF"][0])

    if affection >= aff_min("S4"):
        code = "S4"
    elif affection >= aff_min("S3"):
        code = "S3"
    elif affection >= aff_min("S2"):
        code = "S2"
    elif affection >= aff_min("S1"):
        code = "S1"
    else:
        code = "S0"

    s4 = stages_config["S4"]
    if code == "S4" and not (trust >= s4.get("TRS_min", 0) and attachment >= s4.get("ATT_min", 0)):
        code = "S3"
    if code == "S3" and trust < stages_config["S3"].get("TRS_min", 0):
        code = "S2"
    return code, str(stages_config[code]["label"])


def affective_impact(label: str, intensity: float) -> dict[str, float]:
    """剧烈事件对情感层的冲击（Phase 1 裁决）。

    I > 0.7 视为剧烈事件，impact = (I - 0.7) / 0.3 × 5.5（范围 0 ~ 5.5）。
    正向标签 AFF/TRS 加分，负向标签减分，其余标签不触发。
    输入：事件标签、最终强度 I；输出：情感层增量 dict（未触发时为空）；副作用：无。
    """
    if intensity <= INTENSE_EVENT_THRESHOLD:
        return {}
    impact = ((intensity - INTENSE_EVENT_THRESHOLD)
              / (1.0 - INTENSE_EVENT_THRESHOLD)) * INTENSE_IMPACT_SCALE
    if label in POSITIVE_EVENT_LABELS:
        return {"AFF": impact * 1.0, "TRS": impact * 0.5}
    if label in NEGATIVE_EVENT_LABELS:
        return {"AFF": -impact * 1.0, "TRS": -impact * 0.5}
    return {}
