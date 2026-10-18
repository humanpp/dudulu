"""Prompt 装配器单元测试（文档 2 第 3.9 节 + 文档 1 第 6.3 节注入格式）。"""

from helpers import LIN_ZHIXIA
from orchestrator.prompt_assembler import PromptAssembler

from core.character.profile import CharacterProfile

ASSEMBLER = PromptAssembler()
PROFILE = CharacterProfile.from_dict(LIN_ZHIXIA)

EXPRESSION = {"PLE": 0.62, "ARO": 0.35, "DOM": 0.10, "INT": 0.78, "SEC": 0.65}
AFFECTIVE = {"AFF": 72, "TRS": 58, "ATT": 45, "stage_code": "S3",
             "stage_label": "亲密"}


def build(user_message: str = "今天好累啊") -> str:
    return ASSEMBLER.build(
        character=PROFILE,
        expression_state=EXPRESSION,
        affective_state=AFFECTIVE,
        memories=[],
        recent_turns=[],
        user_message=user_message,
    )


class TestBuild:
    def test_section_order(self):
        """装配顺序：角色 → 说话风格 → 情感状态 → 关系 → 记忆 → 最近对话 → 用户消息。"""
        prompt = build()
        positions = [
            prompt.index("[CHARACTER]"),
            prompt.index("[SPEECH STYLE]"),
            prompt.index("[EMOTION STATE]"),
            prompt.index("[RELATIONSHIP]"),
            prompt.index("[MEMORIES]"),
            prompt.index("[RECENT TURNS]"),
            prompt.index("[USER]"),
        ]
        assert positions == sorted(positions)

    def test_sections_joined_by_blank_line(self):
        prompt = build()
        assert "\n\n[SPEECH STYLE]" in prompt

    def test_character_section_content(self):
        prompt = build()
        assert "姓名：林知夏" in prompt
        assert "昵称：知夏" in prompt
        assert "插画师，自由职业" in prompt
        assert "大学时曾休学一年去云南支教" in prompt
        assert "[/CHARACTER]" in prompt

    def test_speech_style_section_content(self):
        prompt = build()
        assert "语气：温和、口语化、偶尔俏皮" in prompt
        assert "口头禅：嗯……让我想想、这样啊、其实我觉得吧" in prompt

    def test_user_message_wrapped(self):
        prompt = build("今天好累啊")
        assert prompt.endswith("[USER]\n今天好累啊\n[/USER]")

    def test_empty_memories_and_turns_use_placeholders(self):
        prompt = build()
        assert "（暂无相关记忆）" in prompt
        assert "（暂无对话历史）" in prompt

    def test_memories_rendered_when_present(self):
        prompt = ASSEMBLER.build(
            character=PROFILE, expression_state=EXPRESSION,
            affective_state=AFFECTIVE,
            memories=[{"id": "m1", "content": "用户提到工作压力大",
                       "emotion_tag": "vulnerable"}],
            recent_turns=[], user_message="后来呢")
        assert "- [vulnerable] 用户提到工作压力大" in prompt

    def test_memory_without_tag_defaults_neutral(self):
        prompt = ASSEMBLER.build(
            character=PROFILE, expression_state=EXPRESSION,
            affective_state=AFFECTIVE,
            memories=[{"id": "m1", "content": "随便聊聊"}],
            recent_turns=[], user_message="嗨")
        assert "- [neutral] 随便聊聊" in prompt

    def test_recent_turns_rendered_when_present(self):
        from memory.schemas import ConversationTurn

        turns = [
            ConversationTurn(character_id="c", user_id="u", role="user",
                             text="今天好累"),
            ConversationTurn(character_id="c", user_id="u", role="assistant",
                             text="辛苦啦"),
        ]
        prompt = ASSEMBLER.build(
            character=PROFILE, expression_state=EXPRESSION,
            affective_state=AFFECTIVE, memories=[], recent_turns=turns,
            user_message="嗯")
        assert "user: 今天好累" in prompt
        assert "assistant: 辛苦啦" in prompt
        assert "（暂无对话历史）" not in prompt


class TestEmotionStateBlock:
    def test_doc_example_format(self):
        """文档 1 第 6.3 节示例数值 → 文档 2 第 3.9 节的三行块。"""
        block = ASSEMBLER._render_emotion_state(EXPRESSION)
        assert block == (
            "[EMOTION STATE]\n"
            "PLE: 0.62 | ARO: 0.35 | DOM: 0.10\n"
            "INT: 0.78 | SEC: 0.65\n"
            "Mood: warm | Energy: moderate | Stance: equal\n"
            "[/EMOTION STATE]")

    def test_qualitative_label_thresholds(self):
        """±0.4 阈值映射（文档 1 第 6.3 节映射表）。"""
        block = ASSEMBLER._render_emotion_state(
            {"PLE": -0.62, "ARO": -0.50, "DOM": 0.50,
             "INT": 0.0, "SEC": 0.0})
        assert "Mood: cool | Energy: low | Stance: leading" in block

    def test_zero_values_are_neutral(self):
        block = ASSEMBLER._render_emotion_state(
            {"PLE": 0.0, "ARO": 0.0, "DOM": 0.0, "INT": 0.0, "SEC": 0.0})
        assert "Mood: neutral | Energy: moderate | Stance: equal" in block


class TestRelationshipSection:
    def test_stage_and_values(self):
        block = ASSEMBLER._render_relationship(AFFECTIVE)
        assert "关系阶段：亲密（S3）" in block
        assert "好感度：72/100" in block
        assert "信任度：58/100" in block
        assert "依恋度：45/100" in block


class TestAppearanceInjection:
    def test_injected_when_user_asks_looks(self):
        prompt = build("你长什么样？")
        assert "[APPEARANCE]" in prompt
        assert "身高 165cm" in prompt
        assert "左耳有一颗小痣" in prompt

    def test_not_injected_for_normal_message(self):
        prompt = build("今天好累啊")
        assert "[APPEARANCE]" not in prompt

    def test_appearance_before_memories(self):
        prompt = build("你头发什么颜色")
        assert prompt.index("[APPEARANCE]") < prompt.index("[MEMORIES]")
