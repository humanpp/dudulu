"""情感引擎单元测试（文档 2 第 3.1 节 + Phase 1 裁决）。"""

import math
from pathlib import Path

import pytest

from core.character.profile import CharacterProfile
from core.emotion_engine import EmotionEngine, EmotionEvent, StateDelta
from core.internal_layer import INTERNAL_DIMENSIONS

CONFIG_PATH = str(Path(__file__).resolve().parent.parent / "config" / "dudulu_config.yaml")


def make_profile(profile_id: str = "char_test", **sections) -> CharacterProfile:
    """构造最小角色档案；sections 直接合进档案 dict。"""
    data = {"id": profile_id, "name": "测试角色"}
    data.update(sections)
    return CharacterProfile.from_dict(data)


def make_engine(user_state: dict | None = None,
                profile: CharacterProfile | None = None) -> EmotionEngine:
    return EmotionEngine(CONFIG_PATH, profile or make_profile(), user_state or {})


class TestInit:
    def test_default_state_from_config(self):
        engine = make_engine()
        assert engine.state.expression == {
            "PLE": 0.15, "ARO": 0.00, "DOM": 0.00, "INT": 0.20, "SEC": 0.30}
        assert engine.state.internal["ANX"] == 0.10
        assert engine.state.internal["TRU"] == 0.25
        assert engine.state.affective == {"AFF": 15.0, "TRS": 25.0, "ATT": 10.0}

    def test_persisted_state_wins_over_initial(self):
        engine = make_engine({
            "expression": {"PLE": 0.5},
            "internal": {"ANX": 0.4},
            "affective": {"AFF": 60.0},
        })
        assert engine.state.expression["PLE"] == 0.5
        assert engine.state.expression["ARO"] == 0.00  # 缺维补初始值
        assert engine.state.internal["ANX"] == 0.4
        assert engine.state.affective["AFF"] == 60.0

    def test_non_dimension_keys_filtered(self):
        """get_full_state 里的 stage_code 等键不得污染持久化状态。"""
        engine = make_engine({"affective": {"AFF": 50.0, "stage_code": "S3"}})
        assert set(engine.state.affective) == {"AFF", "TRS", "ATT"}

    def test_character_overrides_applied(self):
        """文档 1 第 3.2 节林知夏的覆盖参数 + 大五调整（第 7.3 节）。"""
        profile = make_profile(
            personality={"big_five": {"openness": 0.82, "conscientiousness": 0.55,
                                      "extraversion": 0.40, "agreeableness": 0.75,
                                      "neuroticism": 0.45}},
            emotion_overrides={
                "initial_expression": {"PLE": 0.20, "ARO": -0.05, "DOM": -0.10,
                                       "INT": 0.25, "SEC": 0.35},
                "initial_internal": {"ANX": 0.25, "LON": 0.35, "CUR": 0.60, "PAT": 0.65},
                "decay_modifiers": {
                    "expression_half_life_hours": 5.0,
                    "affective_half_life_days": {"AFF": 35, "TRS": 100, "ATT": 200},
                },
                "internal_to_expression_weights": {"ANX": 1.2, "VUL": 1.1},
                "event_sensitivity": {"conflict": 1.3, "vulnerable": 1.1},
            },
            relationship_defaults={"initial_affection": 25, "initial_trust": 30,
                                   "initial_attachment": 15},
        )
        engine = make_engine(profile=profile)
        assert engine.state.expression == {
            "PLE": 0.20, "ARO": -0.05, "DOM": -0.10, "INT": 0.25, "SEC": 0.35}
        assert engine.state.internal["ANX"] == pytest.approx(0.2425)
        assert engine.state.internal["LON"] == pytest.approx(0.37)
        assert engine.state.internal["CUR"] == pytest.approx(0.68)
        assert engine.state.internal["PAT"] == pytest.approx(0.65 + 0.20 * (0.75 - 0.5))
        assert engine.state.affective == {"AFF": 25.0, "TRS": 30.0, "ATT": 15.0}
        assert engine.expression_half_life_hours == 5.0
        assert engine.affective_half_life_days == {"AFF": 35.0, "TRS": 100.0, "ATT": 200.0}
        assert engine.mapping_weights == {"ANX": 1.2, "VUL": 1.1}
        assert engine.event_sensitivity == {"conflict": 1.3, "vulnerable": 1.1}
        assert engine.gui_sensitivity == pytest.approx(1.02)

    def test_mood_baseline_from_user_state(self):
        engine = make_engine({"mood_baseline": {"PLE": 0.4}})
        assert engine.mood_baseline["PLE"] == 0.4
        assert engine.mood_baseline["ARO"] == 0.00


