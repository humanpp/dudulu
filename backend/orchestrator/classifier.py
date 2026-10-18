"""事件分类器（文档 2 第 3.8 节第 5 步）：用一次 LLM 调用将用户消息分类为情感标签。

输出契约（裁决②）：分类器只产出 label / intensity / busy_signal，
其中 intensity = base_intensity × deviation_factor（分类器不知道关系阶段，
也不知道角色档案；relationship_modifier 与 character_sensitivity 由引擎叠加）。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from core.emotion_engine import EVENT_VALENCE, EmotionEvent
from core.internal_layer import EVENT_LABELS
from llm.base import LLMClient

logger = logging.getLogger(__name__)

# 解析失败时的回退值：neutral 基准强度 × 1.0（文档未定义失败行为，fail-soft 兜底）
FALLBACK_LABEL = "neutral"
FALLBACK_INTENSITY = 0.1


class Classifier:
    """事件分类器：一次 LLM 调用 → EmotionEvent。"""

    def __init__(self, llm_client: LLMClient,
                 prompt_path: str = "config/prompts/classifier.txt"):
        """初始化分类器。

        输入：llm_client（统一接口）、prompt_path（提示词模板路径）；
        副作用：读取提示词模板文件。
        """
        self.llm_client = llm_client
        self.prompt_template = Path(prompt_path).read_text(encoding="utf-8")

    async def classify(self, user_message: str, state: dict,
                       character_profile=None) -> EmotionEvent:
        """把用户消息分类为情感事件。

        输入：
            user_message: 用户最新消息
            state: 当前完整状态（engine.get_full_state()；分类本身不消费状态，
                入参保留是为对齐文档 2 第 3.8 节的调用签名）
            character_profile: 角色档案（同上，当前不消费）
        输出：EmotionEvent（label / intensity / busy_signal）；
        副作用：一次上游 LLM 调用。
        """
        prompt = self.prompt_template.replace("__MESSAGE__", user_message)
        raw = await self.llm_client.generate(prompt)
        return self._parse(raw)

    def _parse(self, raw: str) -> EmotionEvent:
        """从模型回复中解析 JSON 事件。

        成功：返回解析出的 EmotionEvent（valence 按标签映射，裁决⑤临时表）；
        失败（无 JSON / 格式错 / 标签不在九类之内）：记 warning 日志并回退
        neutral。文档未定义失败行为，此处选择 fail-soft 保证管道不中断
        （已在交付报告中报告）。
        """
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                label = str(data.get("label", "")).strip()
                if label in EVENT_LABELS:
                    return EmotionEvent(
                        label=label,
                        intensity=float(data["intensity"]),
                        busy_signal=bool(data.get("busy_signal", False)),
                        valence=EVENT_VALENCE[label],
                    )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass
        logger.warning("事件分类解析失败，回退 %s：%r", FALLBACK_LABEL, raw[:100])
        return EmotionEvent(label=FALLBACK_LABEL, intensity=FALLBACK_INTENSITY,
                            valence=EVENT_VALENCE[FALLBACK_LABEL])
