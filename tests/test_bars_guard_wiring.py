"""확정 일봉 최신성 점검의 **연결** (T09b-6).

판정 함수(주말·휴장일·모르면 낡음)와 설정 검증은 test_bars_freshness.py 가 증명한다. 여기서는 연결만 증명한다.
  - `_run_cycle` 은 마지막 확정 봉의 날짜와 tz 있는 now 로 점검 함수를 **한 번** 부른다. 봉이 없거나 조회에 실패하면 부르지 않는다
  - 낡았다는 사유가 오면 신규 매수만 보류하고(사유가 판단에 그대로 남는다) **손절은 나간다**
  - 점검 함수가 예외를 내도 매수만 보류하고 손절은 나가며, 예외 본문은 로그에 남지 않는다
  - `run_domestic_cycle` 만 점검 함수를 넘기고, 그 함수는 KRX 달력과 호출 시점의 설정값을 쓴다
대조군은 이 시나리오가 실제로 매수·매도를 내는지 보여 준다. 실패하면 시나리오 가정이 틀린 것이다.
실제 API·알림은 쓰지 않는다. `main.py` 도 실행하지 않는다.
"""
import ast
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import config
import db
import history
import market_calendar
import models
import state
import strategy
import trader

SYMBOL, MARKET = "379800", "domestic"
KST = ZoneInfo("Asia/Seoul")
STALE = "확정 일봉이 오래됐습니다 (시험) — 매수 보류"

# 매수 신호가 나오는 확정 종가 시계열 (RSI ≤ 30 AND 종가 ≤ BB 하단) — test_loss_guard_wiring.py 와 같다.
_BUY_CLOSES = [100 + i * 0.5 for i in range(29)] + [95.0]


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
def limits(monkeypatch):
    """봇이 읽는 손실 한도 설정(자본 20만원, 낙폭 5%, 일일 2%)."""
    monkeypatch.setattr(config, "CAPITAL_CAP_KRW", 200_000.0)
    monkeypatch.setattr(config, "MAX_DRAWDOWN_PCT", 5.0)
    monkeypatch.setattr(config, "DAILY_LOSS_LIMIT_PCT", 2.0)


class _Guard:
    """점검 함수 대역: 호출 인자를 기록하고, 정해 둔 값을 돌려주거나 예외를 던진다."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result
        self._error = error

    def __call__(self, last_date, now):
        self.calls.append((last_date, now))
        if self._error is not None:
            raise self._error
        return self._result


def _balance(qty=0.0, avg=0.0, profit=0.0, total=1_000_000.0, cash=1_000_000.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": total, "cash_krw": cash}


def _seed_open(conn):
    db.upsert_position(conn, SYMBOL, MARKET, qty=3.0, avg_price=100.0, status="OPEN")


def _buy_setup(monkeypatch, confirmed_bars):
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars(SYMBOL, _BUY_CLOSES)


def _stop_loss_balance():
    return _balance(qty=3.0, avg=100.0, profit=-10.0)


def _run(conn, monkeypatch, *, balance, bars_guard=None, price=23_220.0):
    """국내 1사이클(주문은 실제로 나가지 않는다). 나간 주문 [(방향, 수량), ...] 을 돌려준다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    submitted = []
    trader._run_cycle(
        "국내", SYMBOL, balance, price, 0, lambda p: f"{p:,.0f}원",
        sell_fn=None, buy_fn=None, market=MARKET,
        submit_buy=lambda qty, p: submitted.append(("BUY", qty)),
        submit_sell=lambda reason, qty: submitted.append(("SELL", qty)),
        status_fn=None, cancel_fn=None,
        params={"capital_cap_krw": 0},
        bars_guard=bars_guard,
    )
    return submitted


def _spy_decisions(monkeypatch):
    """strategy.decide 를 그대로 부르되, 돌려준 판단을 기록한다."""
    real = strategy.decide
    seen = []

    def spy(*args, **kwargs):
        decision = real(*args, **kwargs)
        seen.append(decision)
        return decision

    monkeypatch.setattr(strategy, "decide", spy)
    return seen


def _sides(submitted):
    return [side for side, _ in submitted]


# ── _run_cycle: 점검 연결 ────────────────────────────────

