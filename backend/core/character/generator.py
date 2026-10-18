"""角色随机生成引擎（文档 2 第 3.3 / 3.5 节 + 文档 1 第五章）。

CharacterGenerator：在模板约束下随机生成角色参数并持久化前的完整档案；
PreviewGenerator：为创建向导提供不持久化的随机预览（单字段/全部/校验）。

随机策略照文档 1 第 5.2 节表格；一致性冲突只重试冲突字段（第 5.3 节），
最多 max_retry 次后降级为模板默认值；相同种子 + 相同模板 = 相同角色
（第 5.4 节）。稀有特质/隐藏背景/情感彩蛋的概率见配置 randomization 节
（第 5.6 节），命中即给档案打 is_rare。
"""

from __future__ import annotations

import logging
import random

import yaml

from core.character.profile import CharacterProfile, GenerationMeta
from core.character.template_loader import (
    CONFIG_PATH,
    Template,
    TemplateLoader,
    load_name_pool,
    load_pool,
)
from core.character.validator import validate_consistency
from memory.schemas import generate_id, now_iso

logger = logging.getLogger(__name__)

# 表达层可随机偏移的维度（情感彩蛋用；文档 1 第 5.6 节"特殊的初始状态"）
EXPRESSION_DIMS = ("PLE", "ARO", "DOM", "INT", "SEC")

# 单字段随机支持的字段名（前端 🎲 按钮的取值域）
RANDOMIZABLE_FIELDS = (
    "name", "gender", "age", "body_type", "height", "hair", "occupation",
    "living_situation", "key_experiences", "big_five", "tone", "pace",
    "catchphrases", "initial_affection",
)


def _load_randomization_config(config_path: str = CONFIG_PATH) -> dict:
    """读配置 randomization 节；副作用：读 YAML 文件。"""
    try:
        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        return config.get("randomization") or {}
    except OSError as exc:
        logger.warning("读取随机配置失败，使用内置默认值: %s", exc)
        return {}


