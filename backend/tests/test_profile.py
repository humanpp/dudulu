"""角色档案数据类单元测试（文档 1 第 3.2 节示例档案）。"""

from helpers import LIN_ZHIXIA

from core.character.profile import BigFive, CharacterProfile


class TestCharacterProfile:
    def test_from_dict_restores_doc_example(self):
        profile = CharacterProfile.from_dict(LIN_ZHIXIA)
        assert profile.id == "char_001"
        assert profile.name == "林知夏"
        assert profile.nickname == "知夏"
        assert profile.age == 24
        assert profile.appearance.height_cm == 165
        assert profile.background.occupation == "插画师，自由职业"
        assert len(profile.background.key_experiences) == 3
        assert profile.personality.big_five.openness == 0.82
        assert profile.personality.traits == ["温柔", "敏感", "有主见", "偶尔固执", "善于倾听"]
        assert profile.speech_style.catchphrases == ["嗯……让我想想", "这样啊", "其实我觉得吧"]
        assert profile.theme.primary == "#C4A882"
        assert profile.relationship_defaults.initial_affection == 25
        assert profile.proactive_enabled is True
        assert profile.visibility.is_public is False
        assert profile.visibility.owner_user_id == "user_001"
        assert profile.generation_meta.source == "template"

    def test_emotion_overrides_restored(self):
        profile = CharacterProfile.from_dict(LIN_ZHIXIA)
        overrides = profile.emotion_overrides
        assert overrides.initial_expression["PLE"] == 0.20
        assert overrides.initial_internal["ANX"] == 0.25
        assert overrides.decay_modifiers.expression_half_life_hours == 5.0
        assert overrides.decay_modifiers.affective_half_life_days == {
            "AFF": 35, "TRS": 100, "ATT": 200}
        assert overrides.internal_to_expression_weights == {"ANX": 1.2, "VUL": 1.1}
        assert overrides.event_sensitivity == {"conflict": 1.3, "vulnerable": 1.1}
        assert overrides.proactive.eagerness == 1.0
        assert overrides.proactive.silence_recovery_hours == 12

    def test_minimal_profile_defaults(self):
        """只有 id/name 时其余走缺省，不影响引擎消费。"""
        profile = CharacterProfile.from_dict({"id": "char_x", "name": "测试"})
        assert profile.personality.big_five == BigFive()
        assert profile.emotion_overrides.initial_expression == {}
        assert profile.relationship_defaults.initial_affection is None
        assert profile.theme.primary == "#C4A882"
        assert profile.is_rare is False

    def test_big_five_to_dict_round_trip(self):
        profile = CharacterProfile.from_dict(LIN_ZHIXIA)
        assert profile.personality.big_five.to_dict() == {
            "openness": 0.82, "conscientiousness": 0.55,
            "extraversion": 0.40, "agreeableness": 0.75, "neuroticism": 0.45,
        }

    def test_to_dict_round_trip(self):
        """to_dict → from_dict 往返一致（持久化/导出的基础）。"""
        profile = CharacterProfile.from_dict(LIN_ZHIXIA)
        restored = CharacterProfile.from_dict(profile.to_dict())
        assert restored.to_dict() == profile.to_dict()

    def test_to_dict_covers_documented_sections(self):
        """to_dict 覆盖文档 1 第 3.2 节全部顶层节。"""
        profile = CharacterProfile.from_dict({"id": "c", "name": "n"})
        assert set(profile.to_dict()) == {
            "id", "name", "nickname", "gender", "age", "birthday", "zodiac",
            "appearance", "background", "personality", "speech_style", "theme",
            "relationship_defaults", "emotion_overrides", "proactive_enabled",
            "visibility", "generation_meta", "is_rare"}

    def test_is_rare_flag(self):
        """稀有变体标记（文档 1 第 5.6 节）。"""
        profile = CharacterProfile.from_dict(
            {"id": "c", "name": "n", "is_rare": True})
        assert profile.is_rare is True
        assert profile.to_dict()["is_rare"] is True
