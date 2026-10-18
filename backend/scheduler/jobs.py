"""定时任务定义（文档 2 第 10.2 节）。

APScheduler 任务：
- sediment_job：周期性遍历近期有互动的 (character_id, user_id) 对，
  调用 Sedimenter.maybe_sediment（会话/日/周沉淀各自判重，幂等）

进程模型（文档 2 第 10.2 节）：
- 开发环境（uvicorn --reload 单进程）：main.py 的 lifespan 里直接启动
- 生产环境：Web 进程 DUDULU_SCHEDULER_ENABLED=false 关掉本模块，
  由独立进程 `python -m scheduler.run` 全局只跑一份
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from config.settings import Settings, get_settings
from db.models import CharacterUserState
from memory.memory_manager import MemoryManager, get_memory_manager
from memory.sedimenter import Sedimenter

logger = logging.getLogger(__name__)

# 沉淀任务执行间隔（分钟）。文档未定义，取 15 分钟（待调优）：
# 日沉淀按天判重、会话沉淀按空闲判重，间隔只影响触发延迟。
SEDIMENT_INTERVAL_MINUTES = 15

# 主动行为心跳间隔（分钟）。文档未定义"心跳"本身多快——proactive_interval
# （默认 4.5h）是每对的审视间隔，心跳只决定"多久扫一遍全量"，
# 实际是否发送由引擎内的间隔/上限门控决定。取 15 分钟（待调优）。
# 主动行为心跳的默认间隔（分钟）。文档未定义"心跳本身多快"
# （`proactive_interval` 是每对审视间隔，心跳只决定扫描全量的频率），
# 默认 15 分钟（第十六章待调优；可用 Settings.PROACTIVE_TICK_MINUTES
# 即 DUDULU_PROACTIVE_TICK_MINUTES 环境变量覆盖，E2E 会缩短它）。
PROACTIVE_TICK_MINUTES = 15

# 只对近期有互动的双键跑沉淀（天）。陈旧状态跑沉淀无意义。
ACTIVE_WINDOW_DAYS = 7

_scheduler: AsyncIOScheduler | None = None


def build_sedimenter(db_session_factory: sessionmaker,
                     memory_manager: MemoryManager,
                     settings: Settings) -> Sedimenter:
    """装配一个 Sedimenter（Web 进程与调度进程共用同一装配方式）。"""
    return Sedimenter(settings.EMOTION_CONFIG_PATH, memory_manager,
                      db_session_factory)


async def proactive_job(db_session_factory: sessionmaker,
                        settings: Settings,
                        ws_manager=None) -> dict:
    """主动行为心跳：审视全部活跃双键，按需生成并推送主动消息。

    输入：SQLite 会话工厂、Settings、WebSocket 管理器（None = 调度进程，
        推送退化为空操作，消息仍落库）；
    输出：{"pairs": N, "sent": M, "skipped": K}；副作用：见 ProactiveEngine。
    """
    from scheduler.proactive import build_proactive_engine

    engine = build_proactive_engine(db_session_factory, settings,
                                    ws_manager=ws_manager)
    stats = await engine.tick()
    logger.info("主动行为心跳完成：%s", stats)
    return stats


def sediment_job(db_session_factory: sessionmaker,
                 memory_manager: MemoryManager,
                 settings: Settings) -> dict:
    """遍历活跃双键执行 maybe_sediment，返回统计（测试/日志用）。

    输入：SQLite 会话工厂、MemoryManager、Settings；
    输出：{"pairs": N, "sedimented": M}；副作用：写沉淀记忆与状态。
    """
    sedimenter = build_sedimenter(db_session_factory, memory_manager, settings)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=ACTIVE_WINDOW_DAYS)
    with db_session_factory() as db:
        pairs = db.execute(
            select(CharacterUserState.character_id,
                   CharacterUserState.user_id).where(
                (CharacterUserState.last_interaction.is_(None))
                | (CharacterUserState.last_interaction >= cutoff))
        ).all()
    sedimented = 0
    for character_id, user_id in pairs:
        try:
            done = sedimenter.maybe_sediment(character_id, user_id)
        except Exception:
            logger.exception("沉淀失败: %s / %s", character_id, user_id)
            continue
        if done:
            sedimented += 1
    logger.info("沉淀任务完成：%d 个双键，%d 个有沉淀", len(pairs), sedimented)
    return {"pairs": len(pairs), "sedimented": sedimented}


def start_scheduler(db_session_factory: sessionmaker | None = None,
                    memory_manager: MemoryManager | None = None,
                    settings: Settings | None = None,
                    ws_manager=None) -> AsyncIOScheduler | None:
    """按 Settings.SCHEDULER_ENABLED 启动调度器（幂等，重复调用返回原实例）。

    关闭时返回 None（Web worker 的生产形态）。副作用：启动后台任务。
    ws_manager：Web 进程传 api.routes_ws 单例（主动消息可推送）；
    调度进程不传（无 WS 连接，推送退化为空操作，消息仍落库）。
    """
    global _scheduler
    settings = settings or get_settings()
    if not settings.SCHEDULER_ENABLED:
        logger.info("调度器已禁用（SCHEDULER_ENABLED=false）")
        return None
    if _scheduler is not None:
        return _scheduler

    from db.sqlite import get_session_factory

    factory = db_session_factory or get_session_factory()
    manager = memory_manager or get_memory_manager(
        settings.YANTRIK_PATH, db_session_factory=factory)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        sediment_job,
        trigger="interval",
        minutes=SEDIMENT_INTERVAL_MINUTES,
        args=[factory, manager, settings],
        id="sediment_job",
        replace_existing=True,
    )
    scheduler.add_job(
        proactive_job,
        trigger="interval",
        minutes=settings.PROACTIVE_TICK_MINUTES
        or PROACTIVE_TICK_MINUTES_DEFAULT,
        args=[factory, settings, ws_manager],
        id="proactive_job",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("调度器已启动（沉淀每 %d 分钟，主动心跳每 %s 分钟）",
                SEDIMENT_INTERVAL_MINUTES,
                settings.PROACTIVE_TICK_MINUTES
                or PROACTIVE_TICK_MINUTES_DEFAULT)
    return scheduler


def stop_scheduler() -> None:
    """停止调度器（lifespan 关闭时调用；未启动时空操作）。"""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("调度器已停止")
