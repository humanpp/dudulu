"""character_user_state 读写与 mood_baseline 持久化（Phase 7 裁决的 _baseline 子键）。"""

import json

import pytest

from helpers import make_db_session_factory
from db.models import CharacterUserState, utc_now
from db.user_state import (
    BASELINE_KEY,
    load_mood_baseline,
    load_user_state,
    save_mood_baseline,
)

CHARACTER_ID = "char_001"
USER_ID = "user_001"


@pytest.fixture
def factory(tmp_path):
    return make_db_session_factory(tmp_path / "dudulu.db")


class TestLoadUserState:
    def test_no_row_returns_empty(self, factory):
        with factory() as db:
            state, updated_at = load_user_state(db, CHARACTER_ID, USER_ID)
        assert state == {}
        assert updated_at is None

    def test_baseline_extracted_to_top_level(self, factory):
        """_baseline 子键提取到顶层 mood_baseline，expression 保持干净。"""
        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                expression_state=json.dumps({
                    "PLE": 0.4, "ARO": 0.1, "DOM": 0.0, "INT": 0.6, "SEC": 0.5,
                    BASELINE_KEY: {"PLE": 0.2, "ARO": 0.0, "DOM": 0.0,
                                   "INT": 0.3, "SEC": 0.4}}),
                internal_state=json.dumps({"ANX": 0.2}),
                affective_state=json.dumps({"AFF": 30, "TRS": 25, "ATT": 10}),
            ))
            db.commit()
            state, _ = load_user_state(db, CHARACTER_ID, USER_ID)
        assert set(state["expression"]) == {"PLE", "ARO", "DOM", "INT", "SEC"}
        assert state["mood_baseline"]["PLE"] == 0.2
        assert state["internal"]["ANX"] == 0.2
        assert state["affective"]["AFF"] == 30

    def test_no_baseline_key_absent(self, factory):
        """未沉淀过的状态不带 mood_baseline 键（引擎用角色初始值）。"""
        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                expression_state=json.dumps({"PLE": 0.1}),
            ))
            db.commit()
            state, _ = load_user_state(db, CHARACTER_ID, USER_ID)
        assert "mood_baseline" not in state


class TestSaveMoodBaseline:
    def test_creates_row_and_merges(self, factory):
        with factory() as db:
            save_mood_baseline(db, CHARACTER_ID, USER_ID,
                               {"PLE": 0.42, "ARO": 0.15, "DOM": 0.05,
                                "INT": 0.65, "SEC": 0.58})
            db.commit()
        with factory() as db:
            row = db.get(CharacterUserState, (CHARACTER_ID, USER_ID))
        stored = json.loads(row.expression_state)
        assert stored[BASELINE_KEY]["PLE"] == 0.42
        # 其余状态列不动（只写 expression_state）
        assert row.affective_state is None

    def test_overwrites_previous_baseline(self, factory):
        with factory() as db:
            save_mood_baseline(db, CHARACTER_ID, USER_ID, {"PLE": 0.1})
            db.commit()
            save_mood_baseline(db, CHARACTER_ID, USER_ID, {"PLE": 0.9})
            db.commit()
            assert load_mood_baseline(db, CHARACTER_ID, USER_ID) == {"PLE": 0.9}

    def test_preserves_expression_dims(self, factory):
        """已有的表达层维度不被基线写入冲掉。"""
        with factory() as db:
            db.add(CharacterUserState(
                character_id=CHARACTER_ID, user_id=USER_ID,
                expression_state=json.dumps({"PLE": 0.7, "ARO": 0.2}),
            ))
            db.commit()
            save_mood_baseline(db, CHARACTER_ID, USER_ID, {"PLE": 0.3})
            db.commit()
            stored = json.loads(
                db.get(CharacterUserState,
                       (CHARACTER_ID, USER_ID)).expression_state)
        assert stored["PLE"] == 0.7          # 原维度保留
        assert stored[BASELINE_KEY]["PLE"] == 0.3

    def test_load_mood_baseline_no_row(self, factory):
        with factory() as db:
            assert load_mood_baseline(db, CHARACTER_ID, USER_ID) == {}
