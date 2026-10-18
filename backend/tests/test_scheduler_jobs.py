"""调度任务单元测试（文档 2 第 10.2 节）。"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import CONFIG_PATH, make_db_session_factory
from config.settings import Settings
from db.models import StateChange, utc_now
from memory.memory_manager import (
    MEMORY_TYPE_DAILY_SNAPSHOT,
    MemoryManager,
)
from memory.sedimenter import utc_today
from scheduler.jobs import (
    SEDIMENT_INTERVAL_MINUTES,
    build_sedimenter,
    sediment_job,
    start_scheduler,
    stop_scheduler,
)

CHARACTER_ID = "char_001"
USER_ID = "user_001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def memory_manager(tmp_path):
    return MemoryManager(str(tmp_path / "mem.db"),
                         db_session_factory=None, embedder="fake")


@pytest.fixture
def settings():
    return Settings(_env_file=None, SCHEDULER_ENABLED=False)


def add_activity(factory, minutes_ago=30):
    """种一条今天的状态变更 + 用户状态行（有互动的双键）。"""
    from db.models import CharacterUserState

    with factory() as db:
        db.add(CharacterUserState(
            character_id=CHARACTER_ID, user_id=USER_ID,
            affective_state='{"AFF": 20, "TRS": 20, "ATT": 10}',
            relationship_stage="S1", last_interaction=utc_now()))
        db.add(StateChange(
            character_id=CHARACTER_ID, user_id=USER_ID,
            changed_at=utc_now(), event_label="affectionate",
            event_intensity=0.6,
            delta_json='{"affective": {"AFF": 0.5}}',
            state_after_json='{"expression": {"PLE": 0.5}, "affective":'
                             ' {"AFF": 21, "TRS": 20, "ATT": 10}}'))
        db.commit()


class TestBuildSedimenter:
    def test_builds_sedimenter(self, factory, memory_manager, settings):
        sedimenter = build_sedimenter(factory, memory_manager, settings)
        assert sedimenter.session_enabled is True
        assert sedimenter.daily_enabled is True
        assert sedimenter.weekly_enabled is True
        # 权重来自配置（文档 1 第十四章）
        assert (sedimenter.peak_weight, sedimenter.ending_weight,
                sedimenter.average_weight) == (0.5, 0.3, 0.2)


class TestSedimentJob:
    def test_processes_active_pairs(self, factory, memory_manager,
                                    settings):
        add_activity(factory)
        stats = sediment_job(factory, memory_manager, settings)
        assert stats == {"pairs": 1, "sedimented": 1}
        snapshots = memory_manager.list_sediments(
            CHARACTER_ID, USER_ID, MEMORY_TYPE_DAILY_SNAPSHOT)
        # 快照日期 = 用户本地当天（无用户行 → 缺省 Asia/Shanghai）
        from db.timezone import user_local_date

        with factory() as db:
            expected = user_local_date(db, USER_ID)
        assert [record["metadata"]["date"] for record in snapshots] == [
            expected]

    def test_idle_pair_still_listed_but_not_sedimented(self, factory,
                                                       memory_manager,
                                                       settings):
        """有状态行但今天无互动：列入扫描但无沉淀。"""
        from db.models import CharacterUserState

        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                affective_state="{}", last_interaction=utc_now()))
            db.commit()
        stats = sediment_job(factory, memory_manager, settings)
        assert stats == {"pairs": 1, "sedimented": 0}

    def test_stale_pair_excluded(self, factory, memory_manager, settings):
        """超过活跃窗口（7 天）的双键不扫描。"""
        from datetime import timedelta

        from db.models import CharacterUserState

        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                affective_state="{}",
                last_interaction=utc_now() - timedelta(days=30)))
            db.commit()
        stats = sediment_job(factory, memory_manager, settings)
        assert stats == {"pairs": 0, "sedimented": 0}

    def test_failure_does_not_stop_other_pairs(self, factory,
                                               memory_manager, settings,
                                               monkeypatch):
        """单个双键沉淀异常被吞掉并继续（不影响其余）。"""
        from memory.sedimenter import Sedimenter

        add_activity(factory)

        def exploding_maybe_sediment(self, character_id, user_id):
            raise RuntimeError("boom")

        monkeypatch.setattr(Sedimenter, "maybe_sediment",
                            exploding_maybe_sediment)
        stats = sediment_job(factory, memory_manager, settings)
        assert stats == {"pairs": 1, "sedimented": 0}


class TestStartScheduler:
    def test_disabled_returns_none(self, factory, memory_manager):
        disabled = Settings(_env_file=None, SCHEDULER_ENABLED=False)
        assert start_scheduler(factory, memory_manager, disabled) is None

    def test_enabled_starts_and_stops(self, factory, memory_manager):
        enabled = Settings(_env_file=None, SCHEDULER_ENABLED=True)

        async def scenario():
            scheduler = start_scheduler(factory, memory_manager, enabled)
            assert scheduler is not None
            jobs = {job.id for job in scheduler.get_jobs()}
            # Phase 8b 起调度器带两个任务：沉淀 + 主动心跳
            assert jobs == {"sediment_job", "proactive_job"}
            # 重复启动返回原实例（幂等）
            assert start_scheduler(factory, memory_manager,
                                   enabled) is scheduler
            stop_scheduler()

        asyncio.run(scenario())
        assert start_scheduler.__globals__["_scheduler"] is None

    def test_stop_without_start_is_noop(self):
        stop_scheduler()  # 不抛异常即可


class TestSchedulerRunEntry:
    """调度进程入口冒烟（文档 2 第 10.2 节：python -m scheduler.run）。"""

    def test_disabled_entry_exits_cleanly(self):
        """SCHEDULER_ENABLED=false 时入口报错退出（不挂起）。"""
        result = subprocess.run(
            [sys.executable, "-m", "scheduler.run"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "DUDULU_SCHEDULER_ENABLED": "false"},
            cwd=str(Path(__file__).resolve().parents[1]))
        assert result.returncode == 0
        assert "SCHEDULER_ENABLED=false" in result.stderr

    def test_main_disabled_returns_immediately(self, monkeypatch):
        """main() 在调度器禁用时直接返回（不进入常驻等待）。"""
        from scheduler import run

        monkeypatch.setattr(
            run, "get_settings",
            lambda: Settings(_env_file=None, SCHEDULER_ENABLED=False))
        asyncio.run(run.main())  # 不挂起即通过
