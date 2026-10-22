"""
T03: DB 기반 상태(state.py) 테스트.

기존 메모리 구현에서 유지해야 할 외부 동작을 고정하고, 새로 생긴 영속성을 확인한다.
임시 DB 만 쓴다.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import db
import state

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def st(conn):
    return state.BotState(conn)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "bot.db"


# ── 기존 동작 유지 (T01 이전 메모리 구현과 동일해야 함) ────

def test_initially_not_bought(st):
    assert st.bought_today_for("QQQ") is False
    assert st.buy_count_today("QQQ") == 0
    assert st.last_buy_time("QQQ") is None


def test_record_buy_increments_and_sets_flag(st):
    st.record_buy("QQQ")
    assert st.buy_count_today("QQQ") == 1
    assert st.bought_today_for("QQQ") is True
    st.record_buy("QQQ")
    assert st.buy_count_today("QQQ") == 2


def test_bought_flag_reset_clears_count(st):
    """전량 매도 후 재진입 허용 — 기존 set_bought_today(False) 동작."""
    st.record_buy("QQQ")
    st.set_bought_today("QQQ", False)
    assert st.bought_today_for("QQQ") is False
    assert st.buy_count_today("QQQ") == 0


def test_set_bought_today_true_sets_at_least_one(st):
    st.set_bought_today("QQQ", True)
    assert st.bought_today_for("QQQ") is True


def test_set_bought_today_true_does_not_lower_existing_count(st):
    """이미 2회 매수한 상태에서 True 를 넣어도 횟수가 1로 줄면 안 된다."""
    st.record_buy("QQQ")
    st.record_buy("QQQ")
    st.set_bought_today("QQQ", True)
    assert st.buy_count_today("QQQ") == 2


def test_tickers_are_independent(st):
    st.record_buy("QQQ")
    assert st.buy_count_today("QQQ") == 1
    assert st.buy_count_today("379800") == 0
    assert st.bought_today_for("379800") is False


def test_profit_stage_is_gone(st):
    """T06: 부분 익절 단계 API 는 삭제됐다 (`tests/test_state.py` 상단 설명 참고)."""
    assert not hasattr(st, "profit_stage")
    assert not hasattr(st, "set_profit_stage")


def test_last_buy_time_is_tz_aware_kst(st):
    st.record_buy("QQQ")
    t = st.last_buy_time("QQQ")
    assert t is not None
    assert t.tzinfo is not None, "naive 시각을 돌려주면 안 된다(절대 규칙 9)"
    assert t.utcoffset() == timedelta(hours=9)


def test_last_buy_time_advances_on_new_buy(st):
    """
    새 매수 시각이 이전 시각보다 뒤로 간다.

    기준 시각을 하드코딩하면 그 날짜가 지나는 순간 테스트가 깨진다(실제로 깨졌다).
    첫 매수 시각을 기준으로 상대 시각을 만들어 시간 비의존적으로 검증한다.
    """
    st.record_buy("QQQ")
    first = st.last_buy_time("QQQ")
    assert first is not None

    later = first + timedelta(minutes=5)
    db.set_buy_count(st._conn, st._today(), "QQQ", 1, at=later.isoformat())
    assert st.last_buy_time("QQQ") == later
    assert st.last_buy_time("QQQ") > first


# ── 영속성 (재시작해도 유지) ──────────────────────────────

def test_buy_count_survives_new_instance(db_path):
    """완료 기준: 쓰기 → 새 인스턴스에서 읽기."""
    c1 = db.open_db(db_path)
    state.BotState(c1).record_buy("QQQ")
    c1.close()

    c2 = db.open_db(db_path)
    st2 = state.BotState(c2)
    assert st2.buy_count_today("QQQ") == 1
    assert st2.bought_today_for("QQQ") is True
    assert st2.last_buy_time("QQQ") is not None
    c2.close()


def test_profit_stage_api_is_removed(db_path):
    """
    T06: 부분 익절 단계(`profit_stage`)를 **삭제**했다.

    청산이 전량이고 판단이 `positions` 만 보므로 "오늘 몇 단계까지 팔았나"를
    기억할 필요가 없다. 남아 있으면 다시 쓰이므로 없다는 것을 고정한다.
    """
    c1 = db.open_db(db_path)
    st = state.BotState(c1)
    assert not hasattr(st, "profit_stage")
    assert not hasattr(st, "set_profit_stage")
    c1.close()


# ── 날짜 경계 (완료 기준 3) ───────────────────────────────

def test_counters_reset_when_day_changes(conn, monkeypatch):
    """날짜가 바뀌면 당일 카운터가 자동으로 0이 된다(별도 리셋 로직 없음)."""
    st = state.BotState(conn)
    monkeypatch.setattr(st, "_today", lambda: "2026-10-20")
    st.record_buy("QQQ")
    st.record_buy("QQQ")
    assert st.buy_count_today("QQQ") == 2

    # 자정을 넘겨 다음 날이 됨
    monkeypatch.setattr(st, "_today", lambda: "2026-10-21")
    assert st.buy_count_today("QQQ") == 0, "날짜가 바뀌면 0회여야 한다"
    assert st.bought_today_for("QQQ") is False
    assert st.last_buy_time("QQQ") is None


def test_previous_day_data_is_preserved_after_rollover(conn, monkeypatch):
    """리셋은 조회 키가 바뀌는 것이지 삭제가 아니다(이력 보존)."""
    st = state.BotState(conn)
    monkeypatch.setattr(st, "_today", lambda: "2026-10-20")
    st.record_buy("QQQ")

    monkeypatch.setattr(st, "_today", lambda: "2026-10-21")
    assert st.buy_count_today("QQQ") == 0
    # 어제 행은 그대로 남아 있다
    assert db.get_daily_counter(conn, "2026-10-20", "QQQ")["buy_count"] == 1


def test_today_key_is_kst_not_local(conn):
    """
    날짜 키는 KST 기준이어야 한다. 시스템 로컬 시간을 쓰면 자정 부근에서
    하루가 어긋나 당일 매수 상한이 잘못 리셋된다.
    """
    expected = datetime.now(KST).date().isoformat()
    assert state.BotState(conn)._today() == expected


def test_naive_last_buy_time_is_rejected_not_assumed_kst(conn, monkeypatch):
    """
    DB 에 naive 시각이 있으면 KST 로 추정하지 않고 None 을 돌려준다.
    naive 를 KST 로 승격하면 실제 시각과 9시간 어긋난 간격 계산이 된다.
    """
    st = state.BotState(conn)
    db.set_buy_count(conn, st._today(), "QQQ", 1, at="2026-10-20T09:00:00")
    assert st.last_buy_time("QQQ") is None


def test_broken_last_buy_time_returns_none_not_crash(conn):
    """파싱 불가 값이 있어도 예외로 죽지 않는다(로그 + None)."""
    st = state.BotState(conn)
    db.set_buy_count(conn, st._today(), "QQQ", 1, at="이건 날짜가 아님")
    assert st.last_buy_time("QQQ") is None


# ── 싱글턴 / 지연 생성 ────────────────────────────────────

def test_lazy_singleton_does_not_open_db_on_import():
    """
    `bot_state` 를 import 만 해서 실제 DB 가 열리면
    테스트가 data/bot.db 를 건드리게 된다. 지연 생성이어야 한다.
    """
    import importlib
    import state as s
    importlib.reload(s)
    assert s._bot_state is None, "import 만으로 DB 가 열렸다"


def test_reset_bot_state_uses_given_conn(conn):
    st = state.reset_bot_state(conn)
    assert st is state.get_bot_state()
    st.record_buy("QQQ")
    assert state.bot_state.buy_count_today("QQQ") == 1
    state._bot_state = None


def test_reset_bot_state_without_conn_uses_config_db_path(tmp_path, monkeypatch):
    """conn 을 주지 않으면 config.DB_PATH 를 쓴다(여기서는 임시 경로로 격리됨)."""
    st = state.reset_bot_state()
    try:
        assert st is state.get_bot_state()
        st.record_buy("QQQ")
        assert st.buy_count_today("QQQ") == 1
        assert (tmp_path / "isolated_bot.db").exists()
    finally:
        st.close()
        state._bot_state = None


def test_bot_state_proxy_forwards_to_singleton(db_path):
    """기존 `bot_state.xxx` 호출이 그대로 동작해야 한다(하위 호환)."""
    c = db.open_db(db_path)
    state.reset_bot_state(c)
    try:
        state.bot_state.record_buy("QQQ")
        assert state.bot_state.buy_count_today("QQQ") == 1
        assert state.bot_state.bought_today_for("QQQ") is True
    finally:
        state._bot_state = None
        c.close()