class CharacterGenerator:
    """职责：在模板约束下，随机生成角色参数（文档 2 第 3.5 节）。"""

    def __init__(self, template_loader: TemplateLoader,
                 config_path: str = CONFIG_PATH):
        """初始化生成器。

        输入：模板加载器、数值配置路径；副作用：读一次随机配置。
        """
        self.template_loader = template_loader
        self.config = _load_randomization_config(config_path)
        self.max_retry: int = int(self.config.get("max_retry", 10))
        self.age_range: tuple[int, int] = tuple(
            self.config.get("age_range", [18, 35]))
        self.height_mean: float = float(
            (self.config.get("height_distribution") or {}).get("mean", 165))
        self.height_std: float = float(
            (self.config.get("height_distribution") or {}).get("std", 8))
        self.body_type_weights: dict[str, float] = dict(
            self.config.get("body_type_weights") or {
                "slim": 0.30, "athletic": 0.25, "curvy": 0.25, "plump": 0.20})
        self.gender_weights: dict[str, float] = dict(
            self.config.get("gender_weights") or {
                "male": 0.40, "female": 0.40, "non_binary": 0.20})
        self.rare_trait_probability: float = float(
            self.config.get("rare_trait_probability", 0.05))
        self.hidden_background_probability: float = float(
            self.config.get("hidden_background_probability", 0.10))
        self.emotion_egg_probability: float = float(
            self.config.get("emotion_egg_probability", 0.03))

    # ── 完整生成 ────────────────────────────────────────────

    def generate(self, template: Template, seed: int | None = None,
                 locked_fields: list | None = None,
                 base_profile: dict | None = None) -> CharacterProfile:
        """在模板约束下随机生成角色档案（文档 2 第 3.5 节五步流程）。

        输入：模板、随机种子（None 时现场生成并记录）、用户锁定字段、
            基础档案（随机全部预览时传入当前值，锁定字段保持其值）；
        输出：CharacterProfile（未持久化）；副作用：无（不写库）。
        """
        effective_seed = seed if seed is not None else random.randrange(2 ** 31)
        rng = random.Random(effective_seed)
        locked = set(locked_fields or []) | set(template.locked_fields)
        base = dict(base_profile or {})

        profile_data = self._build_profile_data(template, rng, locked, base)
        profile_data, unresolved = self._retry_conflicts(
            template, rng, locked, base, profile_data)
        if unresolved:
            # 仍失败：降级为"部分随机"，冲突字段使用模板默认值（文档 1 第 5.3 节）；
            # 用户显式锁定的字段不降级（锁定优先）
            self._degrade_to_template_defaults(
                profile_data, unresolved, template,
                user_locked=set(locked_fields or []))
            logger.info("一致性冲突未消除，冲突字段降级为模板默认值: %s",
                        sorted(unresolved))

        profile = CharacterProfile.from_dict(profile_data)
        profile.generation_meta = GenerationMeta(
            source="random",
            template_id=template.id,
            random_seed=effective_seed,
            locked_fields=sorted(set(locked_fields or [])),
            created_at=now_iso(),
        )
        return profile

    def _build_profile_data(self, template: Template, rng: random.Random,
                            locked: set[str], base: dict) -> dict:
        """逐字段随机（跳过锁定字段），产出档案 dict（文档 1 第 5.2 节）。"""
        defaults = template.defaults or {}
        gender = self._random_gender(rng)
        if "gender" in locked and base.get("gender"):
            gender = base["gender"]
        elif "gender" in template.locked_fields and defaults.get("gender"):
            gender = defaults["gender"]

        name = self._random_name(rng, gender)
        if "name" in locked and base.get("name"):
            name = base["name"]

        data: dict = {
            "id": generate_id(),
            "name": name,
            "gender": gender,
            "age": self._random_age(rng, defaults),
            "birthday": None,
            "zodiac": None,
            "appearance": {
                "height_cm": self._random_height(rng),
                "weight_kg": None,
                "body_type": self._random_body_type(rng, defaults),
                "hair": self._random_hair(rng, gender),
                "eyes": None, "skin": None, "distinguishing": None,
                "style": None, "avatar_url": None,
            },
            "background": {
                "birthplace": None,
                "education": None,
                "occupation": self._random_occupation(rng, template),
                "living_situation": self._random_from_pool(
                    rng, (defaults.get("background_hints") or {})
                    .get("living_pool")),
                "family": None,
                "key_experiences": self._random_experiences(rng, template),
                "current_state": None,
            },
            "personality": {
                "big_five": self._random_big_five(rng, defaults, locked),
                "traits": list((defaults.get("personality") or {}).get("traits") or []),
                "flaws": list((defaults.get("personality") or {}).get("flaws") or []),
                "values": [], "fears": [],
            },
            "speech_style": self._random_speech_style(rng, defaults),
            "theme": dict(template.theme),
            "relationship_defaults": {
                "initial_stage": "S1",
                "initial_affection": rng.randint(15, 30),
                "initial_trust": rng.randint(20, 35),
                "initial_attachment": rng.randint(10, 20),
                "first_meeting_context": None,
            },
            "emotion_overrides": self._random_emotion_overrides(rng, defaults),
            "proactive_enabled": True,
            "visibility": {"is_public": False, "owner_user_id": None},
        }

        # 锁定字段回填基础值（用户锁定的字段保持当前值）
        self._apply_locked_values(data, locked, base)
        # 稀有变体（文档 1 第 5.6 节）
        self._apply_rare_variants(rng, data, template)
        return data

    def _apply_locked_values(self, data: dict, locked: set[str],
                             base: dict) -> None:
        """用户锁定字段用 base 值覆盖随机结果（base 没有则保持随机值）。"""
        for field in locked:
            if field not in base:
                continue
            if field in ("age", "gender", "name", "body_type"):
                data[field] = base[field]
            elif field == "height":
                data["appearance"]["height_cm"] = base["height"]
            elif field == "hair":
                data["appearance"]["hair"] = base["hair"]
            elif field == "occupation":
                data["background"]["occupation"] = base["occupation"]
            elif field == "living_situation":
                data["background"]["living_situation"] = base["living_situation"]
            elif field == "key_experiences":
                data["background"]["key_experiences"] = list(
                    base["key_experiences"])
            elif field == "tone":
                data["speech_style"]["tone"] = base["tone"]
            elif field == "pace":
                data["speech_style"]["pace"] = base["pace"]
            elif field == "catchphrases":
                data["speech_style"]["catchphrases"] = list(base["catchphrases"])

    # ── 单字段随机 ──────────────────────────────────────────

    def randomize_single_field(self, template: Template, field: str,
                               current_profile: dict):
        """随机单个字段，返回新值（文档 2 第 3.5 节；不触发完整生成流程）。

        输入：模板、字段名、当前档案 dict；输出：新值；
        副作用：无。字段不在 RANDOMIZABLE_FIELDS 中抛 ValueError。
        """
        if field not in RANDOMIZABLE_FIELDS:
            raise ValueError(f"不支持随机的字段: {field}")
        rng = random.Random()
        gender = current_profile.get("gender") or \
            (template.defaults or {}).get("gender") or "female"
        if field == "name":
            return self._random_name(rng, gender)
        if field == "gender":
            return self._random_gender(rng)
        if field == "age":
            return self._random_age(rng, template.defaults or {})
        if field == "body_type":
            # 单字段随机必须真的变：忽略模板固定体型（完整生成时才沿模板值，
            # 否则模板角色点 🎲 永远返回同一个值——2026-10-18 实测）
            return self._random_body_type(rng, {})
        if field == "height":
            return self._random_height(rng)
        if field == "hair":
            return self._random_hair(rng, gender)
        if field == "occupation":
            return self._random_occupation(rng, template)
        if field == "living_situation":
            return self._random_from_pool(
                rng, ((template.defaults or {}).get("background_hints") or {})
                .get("living_pool"))
        if field == "key_experiences":
            return self._random_experiences(rng, template)
        if field == "big_five":
            return self._random_big_five(rng, template.defaults or {}, set())
        if field == "tone":
            return self._random_speech_style(rng, template.defaults or {})["tone"]
        if field == "pace":
            return self._random_speech_style(rng, template.defaults or {})["pace"]
        if field == "catchphrases":
            return self._random_speech_style(
                rng, template.defaults or {})["catchphrases"]
        if field == "initial_affection":
            return rng.randint(15, 30)
        raise ValueError(f"未实现的字段随机: {field}")  # 防御，理论不可达

    # ── 字段随机实现（文档 1 第 5.2 节逐条） ─────────────────

    def _random_gender(self, rng: random.Random) -> str:
        """性别加权随机：male 40% / female 40% / non_binary 20%。"""
        return self._weighted_choice(rng, self.gender_weights)

    def _random_name(self, rng: random.Random, gender: str) -> str:
        """从按性别分池的姓名池抽取。"""
        pool = load_name_pool(gender)
        if not pool:
            return "无名"
        return rng.choice(pool)

    def _random_age(self, rng: random.Random, defaults: dict) -> int:
        """年龄范围随机（配置 age_range，模板 age_range 优先）。"""
        low, high = self.age_range
        template_range = (defaults or {}).get("age_range")
        if template_range and len(template_range) == 2:
            low, high = int(template_range[0]), int(template_range[1])
        return rng.randint(low, high)

    def _random_body_type(self, rng: random.Random, defaults: dict) -> str:
        """体型加权随机（配置 body_type_weights；模板默认值优先）。"""
        template_body = (defaults or {}).get("body_type")
        if template_body:
            return template_body
        return self._weighted_choice(rng, self.body_type_weights)

    def _random_height(self, rng: random.Random) -> int:
        """身高正态分布（均值 165、标准差 8），钳制到 [140, 200]。"""
        value = int(round(rng.gauss(self.height_mean, self.height_std)))
        return max(140, min(200, value))

    def _random_hair(self, rng: random.Random, gender: str) -> str:
        """从按性别分池的发型池抽取。"""
        pool = (load_pool("hairstyles").get(gender)
                or load_pool("hairstyles").get("neutral") or [])
        return rng.choice(pool) if pool else None

    def _random_occupation(self, rng: random.Random, template: Template) -> str:
        """从模板职业池抽取；模板没有时按标签分类兜底。"""
        hints = (template.defaults or {}).get("background_hints") or {}
        pool = [str(item) for item in hints.get("occupation_pool") or []]
        if not pool:
            pool = self._occupations_by_tags(template)
        return rng.choice(pool) if pool else None

    def _occupations_by_tags(self, template: Template) -> list[str]:
        """按模板标签从全局职业池收集候选（标签匹配不到时用全部）。"""
        data = load_pool("occupations")
        tags = set(template.tags or [])
        matched: list[str] = []
        for category, entries in data.items():
            names = [entry["name"] if isinstance(entry, dict) else str(entry)
                     for entry in entries or []]
            if tags & {category}:
                matched.extend(names)
        if not matched:
            for entries in data.values():
                matched.extend(entry["name"] if isinstance(entry, dict)
                               else str(entry) for entry in entries or [])
        return matched

    def _random_from_pool(self, rng: random.Random, pool) -> str | None:
        """从池中抽一项（空池返回 None）。"""
        items = [str(item) for item in pool or []]
        return rng.choice(items) if items else None

    def _random_experiences(self, rng: random.Random,
                            template: Template) -> list[str]:
        """背景经历抽 2-3 条（模板专属经历池优先，全局池兜底）。"""
        hints = (template.defaults or {}).get("background_hints") or {}
        pool = [str(item) for item in hints.get("experience_pool") or []]
        if not pool:
            pool = [str(item) for item in
                    (load_pool("experiences").get("general") or [])]
        if not pool:
            return []
        count = rng.randint(2, min(3, len(pool)))
        return rng.sample(pool, count)

    def _random_big_five(self, rng: random.Random, defaults: dict,
                         locked: set[str]) -> dict[str, float]:
        """Big Five 每维 0.2~0.9 范围随机；模板锁定维度用模板值。"""
        template_five = ((defaults or {}).get("personality") or {}).get(
            "big_five") or {}
        result: dict[str, float] = {}
        for dim in ("openness", "conscientiousness", "extraversion",
                    "agreeableness", "neuroticism"):
            path = f"personality.big_five.{dim}"
            if path in locked and dim in template_five:
                result[dim] = float(template_five[dim])
            else:
                result[dim] = round(rng.uniform(0.2, 0.9), 2)
        return result

    def _random_speech_style(self, rng: random.Random,
                             defaults: dict) -> dict:
        """语气/节奏/口头禅分别抽取（模板值优先，其次全局池）。"""
        template_style = (defaults or {}).get("speech_style") or {}
        pools = load_pool("catchphrases")
        style = {
            "tone": template_style.get("tone") or rng.choice(
                pools.get("tones") or ["温和、口语化"]),
            "pace": template_style.get("pace") or rng.choice(
                pools.get("paces") or ["不急不缓"]),
            "vocabulary": template_style.get("vocabulary"),
            "catchphrases": list(template_style.get("catchphrases") or []) or
            list(rng.choice(pools.get("catchphrase_sets") or [[]])),
            "avoid": [],
            "emoji_usage": template_style.get("emoji_usage") or rng.choice(
                pools.get("emoji_usages") or ["偶尔使用"]),
            "punctuation": template_style.get("punctuation"),
        }
        return style

    def _random_emotion_overrides(self, rng: random.Random,
                                  defaults: dict) -> dict:
        """情感参数：初始值 ±0.1 浮动、衰减 ±20% 浮动（文档 1 第 5.2 节）。"""
        template_overrides = (defaults or {}).get("emotion_overrides") or {}
        initial_internal = {
            dim: self._jitter(rng, value, 0.1)
            for dim, value in (template_overrides.get("initial_internal")
                               or {}).items()}
        decay = dict(template_overrides.get("decay_modifiers") or {})
        if "expression_half_life_hours" in decay:
            decay["expression_half_life_hours"] = self._jitter(
                rng, decay["expression_half_life_hours"], 0.20)
        return {
            "initial_expression": {},
            "initial_internal": initial_internal,
            "decay_modifiers": decay,
            "internal_to_expression_weights": dict(
                template_overrides.get("internal_to_expression_weights") or {}),
            "event_sensitivity": dict(
                template_overrides.get("event_sensitivity") or {}),
            "proactive": dict(template_overrides.get("proactive") or {}),
        }

    @staticmethod
    def _jitter(rng: random.Random, value: float, spread: float) -> float:
        """数值 ±spread 比例浮动（保留两位小数）。"""
        return round(value * (1.0 + rng.uniform(-spread, spread)), 2)

    @staticmethod
    def _weighted_choice(rng: random.Random, weights: dict[str, float]) -> str:
        """按权重随机抽取一个键（权重自动按总和归一化）。"""
        items = list(weights.items())
        total = sum(weight for _, weight in items)
        if total <= 0:
            return items[0][0]
        roll = rng.random() * total
        cumulative = 0.0
        for key, weight in items:
            cumulative += weight
            if roll <= cumulative:
                return key
        return items[-1][0]

    # ── 稀有变体（文档 1 第 5.6 节） ────────────────────────

    def _apply_rare_variants(self, rng: random.Random, data: dict,
                             template: Template) -> None:
        """按概率追加稀有特质 / 隐藏背景 / 情感彩蛋，命中置 is_rare。"""
        rare = False
        if rng.random() < self.rare_trait_probability:
            trait = self._random_from_pool(rng, load_pool(
                "rare_traits").get("traits"))
            if trait and trait not in data["personality"]["traits"]:
                data["personality"]["traits"].append(trait)
                rare = True
        if rng.random() < self.hidden_background_probability:
            hidden = load_pool("experiences").get("hidden") or []
            template_pool = {str(item) for item in
                             ((template.defaults or {}).get("background_hints")
                              or {}).get("experience_pool") or []}
            candidates = [item for item in hidden if item not in template_pool]
            if candidates:
                data["background"]["key_experiences"].append(
                    rng.choice(candidates))
                rare = True
        if rng.random() < self.emotion_egg_probability:
            dim = rng.choice(EXPRESSION_DIMS)
            data["emotion_overrides"]["initial_expression"][dim] = \
                round(rng.uniform(0.05, 0.15), 2)
            rare = True
        if rare:
            data["is_rare"] = True

    # ── 一致性重试（文档 1 第 5.3 节） ──────────────────────

    def _retry_conflicts(self, template: Template, rng: random.Random,
                         locked: set[str], base: dict,
                         profile_data: dict) -> tuple[dict, set[str]]:
        """校验失败只重试冲突字段，最多 max_retry 次。

        返回：(最终档案 dict, 仍未解决的冲突字段集合)；
        副作用：无。未解决的字段由调用方降级为模板默认值。
        """
        unresolved: set[str] = set()
        for _ in range(self.max_retry):
            profile = CharacterProfile.from_dict(profile_data)
            issues = validate_consistency(profile)
            if not issues:
                return profile_data, unresolved
            fields = {issue.field for issue in issues}
            unresolved = fields
            if not self._rerandomize_fields(profile_data, fields, rng,
                                            template, locked, base):
                break  # 没有可重试的字段（如规则本身无随机解）
        return profile_data, unresolved

    def _rerandomize_fields(self, data: dict, fields: set[str],
                            rng: random.Random, template: Template,
                            locked: set[str], base: dict) -> bool:
        """重随机指定字段；返回是否有字段被真正重随机。"""
        rerandomized = False
        for field in fields:
            if field in locked:
                continue  # 锁定字段不参与重试
            if field == "age":
                data["age"] = self._random_age(rng, template.defaults or {})
                rerandomized = True
            elif field == "appearance.body_type":
                data["appearance"]["body_type"] = self._random_body_type(
                    rng, template.defaults or {})
                rerandomized = True
            elif field == "speech_style.tone":
                data["speech_style"]["tone"] = rng.choice(
                    load_pool("catchphrases").get("tones") or ["温和、口语化"])
                rerandomized = True
            elif field.startswith("emotion_overrides."):
                # 高神经质 vs 低冲突敏感度：直接去掉该覆盖项（回退引擎默认）
                data["emotion_overrides"]["event_sensitivity"].pop(
                    "conflict", None)
                rerandomized = True
        return rerandomized

    def _degrade_to_template_defaults(self, data: dict, fields: set[str],
                                      template: Template,
                                      user_locked: set[str] | None = None
                                      ) -> None:
        """冲突字段降级为模板默认值（文档 1 第 5.3 节末句）。

        输入：档案 dict、冲突字段集合、模板、用户锁定字段集合；
        副作用：原地修改 data。用户锁定的字段跳过（锁定优先于降级）。
        """
        defaults = template.defaults or {}
        user_locked = user_locked or set()
        for field in fields:
            if field in user_locked:
                continue
            if field == "age":
                age_range = defaults.get("age_range") or list(self.age_range)
                data["age"] = (int(age_range[0]) + int(age_range[1])) // 2
            elif field == "appearance.body_type":
                data["appearance"]["body_type"] = defaults.get(
                    "body_type", "slim")
            elif field == "speech_style.tone":
                data["speech_style"]["tone"] = (
                    (defaults.get("speech_style") or {}).get("tone")
                    or "温和、口语化")
            elif field.startswith("emotion_overrides."):
                data["emotion_overrides"]["event_sensitivity"].pop(
                    "conflict", None)


