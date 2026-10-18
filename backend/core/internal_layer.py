"""内部层逻辑：12 维定义、事件增量表、Big Five 调整与内部到表达的映射（文档 1 第七章）。"""

from __future__ import annotations

# 维度符号 -> 中文标签（文档 1 第 7.1 节）
INTERNAL_LABELS: dict[str, str] = {
    "ANX": "焦虑",
    "LON": "孤独",
    "CUR": "好奇",
    "ANT": "期待",
    "GUI": "愧疚",
    "PRO": "保护欲",
    "DEP": "依赖",
    "PAT": "耐心",
    "VUL": "脆弱",
    "SAT": "满足",
    "JEA": "嫉妒",
    "TRU": "信任",
}

INTERNAL_DIMENSIONS: tuple[str, ...] = tuple(INTERNAL_LABELS)

# 内部层统一范围（文档 1 第 7.1 节）
INTERNAL_RANGE: tuple[float, float] = (0.0, 1.0)

# 事件标签体系（文档 1 第 10.1 节）
EVENT_LABELS: tuple[str, ...] = (
    "affectionate", "playful", "vulnerable", "reassuring", "curious",
    "neutral", "conflict", "hostile", "withdrawn",
)

# 负向事件标签：关系阶段调节只作用于这些标签（文档 1 第 10.2 节）
NEGATIVE_EVENT_LABELS: tuple[str, ...] = ("conflict", "hostile", "withdrawn")

# 正向事件标签：剧烈事件对情感层做正向冲击（Phase 1 裁决）
POSITIVE_EVENT_LABELS: tuple[str, ...] = ("affectionate", "playful", "reassuring")

# 事件标签 -> 内部层各维度增量（乘事件强度 I 后生效）。
# Phase 1 裁决下发的初始数值表，标记为「待调优」，与文档 1 第十六章第 1 条同性质。
EVENT_INTERNAL_DELTA: dict[str, dict[str, float]] = {
    "affectionate": {"ANX": -0.02, "LON": -0.05, "CUR": 0.0, "ANT": 0.03, "GUI": 0.0,
                     "PRO": 0.03, "DEP": 0.02, "PAT": 0.02, "VUL": 0.0, "SAT": 0.08,
                     "JEA": -0.02, "TRU": 0.03},
    "playful": {"ANX": -0.03, "LON": -0.03, "CUR": 0.03, "ANT": 0.05, "GUI": 0.0,
                "PRO": 0.0, "DEP": 0.0, "PAT": 0.03, "VUL": 0.02, "SAT": 0.05,
                "JEA": -0.03, "TRU": 0.02},
    "vulnerable": {"ANX": 0.05, "LON": 0.02, "CUR": 0.0, "ANT": 0.0, "GUI": 0.0,
                   "PRO": 0.08, "DEP": 0.03, "PAT": 0.02, "VUL": 0.10, "SAT": 0.0,
                   "JEA": 0.0, "TRU": 0.05},
    "reassuring": {"ANX": -0.05, "LON": -0.02, "CUR": 0.0, "ANT": 0.0, "GUI": -0.03,
                   "PRO": 0.0, "DEP": 0.0, "PAT": 0.03, "VUL": 0.0, "SAT": 0.10,
                   "JEA": -0.02, "TRU": 0.05},
    "curious": {"ANX": 0.0, "LON": -0.02, "CUR": 0.05, "ANT": 0.05, "GUI": 0.0,
                "PRO": 0.0, "DEP": 0.0, "PAT": 0.02, "VUL": 0.0, "SAT": 0.02,
                "JEA": 0.0, "TRU": 0.02},
    "neutral": {"ANX": 0.0, "LON": 0.0, "CUR": 0.0, "ANT": 0.0, "GUI": 0.0,
                "PRO": 0.0, "DEP": 0.0, "PAT": 0.0, "VUL": 0.0, "SAT": 0.0,
                "JEA": 0.0, "TRU": 0.0},
    "conflict": {"ANX": 0.10, "LON": 0.03, "CUR": 0.0, "ANT": -0.05, "GUI": 0.05,
                 "PRO": -0.03, "DEP": 0.0, "PAT": -0.05, "VUL": 0.0, "SAT": -0.08,
                 "JEA": 0.03, "TRU": -0.05},
    "hostile": {"ANX": 0.15, "LON": 0.05, "CUR": 0.0, "ANT": -0.08, "GUI": 0.08,
                "PRO": -0.08, "DEP": -0.05, "PAT": -0.08, "VUL": 0.0, "SAT": -0.12,
                "JEA": 0.05, "TRU": -0.10},
    "withdrawn": {"ANX": 0.05, "LON": 0.08, "CUR": 0.0, "ANT": -0.05, "GUI": 0.0,
                  "PRO": 0.0, "DEP": 0.03, "PAT": 0.0, "VUL": 0.02, "SAT": -0.05,
                  "JEA": 0.0, "TRU": -0.02},
}


