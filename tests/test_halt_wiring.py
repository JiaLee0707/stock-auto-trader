"""
T09a-3: 킬스위치가 trader 에 실제로 연결돼 있다.

risk.py 단독 동작은 test_risk_gate.py 가 증명한다. 여기서는 **연결**을 증명한다.
  - 주문 직전 게이트가 orders 기록보다 앞에 있다(거부된 주문이 행을 남기지 않는다)
  - 거부(TradingHalted)가 일반 실패(except Exception)에 섞이지 않고 CRITICAL 로 남는다
  - STATE 중단·HALT 파일은 사이클을 건너뛰고, RISK 중단은 매수만 막고 청산은 계속한다
  - decide() 는 청산을 판단한 뒤 사유(TRADING_HALTED)와 함께 매수를 보류한다

판단 로직은 패치하지 않는다(strategy.decide 는 호출 기록만 감싼다). 실제 API 는 호출하지 않는다.
"""
import ast
import logging
import pathlib

import pytest

import config
import db
import exit_rules
import models
import reconcile
import risk
import state
import strategy
import trader

SYMBOL, MARKET = "379800", "domestic"
ROOT = pathlib.Path(trader.__file__).resolve().parent

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
def halt_file(tmp_path, monkeypatch):
    """이 테스트의 HALT 파일 경로(아직 만들지 않음)."""
    path = tmp_path / "HALT"
    monkeypatch.setattr(config, "HALT_FILE", str(path))
    return path


@pytest.fixture
def cycles(monkeypatch):
    """run_trading_cycle 이 부르는 시장별 사이클을 기록용으로 바꾼다(실제 API 호출 없음)."""
    calls = []
    monkeypatch.setattr(trader, "run_overseas_cycle", lambda ai: calls.append("overseas"))
    monkeypatch.setattr(trader, "run_domestic_cycle", lambda ai: calls.append("domestic"))
    monkeypatch.setattr(config, "IS_OVERSEAS_ENABLED", True)
    monkeypatch.setattr(config, "IS_DOMESTIC_ENABLED", True)
    return calls


def _seed_open(conn):
    db.upsert_position(conn, SYMBOL, MARKET, qty=3.0, avg_price=100.0, status="OPEN")


def _order_count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]


