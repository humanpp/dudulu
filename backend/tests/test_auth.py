"""认证单元测试（文档 2 第 6.1 / 7.1 节 + X-Timezone 同步机制）。"""

import pytest
from fastapi.testclient import TestClient

from helpers import (
    auth_header,
    build_probe_app,
    build_test_app,
    make_db_session_factory,
    seed_user,
)

CREDENTIALS = {"username": "tester", "password": "secret123"}


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


@pytest.fixture
def app(factory):
    return build_test_app(factory)


class TestPasswordHashing:
    def test_hash_is_not_plaintext_and_verifies(self):
        from auth.password import hash_password, verify_password

        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert hashed.startswith("$2b$")
        assert verify_password("secret123", hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_verify_with_corrupted_hash_returns_false(self):
        from auth.password import verify_password

        assert verify_password("secret123", "not-a-hash") is False


class TestJwt:
    def test_round_trip(self):
        from auth.jwt import create_token, verify_token

        token = create_token("user_123")
        assert verify_token(token) == "user_123"

    def test_expiry_is_30_days(self):
        """文档 2 第 8.1 节：TOKEN_EXPIRE_DAYS = 30。"""
        import time

        import jwt as pyjwt

        from auth.jwt import create_token
        from config.settings import get_settings

        before = time.time()
        payload = pyjwt.decode(create_token("u"),
                               get_settings().SECRET_KEY,
                               algorithms=["HS256"])
        lifetime_days = (payload["exp"] - before) / 86400
        assert lifetime_days == pytest.approx(30, abs=0.01)

    def test_tampered_token_rejected(self):
        import jwt as pyjwt

        from auth.jwt import create_token, verify_token

        token = create_token("user_123")
        with pytest.raises(pyjwt.PyJWTError):
            verify_token(token + "tampered")


class TestRegisterAndLogin:
    def test_register_returns_user_and_token(self, app):
        with TestClient(app) as client:
            response = client.post("/api/auth/register", json=CREDENTIALS)
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"user_id", "token"}
        assert payload["user_id"]
        assert payload["token"]

    def test_register_persists_hashed_password(self, app, factory):
        from db.models import User

        with TestClient(app) as client:
            user_id = client.post("/api/auth/register",
                                  json=CREDENTIALS).json()["user_id"]
        with factory() as db:
            user = db.get(User, user_id)
        assert user.username == "tester"
        assert user.password_hash != "secret123"

    def test_duplicate_username_409(self, app):
        with TestClient(app) as client:
            client.post("/api/auth/register", json=CREDENTIALS)
            response = client.post("/api/auth/register", json=CREDENTIALS)
        assert response.status_code == 409

    def test_login_ok(self, app):
        with TestClient(app) as client:
            client.post("/api/auth/register", json=CREDENTIALS)
            response = client.post("/api/auth/login", json=CREDENTIALS)
        assert response.status_code == 200
        assert set(response.json()) == {"user_id", "token"}

    def test_login_wrong_password_401(self, app):
        with TestClient(app) as client:
            client.post("/api/auth/register", json=CREDENTIALS)
            response = client.post("/api/auth/login", json={
                "username": "tester", "password": "wrong-one"})
        assert response.status_code == 401

    def test_login_unknown_user_401(self, app):
        with TestClient(app) as client:
            response = client.post("/api/auth/login", json={
                "username": "ghost", "password": "whatever"})
        assert response.status_code == 401

    def test_empty_password_rejected_422(self, app):
        with TestClient(app) as client:
            response = client.post("/api/auth/register", json={
                "username": "tester", "password": ""})
        assert response.status_code == 422

    def test_over_long_password_rejected_422(self, app):
        """bcrypt 上限 72 字节，超出直接拒（路由层 max_length=72）。"""
        with TestClient(app) as client:
            response = client.post("/api/auth/register", json={
                "username": "tester", "password": "x" * 73})
        assert response.status_code == 422

    def test_register_with_timezone_header(self, app, factory):
        """注册时带合法 X-Timezone → 落库；非法 → 忽略（留 NULL）。"""
        from db.models import User

        with TestClient(app) as client:
            ok_id = client.post("/api/auth/register", json=CREDENTIALS,
                                headers={"X-Timezone": "Asia/Tokyo"}
                                ).json()["user_id"]
            bad_id = client.post(
                "/api/auth/register",
                json={"username": "other", "password": "secret123"},
                headers={"X-Timezone": "Not/AZone"}).json()["user_id"]
        with factory() as db:
            assert db.get(User, ok_id).timezone == "Asia/Tokyo"
            assert db.get(User, bad_id).timezone is None