def clamp_internal(internal: dict[str, float]) -> dict[str, float]:
    """把内部层各维钳制到 [0.0, 1.0]。

    输入：内部层状态 dict（可缺维，缺维按 0.0 处理）；输出：新 dict；副作用：无。
    """
    low, high = INTERNAL_RANGE
    return {
        dim: min(max(float(internal.get(dim, 0.0)), low), high)
        for dim in INTERNAL_DIMENSIONS
    }


def apply_character_to_internal(internal: dict[str, float],
                                big_five: dict[str, float]) -> dict[str, float]:
    """按 Big Five 调整内部层初始状态（文档 1 第 7.3 节）。

    输入：内部层状态 dict、Big Five dict（五个维度名见函数体）；输出：调整后的新 dict。
    副作用：无。文档 1 第 7.3 节同时给出的 GUI_sensitivity 由 guilt_sensitivity() 单独计算。
    """
    adjusted = dict(internal)
    adjusted["ANX"] += 0.15 * (big_five["neuroticism"] - 0.5)
    adjusted["LON"] += 0.20 * (0.5 - big_five["extraversion"])
    adjusted["CUR"] += 0.25 * (big_five["openness"] - 0.5)
    adjusted["PAT"] += 0.20 * (big_five["agreeableness"] - 0.5)
    return adjusted


def guilt_sensitivity(conscientiousness: float) -> float:
    """愧疚敏感度 = 0.8 + 0.4 × 尽责性（文档 1 第 7.3 节）。

    当前文档未定义其消费点，引擎仅计算并持有，待文档补充落地规则。
    """
    return 0.8 + 0.4 * conscientiousness


def internal_to_expression(internal: dict[str, float],
                           weights: dict[str, float] | None = None) -> dict[str, float]:
    """由内部层状态计算表达层增量（文档 1 第 7.4 节，有损压缩映射）。

    输入：internal 为内部层绝对状态（12 维齐全）；weights 为角色覆盖权重（缺省 1.0）。
    输出：表达层五维增量 dict；副作用：无。
    """
    w = weights or {}
    delta: dict[str, float] = {}

    delta["PLE"] = (
        + 0.15 * (internal["SAT"] - 0.5)
        + 0.10 * (internal["TRU"] - 0.5)
        - 0.12 * internal["ANX"] * w.get("ANX", 1.0)
        - 0.08 * internal["LON"]
        - 0.06 * internal["JEA"]
    )
    delta["ARO"] = (
        + 0.10 * internal["ANX"]
        + 0.08 * internal["ANT"]
        + 0.06 * internal["CUR"]
        + 0.05 * internal["LON"]
        - 0.04 * internal["PAT"]
    )
    delta["DOM"] = (
        + 0.10 * internal["PRO"]
        + 0.06 * internal["TRU"]
        - 0.08 * internal["ANX"]
        - 0.05 * internal["VUL"] * w.get("VUL", 1.0)
    )
    delta["INT"] = (
        + 0.12 * internal["VUL"] * w.get("VUL", 1.0)
        + 0.10 * internal["DEP"]
        + 0.08 * internal["TRU"]
        + 0.06 * internal["LON"]
        - 0.05 * internal["JEA"]
    )
    delta["SEC"] = (
        + 0.15 * internal["TRU"]
        + 0.10 * internal["SAT"]
        - 0.12 * internal["ANX"] * w.get("ANX", 1.0)
        - 0.08 * internal["JEA"]
    )
    return delta
