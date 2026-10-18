"""衰减函数单元测试（文档 1 第 9.2 节）。"""

import math

import pytest

from core.decay import decay


class TestDecay:
    def test_no_elapsed_returns_current(self):
        """经过 0 小时应原样返回。"""
        assert decay(0.5, 0.2, 4.0, 0.0, 0.0) == 0.5

    def test_half_life_halves_distance_to_baseline(self):
        """半衰期 4 小时、经过 4 小时，与基线的距离减半。

        文档公式的常数是 0.693（非 ln2），半衰期为近似口径，容差取 1e-3。
        """
        result = decay(1.0, 0.0, 4.0, 4.0, 0.0)
        assert result == pytest.approx(0.5, abs=1e-3)

    def test_formula_matches_doc_definition(self):
        """与文档公式逐因子对齐：baseline + (current-baseline)*exp(-rate*h)。"""
        current, baseline, half_life, hours, valence = 0.8, 0.3, 6.0, 2.5, 0.1
        expected = baseline + (current - baseline) * math.exp(
            -(0.693 / half_life) * (1.0 - 0.3 * valence) * hours)
        assert decay(current, baseline, half_life, hours, valence) == expected

    def test_positive_valence_slows_decay(self):
        """正效价减慢衰减，负效价加快衰减。"""
        fast = decay(1.0, 0.0, 4.0, 4.0, -1.0)
        neutral = decay(1.0, 0.0, 4.0, 4.0, 0.0)
        slow = decay(1.0, 0.0, 4.0, 4.0, 1.0)
        assert fast < neutral < slow

    def test_decay_crosses_baseline_from_below(self):
        """低于基线时向上回归。"""
        assert decay(0.0, 1.0, 4.0, 4.0, 0.0) == pytest.approx(0.5, abs=1e-3)
