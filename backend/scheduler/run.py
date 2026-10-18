"""调度进程入口（文档 2 第 10.2 节：python -m scheduler.run）。

生产形态：Web 进程以 DUDULU_SCHEDULER_ENABLED=false 运行（多 worker 也
不会重复跑调度），本进程全局只跑一份调度器。运行：python -m scheduler.run
"""

from __future__ import annotations

import asyncio
import logging

from config.settings import get_settings
from db.sqlite import get_session_factory
from memory.memory_manager import get_memory_manager
from scheduler.jobs import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    """启动调度器并常驻（Ctrl-C 退出）。"""
    settings = get_settings()
    factory = get_session_factory()
    memory_manager = get_memory_manager(settings.YANTRIK_PATH,
                                        db_session_factory=factory)
    scheduler = start_scheduler(factory, memory_manager, settings)
    if scheduler is None:
        logger.error("SCHEDULER_ENABLED=false，调度进程无任务可跑，退出")
        return
    logger.info("调度进程已就绪，等待任务触发……")
    try:
        await asyncio.Event().wait()  # 常驻直到被信号中断
    finally:
        stop_scheduler()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("调度进程收到中断信号，退出")