def _balance(qty=0.0, avg=0.0, profit=0.0, total=1_000_000.0, cash=1_000_000.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": total, "cash_krw": cash}


def _run(conn, monkeypatch, *, balance, price=23_220.0, submit_buy=None, submit_sell=None):
    """국내 1사이클(주문은 실제로 나가지 않는다). 나간 주문 [(방향, 수량), ...] 을 돌려준다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    submitted = []
    trader._run_cycle(
        "국내", SYMBOL, balance, price, 0, lambda p: f"{p:,.0f}원",
        sell_fn=None, buy_fn=None, market=MARKET,
        submit_buy=submit_buy or (lambda qty, p: submitted.append(("BUY", qty))),
        submit_sell=submit_sell or (lambda reason, qty: submitted.append(("SELL", qty))),
        status_fn=None, cancel_fn=None,
        params={"capital_cap_krw": 0},
    )
    return submitted


def _spy_decide(monkeypatch):
    """strategy.decide 를 그대로 부르되 결과를 기록한다(판단 로직은 건드리지 않는다)."""
    real = strategy.decide
    seen = []

    def spy(*args, **kwargs):
        decision = real(*args, **kwargs)
        seen.append(decision)
        return decision

    monkeypatch.setattr(strategy, "decide", spy)
    return seen


class _Probe:
    """_submit_order 의 make_body/send 가 불렸는지 센다."""

    def __init__(self):
        self.made = 0
        self.sent = 0

    def make_body(self):
        self.made += 1
        return {"ORD_UNPR": "100"}

    def send(self, body, on_uncertain):
        self.sent += 1
        return {"rt_cd": "0", "output": {"ODNO": "0000001", "KRX_FWDG_ORD_ORGNO": "91252"}}


def _submit(conn, side, probe, reason=None):
    return trader._submit_order(
        conn, market=MARKET, ticker=SYMBOL, side=side, qty=1, price=100.0,
        order_type="LIMIT", make_body=probe.make_body, send=probe.send, reason=reason,
    )


# ── A. _submit_order 게이트: orders 기록보다 앞 ───────────────

@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_submit_order_blocked_by_halt_file_leaves_no_order_row(conn, halt_file, side):
    halt_file.write_text("")
    probe = _Probe()
    with pytest.raises(risk.TradingHalted):
        _submit(conn, side, probe)
    assert _order_count(conn) == 0
    assert (probe.made, probe.sent) == (0, 0)


def test_submit_order_state_halt_blocks_exit_too_and_leaves_no_row(conn, halt_file):
    reconcile.halt_trading(conn, "불일치", "STATE")
    probe = _Probe()
    with pytest.raises(risk.TradingHalted):
        _submit(conn, "SELL", probe, reason="STOP_LOSS")
    assert _order_count(conn) == 0
    assert (probe.made, probe.sent) == (0, 0)


def test_submit_order_risk_halt_blocks_buy_but_lets_exit_through(conn, halt_file):
    reconcile.halt_trading(conn, "낙폭", "RISK")
    buy = _Probe()
    with pytest.raises(risk.TradingHalted):
        _submit(conn, "BUY", buy)
    assert _order_count(conn) == 0
    sell = _Probe()
    _submit(conn, "SELL", sell, reason="STOP_LOSS")
    assert sell.sent == 1 and _order_count(conn) == 1


def test_blocked_order_leaves_no_orphan_row_that_blocks_later_orders(conn, halt_file):
    """거부된 주문이 SUBMITTED 행을 남기면 has_open_order 가 이후 주문(손절 포함)을 막는다."""
    halt_file.write_text("")
    with pytest.raises(risk.TradingHalted):
        _submit(conn, "SELL", _Probe(), reason="STOP_LOSS")
    halt_file.unlink()
    probe = _Probe()
    _submit(conn, "SELL", probe, reason="STOP_LOSS")        # DuplicateOrderBlocked 가 나면 안 된다
    assert probe.sent == 1 and _order_count(conn) == 1


def test_submit_order_with_open_gate_creates_exactly_one_row(conn, halt_file):
    probe = _Probe()
    _submit(conn, "BUY", probe)
    assert (probe.made, probe.sent, _order_count(conn)) == (1, 1, 1)


def test_kill_switch_guard_is_the_first_statement_of_submit_order():
    """가드 위치를 구조로 고정한다 — 누가 has_open_order·사전 기록 뒤로 옮기면 실패한다."""
    tree = ast.parse((ROOT / "trader.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_submit_order")
    body = list(fn.body)
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]                                      # docstring
    heads = [ast.unparse(s) for s in body[:2]]
    assert heads[0].startswith("config.assert_order_allowed("), heads      # 실전 가드(T09a-4)
    assert heads[1].startswith("risk.assert_order_permitted("), heads      # 킬스위치 게이트


# ── B. TradingHalted 는 일반 실패와 구분돼 CRITICAL 로 남는다 ──

def test_exit_blocked_by_kill_switch_is_critical_not_a_generic_failure(conn, bot, monkeypatch, caplog):
    _seed_open(conn)

    def halted(reason, qty):
        raise risk.TradingHalted("SELL 주문 차단 — 테스트")

    with caplog.at_level(logging.INFO, logger="bot"):
        _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0), submit_sell=halted)
    messages = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(lvl == logging.CRITICAL and "킬스위치" in msg for lvl, msg in messages)
    assert not any("주문 실패" in msg for _, msg in messages)


def test_entry_blocked_by_kill_switch_is_critical_and_not_counted_as_a_buy(
        conn, bot, monkeypatch, confirmed_bars, caplog):
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars(SYMBOL, _BUY_CLOSES)

    def halted(qty, price):
        raise risk.TradingHalted("BUY 주문 차단 — 테스트")

    with caplog.at_level(logging.INFO, logger="bot"):
        _run(conn, monkeypatch, balance=_balance(), submit_buy=halted)
    messages = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(lvl == logging.CRITICAL and "킬스위치" in msg for lvl, msg in messages)
    assert not any("매수 주문 실패" in msg for _, msg in messages)
    assert bot.buy_count_today(SYMBOL) == 0


# ── C. _run_cycle: RISK 중단은 청산 유지·매수 보류 ─────────────

def test_risk_halt_still_lets_stop_loss_through(conn, bot, monkeypatch):
    _seed_open(conn)
    reconcile.halt_trading(conn, "낙폭", "RISK")
    submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert ("SELL", 3.0) in submitted


def test_risk_halt_holds_entries_with_trading_halted_reason(conn, bot, monkeypatch, confirmed_bars):
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars(SYMBOL, _BUY_CLOSES)
    reconcile.halt_trading(conn, "낙폭", "RISK")
    seen = _spy_decide(monkeypatch)
    submitted = _run(conn, monkeypatch, balance=_balance())
    assert submitted == []
    assert [d.reason for d in seen] == [models.HOLD_TRADING_HALTED]
    assert "낙폭" in seen[0].meta["detail"]


def test_same_entry_without_halt_buys(conn, bot, monkeypatch, confirmed_bars):
    """위 테스트의 대조군 — 중단만 없으면 같은 입력에서 매수가 나간다."""
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars(SYMBOL, _BUY_CLOSES)
    submitted = _run(conn, monkeypatch, balance=_balance())
    assert [s[0] for s in submitted] == ["BUY"]


# ── D. decide(): 청산 뒤 매수만 보류 ──────────────────────────

def _decide_params(**extra):
    return strategy.merge_params({"market": MARKET, "fx_rate": 1.0, "use_indicators": False, **extra})


def _snapshot(price):
    return models.MarketSnapshot(symbol=SYMBOL, bars=None, last_price=price, now=db.now_kst())


_ACCOUNT = models.AccountView(equity=1_000_000.0, cash=1_000_000.0)


def test_decide_holds_entries_when_blocked_reason_is_given():
    d = strategy.decide(_snapshot(23_220.0), models.PositionView(), _ACCOUNT,
                        _decide_params(entries_blocked_reason="[RISK] 낙폭"), models.DayCounters())
    assert d.reason == models.HOLD_TRADING_HALTED and not d.is_sell
    assert "낙폭" in d.meta["detail"]


def test_decide_still_exits_while_entries_are_blocked():
    position = models.PositionView(qty=3.0, avg_price=100.0)
    d = strategy.decide(_snapshot(90.0), position, _ACCOUNT,
                        _decide_params(entries_blocked_reason="[RISK] 낙폭", exit_price=90.0),
                        models.DayCounters())
    assert d.is_sell and d.reason == exit_rules.STOP_LOSS


def test_decide_blocked_reason_takes_precedence_over_exposure_failure():
    unblocked = strategy.decide(_snapshot(23_220.0), models.PositionView(), _ACCOUNT,
                                _decide_params(), None)
    blocked = strategy.decide(_snapshot(23_220.0), models.PositionView(), _ACCOUNT,
                              _decide_params(entries_blocked_reason="[STATE] 불일치"), None)
    assert unblocked.reason == models.HOLD_EXPOSURE_UNAVAILABLE
    assert blocked.reason == models.HOLD_TRADING_HALTED


@pytest.mark.parametrize("blank", [None, ""])
def test_decide_blank_blocked_reason_means_not_blocked(blank):
    d = strategy.decide(_snapshot(23_220.0), models.PositionView(), _ACCOUNT,
                        _decide_params(entries_blocked_reason=blank), None)
    assert d.reason == models.HOLD_EXPOSURE_UNAVAILABLE


# ── E. run_trading_cycle: 사이클 게이트 ───────────────────────

def test_open_gate_runs_both_markets(conn, bot, cycles, halt_file):
    trader.run_trading_cycle(0, True, True)
    assert cycles == ["overseas", "domestic"]


def test_state_halt_skips_the_cycle_and_tells_how_to_resume(conn, bot, cycles, halt_file, caplog):
    reconcile.halt_trading(conn, "불일치", "STATE")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bot"):
        trader.run_trading_cycle(0, True, True)
    assert cycles == []
    criticals = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any("python -m risk resume --reason" in m for m in criticals)


def test_halt_file_skips_the_cycle(conn, bot, cycles, halt_file):
    halt_file.write_text("")
    trader.run_trading_cycle(0, True, True)
    assert cycles == []


def test_risk_halt_keeps_cycles_running_and_logs_critical_every_cycle(conn, bot, cycles, halt_file, caplog):
    reconcile.halt_trading(conn, "낙폭", "RISK")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="bot"):
        trader.run_trading_cycle(0, True, True)
        trader.run_trading_cycle(0, True, True)
    assert cycles == ["overseas", "domestic", "overseas", "domestic"]
    notices = [r.getMessage() for r in caplog.records
               if r.levelno == logging.CRITICAL and "신규 매수 중단" in r.getMessage()]
    assert len(notices) == 2
    assert all("청산" in m for m in notices)


def test_unreadable_halt_state_skips_the_cycle(conn, bot, cycles, halt_file, monkeypatch):
    def boom(c):
        raise RuntimeError("db gone")

    monkeypatch.setattr(reconcile, "is_trading_halted", boom)
    trader.run_trading_cycle(0, True, True)
    assert cycles == []


# ── F. 사유 없이 해제하라는 안내가 소스에 남지 않는다 ─────────

def test_no_instruction_tells_the_user_to_resume_without_a_reason():
    banned = ("resume_trading(c)", "resume_trading(conn)", "resume_trading()")
    hits = []
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {b}" for b in banned if b in text]
    assert hits == [], f"사유 없이 해제하는 안내가 남아 있습니다: {hits}"


# ── G. 실전 가드(config.assert_order_allowed)도 orders 기록보다 앞이다 (T09a-4) ──

def _refuse_live():
    raise RuntimeError("실전 확인값 없음(테스트)")


def test_live_guard_rejection_leaves_no_order_row(conn, halt_file, monkeypatch):
    monkeypatch.setattr(config, "assert_order_allowed", _refuse_live)
    probe = _Probe()
    with pytest.raises(RuntimeError, match="실전 확인값"):
        _submit(conn, "SELL", probe, reason="STOP_LOSS")
    assert _order_count(conn) == 0
    assert (probe.made, probe.sent) == (0, 0)


def test_live_guard_rejection_does_not_block_later_orders(conn, halt_file, monkeypatch):
    """거부된 주문이 SUBMITTED 행을 남기면 has_open_order 가 이후 주문(손절 포함)을 막는다."""
    with monkeypatch.context() as m:
        m.setattr(config, "assert_order_allowed", _refuse_live)
        with pytest.raises(RuntimeError):
            _submit(conn, "SELL", _Probe(), reason="STOP_LOSS")
    probe = _Probe()
    _submit(conn, "SELL", probe, reason="STOP_LOSS")        # DuplicateOrderBlocked 가 나면 안 된다
    assert probe.sent == 1 and _order_count(conn) == 1
