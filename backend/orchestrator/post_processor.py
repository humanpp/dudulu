"""回复后处理（文档 2 第 3.8 节第 10 步）。

TODO: 文档未定义后处理规则（如长度截断、格式清理），当前为恒等实现，
    待文档补充规则后在此扩展。
"""

from __future__ import annotations


class PostProcessor:
    """回复后处理器：当前原样返回（恒等）。"""

    def process(self, reply: str) -> str:
        """后处理回复。

        输入：LLM 原始回复；输出：处理后的回复（当前不变）；
        副作用：无。
        """
        return reply
