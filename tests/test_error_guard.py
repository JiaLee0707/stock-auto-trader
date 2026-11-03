"""
T09b-2c: 연속 사이클 오류와 응답 불명(UNKNOWN) 주문 시한이 RISK 중단을 건다.

  - 연속 오류: 대조 실패와 `job()` 바깥 예외(네트워크·타임아웃·기타)를 사이클 단위로 센다. 정상 완료(주문 구간 밖 포함)하면 0 으로 돌아간다.
    DB 사용 불가는 API 오류가 아니므로 세지 않는다. 한도에 닿으면 RISK 중단(신규 매수만 차단·청산 계속).
  - UNKNOWN 시한: 생성 후 시한 이상 확정되지 않은 UNKNOWN 주문이 있으면 RISK 중단. 대조 성공 뒤 매매 사이클 앞에서 점검한다.
  - 중단 기록·점검이 실패해도 사이클은 멈추지 않고, 예외 본문은 로그에 남기지 않는다(requests 예외에 URL·토큰이 들어간다).
실제 API·알림은 쓰지 않는다. `main.main()` 은 실행하지 않는다.
"""
import logging
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import config
import db
import main
import market_calendar
import reconcile
import risk
import state

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=KST)
BASE = dict(capital_cap_krw=200_000.0, max_drawdown_pct=5.0, daily_loss_limit_pct=2.0,
            halt_action="hold", overseas_enabled=False)


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def bot(conn):
    st = state.reset_bot_state(conn)
    yield st
    state.reset_bot_state(None)


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile.notifier, "notify",
                        lambda level, title, message="": calls.append((level, title, message)) or True)
    return calls


def _ago(minutes, now=None):
    return ((now or db.now_kst()) - timedelta(minutes=minutes)).isoformat()


def _order(status, minutes, **over):
    row = {"id": 1, "symbol": "379800", "market": "domestic", "side": "BUY",
           "status": status, "created_at": _ago(minutes, NOW)}
    row.update(over)
    return row


def _insert_order(conn, status, minutes, *, now=None, symbol="379800", side="BUY"):
    created = _ago(minutes, now)
    conn.execute(
        "INSERT INTO orders (client_ref, symbol, market, side, qty, status, created_at, updated_at) "
        "VALUES (?, ?, 'domestic', ?, 1, ?, ?, ?)",
        (f"t-{status}-{minutes}-{symbol}-{side}", symbol, side, status, created, created),
    )
    conn.commit()


# ── 설정 검증 ────────────────────────────────────────────

def test_new_limits_are_optional_and_the_defaults_pass():
    assert config.risk_config_error(**BASE) is None
    assert config.risk_config_error(**BASE, max_consecutive_errors=5, unknown_timeout_minutes=120.0) is None
    assert config.risk_config_error(**BASE, max_consecutive_errors=config.MAX_CONSECUTIVE_ERRORS,
                                    unknown_timeout_minutes=config.UNKNOWN_TIMEOUT_MINUTES) is None


@pytest.mark.parametrize("bad", [0, -1, 2.5])
def test_invalid_max_consecutive_errors_is_rejected(bad):
    msg = config.risk_config_error(**BASE, max_consecutive_errors=bad)
    assert msg and "MAX_CONSECUTIVE_ERRORS" in msg


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf")])
def test_invalid_unknown_timeout_is_rejected(bad):
    msg = config.risk_config_error(**BASE, unknown_timeout_minutes=bad)
    assert msg and "UNKNOWN_TIMEOUT_MINUTES" in msg


@pytest.mark.parametrize("name,value", [("MAX_CONSECUTIVE_ERRORS", 0), ("UNKNOWN_TIMEOUT_MINUTES", 0.0)])
def test_enforce_risk_config_checks_the_new_limits(monkeypatch, name, value):
    monkeypatch.setattr(config, "CAPITAL_CAP_KRW", 200_000.0)
    monkeypatch.setattr(config, "MAX_DRAWDOWN_PCT", 5.0)
    monkeypatch.setattr(config, "DAILY_LOSS_LIMIT_PCT", 2.0)
    monkeypatch.setattr(config, "HALT_ACTION", "hold")
    monkeypatch.setattr(config, "IS_OVERSEAS_ENABLED", False)
    config.enforce_risk_config()                      # 대조: 새 값이 정상이면 통과한다
    monkeypatch.setattr(config, name, value)
    with pytest.raises(SystemExit):
        config.enforce_risk_config()


# ── 연속 오류 카운터(순수) ───────────────────────────────

def test_error_streak_counts_failures_and_resets_on_success():
    s = risk.ErrorStreak()
    assert s.count == 0
    assert s.failure() == 1
    assert s.failure() == 2
    s.success()
    assert s.count == 0