def test_without_guard_the_buy_scenario_buys(conn, bot, monkeypatch, confirmed_bars):
    """대조군: 점검 함수를 안 넘기면 예전과 같다 — 이 시나리오는 실제로 산다."""
    _buy_setup(monkeypatch, confirmed_bars)
    assert _sides(_run(conn, monkeypatch, balance=_balance())) == ["BUY"]


def test_guard_is_called_once_with_last_bar_date_and_tz_aware_now(conn, bot, monkeypatch, confirmed_bars):
    _buy_setup(monkeypatch, confirmed_bars)
    guard = _Guard(None)
    submitted = _run(conn, monkeypatch, balance=_balance(), bars_guard=guard)
    assert len(guard.calls) == 1
    last_date, now = guard.calls[0]
    assert last_date == history.load_bars(SYMBOL)[-1]["date"]
    assert now.tzinfo is not None
    assert _sides(submitted) == ["BUY"]          # 신선하다고 답하면 대조군과 같이 산다


def test_stale_guard_holds_the_buy_and_keeps_the_reason(conn, bot, monkeypatch, confirmed_bars, caplog):
    _buy_setup(monkeypatch, confirmed_bars)
    decisions = _spy_decisions(monkeypatch)
    guard = _Guard(STALE)
    with caplog.at_level(logging.WARNING, logger="bot"):
        submitted = _run(conn, monkeypatch, balance=_balance(), bars_guard=guard)
    assert submitted == []
    assert len(guard.calls) == 1
    assert decisions and decisions[0].is_hold
    assert decisions[0].reason == models.HOLD_INDICATOR_UNAVAILABLE
    assert decisions[0].meta["detail"] == STALE
    assert STALE in caplog.text


def test_without_guard_the_stop_loss_sells_with_bars(conn, bot, monkeypatch, confirmed_bars):
    """대조군: 이 시나리오(보유 -10%)는 일봉이 있어도 실제로 손절이 나간다."""
    _buy_setup(monkeypatch, confirmed_bars)
    _seed_open(conn)
    assert _sides(_run(conn, monkeypatch, balance=_stop_loss_balance())) == ["SELL"]


def test_stale_guard_still_sells_the_stop_loss(conn, bot, monkeypatch, confirmed_bars):
    _buy_setup(monkeypatch, confirmed_bars)
    _seed_open(conn)
    guard = _Guard(STALE)
    submitted = _run(conn, monkeypatch, balance=_stop_loss_balance(), bars_guard=guard)
    assert len(guard.calls) == 1, "점검이 실행되지 않았다 — 이 시험은 아무것도 증명하지 못한다"
    assert _sides(submitted) == ["SELL"]


def test_guard_error_holds_the_buy_without_leaking_its_text(conn, bot, monkeypatch, confirmed_bars, caplog):
    _buy_setup(monkeypatch, confirmed_bars)
    decisions = _spy_decisions(monkeypatch)
    secret = "SECRET-TOKEN-123"
    guard = _Guard(error=RuntimeError(secret))
    with caplog.at_level(logging.WARNING, logger="bot"):
        submitted = _run(conn, monkeypatch, balance=_balance(), bars_guard=guard)
    assert submitted == []
    assert len(guard.calls) == 1
    assert decisions[0].reason == models.HOLD_INDICATOR_UNAVAILABLE
    assert "최신성을 확인하지 못했습니다" in decisions[0].meta["detail"]
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert secret not in decisions[0].meta["detail"]


def test_guard_error_still_sells_the_stop_loss(conn, bot, monkeypatch, confirmed_bars):
    _buy_setup(monkeypatch, confirmed_bars)
    _seed_open(conn)
    guard = _Guard(error=RuntimeError("시험"))
    submitted = _run(conn, monkeypatch, balance=_stop_loss_balance(), bars_guard=guard)
    assert len(guard.calls) == 1, "점검이 실행되지 않았다 — 이 시험은 아무것도 증명하지 못한다"
    assert _sides(submitted) == ["SELL"]


def test_guard_is_not_called_when_the_cache_is_empty(conn, bot, monkeypatch):
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    decisions = _spy_decisions(monkeypatch)
    guard = _Guard(STALE)
    submitted = _run(conn, monkeypatch, balance=_balance(), bars_guard=guard)
    assert submitted == []
    assert guard.calls == []
    assert "캐시 없음" in decisions[0].meta["detail"]


