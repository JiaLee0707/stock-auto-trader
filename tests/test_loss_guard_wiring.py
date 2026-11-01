"""
T09b-2b-2: 손실 한도 평가가 `trader` 에 실제로 연결돼 있다.

risk.py 단독 동작(기준선·한도·중단)은 test_loss_limits.py / test_loss_baseline.py 가 증명한다.
여기서는 **연결**만 증명한다.
  - `_run_cycle` 은 `loss_guard` 를 판단 **전에** 부르고, 새로 걸린 RISK 중단이 **같은 사이클의** 매수를 막는다
  - 평가가 실패하면(EquityUnavailable·예상 밖 오류) 신규 매수만 보류하고 **손절은 나간다**
  - 이미 걸린 중단의 사유를 평가 실패 사유로 덮어쓰지 않는다
  - 예상 밖 오류의 본문을 로그에 남기지 않는다(`str(e)` 금지 — requests 예외에 URL·토큰이 들어간다)
  - `_make_loss_guard` 는 DB OPEN 행·누적 실현손익·손익 미확인 건수·모드·한도를 올바르게 넘긴다
  - `run_domestic_cycle` 이 실제로 `loss_guard` 를 넘긴다
대조군(`..._without_guard_...`)은 시나리오 자체가 매수·매도를 내는지 보여 준다. 이게 실패하면 시나리오 가정이 틀린 것이다.
실제 API·알림은 쓰지 않는다. `main.py` 도 실행하지 않는다.
"""
import json
import logging

import pytest

import config
import db
import reconcile
import risk
import state
import strategy
import trader

SYMBOL, MARKET = "379800", "domestic"
CAP = 200_000.0

# 매수 신호가 나오는 확정 종가 시계열 (RSI ≤ 30 AND 종가 ≤ BB 하단) — test_exposure_failure.py 와 같다.
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
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile.notifier, "notify",
                        lambda level, title, message="": calls.append((level, title, message)) or True)
    return calls


@pytest.fixture
def limits(monkeypatch):
    """봇이 읽는 손실 한도 설정(자본 20만원, 낙폭 5% = 10,000원, 일일 2% = 4,000원)."""
    monkeypatch.setattr(config, "CAPITAL_CAP_KRW", CAP)
    monkeypatch.setattr(config, "MAX_DRAWDOWN_PCT", 5.0)
    monkeypatch.setattr(config, "DAILY_LOSS_LIMIT_PCT", 2.0)


def _balance(qty=0.0, avg=0.0, profit=0.0, total=1_000_000.0, cash=1_000_000.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": total, "cash_krw": cash}


def _seed_open(conn):
    db.upsert_position(conn, SYMBOL, MARKET, qty=3.0, avg_price=100.0, status="OPEN")


def _buy_setup(monkeypatch, confirmed_bars):
    """매수 신호가 나오는 상태(test_exposure_failure.py 와 같은 전제)."""
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars(SYMBOL, _BUY_CLOSES)


def _stop_loss_setup(conn):
    """보유 3주가 -10% — 손절이 나가야 하는 상태."""
    _seed_open(conn)
    return _balance(qty=3.0, avg=100.0, profit=-10.0)


def _run(conn, monkeypatch, *, balance, price=23_220.0, loss_guard=None):
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
        loss_guard=loss_guard,
    )
    return submitted


def _spy_decide(monkeypatch):
    """strategy.decide 를 그대로 부르되, 판단에 넘어간 entries_blocked_reason 을 기록한다."""
    real = strategy.decide
    seen = []

    def spy(*args, **kwargs):
        seen.append(args[3].get("entries_blocked_reason"))
        return real(*args, **kwargs)

    monkeypatch.setattr(strategy, "decide", spy)
    return seen


def _sides(submitted):
    return [side for side, _ in submitted]


# ── _run_cycle: loss_guard 연결 ──────────────────────────

def test_without_guard_the_buy_scenario_buys(conn, bot, monkeypatch, confirmed_bars):
    """대조군: loss_guard 를 안 넘기면 예전과 같다 — 이 시나리오는 실제로 산다."""
    _buy_setup(monkeypatch, confirmed_bars)
    assert _sides(_run(conn, monkeypatch, balance=_balance())) == ["BUY"]