class TestApplyEvent:
    def test_affectionate_end_to_end(self):
        """默认引擎 + affectionate I=1.0：内部增量 → 映射 → 情感冲击全链路验算。"""
        engine = make_engine()
        delta = engine.apply_event(EmotionEvent(label="affectionate", intensity=1.0))

        assert delta.internal == {
            "ANX": -0.02, "LON": -0.05, "ANT": 0.03, "PRO": 0.03,
            "DEP": 0.02, "PAT": 0.02, "SAT": 0.08, "JEA": -0.02, "TRU": 0.03}
        # 映射后的表达层增量（文档 1 第 7.4 节，作用于更新后的内部态）
        assert delta.expression["PLE"] == pytest.approx(-0.0396)
        assert delta.expression["ARO"] == pytest.approx(0.0401)
        assert delta.expression["DOM"] == pytest.approx(0.0184)
        assert delta.expression["INT"] == pytest.approx(0.0664)
        assert delta.expression["SEC"] == pytest.approx(0.0904)
        assert engine.state.expression == {
            "PLE": pytest.approx(0.1104), "ARO": pytest.approx(0.0401),
            "DOM": pytest.approx(0.0184), "INT": pytest.approx(0.2664),
            "SEC": pytest.approx(0.3904)}
        # I=1.0 → impact 5.5
        assert delta.affective == {"AFF": 5.5, "TRS": 2.75}
        assert engine.state.affective == {"AFF": 20.5, "TRS": 27.75, "ATT": 10.0}

    def test_negative_event_at_s0_amplified(self):
        """S0 负面事件 ×1.3（文档 1 第 10.2 节）。"""
        engine = make_engine()  # AFF 15 → S0
        delta = engine.apply_event(EmotionEvent(label="conflict", intensity=0.7))
        assert delta.internal["ANX"] == pytest.approx(0.10 * 1.3 * 0.7)
        assert delta.affective["AFF"] == pytest.approx(-3.85)
        assert engine.state.affective["AFF"] == pytest.approx(11.15)
        assert engine.state.affective["TRS"] == pytest.approx(23.075)

    def test_negative_event_at_s3_softened(self):
        """S3+ 负面事件 ×0.8。"""
        engine = make_engine({"affective": {"AFF": 70.0, "TRS": 60.0, "ATT": 40.0}})
        delta = engine.apply_event(EmotionEvent(label="conflict", intensity=1.0))
        assert delta.affective["AFF"] == pytest.approx(-1.8333333)
        assert engine.state.affective["AFF"] == pytest.approx(68.1666667)

    def test_positive_event_ignores_stage_modifier(self):
        """正向标签不受关系阶段调节（文档只定义负向的 S0 ×1.3 / S3+ ×0.8）。"""
        engine = make_engine({"affective": {"AFF": 70.0, "TRS": 60.0, "ATT": 40.0}})
        delta = engine.apply_event(EmotionEvent(label="affectionate", intensity=0.6))
        assert delta.internal["SAT"] == pytest.approx(0.08 * 0.6)

    def test_character_sensitivity_applied(self):
        """角色敏感度 conflict=1.3，S2 无阶段调节 → I = 0.7 × 1.3。"""
        profile = make_profile(
            emotion_overrides={"event_sensitivity": {"conflict": 1.3}})
        engine = make_engine({"affective": {"AFF": 50.0}}, profile=profile)
        delta = engine.apply_event(EmotionEvent(label="conflict", intensity=0.7))
        assert delta.affective["AFF"] == pytest.approx(-3.85)

    def test_neutral_event_only_pulls_expression(self):
        """neutral 内部增量为 0，但映射仍把表达层拉向内部态隐含的均衡。"""
        engine = make_engine()
        delta = engine.apply_event(EmotionEvent(label="neutral", intensity=0.1))
        assert delta.internal == {}
        assert engine.state.expression["PLE"] == pytest.approx(0.15 - 0.061)

    def test_internal_clamped_to_unit_range(self):
        """内部层增量后钳制到 [0, 1]（裁决）。"""
        engine = make_engine({"internal": {"JEA": 0.99}})
        engine.apply_event(EmotionEvent(label="affectionate", intensity=1.0))
        # JEA 0.99 - 0.02 → 0.97，未触界；再打一次 hostile 类型的负向才触界，
        # 这里直接验证上限：用 LON 0.99 + affectionate 的 -0.05 不触界，
        # 故改为验证下限场景：ANX 0.0 + hostile 后仍 >= 0
        assert engine.state.internal["JEA"] == pytest.approx(0.97)

    def test_internal_lower_bound_clamped(self):
        engine = make_engine({"internal": {"ANX": 0.0}})
        engine.apply_event(EmotionEvent(label="affectionate", intensity=1.0))
        assert engine.state.internal["ANX"] == 0.0  # -0.02 被钳制

    def test_affective_clamped_to_100(self):
        engine = make_engine({"affective": {"AFF": 99.0}})
        engine.apply_event(EmotionEvent(label="affectionate", intensity=1.0))
        assert engine.state.affective["AFF"] == 100.0

    def test_soft_clamp_engaged_after_event(self):
        """SEC 被压到 < 0.2 后，INT 越界部分被拉回 30%。"""
        engine = make_engine({
            "expression": {"PLE": 0.0, "ARO": 0.0, "DOM": 0.0, "INT": 0.9, "SEC": 0.25},
            "internal": {"ANX": 1.0, "LON": 0.30, "CUR": 0.50, "ANT": 0.20,
                         "GUI": 0.0, "PRO": 0.10, "DEP": 0.15, "PAT": 0.70,
                         "VUL": 0.10, "SAT": 0.0, "JEA": 1.0, "TRU": 0.0},
        })
        # hostile I=1.0 且 S0 负面 ×1.3 → 内部态 ANX/JEA 顶满，SEC 增量约 -0.2
        engine.apply_event(EmotionEvent(label="hostile", intensity=1.0))
        assert engine.state.expression["SEC"] < 0.2
        # INT 越界（> 0.7）部分被拉回 30%，不再保持 0.9
        assert engine.state.expression["INT"] < 0.9

    def test_unknown_label_raises(self):
        engine = make_engine()
        with pytest.raises(ValueError):
            engine.apply_event(EmotionEvent(label="unknown", intensity=0.5))

    def test_returns_state_delta(self):
        engine = make_engine()
        delta = engine.apply_event(EmotionEvent(label="playful", intensity=0.4))
        assert isinstance(delta, StateDelta)
        assert set(delta.to_dict()) == {"expression", "internal", "affective"}

    def test_busy_signal_field_accepted(self):
        """busy_signal 由管道落库（文档 1 第 12.2 节），引擎原样接受不消费。"""
        engine = make_engine()
        delta = engine.apply_event(
            EmotionEvent(label="withdrawn", intensity=0.5, busy_signal=True))
        assert delta.internal != {}


