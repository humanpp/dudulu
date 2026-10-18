"""用户时区工具（文档 2 第 3.11 节 v1.3 补充 + 裁决①的 Phase 8 待办）。

时区唯一来源：users.timezone（IANA 名，如 Asia/Shanghai），由前端
X-Timezone 头在 API 层落库（6.1 节）。字段为空（用户从未带过时区头）
时缺省 Asia/Shanghai（UTC+8）——与前端兜底同值（文档 3 第 9.2 节）。

消费方：
- memory/sedimenter.py：日/周沉淀的本地日边界（裁决①）
- scheduler/proactive.py：quiet_hours 静默时段判定（文档 1 第 12.4 节）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from db.models import User

# 用户时区为空时的缺省时区（文档 2 第 3.11 节 v1.3）
DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_user_timezone(db: Session, user_id: str) -> str:
    """读用户时区（IANA 名）；用户不存在或字段为空返回缺省时区。

    输入：请求级 Session、user_id；输出：IANA 时区名；副作用：无。
    """
    row = db.get(User, user_id)
    if row is None or not row.timezone:
        return DEFAULT_TIMEZONE
    return row.timezone


def _zone(name: str) -> ZoneInfo:
    """时区名 → ZoneInfo；非法名回退缺省时区（不因脏数据崩掉心跳）。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def user_local_now(db: Session, user_id: str) -> datetime:
    """用户本地当前时间（aware）。"""
    return datetime.now(_zone(get_user_timezone(db, user_id)))


def user_local_date(db: Session, user_id: str) -> str:
    """用户本地当天日期字符串（YYYY-MM-DD）。"""
    return user_local_now(db, user_id).date().isoformat()


def local_day_start_utc(db: Session, user_id: str) -> datetime:
    """用户本地当天零点 → naive UTC（库时间基准，供 SQL 比较用）。"""
    zone = _zone(get_user_timezone(db, user_id))
    local_midnight = datetime.combine(datetime.now(zone).date(),
                                      datetime.min.time(), tzinfo=zone)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def local_week_start_utc(db: Session, user_id: str) -> datetime:
    """用户本地本周一零点 → naive UTC。"""
    zone = _zone(get_user_timezone(db, user_id))
    today = datetime.now(zone).date()
    monday = today - timedelta(days=today.weekday())
    local_midnight = datetime.combine(monday, datetime.min.time(),
                                      tzinfo=zone)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def local_iso_week(db: Session, user_id: str) -> str:
    """用户本地当前 ISO 周字符串（YYYY-Www）。"""
    zone = _zone(get_user_timezone(db, user_id))
    year, week, _ = datetime.now(zone).date().isocalendar()
    return f"{year}-W{week:02d}"


def local_date_days_ago(db: Session, user_id: str, days: int) -> str:
    """用户本地 N 天前的日期字符串（周趋势的快照窗口用）。"""
    zone = _zone(get_user_timezone(db, user_id))
    return (datetime.now(zone).date() - timedelta(days=days)).isoformat()


def is_in_quiet_hours(db: Session, user_id: str,
                      quiet_hours: tuple[int, int] | list) -> bool:
    """当前用户本地时刻是否落在静默时段内（文档 1 第 12.4 节）。

    quiet_hours 形如 (23, 8)：起 > 止视为跨午夜（23:00~08:00）。
    输入：Session、user_id、(start, end) 小时数；输出：是否静默；
    副作用：无。
    """
    start, end = int(quiet_hours[0]), int(quiet_hours[1])
    hour = user_local_now(db, user_id).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # 跨午夜