def test_guard_runs_once_before_the_decision_with_a_tz_aware_now(conn, bot, monkeypatch):
    order, seen_now = [], []
    real = strategy.decide

    def spy(*args, **kwargs):
        order.append("decide")
        return real(*args, **kwargs)

    monkeypatch.setattr(strategy, "decide", spy)

    def guard(now):
        order.append("guard")
        seen_now.append(now)

    _run(conn, monkeypatch, balance=_balance(), loss_guard=guard)
    assert order == ["guard", "decide"]
    assert seen_now[0].tzinfo is not None


def test_a_new_risk_halt_blocks_the_buy_of_the_same_cycle(conn, bot, notes, monkeypatch, confirmed_bars):
    _buy_setup(monkeypatch, confirmed_bars)
    reasons = _spy_decide(monkeypatch)

    def guard(now):
        reconcile.halt_trading(conn, "[손실 한도] 테스트", "RISK", details={"reasons": ["DRAWDOWN"]})
        return risk.LimitOutcome(None, ("DRAWDOWN",), True, False)

    submitted = _run(conn, monkeypatch, balance=_balance(), loss_guard=guard)
    assert submitted == []
    assert reasons and reasons[0]                      # 중단 사유가 판단에 넘어갔다
    assert reasons[0] != trader.LOSS_GUARD_UNAVAILABLE_REASON
    assert risk.gate_status(conn)[0] != risk.OPEN


def test_equity_unavailable_holds_the_buy(conn, bot, monkeypatch, confirmed_bars, caplog):
    _buy_setup(monkeypatch, confirmed_bars)
    reasons = _spy_decide(monkeypatch)

    def guard(now):
        raise risk.EquityUnavailable("domestic 379800 시세를 몰라 평가할 수 없습니다")

    with caplog.at_level(logging.CRITICAL, logger="bot"):
        submitted = _run(conn, monkeypatch, balance=_balance(), loss_guard=guard)
    assert submitted == []
    assert reasons == [trader.LOSS_GUARD_UNAVAILABLE_REASON]
    assert "손실 한도를 평가하지 못했습니다" in caplog.text


def test_unexpected_guard_error_holds_the_buy_without_leaking_its_text(
        conn, bot, monkeypatch, confirmed_bars, caplog):
    _buy_setup(monkeypatch, confirmed_bars)
    reasons = _spy_decide(monkeypatch)
    secret = "SECRET-TOKEN-123"

    def guard(now):
        raise RuntimeError(secret)

    with caplog.at_level(logging.CRITICAL, logger="bot"):
        submitted = _run(conn, monkeypatch, balance=_balance(), loss_guard=guard)
    assert submitted == []
    assert reasons == [trader.LOSS_GUARD_UNAVAILABLE_REASON]
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_without_guard_the_stop_loss_sells(conn, bot, monkeypatch):
    """대조군: 이 시나리오(보유 -10%)는 실제로 손절이 나간다."""
    balance = _stop_loss_setup(conn)
    assert _sides(_run(conn, monkeypatch, balance=balance)) == ["SELL"]


@pytest.mark.parametrize("error", [risk.EquityUnavailable("테스트"), RuntimeError("테스트")],
                         ids=["EquityUnavailable", "RuntimeError"])
def test_stop_loss_still_sells_when_the_guard_fails(conn, bot, monkeypatch, error):
    balance = _stop_loss_setup(conn)

    def guard(now):
        raise error

    assert _sides(_run(conn, monkeypatch, balance=balance, loss_guard=guard)) == ["SELL"]


def test_an_existing_halt_reason_is_not_replaced_by_the_failure_reason(
        conn, bot, notes, monkeypatch, confirmed_bars):
    _buy_setup(monkeypatch, confirmed_bars)
    reconcile.halt_trading(conn, "[손실 한도] 이미 걸린 중단", "RISK", details={"reasons": ["DRAWDOWN"]})
    reasons = _spy_decide(monkeypatch)

    def guard(now):
        raise risk.EquityUnavailable("테스트")

    submitted = _run(conn, monkeypatch, balance=_balance(), loss_guard=guard)
    assert submitted == []
    assert len(reasons) == 1
    assert reasons[0] and reasons[0] != trader.LOSS_GUARD_UNAVAILABLE_REASON


