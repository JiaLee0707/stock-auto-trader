"""
T08b: 노출·보유 조회 실패 시 **청산은 계속하고 매수만 보류**하는지 검증한다.

배경(왜 이 테스트가 필요한가)
  T08 까지 `_run_cycle` 은 카운터(노출·보유 종목 수)를 읽지 못하면 `return` 으로
  사이클을 통째로 끝냈다. 그러면 **손절까지 막힌다** — 노출 조회는 신규 매수의
  상한을 정하기 위한 것뿐인데, 그 실패가 이미 보유한 포지션의 손실을 방치한다.

  그래서 실패를 `counters=None` 으로 `strategy.decide()` 에 넘기고, `decide()` 가
  청산 판단(1번)을 마친 뒤 `HOLD(EXPOSURE_UNAVAILABLE)` 를 돌려주게 바꿨다.

  반면 **"포지션 자체를 못 읽었을 때 매도하지 않는다"(T03)** 는 규칙은 그대로다 —
  모르는 상태로 팔면 봇이 만들지 않은 보유까지 정리할 수 있다. 이 둘은 다른 규칙이며
  여기서 함께 고정한다.

실제 API 를 호출하지 않는다. `main.py` 도 실행하지 않는다.
"""
import logging

import pytest

import config
import db
import reconcile
import state
import trader


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def bot(conn):
    """`trader` 가 쓰는 전역 상태를 이 테스트의 DB 로 바꾼다."""
    st = state.reset_bot_state(conn)
    yield st
    state.reset_bot_state(None)


