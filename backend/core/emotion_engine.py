"""情感数值引擎（纯 Python）：维护三层数值状态，响应事件更新，执行时间衰减。

对应文档 2 第 3.1 节。本模块不调用 LLM、数据库或 HTTP，可独立测试；
数值语义（维度、公式、衰减、沉淀）见文档 1 第三～十一章。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from core.affective_layer import (
    AFFECTIVE_DIMENSIONS,
    affective_impact,
    clamp_affective,
    determine_stage,
)
from core.character.overrides import clamp_multiplier
from core.character.profile import CharacterProfile
from core.decay import decay
from core.expression_layer import (
    EXPRESSION_DIMENSIONS,
    clamp_expression,
    soft_clamp,
)
from core.internal_layer import (
    EVENT_INTERNAL_DELTA,
    INTERNAL_DIMENSIONS,
    NEGATIVE_EVENT_LABELS,
    clamp_internal,
    internal_to_expression,
)


def load_yaml(config_path: str) -> dict:
    """读取 YAML 配置文件（文档 2 第 3.1 节 EmotionEngine 使用）。

    输入：配置文件路径；输出：解析后的 dict；副作用：读文件。
    """
    with open(config_path, "r", encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


@dataclass
class EmotionEvent:
    """一次情感事件，即事件分类器的输出（文档 2 第 3.8 节第 5 步）。

    label: 事件标签（文档 1 第 10.1 节九类之一）
    intensity: 分类器给出的强度，约定已含 base_intensity × deviation_factor
       （冷启动期 deviation_factor 固定 1.0，见文档 1 第 10.2 节）
    busy_signal: 用户是否表达"最近很忙"（文档 1 第 12.2 节，由管道落库，引擎不消费）
    valence: 事件效价，按标签映射（裁决⑤临时表，标记待调优；
       Phase 7 沉淀器接入后可能改用真实情感效价）
    """

    label: str
    intensity: float
    busy_signal: bool = False
    valence: float = 0.0


# 事件标签 -> 效价（裁决⑤临时映射表，待调优）：
# 正向 +1.0 / 中性 0.0 / 负向 -1.0
EVENT_VALENCE: dict[str, float] = {
    "affectionate": 1.0,
    "playful": 1.0,
    "vulnerable": 0.0,
    "reassuring": 1.0,
    "curious": 0.0,
    "neutral": 0.0,
    "conflict": -1.0,
    "hostile": -1.0,
    "withdrawn": -1.0,
}


@dataclass
class StateDelta:
    """一次状态变更的增量，与 expression / internal / affective 同构（文档 2 第 6.11 节）。

    值为变化量，可为负；未变化的维度省略（文档 0 第七章）。
    """

    expression: dict[str, float] = field(default_factory=dict)
    internal: dict[str, float] = field(default_factory=dict)
    affective: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """输出：{"expression": ..., "internal": ..., "affective": ...}；副作用：无。"""
        return {
            "expression": self.expression,
            "internal": self.internal,
            "affective": self.affective,
        }


@dataclass
class UserState:
    """某用户对某角色的三层数值状态（SQLite character_user_state 的 dict 形态）。"""

    expression: dict[str, float] = field(default_factory=dict)
    internal: dict[str, float] = field(default_factory=dict)
    affective: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "UserState":
        """从持久化 dict 还原状态；缺组的维度留空，由引擎补初始值；副作用：无。"""
        data = data or {}
        return cls(
            expression=dict(data.get("expression") or {}),
            internal=dict(data.get("internal") or {}),
            affective=dict(data.get("affective") or {}),
        )


class EmotionEngine:
    """职责：维护三层数值状态，响应事件更新，执行时间衰减（文档 2 第 3.1 节）。

    输入：角色覆盖参数 + 当前状态 + 事件 + 时间戳
    输出：新状态 + 状态变更记录（StateDelta）
    """

    def __init__(self, config_path: str, character_profile: CharacterProfile,
                 user_state: dict):
        """初始化引擎。

        输入：
            config_path: 数值配置 YAML 路径（config/dudulu_config.yaml）
            character_profile: 角色档案，提供 emotion_overrides 等覆盖参数
            user_state: 持久化的三层状态 dict，为空时用初始值；
                可携带可选键 mood_baseline（慢心境基线，文档 1 第 11.4 节）
        副作用：读取配置文件
        """
        self.config = load_yaml(config_path)
        self.profile = character_profile
        self._apply_character_overrides(character_profile)
        self.mood_baseline: dict[str, float] = {
            **self.initial_expression,
            **((user_state or {}).get("mood_baseline") or {}),
        }
        self.state = UserState.from_dict(user_state)
        self._fill_initial_dimensions()

    def _apply_character_overrides(self, profile: CharacterProfile) -> None:
        """应用角色的覆盖参数（文档 1 第 3.2 / 3.5 节）。

        解析逻辑在 core/character/overrides.py（Phase 6 拆分），此处只做赋值：
        输入角色档案；副作用：设置引擎的初始值、半衰期、权重与敏感度。
        """
        from core.character.overrides import resolve_overrides

        resolved = resolve_overrides(profile, self.config)
        self.initial_expression = resolved.initial_expression
        self.initial_internal = resolved.initial_internal
        self.gui_sensitivity = resolved.gui_sensitivity
        self.expression_half_life_hours = resolved.expression_half_life_hours
        self.mood_half_life_hours = resolved.mood_half_life_hours
        self.affective_half_life_days = resolved.affective_half_life_days
        self.mapping_weights = resolved.mapping_weights
        self.event_sensitivity = resolved.event_sensitivity
        # 用户预设全局乘数（与按标签敏感度相乘，钳 [0.5, 2.0]）
        self.event_sensitivity_all = resolved.event_sensitivity_all
        self.valence_modulation = resolved.valence_modulation
        self.initial_affective = resolved.initial_affective
        self.affective_baseline = resolved.affective_baseline
        self.stages_config = resolved.stages_config

    def _fill_initial_dimensions(self) -> None:
        """缺失维度补初始值并钳制范围；顺带过滤 stage_code 等非维度键。

        输入：无；输出：无；副作用：重建 self.state 的三层 dict。
        """
        self.state.expression = clamp_expression({
            dim: self.state.expression.get(dim, self.initial_expression[dim])
            for dim in EXPRESSION_DIMENSIONS
        })
        self.state.internal = clamp_internal({
            dim: self.state.internal.get(dim, self.initial_internal[dim])
            for dim in self.initial_internal
        })
        self.state.affective = clamp_affective({
            dim: self.state.affective.get(dim, self.initial_affective[dim])
            for dim in AFFECTIVE_DIMENSIONS
        })

    def apply_event(self, event: EmotionEvent) -> StateDelta:
        """应用一次情感事件（文档 2 第 3.1 节）。

        输入：EmotionEvent；输出：StateDelta（三层增量，供日志与 WebSocket 推送）；
        副作用：更新内部层、表达层、情感层状态。
        """
        if event.label not in EVENT_INTERNAL_DELTA:
            raise ValueError(f"未知事件标签: {event.label}")

        intensity = self._compute_intensity(event)

        # 1. 内部层：增量表 × I，随后钳制到 [0, 1]
        internal_delta = {
            dim: delta * intensity
            for dim, delta in EVENT_INTERNAL_DELTA[event.label].items()
            if delta != 0.0
        }
        for dim, change in internal_delta.items():
            self.state.internal[dim] += change
        self.state.internal = clamp_internal(self.state.internal)

        # 2. 内部层 → 表达层映射（文档 1 第 7.4 节，输入为更新后的内部层绝对状态）
        expression_delta = internal_to_expression(self.state.internal,
                                                  weights=self.mapping_weights)
        for dim, change in expression_delta.items():
            self.state.expression[dim] += change
        self.state.expression = clamp_expression(self.state.expression)

        # 3. 耦合约束软钳制（文档 1 第 6.4 节）
        self.state.expression = soft_clamp(self.state.expression)

        # 4. 情感层：剧烈事件冲击
        affective_delta = affective_impact(event.label, intensity)
        for dim, change in affective_delta.items():
            self.state.affective[dim] += change
        self.state.affective = clamp_affective(self.state.affective)

        return StateDelta(expression=expression_delta,
                          internal=internal_delta,
                          affective=affective_delta)

    def apply_decay(self, hours_elapsed: float,
                    valence: float | None = None) -> StateDelta:
        """应用时间衰减（文档 1 第 9.1 / 9.2 节 + 裁决④），三层都衰减。

        - 表达层：expression_half_life_hours（默认 4 小时）→ 慢心境基线
        - 内部层：mood_half_life_days × 24（默认 5 天）→ 基线（initial_internal，含大五调整），
          valence 固定 0.0（不区分正负；裁决④，标记待调优）
        - 情感层：AFF 30 / TRS 90 / ATT 180 天（角色可覆盖）→ 基线

        输入：距上次交互的小时数；可选取 valence（事件效价，裁决⑤：
            取上一轮事件的 valence；缺省时用当前表达层 PLE 推导）；
        输出：StateDelta；副作用：更新三层状态。
        """
        if valence is None:
            valence = 0.0
            if self.valence_modulation:
                valence = min(max(self.state.expression.get("PLE", 0.0), -1.0), 1.0)

        expression_delta: dict[str, float] = {}
        for dim in EXPRESSION_DIMENSIONS:
            current = self.state.expression[dim]
            baseline = self.mood_baseline.get(dim, self.initial_expression[dim])
            updated = decay(current, baseline, self.expression_half_life_hours,
                            hours_elapsed, valence)
            expression_delta[dim] = updated - current
            self.state.expression[dim] = updated

        # 内部层衰减（裁决④）：LON 等维度在无互动时同样回归基线；
        # "用户长期不在线时 LON 不降"的行为留待 Phase 7 沉淀器补。
        internal_delta: dict[str, float] = {}
        for dim in INTERNAL_DIMENSIONS:
            current = self.state.internal[dim]
            baseline = self.initial_internal[dim]
            updated = decay(current, baseline, self.mood_half_life_hours,
                            hours_elapsed, 0.0)
            internal_delta[dim] = updated - current
            self.state.internal[dim] = updated

        affective_delta: dict[str, float] = {}
        for dim in AFFECTIVE_DIMENSIONS:
            current = self.state.affective[dim]
            baseline = self.affective_baseline.get(dim, self.initial_affective[dim])
            half_life_hours = self.affective_half_life_days[dim] * 24.0
            updated = decay(current, baseline, half_life_hours,
                            hours_elapsed, valence)
            affective_delta[dim] = updated - current
            self.state.affective[dim] = updated

        return StateDelta(expression=expression_delta,
                          internal=internal_delta,
                          affective=affective_delta)

    def _compute_intensity(self, event: EmotionEvent) -> float:
        """I = base_intensity × deviation_factor × relationship_modifier × character_sensitivity
        （文档 1 第 10.2 节）。event.intensity 视为前两项的合成值（分类器职责），
        本方法叠加关系阶段调节（仅负向标签：S0 ×1.3、S3+ ×0.8）与角色敏感度。

        输入：EmotionEvent；输出：最终强度 I；副作用：无。
        """
        stage_code, _ = determine_stage(self.state.affective, self.stages_config)
        relationship_modifier = 1.0
        if event.label in NEGATIVE_EVENT_LABELS:
            if stage_code == "S0":
                relationship_modifier = 1.3
            elif stage_code in ("S3", "S4"):
                relationship_modifier = 0.8
        # 角色敏感度 = 按标签系数 × 用户预设全局乘数，钳 [0.5, 2.0]
        # （Phase 10 裁决：两者正交相乘；文档 1 第 10.2 节）
        character_sensitivity = clamp_multiplier(
            self.event_sensitivity.get(event.label, 1.0)
            * self.event_sensitivity_all)
        return event.intensity * relationship_modifier * character_sensitivity

    def get_expression_prompt(self) -> str:
        """生成文本注入用的表达式状态字符串（文档 1 第 6.3 节格式）。

        输出：[EMOTION STATE] 三行块（两行数值 + 一行定性标签）；副作用：无。
        """
        state = self.state.expression

        def label(value: float, low: str, mid: str, high: str) -> str:
            if value >= 0.4:
                return high
            if value <= -0.4:
                return low
            return mid

        mood = label(state["PLE"], "cool", "neutral", "warm")
        energy = label(state["ARO"], "low", "moderate", "high")
        stance = label(state["DOM"], "following", "equal", "leading")

        return f"""[EMOTION STATE]
PLE: {state['PLE']:.2f} | ARO: {state['ARO']:.2f} | DOM: {state['DOM']:.2f}
INT: {state['INT']:.2f} | SEC: {state['SEC']:.2f}
Mood: {mood} | Energy: {energy} | Stance: {stance}
[/EMOTION STATE]"""

    def get_full_state(self) -> dict:
        """返回完整状态（文档 2 第 6.8 节形态）。

        输出：{"expression": {...}, "internal": {...},
               "affective": {AFF, TRS, ATT, stage_code, stage_label}}；副作用：无。
        """
        stage_code, stage_label = determine_stage(self.state.affective,
                                                  self.stages_config)
        return {
            "expression": dict(self.state.expression),
            "internal": dict(self.state.internal),
            "affective": {
                **self.state.affective,
                "stage_code": stage_code,
                "stage_label": stage_label,
            },
        }
