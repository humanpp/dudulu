"""主题色管理单元测试（文档 1 第 3.3 节 + 文档 3 第 3.1 节）。"""

import pytest

from core.character.profile import Theme
from core.character.theme import (
    hex_to_rgba,
    is_valid_hex,
    theme_css_variables,
    validate_theme,
)


class TestIsValidHex:
    def test_six_and_three_digits(self):
        assert is_valid_hex("#C4A882") is True
        assert is_valid_hex("#abc") is True
        assert is_valid_hex("#ABCDEF") is True

    def test_invalid_values(self):
        assert is_valid_hex("C4A882") is False   # 缺 #
        assert is_valid_hex("#GGGGGG") is False  # 非十六进制
        assert is_valid_hex("#12345") is False   # 位数不对
        assert is_valid_hex(None) is False
        assert is_valid_hex("") is False


class TestValidateTheme:
    def test_valid_passes(self):
        assert validate_theme({"primary": "#C4A882", "soft": "#E8DDD0",
                               "deep": "#8B7355"}) == []

    def test_missing_keys_are_not_issues(self):
        """缺省键走 Theme 默认值，不算问题。"""
        assert validate_theme({}) == []
        assert validate_theme(None) == []

    def test_invalid_hex_reported(self):
        issues = validate_theme({"primary": "red-ish"})
        assert len(issues) == 1
        assert "theme.primary" in issues[0]


class TestHexToRgba:
    def test_six_digits(self):
        assert hex_to_rgba("#C4A882") == "rgba(196, 168, 130, 0.08)"

    def test_three_digits_expanded(self):
        assert hex_to_rgba("#abc") == "rgba(170, 187, 204, 0.08)"

    def test_custom_alpha(self):
        assert hex_to_rgba("#000000", 0.5) == "rgba(0, 0, 0, 0.5)"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            hex_to_rgba("nope")


class TestCssVariables:
    def test_matches_frontend_contract(self):
        """变量名照文档 3 第 3.1 节：--accent-primary/soft/deep/ghost。"""
        variables = theme_css_variables(Theme(primary="#C4A882",
                                              soft="#E8DDD0",
                                              deep="#8B7355"))
        assert variables == {
            "--accent-primary": "#C4A882",
            "--accent-soft": "#E8DDD0",
            "--accent-deep": "#8B7355",
            "--accent-ghost": "rgba(196, 168, 130, 0.08)",
        }