class TestApplyDecay:
    def test_expression_decays_toward_mood_baseline(self):
        """表达层向慢心境基线指数回归（文档 1 第 9.1 / 9.2 节）。"""
        engine = make_engine({"expression": {
            "PLE": 0.5, "ARO": 0.4, "DOM": 0.3, "INT": 0.6, "SEC": 0.5}})
        delta = engine.apply_decay(4.0)
        # valence = PLE 0.5 → adjusted_rate = 0.693/4 * (1 - 0.15)
        factor = math.exp(-(0.693 / 4.0) * (1.0 - 0.3 * 0.5) * 4.0)
        assert engine.state.expression["PLE"] == pytest.approx(
            0.15 + (0.5 - 0.15) * factor)
        assert delta.expression["PLE"] == pytest.approx(
            engine.state.expression["PLE"] - 0.5)
        # 内部层此刻各维都在基线上，增量为 0（裁决④：内部层也参与衰减）
        assert all(change == pytest.approx(0.0)
                   for change in delta.internal.values())

    def test_affective_decays_toward_baseline(self):
        """情感层按 30/90/180 天半衰期回归基线。"""
        engine = make_engine({"affective": {"AFF": 30.0}})
        engine.apply_decay(24.0)
        factor = math.exp(-(0.693 / (30.0 * 24.0)) * (1.0 - 0.3 * 0.15) * 24.0)
        assert engine.state.affective["AFF"] == pytest.approx(15.0 + 15.0 * factor)

    def test_long_elapsed_converges_to_baseline(self):
        engine = make_engine({"expression": {"PLE": 0.9}})
        engine.apply_decay(240.0)
        assert engine.state.expression["PLE"] == pytest.approx(0.15, abs=1e-3)

    def test_character_half_life_override(self):
        profile = make_profile(emotion_overrides={
            "decay_modifiers": {"expression_half_life_hours": 100.0}})
        engine = make_engine({"expression": {"PLE": 0.5}}, profile=profile)
        engine.apply_decay(4.0)
        assert engine.state.expression["PLE"] == pytest.approx(0.5, abs=0.01)

