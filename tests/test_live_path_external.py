"""T08c: 실제 청산 경로(_run_cycle → decide → _execute_sell)가 봇이 만든 OPEN 포지션만
판다는 것을 증명한다. 판단 로직은 패치하지 않는다 — 실제 decide() 가 SELL 을 내는
입력(손익 ±10%)만 쓴다.
"""
import logging

import pytest

import config
import db
import state
import trader


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def bot(conn):
    """`trader`가 쓰는 전역 상태를 이 테스트의 DB로 바꾼다."""
    st = state.reset_bot_state(conn)
    yield st
    state.reset_bot_state(None)


def _balance(qty=0.0, avg=0.0, profit=0.0, total=1_000_000.0, cash=1_000_000.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": total, "cash_krw": cash}


def _run(conn, monkeypatch, *, ticker="379800", market="domestic",
         balance=None, profit_rate=-10.0, submitted=None):
    """국내 1사이클을 테스트용 콜러블로 실행(주문은 실제로 나가지 않는다)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    submitted = submitted if submitted is not None else []
    trader._run_cycle(
        "국내", ticker, balance if balance is not None else _balance(),
        23220.0, 0, lambda p: f"{p:,.0f}원",
        sell_fn=None, buy_fn=None, market=market,
        submit_buy=lambda qty, p: submitted.append(("BUY", qty)),
        submit_sell=lambda reason, qty: submitted.append(("SELL", qty)),
        status_fn=None, cancel_fn=None,
        params={"capital_cap_krw": 0},
    )
    return submitted


def _seed(conn, status):
    db.upsert_position(conn, "379800", "domestic", qty=3.0, avg_price=100.0, status=status)

def _messages(caplog, min_level):
    return [r.getMessage() for r in caplog.records if r.levelno >= min_level]


def test_a_external_stop_loss_is_not_sold(conn, bot, monkeypatch, caplog):
    _seed(conn, "EXTERNAL")
    with caplog.at_level(logging.WARNING):
        submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert submitted == []
    assert any("EXTERNAL" in m for m in _messages(caplog, logging.WARNING))


def test_b_external_take_profit_is_not_sold(conn, bot, monkeypatch):
    _seed(conn, "EXTERNAL")
    submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=10.0))
    assert submitted == []


def test_b2_open_take_profit_is_sold(conn, bot, monkeypatch):
    """(b)의 대조군 — 같은 목표가 입력이 OPEN 이면 실제로 매도가 나간다."""
    _seed(conn, "OPEN")
    submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=10.0))
    assert len(submitted) == 1 and submitted[0][0] == "SELL"


def test_c_open_stop_loss_is_sold_once(conn, bot, monkeypatch):
    """대조군 — 구성이 실제로 매도를 낼 수 있음을 증명한다."""
    _seed(conn, "OPEN")
    submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert len(submitted) == 1 and submitted[0][0] == "SELL"


def test_d_position_read_failure_is_not_sold(conn, bot, monkeypatch, caplog):
    _seed(conn, "OPEN")
    def boom(*args, **kwargs):
        raise RuntimeError("DB 조회 실패(테스트)")
    monkeypatch.setattr(db, "get_position", boom)
    with caplog.at_level(logging.ERROR):
        submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert submitted == []
    assert any("매도하지 않습니다" in m for m in _messages(caplog, logging.ERROR))


def test_e_missing_row_with_broker_holding_is_not_sold(conn, bot, monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert submitted == []
    assert any("positions 기록이 없습니다" in m for m in _messages(caplog, logging.ERROR))


def test_f_closed_row_with_broker_holding_is_not_sold(conn, bot, monkeypatch, caplog):
    _seed(conn, "CLOSED")
    with caplog.at_level(logging.WARNING):
        submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0))
    assert submitted == []
    assert any("CLOSED" in m for m in _messages(caplog, logging.WARNING))


# ── T08d: 매수가 EXTERNAL 보유 위에 쌓이지 않는다 ─────────────────
# 체결 반영(_apply_fill_to_position)은 기존 행의 status 를 보지 않고 OPEN 으로 덮어쓴다.
# 그래서 EXTERNAL 보유 위에 매수가 체결되면 외부 보유분까지 봇 소유가 된다.
# 판단 로직은 패치하지 않는다 — 실제 decide() 가 BUY 를 내는 입력만 쓴다
# (확정 종가 시계열은 test_exposure_failure.py 와 같다).
_BUY_CLOSES = [100 + i * 0.5 for i in range(29)] + [95.0]


def _t08d_seed(conn, status, qty=3.0):
    db.upsert_position(conn, "379800", "domestic", qty=qty, avg_price=23_220.0, status=status)


def _t08d_msgs(caplog, min_level):
    return [r.getMessage() for r in caplog.records if r.levelno >= min_level]


def test_g_fresh_entry_buys_once(conn, bot, monkeypatch, confirmed_bars):
    """대조군 — 행 없음 + 보유 0 + 매수 조건이면 매수가 정확히 1회 나간다."""
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)
    submitted = _run(conn, monkeypatch, balance=_balance())
    assert [s[0] for s in submitted] == ["BUY"]


def test_h_external_holding_is_not_bought_into(conn, bot, monkeypatch, confirmed_bars, caplog):
    _t08d_seed(conn, "EXTERNAL")
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)
    with caplog.at_level(logging.WARNING):
        submitted = _run(conn, monkeypatch,
                         balance=_balance(qty=3.0, avg=23_220.0, profit=0.0))
    assert submitted == []
    assert any("EXTERNAL" in m for m in _t08d_msgs(caplog, logging.WARNING))


def test_h2_open_holding_with_same_input_buys(conn, bot, monkeypatch, confirmed_bars):
    """(h)의 대조군 — 같은 입력에서 상태만 OPEN 이면 매수가 나간다.
    h 가 막힌 이유가 EXTERNAL 상태 때문임을 증명한다(decide() 가 보유 중이라 막는 게 아님)."""
    _t08d_seed(conn, "OPEN")
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)
    submitted = _run(conn, monkeypatch,
                     balance=_balance(qty=3.0, avg=23_220.0, profit=0.0))
    assert [s[0] for s in submitted] == ["BUY"]


def test_i_position_read_failure_is_not_bought(conn, bot, monkeypatch, confirmed_bars, caplog):
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)

    def boom(*args, **kwargs):
        raise RuntimeError("DB 조회 실패(테스트)")

    monkeypatch.setattr(db, "get_position", boom)
    with caplog.at_level(logging.ERROR):
        submitted = _run(conn, monkeypatch, balance=_balance())
    assert submitted == []
    assert any("매수하지 않습니다" in m for m in _t08d_msgs(caplog, logging.ERROR))


def test_j_closed_row_allows_reentry(conn, bot, monkeypatch, confirmed_bars):
    """CLOSED 행(전량 청산 뒤)은 재진입을 막지 않는다."""
    _t08d_seed(conn, "OPEN")
    db.close_position(conn, "379800", "domestic")
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    confirmed_bars("379800", _BUY_CLOSES)
    submitted = _run(conn, monkeypatch, balance=_balance())
    assert [s[0] for s in submitted] == ["BUY"]
