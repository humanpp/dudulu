"""角色档案数据类：文档 1 第 3.2 节定义的完整结构。

本文件只放数据结构（无管理逻辑）；创建/切换/模板/随机/校验在 Phase 6 的
manager / template_loader / generator / validator 中实现。
情感引擎（文档 2 第 3.1 节）的构造函数以 CharacterProfile 为契约，故先落地数据类。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BigFive:
    """大五人格（文档 1 第 3.2 节 personality.big_five）。"""

    openness: float = 0.5
    conscientiousness: float = 0.5
    extraversion: float = 0.5
    agreeableness: float = 0.5
    neuroticism: float = 0.5

    def to_dict(self) -> dict[str, float]:
        """输出：五维 dict；副作用：无。"""
        return {
            "openness": self.openness,
            "conscientiousness": self.conscientiousness,
            "extraversion": self.extraversion,
            "agreeableness": self.agreeableness,
            "neuroticism": self.neuroticism,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "BigFive":
        """从 dict 还原，缺省 0.5；副作用：无。"""
        data = data or {}
        return cls(**{key: data.get(key, 0.5) for key in
                      ("openness", "conscientiousness", "extraversion",
                       "agreeableness", "neuroticism")})


@dataclass
class Personality:
    """性格与说话风格的性格部分（文档 1 第 3.2 节 personality）。"""

    big_five: BigFive = field(default_factory=BigFive)
    traits: list[str] = field(default_factory=list)
    flaws: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    fears: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Personality":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            big_five=BigFive.from_dict(data.get("big_five")),
            traits=list(data.get("traits") or []),
            flaws=list(data.get("flaws") or []),
            values=list(data.get("values") or []),
            fears=list(data.get("fears") or []),
        )


@dataclass
class Appearance:
    """外貌与体型（文档 1 第 3.2 节 appearance）。"""

    height_cm: int | None = None
    weight_kg: float | None = None
    body_type: str | None = None
    hair: str | None = None
    eyes: str | None = None
    skin: str | None = None
    distinguishing: str | None = None
    style: str | None = None
    avatar_url: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "Appearance":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(**{key: data.get(key) for key in
                      ("height_cm", "weight_kg", "body_type", "hair", "eyes",
                       "skin", "distinguishing", "style", "avatar_url")})


@dataclass
class Background:
    """背景故事（文档 1 第 3.2 节 background）。"""

    birthplace: str | None = None
    education: str | None = None
    occupation: str | None = None
    living_situation: str | None = None
    family: str | None = None
    key_experiences: list[str] = field(default_factory=list)
    current_state: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "Background":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            birthplace=data.get("birthplace"),
            education=data.get("education"),
            occupation=data.get("occupation"),
            living_situation=data.get("living_situation"),
            family=data.get("family"),
            key_experiences=list(data.get("key_experiences") or []),
            current_state=data.get("current_state"),
        )


@dataclass
class SpeechStyle:
    """说话风格（文档 1 第 3.2 节 speech_style）。"""

    tone: str | None = None
    pace: str | None = None
    vocabulary: str | None = None
    catchphrases: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    emoji_usage: str | None = None
    punctuation: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "SpeechStyle":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            tone=data.get("tone"),
            pace=data.get("pace"),
            vocabulary=data.get("vocabulary"),
            catchphrases=list(data.get("catchphrases") or []),
            avoid=list(data.get("avoid") or []),
            emoji_usage=data.get("emoji_usage"),
            punctuation=data.get("punctuation"),
        )


@dataclass
class Theme:
    """角色主题色三色（文档 1 第 3.3 节）。"""

    primary: str = "#C4A882"
    soft: str = "#E8DDD0"
    deep: str = "#8B7355"

    @classmethod
    def from_dict(cls, data: dict | None) -> "Theme":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            primary=data.get("primary", "#C4A882"),
            soft=data.get("soft", "#E8DDD0"),
            deep=data.get("deep", "#8B7355"),
        )


@dataclass
class RelationshipDefaults:
    """关系初始设定（文档 1 第 3.2 节 relationship_defaults）。"""

    initial_stage: str = "S1"
    initial_affection: float | None = None
    initial_trust: float | None = None
    initial_attachment: float | None = None
    first_meeting_context: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "RelationshipDefaults":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            initial_stage=data.get("initial_stage", "S1"),
            initial_affection=data.get("initial_affection"),
            initial_trust=data.get("initial_trust"),
            initial_attachment=data.get("initial_attachment"),
            first_meeting_context=data.get("first_meeting_context"),
        )


@dataclass
class DecayModifiers:
    """衰减覆盖参数（文档 1 第 3.2 节 emotion_overrides.decay_modifiers）。"""

    expression_half_life_hours: float | None = None
    affective_half_life_days: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict | None) -> "DecayModifiers":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            expression_half_life_hours=data.get("expression_half_life_hours"),
            affective_half_life_days=dict(data.get("affective_half_life_days") or {}),
        )


@dataclass
class ProactiveOverrides:
    """主动行为覆盖参数（文档 1 第 3.2 / 12.7 节，缺省项跟随全局配置）。"""

    interval_hours: float | None = None
    min_gap_hours: float | None = None
    max_daily: int | None = None
    silence_threshold: int | None = None
    silence_recovery_hours: float | None = None
    eagerness: float | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "ProactiveOverrides":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(**{key: data.get(key) for key in
                      ("interval_hours", "min_gap_hours", "max_daily",
                       "silence_threshold", "silence_recovery_hours", "eagerness")})


@dataclass
class EmotionOverrides:
    """情感引擎覆盖参数（文档 1 第 3.2 节 emotion_overrides）。"""

    initial_expression: dict[str, float] = field(default_factory=dict)
    initial_internal: dict[str, float] = field(default_factory=dict)
    decay_modifiers: DecayModifiers = field(default_factory=DecayModifiers)
    internal_to_expression_weights: dict[str, float] = field(default_factory=dict)
    event_sensitivity: dict[str, float] = field(default_factory=dict)
    proactive: ProactiveOverrides = field(default_factory=ProactiveOverrides)

    @classmethod
    def from_dict(cls, data: dict | None) -> "EmotionOverrides":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            initial_expression=dict(data.get("initial_expression") or {}),
            initial_internal=dict(data.get("initial_internal") or {}),
            decay_modifiers=DecayModifiers.from_dict(data.get("decay_modifiers")),
            internal_to_expression_weights=dict(
                data.get("internal_to_expression_weights") or {}),
            event_sensitivity=dict(data.get("event_sensitivity") or {}),
            proactive=ProactiveOverrides.from_dict(data.get("proactive")),
        )


@dataclass
class Visibility:
    """角色可见性（文档 1 第 3.4 节）。"""

    is_public: bool = False
    owner_user_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "Visibility":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            is_public=bool(data.get("is_public", False)),
            owner_user_id=data.get("owner_user_id"),
        )


@dataclass
class GenerationMeta:
    """生成元数据（文档 1 第 3.2 节 generation_meta）。"""

    source: str = "manual"
    template_id: str | None = None
    random_seed: int | None = None
    locked_fields: list[str] = field(default_factory=list)
    created_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "GenerationMeta":
        """从 dict 还原；副作用：无。"""
        data = data or {}
        return cls(
            source=data.get("source", "manual"),
            template_id=data.get("template_id"),
            random_seed=data.get("random_seed"),
            locked_fields=list(data.get("locked_fields") or []),
            created_at=data.get("created_at"),
        )


@dataclass
class CharacterProfile:
    """角色档案（文档 1 第 3.2 节完整结构）。"""

    id: str
    name: str
    nickname: str | None = None
    gender: str | None = None
    age: int | None = None
    birthday: str | None = None
    zodiac: str | None = None
    appearance: Appearance = field(default_factory=Appearance)
    background: Background = field(default_factory=Background)
    personality: Personality = field(default_factory=Personality)
    speech_style: SpeechStyle = field(default_factory=SpeechStyle)
    theme: Theme = field(default_factory=Theme)
    relationship_defaults: RelationshipDefaults = field(
        default_factory=RelationshipDefaults)
    emotion_overrides: EmotionOverrides = field(default_factory=EmotionOverrides)
    proactive_enabled: bool = True
    visibility: Visibility = field(default_factory=Visibility)
    generation_meta: GenerationMeta = field(default_factory=GenerationMeta)
    # 稀有变体标记（文档 1 第 5.6 节：稀有特质/隐藏背景/情感彩蛋）
    is_rare: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "CharacterProfile":
        """从 YAML/dict 还原完整档案；副作用：无。"""
        return cls(
            id=data["id"],
            name=data["name"],
            nickname=data.get("nickname"),
            gender=data.get("gender"),
            age=data.get("age"),
            birthday=data.get("birthday"),
            zodiac=data.get("zodiac"),
            appearance=Appearance.from_dict(data.get("appearance")),
            background=Background.from_dict(data.get("background")),
            personality=Personality.from_dict(data.get("personality")),
            speech_style=SpeechStyle.from_dict(data.get("speech_style")),
            theme=Theme.from_dict(data.get("theme")),
            relationship_defaults=RelationshipDefaults.from_dict(
                data.get("relationship_defaults")),
            emotion_overrides=EmotionOverrides.from_dict(data.get("emotion_overrides")),
            proactive_enabled=bool(data.get("proactive_enabled", True)),
            visibility=Visibility.from_dict(data.get("visibility")),
            generation_meta=GenerationMeta.from_dict(data.get("generation_meta")),
            is_rare=bool(data.get("is_rare", False)),
        )

    def to_dict(self) -> dict:
        """输出完整档案 dict（文档 1 第 3.2 节结构）。

        用于持久化（characters.profile_json）、导出角色卡与 API 响应；
        副作用：无。
        """
        return {
            "id": self.id,
            "name": self.name,
            "nickname": self.nickname,
            "gender": self.gender,
            "age": self.age,
            "birthday": self.birthday,
            "zodiac": self.zodiac,
            "appearance": {
                "height_cm": self.appearance.height_cm,
                "weight_kg": self.appearance.weight_kg,
                "body_type": self.appearance.body_type,
                "hair": self.appearance.hair,
                "eyes": self.appearance.eyes,
                "skin": self.appearance.skin,
                "distinguishing": self.appearance.distinguishing,
                "style": self.appearance.style,
                "avatar_url": self.appearance.avatar_url,
            },
            "background": {
                "birthplace": self.background.birthplace,
                "education": self.background.education,
                "occupation": self.background.occupation,
                "living_situation": self.background.living_situation,
                "family": self.background.family,
                "key_experiences": list(self.background.key_experiences),
                "current_state": self.background.current_state,
            },
            "personality": {
                "big_five": self.personality.big_five.to_dict(),
                "traits": list(self.personality.traits),
                "flaws": list(self.personality.flaws),
                "values": list(self.personality.values),
                "fears": list(self.personality.fears),
            },
            "speech_style": {
                "tone": self.speech_style.tone,
                "pace": self.speech_style.pace,
                "vocabulary": self.speech_style.vocabulary,
                "catchphrases": list(self.speech_style.catchphrases),
                "avoid": list(self.speech_style.avoid),
                "emoji_usage": self.speech_style.emoji_usage,
                "punctuation": self.speech_style.punctuation,
            },
            "theme": {
                "primary": self.theme.primary,
                "soft": self.theme.soft,
                "deep": self.theme.deep,
            },
            "relationship_defaults": {
                "initial_stage": self.relationship_defaults.initial_stage,
                "initial_affection": self.relationship_defaults.initial_affection,
                "initial_trust": self.relationship_defaults.initial_trust,
                "initial_attachment": self.relationship_defaults.initial_attachment,
                "first_meeting_context":
                    self.relationship_defaults.first_meeting_context,
            },
            "emotion_overrides": {
                "initial_expression": dict(self.emotion_overrides.initial_expression),
                "initial_internal": dict(self.emotion_overrides.initial_internal),
                "decay_modifiers": {
                    "expression_half_life_hours":
                        self.emotion_overrides.decay_modifiers
                        .expression_half_life_hours,
                    "affective_half_life_days": dict(
                        self.emotion_overrides.decay_modifiers
                        .affective_half_life_days),
                },
                "internal_to_expression_weights": dict(
                    self.emotion_overrides.internal_to_expression_weights),
                "event_sensitivity": dict(self.emotion_overrides.event_sensitivity),
                "proactive": {
                    "interval_hours":
                        self.emotion_overrides.proactive.interval_hours,
                    "min_gap_hours":
                        self.emotion_overrides.proactive.min_gap_hours,
                    "max_daily": self.emotion_overrides.proactive.max_daily,
                    "silence_threshold":
                        self.emotion_overrides.proactive.silence_threshold,
                    "silence_recovery_hours":
                        self.emotion_overrides.proactive.silence_recovery_hours,
                    "eagerness": self.emotion_overrides.proactive.eagerness,
                },
            },
            "proactive_enabled": self.proactive_enabled,
            "visibility": {
                "is_public": self.visibility.is_public,
                "owner_user_id": self.visibility.owner_user_id,
            },
            "generation_meta": {
                "source": self.generation_meta.source,
                "template_id": self.generation_meta.template_id,
                "random_seed": self.generation_meta.random_seed,
                "locked_fields": list(self.generation_meta.locked_fields),
                "created_at": self.generation_meta.created_at,
            },
            "is_rare": self.is_rare,
        }
