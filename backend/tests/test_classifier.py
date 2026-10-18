"""事件分类器单元测试（文档 2 第 3.8 节第 5 步 + 裁决②）。"""

import asyncio

from helpers import PROMPT_PATH, FakeLLM
from orchestrator.classifier import Classifier


def make_classifier(reply: str) -> tuple[Classifier, FakeLLM]:
    """构造分类器，其 LLM 的 generate 恒返回脚本化 reply。"""
    fake_llm = FakeLLM()

    async def scripted_generate(prompt: str, **kwargs) -> str:
        fake_llm.prompts.append(prompt)
        return reply

    fake_llm.generate = scripted_generate  # type: ignore[assignment]
    return Classifier(fake_llm, prompt_path=PROMPT_PATH), fake_llm


def classify(classifier: Classifier, message: str):
    return asyncio.run(classifier.classify(message, {"expression": {}}))


class TestClassify:
    def test_valid_json(self):
        classifier, _ = make_classifier(
            '{"label": "vulnerable", "intensity": 0.84, "busy_signal": false}')
        event = classify(classifier, "最近压力好大")
        assert event.label == "vulnerable"
        assert event.intensity == 0.84
        assert event.busy_signal is False

    def test_json_embedded_in_prose(self):
        classifier, _ = make_classifier(
            '好的，结果如下：{"label": "playful", "intensity": 0.4, '
            '"busy_signal": true} 以上。')
        event = classify(classifier, "哈哈笨蛋")
        assert event.label == "playful"
        assert event.busy_signal is True

    def test_busy_signal_defaults_false(self):
        classifier, _ = make_classifier('{"label": "neutral", "intensity": 0.1}')
        event = classify(classifier, "嗯")
        assert event.busy_signal is False

    def test_prompt_contains_user_message(self):
        classifier, fake_llm = make_classifier(
            '{"label": "neutral", "intensity": 0.1}')
        classify(classifier, "今天加班到十一点")
        assert "今天加班到十一点" in fake_llm.prompts[-1]

    def test_prompt_template_loaded_from_file(self):
        classifier, _ = make_classifier('{"label": "neutral", "intensity": 0.1}')
        assert "事件分类器" in classifier.prompt_template


class TestParseFallback:
    def test_unknown_label_falls_back_to_neutral(self):
        classifier, _ = make_classifier('{"label": "happy", "intensity": 0.9}')
        event = classify(classifier, "嗨")
        assert event.label == "neutral"
        assert event.intensity == 0.1

    def test_malformed_json_falls_back_to_neutral(self):
        classifier, _ = make_classifier("这不是 JSON")
        event = classify(classifier, "嗨")
        assert event.label == "neutral"

    def test_missing_intensity_falls_back(self):
        classifier, _ = make_classifier('{"label": "curious"}')
        event = classify(classifier, "你昨晚干嘛了")
        assert event.label == "neutral"

    def test_empty_reply_falls_back(self):
        classifier, _ = make_classifier("")
        event = classify(classifier, "")
        assert event.label == "neutral"