def test_guard_is_not_called_when_the_bars_lookup_fails(conn, bot, monkeypatch):
    monkeypatch.setattr(config, "USE_INDICATORS", True)

    def boom(symbol, now):
        raise RuntimeError("일봉 읽기 실패(시험)")

    monkeypatch.setattr(trader, "_fetch_confirmed_bars", boom)
    decisions = _spy_decisions(monkeypatch)
    guard = _Guard(STALE)
    submitted = _run(conn, monkeypatch, balance=_balance(), bars_guard=guard)
    assert submitted == []
    assert guard.calls == []
    assert "조회 실패" in decisions[0].meta["detail"]


# ── run_domestic_cycle: 배선 ─────────────────────────────

def _capture_domestic_cycle(conn, monkeypatch):
    """run_domestic_cycle 이 _run_cycle 에 넘기는 키워드 인자를 잡는다(test_loss_guard_wiring.py 와 같은 대역)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    monkeypatch.setattr(config, "DOMESTIC_TICKER", SYMBOL)
    monkeypatch.setattr(trader.kd, "get_domestic_balance", lambda ticker: {
        "quantity": 2.0, "avg_price_krw": 23_000.0, "profit_rate": -4.3,
        "total_asset_krw": 1_000_000.0, "cash_krw": 900_000.0})
    monkeypatch.setattr(trader.kd, "get_domestic_price", lambda ticker: 22_000.0)
    monkeypatch.setattr(trader.broker_module, "for_market", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(trader, "_run_cycle", lambda *a, **k: captured.update(k))
    db.upsert_position(conn, SYMBOL, MARKET, qty=2.0, avg_price=23_000.0, status="OPEN")
    trader.run_domestic_cycle(0)
    return captured


def test_run_domestic_cycle_hands_a_bars_guard_to_the_cycle(conn, limits, monkeypatch):
    captured = _capture_domestic_cycle(conn, monkeypatch)
    assert callable(captured.get("bars_guard"))


def test_domestic_bars_guard_uses_the_krx_calendar(conn, limits, monkeypatch):
    captured = _capture_domestic_cycle(conn, monkeypatch)
    seen = []

    def spy(market, start, end):
        seen.append(market)
        return 2

    monkeypatch.setattr(config, "BARS_MAX_LAG_SESSIONS", 0)
    monkeypatch.setattr(market_calendar, "trading_days_between", spy)
    detail = captured["bars_guard"]("2026-11-19", datetime(2026, 11, 23, 10, 0, tzinfo=KST))
    assert seen == [market_calendar.MARKET_KRX]
    assert detail is not None                    # 거래일 수 2 → 지연 1 > 허용 0


def test_domestic_bars_guard_reads_the_setting_at_call_time(conn, limits, monkeypatch):
    captured = _capture_domestic_cycle(conn, monkeypatch)
    last, now = "2026-11-19", datetime(2026, 11, 23, 10, 0, tzinfo=KST)    # 목요일 봉, 월요일 오전
    monkeypatch.setattr(config, "BARS_MAX_LAG_SESSIONS", 0)
    assert captured["bars_guard"](last, now) is not None
    monkeypatch.setattr(config, "BARS_MAX_LAG_SESSIONS", 1)
    assert captured["bars_guard"](last, now) is None


def test_only_run_domestic_cycle_passes_a_bars_guard():
    """해외 사이클은 점검 함수를 넘기지 않는다(해외는 꺼져 있고 KRX 달력으로 판정할 수 없다)."""
    tree = ast.parse(Path(trader.__file__).read_text(encoding="utf-8"))
    names = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "_run_cycle"
                        and any(kw.arg == "bars_guard" for kw in node.keywords)):
                    names.append(fn.name)
    assert names == ["run_domestic_cycle"]


# ── 실제 점검 함수로 끝까지 (T09b-6) ─────────────────────
# 위 시험은 점검 함수 대역을 쓴다. 여기서는 `_make_bars_guard` 가 만든 **실제** 점검 함수를 `_run_cycle` 에 넣고
# 디스크의 일봉 CSV → `_entry_signal_meta` 의 마지막 날짜 → 판정까지 이어서 확인한다.
# 시계는 고정한다(2026-11-23 월요일 10시 — 직전 거래일은 11-20 금요일, 11-21·22 는 주말).
from datetime import timedelta  # noqa: E402

_MON = datetime(2026, 11, 23, 10, 0, tzinfo=KST)


def _seed_bars(monkeypatch, tmp_path, last_day):
    """마지막 봉이 `last_day` 인 매수 신호 일봉 CSV 를 임시 캐시에 만든다."""
    monkeypatch.setattr(history, "BARS_DIR", tmp_path / "bars")
    start = last_day - timedelta(days=len(_BUY_CLOSES) - 1)
    rows = [{"date": (start + timedelta(days=i)).isoformat(), "open": c, "high": c, "low": c,
             "close": c, "volume": 1000.0} for i, c in enumerate(_BUY_CLOSES)]
    history.save_bars(SYMBOL, rows)


def _spy_stale_detail(monkeypatch):
    """실제 판정 함수를 그대로 부르되 돌려준 값을 기록한다(점검이 실제로 실행됐는지 확인용)."""
    real = trader._stale_bars_detail
    seen = []

    def spy(*args, **kwargs):
        seen.append(real(*args, **kwargs))
        return seen[-1]

    monkeypatch.setattr(trader, "_stale_bars_detail", spy)
    return seen


def _run_real_guard(conn, monkeypatch, *, balance):
    """`_make_bars_guard` 가 만든 실제 점검 함수로 1사이클을 돈다. 나간 주문 [(방향, 수량), ...] 을 돌려준다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    monkeypatch.setattr(config, "BARS_MAX_LAG_SESSIONS", 0)
    submitted = []
    trader._run_cycle(
        "국내", SYMBOL, balance, 23_220.0, 0, lambda p: f"{p:,.0f}원",
        sell_fn=None, buy_fn=None, market=MARKET,
        submit_buy=lambda qty, p: submitted.append(("BUY", qty)),
        submit_sell=lambda reason, qty: submitted.append(("SELL", qty)),
        status_fn=None, cancel_fn=None,
        params={"capital_cap_krw": 0},
        now=_MON,
        bars_guard=trader._make_bars_guard(market_calendar.MARKET_KRX),
    )
    return submitted


