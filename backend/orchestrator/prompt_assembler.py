"""Prompt 装配器（文档 2 第 3.9 节）：把角色档案、情感状态、记忆上下文组装为完整 Prompt。

注入方式：文本注入（结构化文本拼接）。
装配顺序：角色 → 说话风格 → 情感状态 → 关系 →（按需）外貌 → 记忆 → 最近对话 → 用户消息。
"""

from __future__ import annotations

from core.character.profile import CharacterProfile

# 触发外貌注入的关键词（文档 2 第 3.9 节只说"按需注入外貌片段"，
# 未定义判定规则，此处用关键词启发式；已在交付报告中报告）
APPEARANCE_KEYWORDS: tuple[str, ...] = (
    "外貌", "长相", "样子", "长什么样", "身高", "体重", "发型", "头发",
    "眼睛", "胖", "瘦", "漂亮", "帅", "穿着", "穿搭",
)


class PromptAssembler:
    """职责：将角色档案、情感状态、记忆上下文组装为完整 Prompt（文档 2 第 3.9 节）。"""

    def build(self, character: CharacterProfile, expression_state: dict,
              affective_state: dict, memories: list, recent_turns: list,
              user_message: str) -> str:
        """装配完整 Prompt。

        输入：角色档案、表达层状态、情感层状态、记忆列表、最近对话列表、用户消息；
        输出：完整 Prompt 字符串；副作用：无（纯函数）。
        """
        sections = []

        # 1. 角色档案
        sections.append(self._render_character(character))

        # 2. 说话风格
        sections.append(self._render_speech_style(character.speech_style))

        # 3. 情感状态（文本注入）
        sections.append(self._render_emotion_state(expression_state))

        # 4. 关系状态
        sections.append(self._render_relationship(affective_state))

        # 5. 记忆上下文（按需注入外貌片段）
        if self._needs_appearance(user_message):
            sections.append(self._render_appearance(character.appearance))
        sections.append(self._render_memories(memories))

        # 6. 最近对话
        sections.append(self._render_recent_turns(recent_turns))

        # 7. 用户消息
        sections.append(f"[USER]\n{user_message}\n[/USER]")

        return "\n\n".join(sections)

    def _render_character(self, character: CharacterProfile) -> str:
        """角色基础身份与背景（性格部分）；缺省字段自动省略。"""
        lines = ["[CHARACTER]"]
        identity = [f"姓名：{character.name}"]
        if character.nickname:
            identity.append(f"昵称：{character.nickname}")
        basics = [part for part in (
            f"性别：{character.gender}" if character.gender else None,
            f"年龄：{character.age}" if character.age is not None else None,
            f"生日：{character.birthday}" if character.birthday else None,
            f"星座：{character.zodiac}" if character.zodiac else None,
        ) if part]
        if basics:
            identity.append(" ｜ ".join(basics))
        lines.extend(identity)

        background = character.background
        background_parts = [part for part in (
            f"{background.birthplace}人" if background.birthplace else None,
            background.education,
            background.occupation,
            background.living_situation,
            background.family,
        ) if part]
        if background_parts:
            lines.append("背景：" + "。".join(background_parts))
        if background.key_experiences:
            lines.append("经历：")
            lines.extend(f"- {item}" for item in background.key_experiences)
        if background.current_state:
            lines.append(f"当前状态：{background.current_state}")

        personality = character.personality
        trait_parts = [part for part in (
            "性格：" + "、".join(personality.traits) if personality.traits else None,
            "缺点：" + "、".join(personality.flaws) if personality.flaws else None,
            "价值观：" + "、".join(personality.values) if personality.values else None,
            "恐惧：" + "、".join(personality.fears) if personality.fears else None,
        ) if part]
        if trait_parts:
            lines.append(" ｜ ".join(trait_parts))

        lines.append("[/CHARACTER]")
        return "\n".join(lines)

    def _render_speech_style(self, speech_style) -> str:
        """说话风格（文档 1 第 3.2 节 speech_style）。"""
        lines = ["[SPEECH STYLE]"]
        rows = [
            ("语气", speech_style.tone),
            ("节奏", speech_style.pace),
            ("用词", speech_style.vocabulary),
            ("口头禅", "、".join(speech_style.catchphrases) if speech_style.catchphrases else None),
            ("避免", "、".join(speech_style.avoid) if speech_style.avoid else None),
            ("表情使用", speech_style.emoji_usage),
            ("标点习惯", speech_style.punctuation),
        ]
        lines.extend(f"{label}：{value}" for label, value in rows if value)
        lines.append("[/SPEECH STYLE]")
        return "\n".join(lines)

    def _render_emotion_state(self, state: dict) -> str:
        """情感状态文本注入块（文档 2 第 3.9 节，与文档 1 第 6.3 节一致）。"""
        # 由 PLE/ARO/DOM 映射为 Mood/Energy/Stance 三个定性标签，
        # 统一阈值 ±0.4，映射表见文档 1 第 6.3 节
        def label(value, low, mid, high):
            if value >= 0.4:
                return high
            if value <= -0.4:
                return low
            return mid

        mood = label(state['PLE'], 'cool', 'neutral', 'warm')
        energy = label(state['ARO'], 'low', 'moderate', 'high')
        stance = label(state['DOM'], 'following', 'equal', 'leading')

        return f"""[EMOTION STATE]
PLE: {state['PLE']:.2f} | ARO: {state['ARO']:.2f} | DOM: {state['DOM']:.2f}
INT: {state['INT']:.2f} | SEC: {state['SEC']:.2f}
Mood: {mood} | Energy: {energy} | Stance: {stance}
[/EMOTION STATE]"""

    def _render_relationship(self, affective_state: dict) -> str:
        """关系状态（stage_code + stage_label 由后端分开给，展示文案在此组合）。"""
        stage_code = affective_state.get("stage_code", "")
        stage_label = affective_state.get("stage_label", "")
        lines = ["[RELATIONSHIP]"]
        lines.append(f"关系阶段：{stage_label}（{stage_code}）")
        lines.append(
            f"好感度：{affective_state.get('AFF', 0)}/100"
            f" ｜ 信任度：{affective_state.get('TRS', 0)}/100"
            f" ｜ 依恋度：{affective_state.get('ATT', 0)}/100")
        lines.append("[/RELATIONSHIP]")
        return "\n".join(lines)

    def _needs_appearance(self, user_message: str) -> bool:
        """用户消息是否在问外貌（关键词启发式，文档未定义判定规则）。"""
        return any(keyword in user_message for keyword in APPEARANCE_KEYWORDS)

    def _render_appearance(self, appearance) -> str:
        """外貌片段（文档 1 第 3.2 节 appearance）。"""
        lines = ["[APPEARANCE]"]
        basics = [part for part in (
            f"身高 {appearance.height_cm}cm" if appearance.height_cm else None,
            f"体重 {appearance.weight_kg}kg" if appearance.weight_kg else None,
            f"体型：{appearance.body_type}" if appearance.body_type else None,
        ) if part]
        if basics:
            lines.append(" ｜ ".join(basics))
        rows = [
            ("发型", appearance.hair),
            ("眼睛", appearance.eyes),
            ("皮肤", appearance.skin),
            ("特征", appearance.distinguishing),
            ("穿着风格", appearance.style),
        ]
        lines.extend(f"{label}：{value}" for label, value in rows if value)
        lines.append("[/APPEARANCE]")
        return "\n".join(lines)

    def _render_memories(self, memories: list) -> str:
        """记忆上下文（文档 2 第 5.1 节记忆条目结构，Phase 5 前恒为空列表）。"""
        lines = ["[MEMORIES]"]
        if memories:
            for memory in memories:
                tag = memory.get("emotion_tag") or "neutral"
                lines.append(f"- [{tag}] {memory.get('content', '')}")
        else:
            lines.append("（暂无相关记忆）")
        lines.append("[/MEMORIES]")
        return "\n".join(lines)

    def _render_recent_turns(self, recent_turns: list) -> str:
        """最近对话（ConversationTurn 列表，Phase 4 前恒为空列表）。"""
        lines = ["[RECENT TURNS]"]
        if recent_turns:
            for turn in recent_turns:
                lines.append(f"{turn.role}: {turn.text}")
        else:
            lines.append("（暂无对话历史）")
        lines.append("[/RECENT TURNS]")
        return "\n".join(lines)
