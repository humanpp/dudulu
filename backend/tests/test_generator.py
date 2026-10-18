"""随机生成引擎单元测试（文档 2 第 3.3/3.5 节 + 文档 1 第五章）。"""

from pathlib import Path

import pytest

from core.character.generator import CharacterGenerator, PreviewGenerator
from core.character.profile import CharacterProfile
from core.character.template_loader import TemplateLoader
from core.character.validator import validate_consistency

TEMPLATE_ID = "gentle_artist"


@pytest.fixture
def generator():
    return CharacterGenerator(TemplateLoader())


class TestGenerate:
    def test_returns_valid_profile(self, generator):
        profile = generator.generate(generator.template_loader.load(TEMPLATE_ID),
                                     seed=42)
        assert isinstance(profile, CharacterProfile)
        assert profile.name
        assert profile.gender in ("male", "female", "non_binary")
        assert validate_consistency(profile) == []

    def test_seed_reproducibility(self, generator):
        """文档 1 第 5.4 节：相同种子 + 相同模板 = 相同角色。

        id 与 created_at 是存储/时间戳产物，不参与"角色相同"的比较。
        """
        template = generator.template_loader.load(TEMPLATE_ID)
        first = generator.generate(template, seed=2026)
        second = generator.generate(template, seed=2026)

        def content(profile):
            data = profile.to_dict()
            data.pop("id")
            data["generation_meta"].pop("created_at")
            return data

        assert content(first) == content(second)
        assert first.generation_meta.random_seed == 2026

    def test_different_seed_different_character(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        first = generator.generate(template, seed=1)
        second = generator.generate(template, seed=2)
        assert first.to_dict() != second.to_dict()

    def test_seed_recorded_in_meta(self, generator):
        profile = generator.generate(
            generator.template_loader.load(TEMPLATE_ID))
        assert profile.generation_meta.random_seed is not None
        assert profile.generation_meta.source == "random"
        assert profile.generation_meta.template_id == TEMPLATE_ID

    def test_field_ranges(self, generator):
        """文档 1 第 5.2 节：年龄 18~35、Big Five 0.2~0.9、AFF 15~30。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        for seed in range(30):
            profile = generator.generate(template, seed=seed)
            assert 18 <= profile.age <= 35
            assert 140 <= profile.appearance.height_cm <= 200
            for value in profile.personality.big_five.to_dict().values():
                assert 0.2 <= value <= 0.9
            assert 15 <= profile.relationship_defaults.initial_affection <= 30
            assert 20 <= profile.relationship_defaults.initial_trust <= 35
            assert 10 <= profile.relationship_defaults.initial_attachment <= 20

    def test_theme_never_randomized(self, generator):
        """主题色由模板决定，不随机（文档 1 第 5.2 节末行）。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        for seed in range(10):
            profile = generator.generate(template, seed=seed)
            assert profile.theme.primary == "#C4A882"
            assert profile.theme.soft == "#E8DDD0"
            assert profile.theme.deep == "#8B7355"

    def test_locked_fields_keep_base_values(self, generator):
        """用户锁定字段保持 current_profile 的值。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        profile = generator.generate(
            template, seed=7, locked_fields=["name", "gender"],
            base_profile={"name": "锁定名", "gender": "male"})
        assert profile.name == "锁定名"
        assert profile.gender == "male"

    def test_template_locked_big_five_dim_kept(self, generator):
        """模板锁定维度用模板值（gentle_artist 锁定 openness 0.82）。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        for seed in range(5):
            profile = generator.generate(template, seed=seed)
            assert profile.personality.big_five.openness == 0.82

    def test_experiences_count_2_to_3(self, generator):
        """背景经历抽 2-3 条（文档 1 第 5.2 节）；稀有变体可多一条。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        for seed in range(10):
            profile = generator.generate(template, seed=seed)
            count = len(profile.background.key_experiences)
            assert 2 <= count <= (4 if profile.is_rare else 3)

    def test_occupation_from_template_pool(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        pool = template.defaults["background_hints"]["occupation_pool"]
        for seed in range(10):
            profile = generator.generate(template, seed=seed)
            assert profile.background.occupation in pool

    def test_rare_variants_marked(self, generator):
        """稀有变体（5%/10%/3%）命中时 is_rare=True；统计多 seed 有命中。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        flags = {generator.generate(template, seed=seed).is_rare
                 for seed in range(60)}
        # 三种变体总概率约 17%，60 个 seed 里应至少出现一次
        assert True in flags

    def test_rare_variant_contents_when_present(self, generator):
        """is_rare 角色要么多了特质/经历，要么有情感彩蛋初始值。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        for seed in range(60):
            profile = generator.generate(template, seed=seed)
            if not profile.is_rare:
                continue
            extra_trait = (len(profile.personality.traits)
                           > len(template.defaults["personality"]["traits"]))
            extra_experience = (len(profile.background.key_experiences) > 2)
            egg = bool(profile.emotion_overrides.initial_expression)
            assert extra_trait or extra_experience or egg

    def test_all_builtin_templates_generate(self, generator):
        """8 个模板都能生成通过校验的角色。"""
        for template_id in ("gentle_artist", "energetic_coach",
                            "tsundere_childhood", "intellectual_scholar",
                            "gentle_senior", "playful_trickster",
                            "stoic_guardian", "dreamy_poet"):
            template = generator.template_loader.load(template_id)
            for seed in range(5):
                profile = generator.generate(template, seed=seed)
                assert validate_consistency(profile) == [], \
                    f"{template_id} seed={seed} 校验失败"


class TestRandomizeSingleField:
    def test_age_in_range(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        value = generator.randomize_single_field(template, "age", {})
        assert 22 <= value <= 28  # 模板 age_range

    def test_name_from_gender_pool(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        value = generator.randomize_single_field(
            template, "name", {"gender": "female"})
        from core.character.template_loader import load_name_pool

        assert value in load_name_pool("female")

    def test_theme_not_randomizable(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        with pytest.raises(ValueError):
            generator.randomize_single_field(template, "theme", {})

    def test_unknown_field_raises(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        with pytest.raises(ValueError):
            generator.randomize_single_field(template, "bogus", {})


class TestPreviewGenerator:
    def test_randomize_field_returns_field_and_value(self, generator):
        preview = PreviewGenerator(generator)
        result = preview.randomize_field(TEMPLATE_ID, "age", {})
        assert result["field"] == "age"
        assert isinstance(result["value"], int)

    def test_validate_accepts_partial_profile(self, generator):
        """向导实时校验：部分档案（无 id）不能 500，返回 valid/issues。

        2026-10-17 实测事故：前端预览档案没有 id（创建时才分配），
        from_dict 的 data["id"] 直接 KeyError → 500 刷屏。
        """
        preview = PreviewGenerator(generator)
        result = preview.validate({
            "name": "", "gender": "female", "age": 22,
            "appearance": {"body_type": "匀称"},
            "personality": {"big_five": {}},
            "visibility": {"is_public": False}})
        assert result["valid"] is False
        fields = {issue["field"] for issue in result["issues"]}
        assert "name" in fields  # 名字为空 → 拦住「下一步」

    def test_validate_accepts_empty_profile(self, generator):
        """连名字都没有的空档案也不崩。"""
        preview = PreviewGenerator(generator)
        result = preview.validate({})
        assert result["valid"] is False

    def test_validate_rejects_broken_structure(self, generator):
        """结构坏掉（如 personality 是字符串）转为 issue，不抛异常。"""
        preview = PreviewGenerator(generator)
        result = preview.validate({"id": "c", "name": "n",
                                   "personality": "not-a-dict"})
        assert "valid" in result and "issues" in result

    def test_randomize_all_keeps_locked_name(self, generator):
        """锁定字段保持当前值（向导保留用户已填名字）。"""
        preview = PreviewGenerator(generator)
        result = preview.randomize_all(
            TEMPLATE_ID, ["name", "gender"],
            {"name": "我起的名字", "gender": "male"})
        assert result["profile"]["name"] == "我起的名字"
        assert result["profile"]["gender"] == "male"
        assert result["profile"]["age"] is not None

    def test_randomize_all_does_not_persist(self, generator):
        """预览不落库：返回的档案带 id 但无数据库行（由路由层保证不写）。"""
        preview = PreviewGenerator(generator)
        result = preview.randomize_all(TEMPLATE_ID, [], {})
        assert result["profile"]["id"]

    def test_validate_valid_profile(self, generator):
        preview = PreviewGenerator(generator)
        profile = generator.generate(
            generator.template_loader.load(TEMPLATE_ID), seed=3)
        result = preview.validate(profile.to_dict())
        assert result["valid"] is True
        assert result["issues"] == []

    def test_validate_reports_issues(self, generator):
        preview = PreviewGenerator(generator)
        result = preview.validate({"id": "c", "name": "", "age": 19,
                                   "background": {"occupation": "大学讲师"}})
        assert result["valid"] is False
        fields = {issue["field"] for issue in result["issues"]}
        assert "name" in fields
        assert "age" in fields


class TestLockedFieldBranches:
    """_apply_locked_values 的各字段回填分支（用户锁定字段保持当前值）。"""

    def test_all_lockable_fields_keep_base(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        profile = generator.generate(
            template, seed=11,
            locked_fields=["height", "hair", "occupation",
                           "living_situation", "key_experiences",
                           "tone", "pace", "catchphrases"],
            base_profile={
                "height": 170, "hair": "锁定发型", "occupation": "锁定职业",
                "living_situation": "锁定居住", "key_experiences": ["锁定经历"],
                "tone": "锁定语气", "pace": "锁定节奏",
                "catchphrases": ["锁定口头禅"]})
        assert profile.appearance.height_cm == 170
        assert profile.appearance.hair == "锁定发型"
        assert profile.background.occupation == "锁定职业"
        assert profile.background.living_situation == "锁定居住"
        assert profile.background.key_experiences == ["锁定经历"]
        assert profile.speech_style.tone == "锁定语气"
        assert profile.speech_style.pace == "锁定节奏"
        assert profile.speech_style.catchphrases == ["锁定口头禅"]

    def test_template_locked_gender_kept(self, generator, tmp_path):
        """模板 defaults.gender 在 gender 被锁定时生效。"""
        (tmp_path / "gender_tpl.yaml").write_text(
            "template:\n"
            "  id: gender_tpl\n"
            "  name: 锁性别模板\n"
            "  locked_fields: [gender]\n"
            "  defaults:\n"
            "    gender: female\n",
            encoding="utf-8")
        loader = TemplateLoader(tmp_path)
        template = loader.load("gender_tpl")
        profile = generator.generate(template, seed=5)
        assert profile.gender == "female"


class TestPoolFallbacks:
    """模板未提供的字段走全局池回退。"""

    def _bare_loader(self, tmp_path):
        (tmp_path / "bare_tpl.yaml").write_text(
            "template:\n"
            "  id: bare_tpl\n"
            "  name: 空模板\n"
            "  theme: {primary: '#111111', soft: '#222222', deep: '#333333'}\n",
            encoding="utf-8")
        return TemplateLoader(tmp_path)

    def test_hair_from_global_pool(self, generator, tmp_path):
        loader = self._bare_loader(tmp_path)
        profile = generator.generate(loader.load("bare_tpl"), seed=3)
        from core.character.template_loader import load_pool

        assert profile.appearance.hair in (
            load_pool("hairstyles")["female"]
            + load_pool("hairstyles")["male"]
            + load_pool("hairstyles")["neutral"])

    def test_occupation_from_global_pool(self, generator, tmp_path):
        loader = self._bare_loader(tmp_path)
        profile = generator.generate(loader.load("bare_tpl"), seed=3)
        from core.character.template_loader import load_pool

        all_names = [entry["name"] if isinstance(entry, dict) else entry
                     for entries in load_pool("occupations").values()
                     for entry in entries]
        assert profile.background.occupation in all_names

    def test_experiences_from_global_pool(self, generator, tmp_path):
        loader = self._bare_loader(tmp_path)
        profile = generator.generate(loader.load("bare_tpl"), seed=3)
        from core.character.template_loader import load_pool

        allowed = (load_pool("experiences")["general"]
                   + load_pool("experiences")["hidden"])
        assert all(item in allowed
                   for item in profile.background.key_experiences)

    def test_speech_style_from_global_pool(self, generator, tmp_path):
        loader = self._bare_loader(tmp_path)
        profile = generator.generate(loader.load("bare_tpl"), seed=3)
        from core.character.template_loader import load_pool

        assert profile.speech_style.tone in load_pool("catchphrases")["tones"]
        assert profile.speech_style.catchphrases in (
            load_pool("catchphrases")["catchphrase_sets"])


class TestRareVariants:
    """稀有变体概率可调（配置驱动），全开/全关覆盖分支。"""

    def _generator_with(self, **probabilities):
        loader = TemplateLoader()
        generator = CharacterGenerator(loader)
        for key, value in probabilities.items():
            setattr(generator, key, value)
        return generator

    def test_all_variants_fire(self):
        generator = self._generator_with(
            rare_trait_probability=1.0,
            hidden_background_probability=1.0,
            emotion_egg_probability=1.0)
        template = generator.template_loader.load(TEMPLATE_ID)
        profile = generator.generate(template, seed=1)
        assert profile.is_rare is True
        # 稀有特质：多于模板 traits
        assert len(profile.personality.traits) > len(
            template.defaults["personality"]["traits"])
        # 情感彩蛋：有初始表达偏移
        assert profile.emotion_overrides.initial_expression

    def test_no_variants_when_probability_zero(self):
        generator = self._generator_with(
            rare_trait_probability=0.0,
            hidden_background_probability=0.0,
            emotion_egg_probability=0.0)
        template = generator.template_loader.load(TEMPLATE_ID)
        for seed in range(10):
            profile = generator.generate(template, seed=seed)
            assert profile.is_rare is False
            assert not profile.emotion_overrides.initial_expression


class TestConflictRetryAndDegrade:
    """一致性冲突：只重试冲突字段；仍失败降级模板默认值（文档 1 第 5.3 节）。"""

    def test_conflicting_field_rerandomized(self, generator, monkeypatch):
        """age 冲突 → age 被重随机（其余字段不参与重试）。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        calls: list[str] = []
        original = generator._random_age

        def spy_age(rng, defaults):
            calls.append("age")
            return original(rng, defaults)

        monkeypatch.setattr(generator, "_random_age", spy_age)
        # 前两次校验都报 age 冲突，第三次通过
        from core.character import generator as generator_module

        states = {"count": 0}
        original_validate = generator_module.validate_consistency

        def flaky_validate(profile):
            states["count"] += 1
            if states["count"] <= 2:
                from core.character.validator import ConsistencyIssue

                return [ConsistencyIssue("age", "假冲突")]
            return original_validate(profile)

        monkeypatch.setattr(generator_module, "validate_consistency",
                            flaky_validate)
        profile = generator.generate(template, seed=3)
        assert calls.count("age") >= 3  # 初次 + 两次重试
        assert original_validate(profile) == []

    def test_unresolvable_conflict_degrades_to_template_default(
            self, generator, monkeypatch):
        """始终冲突 → 降级为模板默认值（age 取 age_range 中点）。"""
        from core.character import generator as generator_module
        from core.character.validator import ConsistencyIssue

        template = generator.template_loader.load(TEMPLATE_ID)
        monkeypatch.setattr(
            generator_module, "validate_consistency",
            lambda profile: [ConsistencyIssue("age", "永远冲突")])
        profile = generator.generate(template, seed=3)
        # gentle_artist 的 age_range 是 [22, 28] → 中点 25
        assert profile.age == 25

    def test_locked_field_not_rerandomized(self, generator, monkeypatch):
        """锁定字段不参与重试（无可重试字段时直接退出循环）。"""
        from core.character import generator as generator_module
        from core.character.validator import ConsistencyIssue

        template = generator.template_loader.load(TEMPLATE_ID)
        monkeypatch.setattr(
            generator_module, "validate_consistency",
            lambda profile: [ConsistencyIssue("age", "永远冲突")])
        # age 被用户锁定 → 没有可重试字段 → 保留锁定值并降级
        profile = generator.generate(template, seed=3,
                                     locked_fields=["age"],
                                     base_profile={"age": 24})
        assert profile.age == 24

    def test_sensitivity_conflict_drops_override(self, generator, monkeypatch):
        """event_sensitivity 冲突 → 去掉该覆盖项（回退引擎默认）。"""
        from core.character import generator as generator_module
        from core.character.validator import ConsistencyIssue

        template = generator.template_loader.load(TEMPLATE_ID)
        monkeypatch.setattr(
            generator_module, "validate_consistency",
            lambda profile: [ConsistencyIssue(
                "emotion_overrides.event_sensitivity.conflict", "假冲突")])
        profile = generator.generate(template, seed=3)
        assert "conflict" not in profile.emotion_overrides.event_sensitivity


class TestConfigFallback:
    def test_missing_config_uses_defaults(self, tmp_path):
        """配置不可读 → 内置默认值（不崩）。"""
        generator = CharacterGenerator(TemplateLoader(),
                                       config_path=str(tmp_path / "no.yaml"))
        assert generator.max_retry == 10
        assert generator.age_range == (18, 35)
        profile = generator.generate(
            generator.template_loader.load(TEMPLATE_ID), seed=1)
        assert 18 <= profile.age <= 35


class TestRandomizeSingleFieldFull:
    """randomize_single_field 的全部分支。"""

    def test_all_supported_fields(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        current = {"gender": "female"}
        for field in ("gender", "body_type", "height", "hair", "occupation",
                      "living_situation", "key_experiences", "big_five",
                      "tone", "pace", "catchphrases", "initial_affection"):
            value = generator.randomize_single_field(template, field, current)
            assert value is not None, field

    def test_body_type_actually_varies(self, generator):
        """单字段随机体型必须真的变（2026-10-18 实测：模板默认值被复用，
        点 🎲 永远返回 slim，界面看着像"没反应"）。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        values = {generator.randomize_single_field(template, "body_type", {})
                  for _ in range(30)}
        assert len(values) > 1, f"体型随机恒为 {values}"
        assert values <= {"slim", "athletic", "curvy", "plump"}

    def test_name_follows_gender(self, generator):
        """名字随机按性别取池（女池/男池不混）。"""
        template = generator.template_loader.load(TEMPLATE_ID)
        from core.character.template_loader import load_name_pool

        female_pool = set(load_name_pool("female"))
        male_pool = set(load_name_pool("male"))
        for _ in range(10):
            female_name = generator.randomize_single_field(
                template, "name", {"gender": "female"})
            male_name = generator.randomize_single_field(
                template, "name", {"gender": "male"})
            assert female_name in female_pool
            assert male_name in male_pool

    def test_random_field_paths_documented(self):
        """字段清单与前端 FIELD_PATHS 一一对应（防两侧漂移）。"""
        from core.character.generator import RANDOMIZABLE_FIELDS

        frontend = (Path(__file__).resolve().parents[2] / "frontend" / "js"
                    / "components" / "character-create.js")
        text = frontend.read_text(encoding="utf-8")
        missing = [field for field in RANDOMIZABLE_FIELDS
                   if f"{field}:" not in text]
        assert not missing, f"前端 FIELD_PATHS 缺字段: {missing}"

    def test_big_five_returns_five_dims(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        value = generator.randomize_single_field(template, "big_five", {})
        assert set(value) == {"openness", "conscientiousness", "extraversion",
                              "agreeableness", "neuroticism"}

    def test_key_experiences_returns_list(self, generator):
        template = generator.template_loader.load(TEMPLATE_ID)
        value = generator.randomize_single_field(template, "key_experiences",
                                                 {})
        assert isinstance(value, list)
        assert 2 <= len(value) <= 3
