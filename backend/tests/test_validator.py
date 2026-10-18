"""一致性校验单元测试（文档 1 第 5.3 节四条约束）。"""

from core.character.profile import CharacterProfile
from core.character.validator import validate_consistency


def make_profile(**sections) -> CharacterProfile:
    return CharacterProfile.from_dict({"id": "c", "name": "测试", **sections})


class TestAgeVsOccupation:
    def test_young_age_with_senior_occupation(self):
        """文档示例：年龄 19 + 大学教授 → 冲突。"""
        profile = make_profile(age=19,
                               background={"occupation": "大学讲师"})
        issues = validate_consistency(profile)
        assert any(issue.field == "age" for issue in issues)

    def test_proper_age_passes(self):
        profile = make_profile(age=30,
                               background={"occupation": "大学讲师"})
        assert validate_consistency(profile) == []

    def test_junior_occupation_no_age_rule(self):
        """无 min_age 的职业不受年龄规则约束。"""
        profile = make_profile(age=19, background={"occupation": "咖啡师"})
        assert validate_consistency(profile) == []


class TestExtraversionVsTone:
    def test_extreme_extraversion_with_silent_tone(self):
        """文档示例：极度外向 + 沉默寡言 → 冲突。"""
        profile = make_profile(
            personality={"big_five": {"extraversion": 0.9}},
            speech_style={"tone": "沉默寡言，话很少"})
        issues = validate_consistency(profile)
        assert any(issue.field == "speech_style.tone" for issue in issues)

    def test_extreme_extraversion_with_lively_tone(self):
        profile = make_profile(
            personality={"big_five": {"extraversion": 0.9}},
            speech_style={"tone": "爽朗直接"})
        assert validate_consistency(profile) == []


class TestBodyVsBackground:
    def test_athletic_with_sedentary_background(self):
        """文档示例：athletic + 从不运动 → 冲突。"""
        profile = make_profile(
            appearance={"body_type": "athletic"},
            background={"key_experiences": ["从小从不运动"]})
        issues = validate_consistency(profile)
        assert any(issue.field == "appearance.body_type" for issue in issues)

    def test_athletic_with_active_background(self):
        profile = make_profile(
            appearance={"body_type": "athletic"},
            background={"key_experiences": ["每天晨跑五公里"]})
        assert validate_consistency(profile) == []


class TestNeuroticismVsSensitivity:
    def test_high_neuroticism_with_low_conflict_sensitivity(self):
        """文档示例：神经质 0.9 + 冲突 ×0.5 → 冲突。"""
        profile = make_profile(
            personality={"big_five": {"neuroticism": 0.9}},
            emotion_overrides={"event_sensitivity": {"conflict": 0.5}})
        issues = validate_consistency(profile)
        assert any(issue.field.startswith(
            "emotion_overrides.event_sensitivity") for issue in issues)

    def test_high_neuroticism_without_override(self):
        """没有显式覆盖时不触发（用引擎默认）。"""
        profile = make_profile(
            personality={"big_five": {"neuroticism": 0.9}})
        assert validate_consistency(profile) == []


class TestName:
    def test_empty_name_reported(self):
        profile = make_profile(name="")
        issues = validate_consistency(profile)
        assert any(issue.field == "name" for issue in issues)
