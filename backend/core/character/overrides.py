"""角色覆盖参数 → 情感引擎参数的解析（文档 1 第 3.2 / 3.5 / 7.3 节）。

Phase 1 的 `EmotionEngine._apply_character_overrides` 已有完整逻辑，
本模块把它拆为纯函数 `resolve_overrides(profile, config)`：引擎构造时
委托此处计算，行为与原实现完全一致（现有测试即回归保障）。

文档 1 第 3.5 节要点：Big Five 是默认调节方向，档案里的
`emotion_overrides.*` 是显式覆盖、优先级更高（如温柔艺术家宜人性高、
但因"对批评敏感"把 conflict 显式设为 1.3）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.character.profile import CharacterProfile
from core.internal_layer import apply_character_to_internal, clamp_internal, guilt_sensitivity


@dataclass
class ResolvedOverrides:
    """角色档案 + 全局配置解析出的引擎参数（供 EmotionEngine 消费）。"""

    initial_expression: dict[str, float] = field(default_factory=dict)
    initial_internal: dict[str, float] = field(default_factory=dict)
    gui_sensitivity: float = 1.0
    expression_half_life_hours: float = 4.0
    mood_half_life_hours: float = 5.0 * 24.0
    affective_half_life_days: dict[str, float] = field(default_factory=dict)
    mapping_weights: dict[str, float] = field(default_factory=dict)
    # 按事件标签的敏感度（角色特质，如"对批评敏感"= conflict ×1.3）
    event_sensitivity: dict[str, float] = field(default_factory=dict)
    # 用户预设全局乘数（文档 1 第 10.2 节；与按标签敏感度相乘，见下）
    event_sensitivity_all: float = 1.0
    valence_modulation: bool = True
    initial_affective: dict[str, float] = field(default_factory=dict)
    affective_baseline: dict[str, float] = field(default_factory=dict)
    stages_config: dict = field(default_factory=dict)


# event_sensitivity 里的全局乘数字段名（前端三档预设写入，
# 文档 1 第 10.2 节：与按标签敏感度相乘，钳制 [min, max]）
SENSITIVITY_ALL_KEY = "all"
SENSITIVITY_MULTIPLIER_MIN = 0.5
SENSITIVITY_MULTIPLIER_MAX = 2.0


def clamp_multiplier(value: float) -> float:
    """钳制乘数到 [0.5, 2.0]（防止极端值）。"""
    return max(SENSITIVITY_MULTIPLIER_MIN,
               min(SENSITIVITY_MULTIPLIER_MAX, float(value)))


def split_event_sensitivity(raw: dict | None) -> tuple[dict, float]:
    """把 event_sensitivity 拆成（按标签 dict, 全局乘数）。

    输入：档案里的 event_sensitivity（可能含 'all' 键）；
    输出：(不含 'all' 的按标签 dict, 'all' 乘数，缺省 1.0)。
    """
    raw = raw or {}
    per_label = {label: float(value) for label, value in raw.items()
                 if label != SENSITIVITY_ALL_KEY}
    multiplier = clamp_multiplier(raw.get(SENSITIVITY_ALL_KEY, 1.0))
    return per_label, multiplier


def resolve_overrides(profile: CharacterProfile, config: dict) -> ResolvedOverrides:
    """把角色档案的覆盖参数解析成引擎可用参数。

    输入：角色档案、数值配置 dict（dudulu_config.yaml 的内容）；
    输出：ResolvedOverrides；副作用：无。
    """
    overrides = profile.emotion_overrides
    expression_cfg = config["expression_layer"]
    internal_cfg = config["internal_layer"]
    affective_cfg = config["affective_layer"]
    decay_cfg = config["decay"]

    initial_expression: dict[str, float] = {
        **expression_cfg["initial"], **(overrides.initial_expression or {})
    }

    base_internal: dict[str, float] = {
        **internal_cfg["initial"], **(overrides.initial_internal or {})
    }
    big_five = profile.personality.big_five.to_dict()
    # Big Five 对内部层初始状态的调整（文档 1 第 7.3 节）
    adjusted = apply_character_to_internal(base_internal, big_five)
    initial_internal = clamp_internal(adjusted)
    gui_sensitivity = guilt_sensitivity(big_five["conscientiousness"])

    decay_modifiers = overrides.decay_modifiers
    expression_half_life_hours = (
        decay_modifiers.expression_half_life_hours
        if decay_modifiers.expression_half_life_hours is not None
        else float(decay_cfg["expression_half_life_hours"])
    )
    # 内部层（慢心境）半衰期：mood_half_life_days 折算为小时（裁决④，标记待调优）
    mood_half_life_hours = float(decay_cfg["mood_half_life_days"]) * 24.0
    affective_half_life_days: dict[str, float] = {
        **{key: float(value)
           for key, value in decay_cfg["affective_half_life_days"].items()},
        **{key: float(value)
           for key, value in (decay_modifiers.affective_half_life_days or {}).items()},
    }

    relationship = profile.relationship_defaults
    initial_affective = {
        "AFF": float(relationship.initial_affection
                     if relationship.initial_affection is not None
                     else affective_cfg["initial"]["AFF"]),
        "TRS": float(relationship.initial_trust
                     if relationship.initial_trust is not None
                     else affective_cfg["initial"]["TRS"]),
        "ATT": float(relationship.initial_attachment
                     if relationship.initial_attachment is not None
                     else affective_cfg["initial"]["ATT"]),
    }
    # 情感层衰减基线：文档未定义具体取值，暂取关系初始值（Phase 1 实现说明已报告）
    affective_baseline = dict(initial_affective)

    # event_sensitivity：'all' 是用户预设全局乘数（与按标签敏感度相乘），
    # 其余键是按标签的角色特质敏感度（Phase 10 裁决：方案 B）
    per_label, multiplier = split_event_sensitivity(
        overrides.event_sensitivity)

    return ResolvedOverrides(
        initial_expression=initial_expression,
        initial_internal=initial_internal,
        gui_sensitivity=gui_sensitivity,
        expression_half_life_hours=expression_half_life_hours,
        mood_half_life_hours=mood_half_life_hours,
        affective_half_life_days=affective_half_life_days,
        mapping_weights=overrides.internal_to_expression_weights or {},
        event_sensitivity=per_label,
        event_sensitivity_all=multiplier,
        valence_modulation=bool(decay_cfg.get("valence_modulation", True)),
        initial_affective=initial_affective,
        affective_baseline=affective_baseline,
        stages_config=affective_cfg["stages"],
    )