@pytest.mark.parametrize("count,limit,reached", [(4, 5, False), (5, 5, True), (6, 5, True)])
def test_error_streak_limit_boundary(count, limit, reached):
    assert risk.error_streak_reached(count, limit) is reached


def test_error_streak_limit_below_one_is_rejected():
    with pytest.raises(ValueError):
        risk.error_streak_reached(1, 0)


# ── UNKNOWN 시한(순수) ───────────────────────────────────

def test_stale_boundary_is_inclusive():
    orders = [_order("UNKNOWN", 119.9, id=1), _order("UNKNOWN", 120, id=2), _order("UNKNOWN", 121, id=3)]
    stale = risk.find_stale_unknown_orders(orders, now=NOW, timeout_minutes=120)
    assert [o["id"] for o in stale] == [2, 3]


def test_only_unknown_orders_can_be_stale():
    orders = [_order(s, 999, id=i) for i, s in enumerate(["SUBMITTED", "PARTIAL", "FILLED", "CANCELED"], start=1)]
    assert risk.find_stale_unknown_orders(orders, now=NOW, timeout_minutes=120) == []


def test_unreadable_creation_time_counts_as_stale():
    orders = [_order("UNKNOWN", 0, id=1, created_at=None),
              _order("UNKNOWN", 0, id=2, created_at="어제"),
              _order("UNKNOWN", 0, id=3, created_at="2026-11-03T11:59:00")]     # tz 없음: 신뢰하지 않는다
    stale = risk.find_stale_unknown_orders(orders, now=NOW, timeout_minutes=120)
    assert [o["id"] for o in stale] == [1, 2, 3]


def test_find_stale_requires_a_tz_aware_now():
    with pytest.raises(ValueError):
        risk.find_stale_unknown_orders([], now=datetime(2026, 11, 3, 12, 0), timeout_minutes=120)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_find_stale_rejects_a_bad_timeout(bad):
    with pytest.raises(ValueError):
        risk.find_stale_unknown_orders([], now=NOW, timeout_minutes=bad)


# ── UNKNOWN 시한: DB 와 중단 ─────────────────────────────

def test_stale_unknown_order_sets_a_risk_halt(conn, notes):
    _insert_order(conn, "UNKNOWN", 150, now=NOW)
    stale = risk.enforce_unknown_deadline(conn, now=NOW, timeout_minutes=120)
    assert len(stale) == 1
    assert risk.gate_status(conn)[0] == risk.ENTRIES_BLOCKED
    assert reconcile.halt_kind(conn) == "RISK"
    assert "UNKNOWN" in (reconcile.halt_reason(conn) or "")
    assert len(notes) == 1


def test_young_unknown_order_is_left_alone(conn, notes):
    _insert_order(conn, "UNKNOWN", 30, now=NOW)
    assert risk.enforce_unknown_deadline(conn, now=NOW, timeout_minutes=120) == []
    assert risk.gate_status(conn)[0] == risk.OPEN
    assert notes == []


def test_the_halt_is_recorded_once_not_every_cycle(conn, notes):
    _insert_order(conn, "UNKNOWN", 150, now=NOW)
    risk.enforce_unknown_deadline(conn, now=NOW, timeout_minutes=120)
    risk.enforce_unknown_deadline(conn, now=NOW + timedelta(hours=1), timeout_minutes=120)
    count = conn.execute("SELECT COUNT(*) AS n FROM halt_events").fetchone()["n"]
    assert count == 1
    assert len(notes) == 1


def test_a_state_halt_is_not_replaced(conn, notes):
    reconcile.halt_trading(conn, "상태 불일치(테스트)")                  # STATE
    _insert_order(conn, "UNKNOWN", 150, now=NOW)
    risk.enforce_unknown_deadline(conn, now=NOW, timeout_minutes=120)
    assert reconcile.halt_kind(conn) == "STATE"
    assert risk.gate_status(conn)[0] == risk.FULL_STOP


# ── main.job() 연결 ──────────────────────────────────────

def _win(orderable):
    return {"orderable": orderable, "label": "테스트", "reason": "테스트",
            "local_time": datetime(2026, 11, 3, 10, 0, tzinfo=KST)}