def test_real_guard_lets_the_buy_through_when_the_last_bar_is_the_previous_session(conn, bot, monkeypatch, tmp_path):
    _seed_bars(monkeypatch, tmp_path, datetime(2026, 11, 20).date())      # 금요일 봉, 월요일 오전
    seen = _spy_stale_detail(monkeypatch)
    assert _sides(_run_real_guard(conn, monkeypatch, balance=_balance())) == ["BUY"]
    assert seen == [None]


def test_real_guard_holds_the_buy_when_a_session_is_missing(conn, bot, monkeypatch, tmp_path, caplog):
    _seed_bars(monkeypatch, tmp_path, datetime(2026, 11, 19).date())      # 목요일 봉 — 금요일 봉이 없다
    decisions = _spy_decisions(monkeypatch)
    seen = _spy_stale_detail(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="bot"):
        submitted = _run_real_guard(conn, monkeypatch, balance=_balance())
    assert submitted == []
    assert len(seen) == 1 and seen[0] is not None
    assert decisions[0].reason == models.HOLD_INDICATOR_UNAVAILABLE
    assert "2026-11-19" in decisions[0].meta["detail"]
    assert "1거래일 누락" in decisions[0].meta["detail"]
    assert "오래됐습니다" in caplog.text


def test_real_guard_still_sells_the_stop_loss_on_stale_bars(conn, bot, monkeypatch, tmp_path):
    _seed_bars(monkeypatch, tmp_path, datetime(2026, 11, 19).date())
    _seed_open(conn)
    seen = _spy_stale_detail(monkeypatch)
    submitted = _run_real_guard(conn, monkeypatch, balance=_stop_loss_balance())
    assert len(seen) == 1 and seen[0] is not None, "점검이 낡음을 판정하지 않았다 — 이 시험은 아무것도 증명하지 못한다"
    assert _sides(submitted) == ["SELL"]
