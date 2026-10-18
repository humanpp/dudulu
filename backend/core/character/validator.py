"""角色一致性校验（文档 1 第 5.3 节）。

校验随机/手动档案是否自相矛盾，返回结构化问题列表（字段 + 描述）。
生成器按问题里的字段做"只重试冲突字段"的重试；前端实时校验直接展示。

已实现的约束（文档 1 第 5.3 节示例逐条对应）：
1. 年龄 vs 职业：资深职业有最低年龄要求（occupations.yaml 的 min_age）
2. 性格 vs 说话风格：极度外向（extraversion > 0.8）不能配沉默寡言的语气
3. 体型 vs 背景：athletic 不能配"从不运动"类背景
4. 神经质 vs 冲突冲击：神经质 > 0.8 时冲突敏感度不能低于 0.8

姓名与性别的一致性（文档 1 第 5.2 节表格第 1 行）无法从中文姓名可靠
推断性别，未实现为校验规则——生成路径按性别池取名天然一致，手动创建
路径不拦截（已在交付报告中说明）。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.character.profile import CharacterProfile

# 极度外向阈值（文档 1 第 5.3 节示例"性格极度外向 + 说话风格沉默寡言"）
EXTREME_EXTRAVERSION = 0.8
# 沉默类语气关键词（命中即视为"沉默寡言"）
SILENT_TONE_KEYWORDS = ("沉默", "寡言", "不苟言笑", "话少")
# 神经质高值阈值与冲突敏感度下限（文档 1 第 5.3 节示例"神经质 0.9 + 冲突 ×0.5"）
HIGH_NEUROTICISM = 0.8
MIN_CONFLICT_SENSITIVITY = 0.8
# "从不运动"类背景关键词（与 athletic 体型冲突）
SEDENTARY_KEYWORDS = ("从不运动", "不爱运动", "讨厌运动")


@dataclass
class ConsistencyIssue:
    """一条一致性问题：冲突字段 + 人类可读描述。"""

    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """输出 API 用的 dict 形态；副作用：无。"""
        return {"field": self.field, "message": self.message}


def validate_consistency(profile: CharacterProfile) -> list[ConsistencyIssue]:
    """校验角色档案的一致性。

    输入：CharacterProfile；输出：ConsistencyIssue 列表（空 = 通过）；
    副作用：无。
    """
    issues: list[ConsistencyIssue] = []

    if not profile.name or not profile.name.strip():
        issues.append(ConsistencyIssue("name", "名字不能为空"))

    issues.extend(_check_age_vs_occupation(profile))
    issues.extend(_check_extraversion_vs_tone(profile))
    issues.extend(_check_body_vs_background(profile))
    issues.extend(_check_neuroticism_vs_sensitivity(profile))
    return issues


def _check_age_vs_occupation(profile: CharacterProfile) -> list[ConsistencyIssue]:
    """年龄 vs 职业：occupations.yaml 中带 min_age 的职业要求年龄达标。"""
    occupation = profile.background.occupation
    age = profile.age
    if not occupation or age is None:
        return []
    min_age = _occupation_min_age(occupation)
    if min_age is not None and age < min_age:
        return [ConsistencyIssue(
            "age", f"年龄 {age} 岁与职业「{occupation}」不符"
                   f"（该职业通常至少 {min_age} 岁）")]
    return []


def _occupation_min_age(occupation: str) -> int | None:
    """查职业的最低年龄要求（数据在 pools/occupations.yaml）。

    Phase 6 的 occupations.yaml 条目形如 {name, min_age}；纯字符串条目
    （无年龄要求）返回 None。加载失败时返回 None（不阻塞校验）。
    """
    from core.character.template_loader import load_occupation_rules

    try:
        return load_occupation_rules().get(occupation)
    except Exception:  # 数据缺失不应让校验崩溃
        return None


def _check_extraversion_vs_tone(profile: CharacterProfile) -> list[ConsistencyIssue]:
    """极度外向不能配沉默寡言的语气。"""
    extraversion = profile.personality.big_five.extraversion
    tone = profile.speech_style.tone or ""
    if extraversion > EXTREME_EXTRAVERSION and any(
            keyword in tone for keyword in SILENT_TONE_KEYWORDS):
        return [ConsistencyIssue(
            "speech_style.tone",
            f"外向性 {extraversion} 与说话风格「{tone}」矛盾")]
    return []


def _check_body_vs_background(profile: CharacterProfile) -> list[ConsistencyIssue]:
    """athletic 体型不能配"从不运动"类背景。"""
    body_type = profile.appearance.body_type
    if body_type != "athletic":
        return []
    texts = list(profile.background.key_experiences)
    if profile.background.living_situation:
        texts.append(profile.background.living_situation)
    for text in texts:
        if any(keyword in text for keyword in SEDENTARY_KEYWORDS):
            return [ConsistencyIssue(
                "appearance.body_type",
                f"体型 athletic 与背景「{text}」矛盾")]
    return []


def _check_neuroticism_vs_sensitivity(profile: CharacterProfile) -> list[ConsistencyIssue]:
    """高神经质角色的冲突冲击不能过低。"""
    neuroticism = profile.personality.big_five.neuroticism
    sensitivity = (profile.emotion_overrides.event_sensitivity or {}).get("conflict")
    if (neuroticism > HIGH_NEUROTICISM and sensitivity is not None
            and sensitivity < MIN_CONFLICT_SENSITIVITY):
        return [ConsistencyIssue(
            "emotion_overrides.event_sensitivity.conflict",
            f"神经质 {neuroticism} 与冲突冲击 ×{sensitivity} 矛盾"
            f"（高神经质角色冲突敏感度不应低于 {MIN_CONFLICT_SENSITIVITY}）")]
    return []
