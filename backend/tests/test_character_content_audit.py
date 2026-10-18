"""Phase 7 开工前置：模板/池内容抽查（用户裁决⑪要求）。

机械断言，不依赖主观判断：
1. 8 个模板的三色与文档 1 第 3.3 节对照表逐字一致
2. 每个模板的 Big Five 主导维度符合其核心特质（第 4.3 节）
3. 姓名池无重复、数量达标
4. 职业池 min_age 在合理区间
"""

from pathlib import Path

import pytest
import yaml

from core.character.template_loader import (
    CHARACTER_DIR,
    load_name_pool,
    load_pool,
)

# 文档 1 第 3.3 节「模板默认主题色对照表」逐字抄录
DOC_THEME_TABLE = {
    "gentle_artist": ("#C4A882", "#E8DDD0", "#8B7355"),
    "energetic_coach": ("#5B7C99", "#C8D8E8", "#3D5A73"),
    "tsundere_childhood": ("#C99BB5", "#E8D5E0", "#9B6B8A"),
    "intellectual_scholar": ("#7A9B8A", "#D0E0D8", "#5A7A6A"),
    "gentle_senior": ("#A89888", "#DDD5CC", "#7A6B5A"),
    "playful_trickster": ("#D4A055", "#F0DDB8", "#A87A35"),
    "stoic_guardian": ("#6B7A8A", "#C8D0D8", "#4A5A6A"),
    "dreamy_poet": ("#A898C4", "#DDD5E8", "#7A6B9B"),
}

# 模板核心特质 → Big Five 主导维度要求（文档 1 第 4.3 节清单）
DOC_TRAIT_REQUIREMENTS = {
    "gentle_artist": lambda b: b["openness"] >= 0.75,          # 创造力
    "energetic_coach": lambda b: b["extraversion"] >= 0.75,   # 外向直接
    "tsundere_childhood": lambda b: b["neuroticism"] >= 0.45,  # 嘴硬心软
    "intellectual_scholar": lambda b: b["openness"] >= 0.85,  # 理性博学
    "gentle_senior": lambda b: b["agreeableness"] >= 0.85,    # 成熟包容
    "playful_trickster": lambda b: b["extraversion"] >= 0.85,  # 幽默跳脱
    "stoic_guardian": lambda b: b["extraversion"] <= 0.30,    # 寡言
    "dreamy_poet": lambda b: b["openness"] >= 0.90,           # 浪漫感性
}


def _load_all_templates() -> dict:
    templates = {}
    for path in sorted((CHARACTER_DIR / "templates").glob("*.yaml")):
        template = yaml.safe_load(path.read_text(encoding="utf-8"))["template"]
        templates[template["id"]] = template
    return templates


class TestThemeMatchesDocTable:
    @pytest.mark.parametrize("template_id", sorted(DOC_THEME_TABLE))
    def test_three_colors_verbatim(self, template_id):
        theme = _load_all_templates()[template_id]["theme"]
        actual = (theme["primary"], theme["soft"], theme["deep"])
        assert actual == DOC_THEME_TABLE[template_id]

    def test_no_extra_templates(self):
        assert set(_load_all_templates()) == set(DOC_THEME_TABLE)


class TestBigFiveMatchesTraits:
    @pytest.mark.parametrize("template_id", sorted(DOC_TRAIT_REQUIREMENTS))
    def test_dominant_dimension(self, template_id):
        big_five = _load_all_templates()[template_id]["defaults"][
            "personality"]["big_five"]
        assert DOC_TRAIT_REQUIREMENTS[template_id](big_five), \
            f"{template_id} 的 Big Five {big_five} 不符合核心特质"

    @pytest.mark.parametrize("template_id", sorted(DOC_TRAIT_REQUIREMENTS))
    def test_all_dimensions_in_range(self, template_id):
        big_five = _load_all_templates()[template_id]["defaults"][
            "personality"]["big_five"]
        for dim, value in big_five.items():
            assert 0.0 <= value <= 1.0, f"{template_id}.{dim}={value} 越界"


class TestNamePoolsClean:
    @pytest.mark.parametrize("gender", ["male", "female", "non_binary"])
    def test_no_duplicates(self, gender):
        names = load_name_pool(gender)
        assert len(names) == len(set(names)), f"{gender} 池有重复名字"

    @pytest.mark.parametrize("gender", ["male", "female", "non_binary"])
    def test_minimum_size(self, gender):
        """文档 1 第 5.2 节：每池 50+。"""
        assert len(load_name_pool(gender)) >= 50

    def test_no_cross_gender_leakage(self):
        """三池互不相交（中性名不与男/女池重复）。"""
        male = set(load_name_pool("male"))
        female = set(load_name_pool("female"))
        neutral = set(load_name_pool("non_binary"))
        assert not (male & female), f"男女池重复: {male & female}"
        assert not (male & neutral), f"男/中性池重复: {male & neutral}"
        assert not (female & neutral), f"女/中性池重复: {female & neutral}"


class TestOccupationRules:
    def test_min_age_within_sane_range(self):
        data = load_pool("occupations")
        for entries in data.values():
            for entry in entries or []:
                if isinstance(entry, dict):
                    assert 18 <= entry["min_age"] <= 60, \
                        f"{entry['name']} 的 min_age={entry['min_age']} 越界"

    def test_senior_occupations_have_min_age(self):
        """文档 1 第 5.3 节示例：大学教授是资深职业，必须有年龄门槛。"""
        data = load_pool("occupations")
        senior = [entry for entries in data.values() for entry in entries
                  if isinstance(entry, dict)]
        names = {entry["name"] for entry in senior}
        assert "大学讲师" in names
        assert "人力资源总监" in names
        assert "儿科医生" in names

    def test_junior_occupations_are_plain_strings(self):
        """无年龄要求的职业是纯字符串（不强制 dict）。"""
        data = load_pool("occupations")
        assert "咖啡师" in [entry for entries in data.values()
                            for entry in entries]