class PreviewGenerator:
    """职责：为角色创建向导提供不持久化的随机预览（文档 2 第 3.3 节）。

    与 CharacterGenerator 的区别：不写入数据库，只返回预览结果。
    """

    def __init__(self, generator: CharacterGenerator):
        """输入：完整生成器（复用其模板加载与字段随机能力）；副作用：无。"""
        self.generator = generator

    def randomize_field(self, template_id: str, field: str,
                        current_profile: dict) -> dict:
        """随机单个字段，返回 {field, value}（前端 🎲 单字段按钮）。"""
        template = self.generator.template_loader.load(template_id)
        value = self.generator.randomize_single_field(
            template, field, current_profile)
        return {"field": field, "value": value}

    def randomize_all(self, template_id: str, locked_fields: list,
                      current_profile: dict) -> dict:
        """随机全部未锁定字段，返回 {profile}（前端"随机全部"按钮）。

        current_profile 作为基础档案：锁定字段保持其当前值
        （文档 2 第 3.3 节示例未使用该参数，但端点契约带它，
        且向导场景必须保留用户已填名字——见交付报告说明）。
        """
        template = self.generator.template_loader.load(template_id)
        profile = self.generator.generate(
            template=template,
            locked_fields=locked_fields,
            base_profile=current_profile,
        )
        return {"profile": profile.to_dict()}

    def validate(self, profile_data: dict) -> dict:
        """校验当前档案的一致性，返回 {valid, issues}（前端实时校验）。

        输入是向导里的**部分档案**：`id` 要等创建时才分配，字段可能缺。
        因此先用缺省值补齐再解析；结构本身不可解析时按 issue 返回，
        不抛异常——前端每敲一个键都调它，500 会刷屏（2026-10-17 实测）。
        """
        try:
            data = dict(profile_data or {})
            data.setdefault("id", "")
            data.setdefault("name", "")
            profile = CharacterProfile.from_dict(data)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            # 结构坏掉（字段类型不对/缺节）一律转 issue：
            # 这是面向用户输入的实时校验端点，不许 500
            return {"valid": False,
                    "issues": [{"field": "profile",
                                "message": f"档案结构无法解析: {exc}"}]}
        issues = validate_consistency(profile)
        return {"valid": not issues,
                "issues": [issue.to_dict() for issue in issues]}
