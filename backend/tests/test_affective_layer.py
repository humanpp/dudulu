"""情感层单元测试（文档 1 第八章 + Phase 1 裁决冲击规则）。"""

import pytest

from core.affective_layer import affective_impact, determine_stage

STAGES_CONFIG = {
    "S0": {"AFF": [0, 20], "label": "陌生"},
    "S1": {"AFF": [20, 40], "label": "认识"},
    "S2": {"AFF": [40, 60], "label": "熟悉"},
    "S3": {"AFF": [60, 80], "TRS_min": 50, "label": "亲密"},
    "S4": {"AFF": [80, 100], "TRS_min": 70, "ATT_min": 60, "label": "深度绑定"},
}


def make_affective(aff: float, trs: float = 25.0, att: float = 10.0) -> dict:
    return {"AFF": aff, "TRS": trs, "ATT": att}


class TestDetermineStage:
    @pytest.mark.parametrize("aff,expected", [
        (0.0, "S0"), (19.9, "S0"),
        (20.0, "S1"), (39.9, "S1"),
        (40.0, "S2"), (59.9, "S2"),
        (60.0, "S3"), (79.9, "S3"),
        (80.0, "S4"), (100.0, "S4"),
    ])
    def test_affection_boundaries(self, aff, expected):
        """AFF 边界按文档 1 第 8.2 节半开区间判定。"""
        trs = 70.0 if expected == "S4" else (50.0 if expected == "S3" else 25.0)
        att = 60.0 if expected == "S4" else 10.0
        code, _ = determine_stage(make_affective(aff, trs, att), STAGES_CONFIG)
        assert code == expected

    def test_s3_requires_trust_50(self):
        """AFF 60 但 TRS 49.9 → 降级为 S2。"""
        code, _ = determine_stage(make_affective(60.0, trs=49.9), STAGES_CONFIG)
        assert code == "S2"

    def test_s4_requires_trust_70_and_attachment_60(self):
        code, _ = determine_stage(make_affective(85.0, trs=70.0, att=59.9),
                                  STAGES_CONFIG)
        assert code == "S3"
        code, _ = determine_stage(make_affective(85.0, trs=69.9, att=60.0),
                                  STAGES_CONFIG)
        assert code == "S3"

    def test_low_trust_falls_back_to_s2(self):
        """AFF 85 但 TRS 40 → 连降两级到 S2。"""
        code, _ = determine_stage(make_affective(85.0, trs=40.0, att=90.0),
                                  STAGES_CONFIG)
        assert code == "S2"

    def test_labels_from_config(self):
        assert determine_stage(make_affective(0.0), STAGES_CONFIG) == ("S0", "陌生")
        assert determine_stage(make_affective(72.0, trs=58.0, att=45.0),
                               STAGES_CONFIG) == ("S3", "亲密")


class TestAffectiveImpact:
    def test_below_threshold_no_impact(self):
        assert affective_impact("affectionate", 0.7) == {}
        assert affective_impact("hostile", 0.68) == {}

    def test_positive_label_adds(self):
        """I=1.0 → impact 5.5：AFF +5.5、TRS +2.75。"""
        assert affective_impact("affectionate", 1.0) == {"AFF": 5.5, "TRS": 2.75}

    def test_negative_label_subtracts(self):
        impact = affective_impact("hostile", 1.0)
        assert impact == {"AFF": -5.5, "TRS": -2.75}

    def test_partial_impact_scaled(self):
        """I=0.85 → impact = 0.15/0.3*5.5 = 2.75。"""
        assert affective_impact("reassuring", 0.85) == {"AFF": 2.75, "TRS": 1.375}

    def test_other_labels_never_trigger(self):
        """vulnerable / curious / neutral 不在正负向清单，不触发剧烈事件。"""
        assert affective_impact("vulnerable", 0.95) == {}
        assert affective_impact("curious", 0.95) == {}
        assert affective_impact("neutral", 0.95) == {}

    def test_withdrawn_counts_as_negative(self):
        assert affective_impact("withdrawn", 0.8) == {
            "AFF": pytest.approx(-1.8333333), "TRS": pytest.approx(-0.9166667)}
