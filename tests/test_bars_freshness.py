"""확정 일봉 최신성 점검 (T09b-6) — 판정 함수·설정 검증·픽스처.

낡은 일봉으로 지표를 계산하면 이미 지난 신호로 주문이 나간다. 마지막 확정 봉이 직전 거래일보다 허용 거래일 수를 넘겨
오래됐거나 최신성을 확인하지 못하면 신규 매수만 보류한다(청산은 계속). 여기서는 판정 함수와 설정 검증을 고정한다.
날짜는 D-0/D-1 점검에서 달력으로 확인한 2026-11-19(목)·20(금)·23(월)·24(화)와 주말(21·22)을 쓴다.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
import db
import history
import market_calendar
import trader

KST = ZoneInfo("Asia/Seoul")
KRX = market_calendar.MARKET_KRX


def _now(day, hour=10):
    return datetime(2026, 11, day, hour, 0, tzinfo=KST)


def _detail(last, now, max_lag=0):
    return trader._stale_bars_detail(last, now, calendar_market=KRX, max_lag=max_lag)


def test_fresh_when_last_bar_is_previous_session():
    assert _detail("2026-11-19", _now(20)) is None


def test_weekend_is_not_counted():
    assert _detail("2026-11-20", _now(23)) is None


def test_missing_session_is_stale_without_allowance():
    detail = _detail("2026-11-19", _now(23))
    assert detail is not None
    assert "1거래일 누락" in detail


def test_allowance_covers_missing_sessions():
    assert _detail("2026-11-19", _now(23), max_lag=1) is None
    assert _detail("2026-11-19", _now(24), max_lag=1) is not None
    assert _detail("2026-11-19", _now(24), max_lag=2) is None


def test_last_bar_on_non_session_day_holds():
    detail = _detail("2026-11-21", _now(23))
    assert detail is not None
    assert "거래일이 아니거나" in detail


@pytest.mark.parametrize("last", [None, "", "not-a-date"])
def test_unknown_or_malformed_last_date_holds(last):
    assert _detail(last, _now(20)) is not None


def test_calendar_failure_zero_is_not_treated_as_fresh(monkeypatch):
    monkeypatch.setattr(market_calendar, "trading_days_between", lambda *a, **k: 0)
    assert _detail("2026-11-19", _now(20)) is not None


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        _detail("2026-11-19", datetime(2026, 11, 20, 10, 0))


def test_now_is_converted_to_market_timezone():
    # 2026-11-20 16:00 UTC = 2026-11-21 01:00 KST(토). 변환하지 않으면 날짜가 같은 20일이라 0 이 나와 보류된다.
    assert _detail("2026-11-20", datetime(2026, 11, 20, 16, 0, tzinfo=ZoneInfo("UTC"))) is None


_VALID = dict(capital_cap_krw=200000, max_drawdown_pct=5.0, daily_loss_limit_pct=2.0,
              halt_action="hold", overseas_enabled=False)


@pytest.mark.parametrize("value", [0, 1, 5])
def test_bars_max_lag_accepts_valid_values(value):
    assert config.risk_config_error(**_VALID, bars_max_lag_sessions=value) is None


@pytest.mark.parametrize("value", [-1, 6, 1.5, True, "1"])
def test_bars_max_lag_rejects_invalid_values(value):
    error = config.risk_config_error(**_VALID, bars_max_lag_sessions=value)
    assert error is not None
    assert "BARS_MAX_LAG_SESSIONS" in error


def test_bars_max_lag_none_is_not_checked():
    assert config.risk_config_error(**_VALID, bars_max_lag_sessions=None) is None


def test_enforce_risk_config_passes_bars_max_lag(monkeypatch):
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(config, "risk_config_error", spy)
    monkeypatch.setattr(config, "BARS_MAX_LAG_SESSIONS", 3)
    config.enforce_risk_config()
    assert seen["bars_max_lag_sessions"] == 3


@pytest.fixture
def monday_clock(monkeypatch):
    fixed = datetime(2026, 11, 23, 10, 0, tzinfo=KST)   # 월요일: 달력상 어제는 일요일
    monkeypatch.setattr(db, "now_kst", lambda: fixed)
    return fixed


def test_fixture_bars_end_on_last_krx_session_even_on_monday(monday_clock, confirmed_bars):
    confirmed_bars("379800", [100.0] * 30)
    bars = history.get_confirmed_bars("379800", as_of=monday_clock)
    assert bars[-1]["date"] == "2026-11-20"
    assert _detail(bars[-1]["date"], monday_clock) is None
