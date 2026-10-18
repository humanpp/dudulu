"""内部层单元测试（文档 1 第七章 + Phase 1 裁决增量表）。"""

import pytest

from core.internal_layer import (
    EVENT_INTERNAL_DELTA,
    INTERNAL_DIMENSIONS,
    apply_character_to_internal,
    clamp_internal,
    guilt_sensitivity,
    internal_to_expression,
)

LIN_ZHIXIA_BIG_FIVE = {
    "openness": 0.82,
    "conscientiousness": 0.55,
    "extraversion": 0.40,
    "agreeableness": 0.75,
    "neuroticism": 0.45,
}

CONFIG_INITIAL_INTERNAL = {
    "ANX": 0.10, "LON": 0.30, "CUR": 0.50, "ANT": 0.20,
    "GUI": 0.00, "PRO": 0.10, "DEP": 0.15, "PAT": 0.70,
    "VUL": 0.10, "SAT": 0.50, "JEA": 0.00, "TRU": 0.25,
}


class TestEventDeltaTable:
    def test_all_labels_cover_all_dimensions(self):
        for label, deltas in EVENT_INTERNAL_DELTA.items():
            assert set(deltas) == set(INTERNAL_DIMENSIONS), label

    def test_neutral_is_all_zero(self):
        assert set(EVENT_INTERNAL_DELTA["neutral"].values()) == {0.0}


class TestClampInternal:
    def test_clamps_to_unit_range(self):
        result = clamp_internal({"ANX": 1.5, "LON": -0.2, "SAT": 0.5})
        assert result["ANX"] == 1.0
        assert result["LON"] == 0.0
        assert result["SAT"] == 0.5


class TestApplyCharacterToInternal:
    def test_doc_example_values(self):
        """按文档 1 第 7.3 节公式逐项验算林知夏的大五调整。"""
        result = apply_character_to_internal(CONFIG_INITIAL_INTERNAL,
                                             LIN_ZHIXIA_BIG_FIVE)
        assert result["ANX"] == pytest.approx(0.10 + 0.15 * (0.45 - 0.5))
        assert result["LON"] == pytest.approx(0.30 + 0.20 * (0.50 - 0.40))
        assert result["CUR"] == pytest.approx(0.50 + 0.25 * (0.82 - 0.50))
        assert result["PAT"] == pytest.approx(0.70 + 0.20 * (0.75 - 0.50))

    def test_input_not_mutated(self):
        snapshot = dict(CONFIG_INITIAL_INTERNAL)
        apply_character_to_internal(CONFIG_INITIAL_INTERNAL, LIN_ZHIXIA_BIG_FIVE)
        assert CONFIG_INITIAL_INTERNAL == snapshot

    def test_guilt_sensitivity_formula(self):
        assert guilt_sensitivity(0.55) == pytest.approx(0.8 + 0.4 * 0.55)


class TestInternalToExpression:
    def test_doc_initial_values_mapping(self):
        """按文档 1 第 7.4 节公式对配置初始内部态逐维验算。"""
        delta = internal_to_expression(CONFIG_INITIAL_INTERNAL)
        assert delta["PLE"] == pytest.approx(-0.061)
        assert delta["ARO"] == pytest.approx(0.043)
        assert delta["DOM"] == pytest.approx(0.012)
        assert delta["INT"] == pytest.approx(0.065)
        assert delta["SEC"] == pytest.approx(0.0755)

    def test_weights_amplify_anx_and_vul(self):
        """角色权重 {ANX: 1.2, VUL: 1.1} 只作用于含 ANX / VUL 的项。"""
        plain = internal_to_expression(CONFIG_INITIAL_INTERNAL)
        weighted = internal_to_expression(
            CONFIG_INITIAL_INTERNAL, weights={"ANX": 1.2, "VUL": 1.1})
        # PLE 中 -0.12*ANX 项被放大：-0.012 → -0.0144
        assert weighted["PLE"] == pytest.approx(plain["PLE"] - 0.12 * 0.10 * 0.2)
        # ARO 不含 ANX/VUL 权重项，保持不变
        assert weighted["ARO"] == pytest.approx(plain["ARO"])
        # DOM 中 -0.05*VUL 项被放大：-0.005 → -0.0055
        assert weighted["DOM"] == pytest.approx(plain["DOM"] - 0.05 * 0.10 * 0.1)

    def test_higher_satisfaction_raises_pleasure_delta(self):
        low = internal_to_expression({**CONFIG_INITIAL_INTERNAL, "SAT": 0.2})
        high = internal_to_expression({**CONFIG_INITIAL_INTERNAL, "SAT": 0.8})
        assert high["PLE"] > low["PLE"]
