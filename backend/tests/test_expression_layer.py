"""表达层单元测试（文档 1 第六章）。"""

from core.expression_layer import (
    EXPRESSION_DIMENSIONS,
    clamp_expression,
    soft_clamp,
)


def make_expression(**overrides) -> dict:
    base = {"PLE": 0.0, "ARO": 0.0, "DOM": 0.0, "INT": 0.5, "SEC": 0.5}
    base.update(overrides)
    return base


class TestClampExpression:
    def test_clamps_to_documented_ranges(self):
        result = clamp_expression(
            {"PLE": 1.5, "ARO": -1.5, "DOM": 2.0, "INT": -0.2, "SEC": 1.2})
        assert result == {"PLE": 1.0, "ARO": -1.0, "DOM": 1.0, "INT": 0.0, "SEC": 1.0}

    def test_missing_dimensions_default_to_zero(self):
        result = clamp_expression({})
        assert set(result) == set(EXPRESSION_DIMENSIONS)


class TestSoftClamp:
    def test_no_violation_returns_unchanged(self):
        state = make_expression(PLE=0.1, ARO=0.2, DOM=0.1, INT=0.5, SEC=0.5)
        assert soft_clamp(state) == state

    def test_security_low_pulls_intimacy_back_30_percent(self):
        """裁决示例：SEC=0.15 且 INT=0.75 → INT 拉回 30% 到 0.735。"""
        result = soft_clamp(make_expression(INT=0.75, SEC=0.15))
        assert result["INT"] == 0.75 - (0.75 - 0.7) * 0.3

    def test_negative_pleasure_high_arousal_pulls_dominance(self):
        """PLE < -0.3 且 ARO > 0.5 时 DOM > 0.3 越界，拉回 30%。"""
        result = soft_clamp(make_expression(PLE=-0.5, ARO=0.6, DOM=0.5))
        assert result["DOM"] == 0.5 - (0.5 - 0.3) * 0.3

    def test_low_intimacy_pulls_extreme_pleasure(self):
        """INT < 0.2 时 |PLE| > 0.8 越界，向同号边界拉回 30%。"""
        high = soft_clamp(make_expression(INT=0.1, PLE=0.9))
        low = soft_clamp(make_expression(INT=0.1, PLE=-0.9))
        assert high["PLE"] == 0.9 - (0.9 - 0.8) * 0.3
        assert low["PLE"] == -0.9 - (-0.9 - -0.8) * 0.3

    def test_constraint_not_triggered_at_boundary(self):
        """SEC 恰好 0.2 不触发（文档为严格小于）。"""
        state = make_expression(INT=0.75, SEC=0.2)
        assert soft_clamp(state)["INT"] == 0.75

    def test_input_not_mutated(self):
        state = make_expression(INT=0.75, SEC=0.15)
        soft_clamp(state)
        assert state["INT"] == 0.75