def _balance(qty=0.0, avg=0.0, profit=0.0, total=1_000_000.0, cash=1_000_000.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": total, "cash_krw": cash}


def _run(conn, monkeypatch, *, balance, price=23_220.0, submitted=None,
         ticker="379800", market="domestic"):
    """국내 1사이클을 테스트용 콜러블로 실행한다(주문은 실제로 나가지 않는다)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    submitted = submitted if submitted is not None else []
    trader._run_cycle(
        "국내", ticker, balance, price, 0, lambda p: f"{p:,.0f}원",
        sell_fn=None, buy_fn=None, market=market,
        submit_buy=lambda qty, p: submitted.append(("BUY", qty)),
        submit_sell=lambda reason, qty: submitted.append(("SELL", qty)),
        status_fn=None, cancel_fn=None,
        params={"capital_cap_krw": 0},
    )
    return submitted


def _break_exposure(monkeypatch):
    """
    노출·보유 조회를 실패시킨다 — `_exposure_snapshot` 안의 `db.list_positions`.

    `_run_cycle` 은 이 예외를 잡아 `counters=None` 으로 판단에 넘긴다.
    """
    def boom(*args, **kwargs):
        raise RuntimeError("DB 조회 실패(테스트)")

    real = db.list_positions
    monkeypatch.setattr(db, "list_positions", boom)
    return real


# 매수 신호가 나오는 확정 종가 시계열 (RSI ≤ 30 AND 종가 ≤ BB 하단).
_BUY_CLOSES = [100 + i * 0.5 for i in range(29)] + [95.0]


# ── (a) 노출 조회 실패 → 매수 보류 ────────────────────────

def test_buy_is_held_when_exposure_lookup_fails(conn, bot, monkeypatch, confirmed_bars):
    """
    노출을 모르면 **사지 않는다**(상한을 넘길 수 있다). 같은 조건에서 조회가 되면
    사는 것을 함께 확인해, "실패 때문에 막혔다"를 실제로 증명한다.
    """
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)
    real = _break_exposure(monkeypatch)

    submitted = _run(conn, monkeypatch, balance=_balance())
    assert submitted == [], "노출을 모르는 채로 매수했다"

    # 대조군: 조회가 정상이면 같은 조건에서 매수한다(자산 100만원 → 14주)
    monkeypatch.setattr(db, "list_positions", real)
    submitted = _run(conn, monkeypatch, balance=_balance())
    assert submitted and submitted[0][0] == "BUY", "조회 실패가 아니라 다른 이유로 막혔다"


# ── (b) 실패 중에도 손절은 발동 ───────────────────────────

def test_stop_loss_still_fires_when_exposure_lookup_fails(conn, bot, monkeypatch):
    """
    **이 변경의 핵심.** 청산은 노출 조회에 의존하지 않는다 — 손절이 막히면
    손실이 무한정 커진다. 조회 실패와 무관하게 매도는 나가야 한다.
    """
    # T08c: 청산 경로는 봇 소유(OPEN) 행이 있어야 매도한다. 노출 실패만 검증하려고 행을 넣는다.
    db.upsert_position(conn, "379800", "domestic", qty=3.0, avg_price=100.0, status="OPEN")
    _break_exposure(monkeypatch)
    submitted = _run(conn, monkeypatch,
                     balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert ("SELL", 3.0) in submitted, "노출 조회 실패로 손절까지 막혔다"


# ── (c) CRITICAL 로그 기록 ────────────────────────────────

def test_exposure_failure_logs_critical_with_exception_type(conn, bot, monkeypatch,
                                                            confirmed_bars, caplog):
    """
    실패마다 CRITICAL 로 남긴다 — ERROR 로 두면 "봇은 도는데 매수만 안 되는" 상태를
    사용자가 눈치채지 못한다. 예외 **타입**까지 남겨 원인을 로그만으로 구분한다.
    """
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)
    _break_exposure(monkeypatch)

    with caplog.at_level(logging.CRITICAL, logger="bot"):
        _run(conn, monkeypatch, balance=_balance())

    assert any(r.levelno == logging.CRITICAL for r in caplog.records), \
        "조회 실패가 CRITICAL 로 남지 않았다"
    assert "RuntimeError" in caplog.text, "예외 타입이 로그에 없다"
    assert "매수만 보류" in caplog.text, "무엇이 보류됐는지(매수)가 로그에 없다"


# ── (d) DB 가 없으면 모르는 채로 사지 않는다 ──────────────

def test_exposure_snapshot_raises_when_market_set_without_db(monkeypatch):
    """
    `market` 이 있는데 DB 가 없으면 (0, 0.0) 을 돌려주면 안 된다 —
    0 은 "노출 없음"으로 읽혀 상한이 사라진다. 모르면 실패로 올린다.
    `market=None`(DB 미사용 예전 경로)은 그대로 (0, 0.0) 이다.
    """
    monkeypatch.setattr(trader, "_state_conn", lambda: None)

    with pytest.raises(RuntimeError):
        trader._exposure_snapshot("overseas", "QQQ", 1_400.0)
    assert trader._exposure_snapshot(None, "QQQ") == (0, 0.0)


# ── (e) 포지션 자체를 못 읽으면 팔지 않는다 (T03, 회귀) ────

def test_sell_is_blocked_when_position_read_fails(conn, bot, monkeypatch):
    """
    상태를 못 읽었는데 매도하면 봇이 만들지 않은 보유까지 팔 수 있다.
    **모르면 팔지 않는다** — 노출 조회 실패와 달리 이쪽은 매도를 막는 것이 맞다.
    """
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)

    def boom(*args, **kwargs):
        raise RuntimeError("포지션 조회 실패(테스트)")

    monkeypatch.setattr(db, "get_position", boom)
    submitted = []

    trader._check_sell("국내", "379800", 3, profit_rate=-10.0, sell_fn=None,
                       market="domestic", avg_price=100.0,
                       submit_sell=lambda reason, qty: submitted.append(qty))
    assert submitted == [], "포지션 상태를 모르는 채로 매도했다"


def test_external_holding_is_not_sold(conn, bot, monkeypatch):
    """봇이 만들지 않은 보유(`EXTERNAL`)는 손절 조건이어도 팔지 않는다 (T03 회귀)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    db.upsert_position(conn, "379800", "domestic", qty=3, avg_price=100.0,
                       status="EXTERNAL")
    submitted = []

    trader._check_sell("국내", "379800", 3, profit_rate=-10.0, sell_fn=None,
                       market="domestic", avg_price=100.0,
                       submit_sell=lambda reason, qty: submitted.append(qty))
    assert submitted == [], "외부 보유를 손절 규칙으로 팔았다"


# ── (f) reconcile 중단 중에는 사이클을 돌리지 않는다 (회귀) ─

def test_halted_reconcile_skips_whole_cycle(conn, bot, monkeypatch):
    """
    불일치로 중단된 상태에서는 신규 매매를 하지 않는다 (T03). 이 경로는
    브로커 호출 **전에** 끝나야 한다 — 여기서 실패하면 실제 API 가 나간다.
    """
    def boom(*args, **kwargs):
        raise AssertionError("중단 중인데 사이클을 실행했다")

    monkeypatch.setattr(trader, "run_overseas_cycle", boom)
    monkeypatch.setattr(trader, "run_domestic_cycle", boom)

    reconcile.halt_trading(conn, "테스트 중단")
    trader.run_trading_cycle(0)      # 예외 없이 즉시 반환해야 한다
    assert reconcile.is_trading_halted(conn) is True