# ── _make_loss_guard: 입력 ───────────────────────────────

def _guard(conn, price=22_000.0):
    return trader._make_loss_guard(conn, ticker=SYMBOL, market=MARKET, current_price=price)


def _baseline(conn):
    return json.loads(db.get_kv(conn, risk.KV_LOSS_BASELINE))


def test_guard_values_open_db_rows_at_the_cycle_price(conn, limits):
    db.upsert_position(conn, SYMBOL, MARKET, qty=2.0, avg_price=23_000.0, status="OPEN")
    outcome = _guard(conn, 22_000.0)(db.now_kst())
    assert _baseline(conn)["last_equity"] == pytest.approx(CAP - 2_000.0)
    assert outcome.reasons == ()
    assert risk.gate_status(conn)[0] == risk.OPEN


def test_guard_ignores_external_holdings(conn, limits):
    db.upsert_position(conn, SYMBOL, MARKET, qty=5.0, avg_price=20_000.0, status="EXTERNAL")
    _guard(conn, 22_000.0)(db.now_kst())
    assert _baseline(conn)["last_equity"] == pytest.approx(CAP)


def test_guard_adds_cumulative_realized_pnl(conn, limits, monkeypatch):
    monkeypatch.setattr(db, "sum_realized_pnl", lambda c, **kw: -3_000.0)
    _guard(conn)(db.now_kst())
    assert _baseline(conn)["last_equity"] == pytest.approx(CAP - 3_000.0)


def test_guard_passes_the_unknown_pnl_count(conn, limits, notes, monkeypatch):
    monkeypatch.setattr(db, "count_unknown_pnl", lambda c, **kw: 1)
    outcome = _guard(conn)(db.now_kst())
    assert risk.PNL_UNKNOWN in outcome.reasons
    assert outcome.halted_now
    assert risk.gate_status(conn)[0] != risk.OPEN


@pytest.mark.parametrize("is_mock,label", [(True, "MOCK"), (False, "LIVE")])
def test_guard_labels_the_mode_and_passes_the_configured_limits(conn, limits, monkeypatch, is_mock, label):
    monkeypatch.setattr(config, "IS_MOCK", is_mock)
    seen = {}

    def fake(c, **kw):
        seen.update(kw)
        return risk.LimitOutcome(None, (), False, False)

    monkeypatch.setattr(risk, "evaluate_and_enforce", fake)
    _guard(conn)(db.now_kst())
    assert seen["mode"] == label
    assert seen["capital_cap_krw"] == CAP
    assert seen["max_drawdown_pct"] == 5.0
    assert seen["daily_loss_limit_pct"] == 2.0
    assert seen["equity"] == pytest.approx(CAP)
    assert seen["unknown_pnl_count"] == 0


def test_equity_log_accepts_the_live_label(conn):
    """실전 모드 라벨 'LIVE' 를 DB 가 받는지(실전 코드는 실행하지 않는다 — 라벨만 넘긴다)."""
    out = risk.evaluate_and_enforce(
        conn, equity=CAP, unknown_pnl_count=0, now=db.now_kst(), mode="LIVE",
        capital_cap_krw=CAP, max_drawdown_pct=5.0, daily_loss_limit_pct=2.0,
    )
    assert out.reasons == ()


def test_guard_does_not_guess_a_missing_price(conn, limits):
    db.upsert_position(conn, SYMBOL, MARKET, qty=2.0, avg_price=23_000.0, status="OPEN")
    with pytest.raises(risk.EquityUnavailable):
        _guard(conn, 0.0)(db.now_kst())
    assert db.get_kv(conn, risk.KV_LOSS_BASELINE) is None


# ── run_domestic_cycle: 배선 ─────────────────────────────

def test_run_domestic_cycle_hands_a_working_guard_to_the_cycle(conn, limits, monkeypatch):
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

    guard = captured.get("loss_guard")
    assert callable(guard)
    guard(db.now_kst())
    assert _baseline(conn)["last_equity"] == pytest.approx(CAP - 2_000.0)   # run_domestic_cycle 의 시세가 쓰였다
