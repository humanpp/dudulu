"""模板加载器单元测试（文档 2 第 3.4 节 + 文档 1 第 4.2/4.3 节）。"""

import pytest

from core.character.template_loader import (
    TemplateLoader,
    deep_merge,
    load_name_pool,
    load_occupation_rules,
    load_pool,
)

BUILTIN_IDS = ["gentle_artist", "energetic_coach", "tsundere_childhood",
               "intellectual_scholar", "gentle_senior", "playful_trickster",
               "stoic_guardian", "dreamy_poet"]


@pytest.fixture
def loader():
    return TemplateLoader()


class TestDeepMerge:
    def test_dict_recursive(self):
        merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}})
        assert merged == {"a": {"x": 1, "y": 3}}

    def test_lists_replaced_not_concatenated(self):
        merged = deep_merge({"tags": ["a", "b"]}, {"tags": ["c"]})
        assert merged == {"tags": ["c"]}


class TestLoad:
    def test_all_builtin_templates_load(self, loader):
        """文档 1 第 4.3 节 8 个内置模板全部可加载。"""
        for template_id in BUILTIN_IDS:
            template = loader.load(template_id)
            assert template.id == template_id
            assert template.name
            assert template.theme.get("primary", "").startswith("#")
            assert template.locked_fields

    def test_gentle_artist_matches_doc_example(self, loader):
        """文档 1 第 4.2 节示例的关键值。"""
        template = loader.load("gentle_artist")
        assert template.icon == "🌿"
        assert template.defaults["gender"] == "female"
        assert template.defaults["age_range"] == [22, 28]
        assert (template.defaults["personality"]["big_five"]["openness"]
                == 0.82)
        assert (template.defaults["emotion_overrides"]["event_sensitivity"]
                ["conflict"] == 1.3)
        assert "theme.primary" in template.locked_fields

    def test_cache_returns_same_instance(self, loader):
        first = loader.load("gentle_artist")
        second = loader.load("gentle_artist")
        assert first is second

    def test_unknown_template_raises(self, loader):
        with pytest.raises(FileNotFoundError):
            loader.load("no_such_template")


class TestList:
    def test_builtin_order_follows_config(self, loader):
        metas = loader.list_builtin()
        assert [meta.id for meta in metas] == BUILTIN_IDS

    def test_builtin_meta_fields(self, loader):
        meta = loader.list_builtin()[0]
        assert meta.id == "gentle_artist"
        assert meta.name == "温柔艺术家"
        assert meta.icon == "🌿"
        assert meta.tags

    def test_community_empty_initially(self, loader):
        assert loader.list_community() == []


class TestInheritance:
    def test_extends_chain_resolves(self, loader, tmp_path):
        """extends 继承链：子模板覆盖差异字段，其余继承父模板。"""
        (tmp_path / "base_parent.yaml").write_text(
            "template:\n"
            "  id: base_parent\n"
            "  name: 父模板\n"
            "  theme: {primary: '#111111', soft: '#222222', deep: '#333333'}\n"
            "  defaults:\n"
            "    gender: female\n"
            "    personality: {traits: [温柔, 坚定]}\n",
            encoding="utf-8")
        (tmp_path / "child_tpl.yaml").write_text(
            "template:\n"
            "  id: child_tpl\n"
            "  extends: base_parent\n"
            "  name: 子模板\n"
            "  defaults:\n"
            "    gender: male\n",
            encoding="utf-8")
        child_loader = TemplateLoader(tmp_path)
        template = child_loader.load("child_tpl")
        assert template.name == "子模板"               # 子覆盖
        assert template.defaults["gender"] == "male"   # 子覆盖
        assert template.theme["primary"] == "#111111"  # 继承父
        assert template.defaults["personality"]["traits"] == ["温柔", "坚定"]


class TestNamePools:
    @pytest.mark.parametrize("gender", ["male", "female", "non_binary"])
    def test_pool_has_50_plus_names(self, gender):
        """文档 1 第 5.2 节：每池 50+ 名字。"""
        assert len(load_name_pool(gender)) >= 50

    def test_unknown_gender_falls_back_to_neutral(self):
        assert load_name_pool("unknown") == load_name_pool("non_binary")


class TestPools:
    def test_all_five_pools_load(self):
        for pool_name in ("occupations", "experiences", "hairstyles",
                          "catchphrases", "rare_traits"):
            assert load_pool(pool_name)

    def test_hairstyles_by_gender(self):
        pool = load_pool("hairstyles")
        for gender in ("male", "female", "neutral"):
            assert len(pool[gender]) >= 20  # 文档：每池 20+ 选项

    def test_occupation_rules_min_age(self):
        """资深职业带 min_age（一致性校验用）。"""
        rules = load_occupation_rules()
        assert rules["大学讲师"] >= 26
        assert rules["消防员"] is None if "消防员" in rules else True
