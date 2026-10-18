"""用户时区工具测试（文档 2 第 3.11 节 v1.3 + Phase 8a 裁决①）。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from helpers import make_db_session_factory
from db.models import User
from db.timezone import (
    DEFAULT_TIMEZONE,
    get_user_timezone,
    is_in_quiet_hours,
    local_date_days_ago,
    local_day_start_utc,
    local_iso_week,
    local_week_start_utc,
    user_local_date,
    user_local_now,
)

USER_ID = "user_001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


def seed_user(factory, timezone_name=None, username="tz",
              user_id=USER_ID):
    with factory() as db:
        db.add(User(id=user_id, username=username,
                    password_hash="x", timezone=timezone_name))
        db.commit()


class TestGetUserTimezone:
    def test_null_falls_back_to_default(self, factory):
        seed_user(factory, None)
        with factory() as db:
            assert get_user_timezone(db, USER_ID) == DEFAULT_TIMEZONE
            assert DEFAULT_TIMEZONE == "Asia/Shanghai"

    def test_explicit_timezone(self, factory):
        seed_user(factory, "America/Los_Angeles")
        with factory() as db:
            assert get_user_timezone(db, USER_ID) == "America/Los_Angeles"

    def test_missing_user_falls_back(self, factory):
        with factory() as db:
            assert get_user_timezone(db, "ghost") == DEFAULT_TIMEZONE

    def test_invalid_timezone_falls_back(self, factory):
        seed_user(factory, "Not/A_Zone")
        with factory() as db:
            # 非法时区名不崩，按缺省时区给日期
            assert user_local_date(db, USER_ID) == user_local_date(
                db, "ghost")


class TestLocalDate:
    def test_matches_zoneinfo_for_user_timezone(self, factory):
        """本地日期与 ZoneInfo 直接计算一致（东/西时区各验）。"""
        for index, name in enumerate(("Pacific/Kiritimati",
                                      "America/Los_Angeles",
                                      "Asia/Shanghai", "UTC")):
            user_id = f"user_{index}"
            seed_user(factory, name, username=f"tz-{name}", user_id=user_id)
            with factory() as db:
                expected = datetime.now(
                    ZoneInfo(name)).date().isoformat()
                assert user_local_date(db, user_id) == expected, name

    def test_days_ago(self, factory):
        seed_user(factory, "Asia/Shanghai")
        with factory() as db:
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            assert local_date_days_ago(db, USER_ID, 1) == \
                (today - timedelta(days=1)).isoformat()
            assert local_date_days_ago(db, USER_ID, 7) == \
                (today - timedelta(days=7)).isoformat()

    def test_local_iso_week(self, factory):
        seed_user(factory, "Asia/Shanghai")
        with factory() as db:
            year, week, _ = datetime.now(
                ZoneInfo("Asia/Shanghai")).date().isocalendar()
            assert local_iso_week(db, USER_ID) == f"{year}-W{week:02d}"


class TestDayStartUtc:
    def test_midnight_conversion(self, factory):
        """本地零点 → naive UTC 与 ZoneInfo 手算一致。"""
        seed_user(factory, "Asia/Shanghai")
        with factory() as db:
            zone = ZoneInfo("Asia/Shanghai")
            expected = datetime.combine(
                datetime.now(zone).date(), datetime.min.time(),
                tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
            assert local_day_start_utc(db, USER_ID) == expected

    def test_week_start_is_monday(self, factory):
        seed_user(factory, "Asia/Shanghai")
        with factory() as db:
            week_start = local_week_start_utc(db, USER_ID)
        # 换算回本地时间应为周一 00:00
        local = week_start.replace(tzinfo=timezone.utc).astimezone(
            ZoneInfo("Asia/Shanghai"))
        assert local.weekday() == 0
        assert (local.hour, local.minute) == (0, 0)

    def test_far_east_timezone_is_ahead(self, factory):
        """UTC+14 的本地零点换算成 UTC 是前一天（验证方向正确）。"""
        seed_user(factory, "Pacific/Kiritimati")
        with factory() as db:
            day_start = local_day_start_utc(db, USER_ID)
        local_midnight = day_start.replace(
            tzinfo=timezone.utc).astimezone(ZoneInfo("Pacific/Kiritimati"))
        assert (local_midnight.hour, local_midnight.minute) == (0, 0)


class TestQuietHours:
    @pytest.mark.parametrize("hour,expected", [
        (7, True), (8, False), (12, False), (22, False), (23, True),
        (2, True),   # 跨午夜：凌晨也在静默期
    ])
    def test_cross_midnight_range(self, factory, monkeypatch, hour, expected):
        """quiet_hours (23, 8)：23:00~08:00 静默（跨午夜）。"""
        seed_user(factory, "Asia/Shanghai")
        import db.timezone as timezone_module

        fixed = datetime(2026, 10, 15, hour, 30,
                         tzinfo=ZoneInfo("Asia/Shanghai"))
        monkeypatch.setattr(timezone_module, "user_local_now",
                            lambda db, user_id: fixed)
        with factory() as db:
            assert is_in_quiet_hours(db, USER_ID, (23, 8)) is expected

    @pytest.mark.parametrize("hour,expected", [
        (7, False), (9, True), (20, True), (23, False),
    ])
    def test_same_day_range(self, factory, monkeypatch, hour, expected):
        """quiet_hours (8, 23)：同日区间。"""
        seed_user(factory, "Asia/Shanghai")
        import db.timezone as timezone_module

        fixed = datetime(2026, 10, 15, hour, 30,
                         tzinfo=ZoneInfo("Asia/Shanghai"))
        monkeypatch.setattr(timezone_module, "user_local_now",
                            lambda db, user_id: fixed)
        with factory() as db:
            assert is_in_quiet_hours(db, USER_ID, (8, 23)) is expected

    def test_equal_bounds_never_quiet(self, factory, monkeypatch):
        seed_user(factory, "Asia/Shanghai")
        import db.timezone as timezone_module

        monkeypatch.setattr(
            timezone_module, "user_local_now",
            lambda db, user_id: datetime(2026, 10, 15, 5, 0,
                                         tzinfo=ZoneInfo("Asia/Shanghai")))
        with factory() as db:
            assert is_in_quiet_hours(db, USER_ID, (8, 8)) is False

    # ── Phase 8b 前置专项：时区回退链 ──────────────────────

    def test_null_timezone_user_uses_default_for_quiet_hours(
            self, factory, monkeypatch):
        """时区为 NULL 的用户按缺省 Asia/Shanghai 判静默（回退链一致）。"""
        seed_user(factory, None)
        import db.timezone as timezone_module

        # UTC 15:30 = Asia/Shanghai 23:30 → 在 (23, 8) 静默期内
        fixed_utc = datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(
            timezone_module, "datetime",
            type("Dt", (), {"now": staticmethod(
                lambda tz=None: fixed_utc.astimezone(
                    tz or ZoneInfo(DEFAULT_TIMEZONE)))}))
        with factory() as db:
            assert is_in_quiet_hours(db, USER_ID, (23, 8)) is True
            assert is_in_quiet_hours(db, USER_ID, (1, 6)) is False

    def test_invalid_timezone_user_uses_default(self, factory, monkeypatch):
        """非法时区名回退缺省时区（与 NULL 同行为）。"""
        seed_user(factory, "Mars/Olympus")
        import db.timezone as timezone_module

        fixed_utc = datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(
            timezone_module, "datetime",
            type("Dt", (), {"now": staticmethod(
                lambda tz=None: fixed_utc.astimezone(
                    tz or ZoneInfo(DEFAULT_TIMEZONE)))}))
        with factory() as db:
            assert is_in_quiet_hours(db, USER_ID, (23, 8)) is True

    def test_far_east_user_not_in_default_quiet_hours(self, factory,
                                                      monkeypatch):
        """UTC+14 用户的本地时刻不同：UTC 15:30 → 本地 05:30（次日），
        在 (23, 8) 静默期内。"""
        seed_user(factory, "Pacific/Kiritimati")
        import db.timezone as timezone_module

        fixed_utc = datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(
            timezone_module, "datetime",
            type("Dt", (), {"now": staticmethod(
                lambda tz=None: fixed_utc.astimezone(
                    tz or ZoneInfo(DEFAULT_TIMEZONE)))}))
        with factory() as db:
            # 本地 05:30 < 8 → 静默
            assert is_in_quiet_hours(db, USER_ID, (23, 8)) is True

    def test_far_west_user_awake_at_default_quiet_hours(self, factory,
                                                        monkeypatch):
        """UTC-7 用户：UTC 15:30 → 本地 08:30，不在 (23, 8) 静默期。"""
        seed_user(factory, "America/Los_Angeles")
        import db.timezone as timezone_module

        fixed_utc = datetime(2026, 10, 15, 15, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(
            timezone_module, "datetime",
            type("Dt", (), {"now": staticmethod(
                lambda tz=None: fixed_utc.astimezone(
                    tz or ZoneInfo(DEFAULT_TIMEZONE)))}))
        with factory() as db:
            assert is_in_quiet_hours(db, USER_ID, (23, 8)) is False


class TestSedimenterLocalBoundary:
    """沉淀器的日边界按用户本地时区（裁决①）。"""

    def test_snapshot_date_is_user_local(self, factory, tmp_path):
        from helpers import CONFIG_PATH
        from memory.memory_manager import MemoryManager
        from memory.sedimenter import Sedimenter

        seed_user(factory, "Pacific/Kiritimati")
        memory_manager = MemoryManager(str(tmp_path / "mem.db"),
                                       db_session_factory=None,
                                       embedder="fake")
        sedimenter = Sedimenter(CONFIG_PATH, memory_manager, factory)
        # 本地日期与 ZoneInfo 手算一致（不依赖运行时刻）
        expected = datetime.now(
            ZoneInfo("Pacific/Kiritimati")).date().isoformat()
        assert sedimenter._local_today(USER_ID) == expected

    def test_day_start_excludes_before_midnight(self, factory, tmp_path):
        """本地零点之前的状态变更不算"今天"（边界前 1 分钟排除）。"""
        from db.models import StateChange
        from db.timezone import local_day_start_utc
        from helpers import CONFIG_PATH
        from memory.memory_manager import MemoryManager
        from memory.sedimenter import Sedimenter

        seed_user(factory, "Asia/Shanghai")
        memory_manager = MemoryManager(str(tmp_path / "mem.db"),
                                       db_session_factory=None,
                                       embedder="fake")
        sedimenter = Sedimenter(CONFIG_PATH, memory_manager, factory)
        with factory() as db:
            day_start = local_day_start_utc(db, USER_ID)
        before = day_start - timedelta(minutes=1)
        after = day_start + timedelta(minutes=1)
        with factory() as db:
            db.add(StateChange(
                character_id="char_001", user_id=USER_ID,
                changed_at=before, event_label="affectionate",
                event_intensity=0.5,
                state_after_json='{"expression": {"PLE": -0.9}}'))
            db.add(StateChange(
                character_id="char_001", user_id=USER_ID,
                changed_at=after, event_label="affectionate",
                event_intensity=0.6,
                state_after_json='{"expression": {"PLE": 0.9}}'))
            db.commit()
        snapshot = sedimenter._daily_sediment("char_001", USER_ID)
        assert snapshot is not None
        # day_end 只取本地零点之后的那条（PLE 0.9）
        assert snapshot.day_end_expression["PLE"] == 0.9