@pytest.fixture
def job_env(conn, bot, notes, monkeypatch):
    """job() 의 바깥 의존성(대조·매매 사이클·AI·시장 시간)만 대체한다. 판단·중단 로직은 건드리지 않는다."""
    env = types.SimpleNamespace(calls=[], gate_at_cycle=[], reconcile_error=None,
                                trading_error=None, orderable=True)
    monkeypatch.setattr(main, "_ERROR_STREAK", risk.ErrorStreak())
    monkeypatch.setattr(config, "MAX_CONSECUTIVE_ERRORS", 3)
    monkeypatch.setattr(config, "UNKNOWN_TIMEOUT_MINUTES", 120.0)
    monkeypatch.setattr(config, "ORDER_TIMEOUT_MINUTES", 30.0)

    def fake_reconcile(c, **kwargs):
        env.calls.append("reconcile")
        if env.reconcile_error is not None:
            raise env.reconcile_error

    def fake_cycle(ai_score, overseas_open=True, domestic_open=True):
        env.calls.append("cycle")
        env.gate_at_cycle.append(risk.gate_status(conn)[0])
        if env.trading_error is not None:
            raise env.trading_error

    monkeypatch.setattr(reconcile, "reconcile", fake_reconcile)
    monkeypatch.setattr(main, "run_trading_cycle", fake_cycle)
    monkeypatch.setattr(main, "analyze_market_sentiment", lambda: {"score": 1, "reason": "테스트"})
    monkeypatch.setattr(main, "market_windows", lambda now=None: {
        market_calendar.MARKET_US: _win(False), market_calendar.MARKET_KRX: _win(env.orderable)})
    return env


def test_consecutive_reconcile_failures_halt_entries_at_the_limit(conn, job_env):
    job_env.reconcile_error = RuntimeError("테스트")
    for _ in range(2):
        main.job()
    assert main._ERROR_STREAK.count == 2
    assert risk.gate_status(conn)[0] == risk.OPEN
    main.job()                                         # 3번째 = 한도(3)
    assert risk.gate_status(conn)[0] == risk.ENTRIES_BLOCKED
    assert reconcile.halt_kind(conn) == "RISK"
    assert "cycle" not in job_env.calls


def test_a_clean_cycle_resets_the_streak(conn, job_env):
    job_env.reconcile_error = RuntimeError("테스트")
    main.job()
    main.job()
    job_env.reconcile_error = None
    main.job()
    assert main._ERROR_STREAK.count == 0
    job_env.reconcile_error = RuntimeError("테스트")
    main.job()
    main.job()
    assert risk.gate_status(conn)[0] == risk.OPEN


@pytest.mark.parametrize("error", [ConnectionError("테스트"), TimeoutError("테스트"), RuntimeError("테스트")],
                         ids=["ConnectionError", "TimeoutError", "RuntimeError"])
def test_failures_of_the_trading_cycle_are_counted(conn, job_env, error):
    job_env.trading_error = error
    for _ in range(3):
        main.job()
    assert risk.gate_status(conn)[0] == risk.ENTRIES_BLOCKED


def test_database_unavailable_is_not_counted_as_an_api_error(conn, job_env):
    job_env.reconcile_error = db.DatabaseUnavailable("테스트")
    for _ in range(5):
        main.job()
    assert main._ERROR_STREAK.count == 0
    assert risk.gate_status(conn)[0] == risk.OPEN


def test_a_failed_streak_halt_is_critical_and_does_not_leak_its_text(conn, job_env, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("SECRET-TOKEN-123")

    monkeypatch.setattr(risk, "halt_for_error_streak", broken)
    job_env.reconcile_error = RuntimeError("테스트")
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        for _ in range(3):
            main.job()
    assert "중단을 기록하지 못했습니다" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "SECRET-TOKEN-123" not in caplog.text


def test_a_cycle_outside_the_order_window_counts_as_clean(conn, job_env):
    main._ERROR_STREAK.count = 2
    job_env.orderable = False
    main.job()
    assert main._ERROR_STREAK.count == 0
    assert "cycle" not in job_env.calls


def test_a_stale_unknown_order_halts_entries_before_the_trading_cycle(conn, job_env):
    _insert_order(conn, "UNKNOWN", 300)
    main.job()
    assert job_env.gate_at_cycle == [risk.ENTRIES_BLOCKED]
    assert "UNKNOWN" in (reconcile.halt_reason(conn) or "")


def test_young_unknown_and_old_non_unknown_orders_do_not_halt(conn, job_env):
    _insert_order(conn, "UNKNOWN", 60, symbol="379800")          # 30분(ORDER_TIMEOUT)은 넘고 120분(UNKNOWN 시한)은 안 넘는다
    _insert_order(conn, "SUBMITTED", 300, symbol="069500")
    main.job()
    assert job_env.gate_at_cycle == [risk.OPEN]


def test_a_failed_unknown_check_does_not_stop_the_cycle(conn, job_env, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("SECRET-TOKEN-123")

    monkeypatch.setattr(risk, "enforce_unknown_deadline", broken)
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        main.job()
    assert "cycle" in job_env.calls
    assert "UNKNOWN 주문 시한 점검 실패" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "SECRET-TOKEN-123" not in caplog.text
