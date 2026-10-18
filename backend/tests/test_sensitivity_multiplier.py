"""event_sensitivity 的 {all} 乘数语义（Phase 10 额外问题 2 的裁决）。

方案 B：前端三档预设（标准 1.0 / 敏感 1.3 / 克制 0.8）作为全局乘数，
与角色档案的按标签敏感度相乘，钳制 [0.5, 2.0]。
注意：负向标签在 S0 阶段还有 ×1.3 的关系阶段系数（文档 1 第 10.2 节），
下面用例的期望值都把它算进去。
"""

import pytest

from helpers import CONFIG_PATH
from core.character.overrides import (
    SENSITIVITY_ALL_KEY,
    clamp_multiplier,
    split_event_sensitivity,
)
from core.character.profile import CharacterProfile
from core.emotion_engine import EmotionEngine, EmotionEvent


def engine_with(sensitivity: dict) -> EmotionEngine:
    profile = CharacterProfile.from_dict({
        "id": "c", "name": "n",
        "emotion_overrides": {"event_sensitivity": sensitivity}})
    return EmotionEngine(CONFIG_PATH, profile, {})


class TestSplitEventSensitivity:
    def test_all_extracted_from_labels(self):
        per_label, multiplier = split_event_sensitivity(
            {"conflict": 1.3, "all": 1.3})
        assert per_label == {"conflict": 1.3}
        assert multiplier == 1.3

    def test_missing_all_defaults_to_one(self):
        per_label, multiplier = split_event_sensitivity({"conflict": 1.3})
        assert multiplier == 1.0

    def test_empty(self):
        assert split_event_sensitivity(None) == ({}, 1.0)

    def test_all_clamped(self):
        _, multiplier = split_event_sensitivity({"all": 9.0})
        assert multiplier == 2.0
        _, multiplier = split_event_sensitivity({"all": 0.01})
        assert multiplier == 0.5


class TestClampMultiplier:
    @pytest.mark.parametrize("value,expected", [
        (1.0, 1.0), (1.3, 1.3), (0.8, 0.8),
        (0.1, 0.5), (5.0, 2.0), (-1.0, 0.5),
    ])
    def test_bounds(self, value, expected):
        assert clamp_multiplier(value) == expected


class TestMultiplierApplied:
    def test_sensitive_preset_multiplies_label(self):
        """敏感档（all=1.3）× conflict 1.3 × S0 阶段 1.3 = 2.197。"""
        engine = engine_with({"conflict": 1.3, SENSITIVITY_ALL_KEY: 1.3})
        intensity = engine._compute_intensity(
            EmotionEvent(label="conflict", intensity=1.0))
        assert intensity == pytest.approx(1.0 * 1.3 * 1.3 * 1.3)

    def test_restrained_preset_multiplies_label(self):
        """克制档（all=0.8）× conflict 1.3 × S0 阶段 1.3 = 1.352。"""
        engine = engine_with({"conflict": 1.3, SENSITIVITY_ALL_KEY: 0.8})
        intensity = engine._compute_intensity(
            EmotionEvent(label="conflict", intensity=1.0))
        assert intensity == pytest.approx(1.0 * 1.3 * 1.3 * 0.8)

    def test_standard_preset_is_noop(self):
        """标准档（all=1.0）不改变按标签敏感度的行为。"""
        with_all = engine_with({"conflict": 1.3, SENSITIVITY_ALL_KEY: 1.0})
        without_all = engine_with({"conflict": 1.3})
        event = EmotionEvent(label="conflict", intensity=0.9)
        assert with_all._compute_intensity(event) == pytest.approx(
            without_all._compute_intensity(event))

    def test_positive_label_only_gets_multiplier(self):
        """正向标签无阶段系数：affectionate = 1.0 × all 1.3。"""
        engine = engine_with({SENSITIVITY_ALL_KEY: 1.3})
        intensity = engine._compute_intensity(
            EmotionEvent(label="affectionate", intensity=1.0))
        assert intensity == pytest.approx(1.3)

    def test_no_sensitivity_unchanged(self):
        """无任何敏感度配置：行为与 Phase 9 之前一致（回归保障）。"""
        engine = engine_with({})
        intensity = engine._compute_intensity(
            EmotionEvent(label="conflict", intensity=1.0))
        assert intensity == pytest.approx(1.0 * 1.3)  # 仅 S0 阶段系数

    def test_extreme_multiplier_clamped_end_to_end(self):
        """端到端钳制：all=10 也被压到 2.0。"""
        engine = engine_with({SENSITIVITY_ALL_KEY: 10.0})
        assert engine.event_sensitivity_all == 2.0
        intensity = engine._compute_intensity(
            EmotionEvent(label="affectionate", intensity=1.0))
        assert intensity == pytest.approx(2.0)

    def test_engine_default_multiplier_is_one(self):
        """引擎默认 event_sensitivity_all = 1.0（无预设时）。"""
        engine = engine_with({})
        assert engine.event_sensitivity_all == 1.0