class TestChangePassword:
    def test_change_and_login_with_new(self, app):
        with TestClient(app) as client:
            user_id = client.post("/api/auth/register",
                                  json=CREDENTIALS).json()["user_id"]
            response = client.put("/api/auth/password", json={
                "old_password": "secret123",
                "new_password": "new-secret-1"},
                headers=auth_header(user_id))
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            ok = client.post("/api/auth/login", json={
                "username": "tester", "password": "new-secret-1"})
            stale = client.post("/api/auth/login", json=CREDENTIALS)
        assert ok.status_code == 200
        assert stale.status_code == 401

    def test_wrong_old_password_401(self, app):
        with TestClient(app) as client:
            user_id = client.post("/api/auth/register",
                                  json=CREDENTIALS).json()["user_id"]
            response = client.put("/api/auth/password", json={
                "old_password": "nope", "new_password": "new-secret-1"},
                headers=auth_header(user_id))
        assert response.status_code == 401

    def test_requires_auth(self, app):
        with TestClient(app) as client:
            response = client.put("/api/auth/password", json={
                "old_password": "a", "new_password": "b"})
        assert response.status_code == 401


class TestCurrentUserDependency:
    def test_valid_token_passes(self, factory):
        user_id = seed_user(factory)
        with TestClient(build_probe_app(factory)) as client:
            response = client.get("/whoami", headers=auth_header(user_id))
        assert response.status_code == 200
        assert response.json()["id"] == user_id
        assert response.json()["username"] == "tester"

    def test_missing_header_401(self, factory):
        with TestClient(build_probe_app(factory)) as client:
            assert client.get("/whoami").status_code == 401

    def test_malformed_header_401(self, factory):
        with TestClient(build_probe_app(factory)) as client:
            response = client.get(
                "/whoami", headers={"Authorization": "Basic abc"})
        assert response.status_code == 401

    def test_unknown_user_401(self, factory):
        from auth.jwt import create_token

        with TestClient(build_probe_app(factory)) as client:
            response = client.get(
                "/whoami",
                headers={"Authorization": f"Bearer {create_token('ghost')}"})
        assert response.status_code == 401


class TestTimezoneSync:
    """X-Timezone 同步（文档 2 第 6.1 节 v1.4）：内存比对，变化才写库。"""

    def test_header_writes_db_on_change(self, factory):
        user_id = seed_user(factory)
        with TestClient(build_probe_app(factory)) as client:
            client.get("/whoami", headers={**auth_header(user_id),
                                           "X-Timezone": "Asia/Tokyo"})
        from db.models import User

        with factory() as db:
            assert db.get(User, user_id).timezone == "Asia/Tokyo"

    def test_same_header_second_time_no_write(self, factory):
        """一致时零成本：假 session 的 commit 一被调用就炸。"""
        from auth.dependencies import _TIMEZONE_CACHE, _sync_timezone
        from db.models import User

        user_id = seed_user(factory)
        with factory() as db:
            user = db.get(User, user_id)
            _sync_timezone(db, user, "Asia/Tokyo")   # 首次：写库 + 入缓存
            _TIMEZONE_CACHE.pop(user_id, None)
            _TIMEZONE_CACHE[user_id] = "Asia/Tokyo"  # 模拟"已缓存且一致"

            class ExplodingSession:
                def commit(self):
                    raise AssertionError("一致时不应写库")

            _sync_timezone(ExplodingSession(), user, "Asia/Tokyo")

    def test_invalid_timezone_ignored(self, factory):
        from auth.dependencies import _sync_timezone
        from db.models import User

        user_id = seed_user(factory)
        with factory() as db:
            user = db.get(User, user_id)
            _sync_timezone(db, user, "Not/AZone")
            db.commit()
        assert user.timezone is None

    def test_missing_header_keeps_null(self, factory):
        from auth.dependencies import _sync_timezone
        from db.models import User

        user_id = seed_user(factory)
        with factory() as db:
            user = db.get(User, user_id)
            _sync_timezone(db, user, None)
            db.commit()
        assert user.timezone is None

    def test_effective_timezone_fallback(self):
        """NULL 兜底 Asia/Shanghai（UTC+8），与前端同一个值。"""
        from auth.dependencies import effective_timezone

        assert effective_timezone(None) == "Asia/Shanghai"
        assert effective_timezone("") == "Asia/Shanghai"
        assert effective_timezone("Europe/London") == "Europe/London"