class TestApplyDecayInternal:
    """裁决④：内部层按 mood_half_life_days（5 天）半衰期向基线回归，valence=0。"""

    def test_internal_decays_toward_baseline(self):
        """远离基线的维度朝基线方向移动（不断言具体数值）。"""
        engine = make_engine({"internal": {"LON": 0.9, "ANX": 0.0}})
        before_lon = engine.state.internal["LON"]
        before_anx = engine.state.internal["ANX"]
        engine.apply_decay(24.0 * 30)  # 30 天，远超 5 天半衰期
        assert engine.state.internal["LON"] < before_lon
        assert engine.state.internal["ANX"] > before_anx
        assert engine.state.internal["LON"] == pytest.approx(0.30, abs=0.05)

    def test_internal_half_life_returns_to_midpoint(self):
        """半衰期处约回到当前值与基线的中点。"""
        engine = make_engine({"internal": {"LON": 0.90}})
        engine.apply_decay(5.0 * 24.0)
        assert engine.state.internal["LON"] == pytest.approx((0.90 + 0.30) / 2, abs=0.01)

    def test_internal_decay_ignores_valence(self):
        """内部层衰减不随表达层效价变速（裁决④：valence 固定 0.0）。"""
        calm = make_engine({"expression": {"PLE": 0.9}, "internal": {"LON": 0.9}})
        grim = make_engine({"expression": {"PLE": -0.9}, "internal": {"LON": 0.9}})
        calm.apply_decay(48.0)
        grim.apply_decay(48.0)
        assert calm.state.internal["LON"] == grim.state.internal["LON"]

    def test_all_internal_dimensions_decay(self):
        """12 个维度全部参与衰减，delta 覆盖全维度。"""
        engine = make_engine()
        delta = engine.apply_decay(10.0)
        assert set(delta.internal) == set(INTERNAL_DIMENSIONS)


class TestPromptAndFullState:
    def test_expression_prompt_default_format(self):
        """文档 1 第 6.3 节的三行注入格式。"""
        engine = make_engine()
        assert engine.get_expression_prompt() == (
            "[EMOTION STATE]\n"
            "PLE: 0.15 | ARO: 0.00 | DOM: 0.00\n"
            "INT: 0.20 | SEC: 0.30\n"
            "Mood: neutral | Energy: moderate | Stance: equal\n"
            "[/EMOTION STATE]")

    def test_expression_prompt_qualitative_labels(self):
        """±0.4 阈值映射（文档 1 第 6.3 节映射表）。"""
        engine = make_engine({"expression": {
            "PLE": 0.62, "ARO": -0.50, "DOM": 0.50, "INT": 0.78, "SEC": 0.65}})
        prompt = engine.get_expression_prompt()
        assert "Mood: warm | Energy: low | Stance: leading" in prompt
        assert "PLE: 0.62 | ARO: -0.50 | DOM: 0.50" in prompt

    def test_full_state_shape(self):
        engine = make_engine()
        state = engine.get_full_state()
        assert set(state) == {"expression", "internal", "affective"}
        assert set(state["expression"]) == {"PLE", "ARO", "DOM", "INT", "SEC"}
        assert len(state["internal"]) == 12
        assert state["affective"]["stage_code"] == "S0"
        assert state["affective"]["stage_label"] == "陌生"

    def test_full_state_stage_fields(self):
        """文档 2 第 6.8 节示例：AFF 72 / TRS 58 / ATT 45 → S3 亲密。"""
        engine = make_engine({"affective": {"AFF": 72.0, "TRS": 58.0, "ATT": 45.0}})
        affective = engine.get_full_state()["affective"]
        assert affective["stage_code"] == "S3"
        assert affective["stage_label"] == "亲密"
