"""T09e: 재진입(청산 뒤 다시 매수)은 이전 진입의 진입 시각·진입 신호·고점을 이어받지 않는다.

배경: `close_position` 은 이력 보존을 위해 CLOSED 행의 진입 시각·진입 신호·고점을 지우지 않는다.
2026-11-25 모의 시험에서 이날 다시 산 포지션의 `entry_time` 이 11/23 값으로 남는 것이 관찰됐다.
보유 거래일(시간청산)과 트레일링 고점은 이 값들로 계산하므로, 이어받으면 새 진입이 옛 기준으로 평가된다.
증명하는 것:
  - CLOSED 행(또는 수량 0 행) 위의 매수 체결은 새 진입이다: 진입 시각은 지금, 진입 신호는 이번 값, 고점은 비어 있다
  - 열려 있는 포지션의 분할 매수·부분 매도는 첫 진입의 값을 유지한다
  - 외부 보유(EXTERNAL) 행의 동작은 바꾸지 않는다(범위 밖)
  - 주문 체결 반영 경로(`_resolve_open_orders`)에서도 같다
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

import db
import state
import trader

SYMBOL = "379800"
MARKET = trader.MARKET_DOMESTIC
OLD_TIME = "2026-11-23T12:00:26+09:00"
OLD_SIGNAL = {"rsi": 25.0}
OLD_PEAK = 22_500.0


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    state.reset_bot_state(c)
    yield c
    state.reset_bot_state(None)
    c.close()


def _seed(conn, *, qty, status, avg=21_700.0):
    db.upsert_position(conn, SYMBOL, MARKET, qty=qty, avg_price=avg, entry_time=OLD_TIME,
                       entry_signal=OLD_SIGNAL, peak_price_since_entry=OLD_PEAK, status=status)


def _buy(conn, *, qty, price, prev_qty, prev_avg, signal=None):
    trader._apply_fill_to_position(conn, market=MARKET, ticker=SYMBOL, side="BUY", qty=qty, price=price,
                                   prev_qty=prev_qty, prev_avg=prev_avg, entry_signal=signal)


def _assert_entered_just_now(text):
    entered = datetime.fromisoformat(text)
    assert entered.tzinfo is not None
    assert abs((db.now_kst() - entered).total_seconds()) < 60


def test_reentry_after_a_close_is_a_new_entry(conn):
    _seed(conn, qty=1.0, status="OPEN", avg=21_350.0)
    db.close_position(conn, SYMBOL, MARKET)
    closed = db.get_position(conn, SYMBOL, MARKET)
    assert closed["status"] == "CLOSED" and float(closed["qty"]) == 0.0
    # 이 시험의 전제: 닫힌 행에는 옛 이력이 남아 있다
    assert closed["entry_time"] == OLD_TIME and closed["peak_price_since_entry"] == OLD_PEAK
    assert closed["entry_signal"] == OLD_SIGNAL

    _buy(conn, qty=1.0, price=21_700.0, prev_qty=0.0, prev_avg=21_350.0, signal={"rsi": 28.0})
    pos = db.get_position(conn, SYMBOL, MARKET)
    assert pos["status"] == "OPEN" and float(pos["qty"]) == 1.0 and float(pos["avg_price"]) == 21_700.0
    _assert_entered_just_now(pos["entry_time"])
    assert pos["peak_price_since_entry"] is None
    assert pos["entry_signal"] == {"rsi": 28.0}


def test_reentry_over_an_open_row_with_zero_quantity_is_a_new_entry(conn):
    _seed(conn, qty=0.0, status="OPEN")
    _buy(conn, qty=1.0, price=21_700.0, prev_qty=0.0, prev_avg=21_700.0, signal={"rsi": 28.0})
    pos = db.get_position(conn, SYMBOL, MARKET)
    assert pos["status"] == "OPEN" and float(pos["qty"]) == 1.0
    _assert_entered_just_now(pos["entry_time"])
    assert pos["peak_price_since_entry"] is None
    assert pos["entry_signal"] == {"rsi": 28.0}


def test_a_rebuy_settled_from_an_order_starts_a_fresh_entry(conn):
    _seed(conn, qty=1.0, status="OPEN", avg=21_350.0)
    db.close_position(conn, SYMBOL, MARKET)
    order_id = db.record_order(conn, symbol=SYMBOL, market=MARKET, side="BUY", qty=1, price=21_700.0,
                               order_type="LIMIT", status="SUBMITTED", client_ref="t09e-rebuy",
                               broker_order_no=None, reason=None, raw_request=None)
    db.update_order_status(conn, order_id, status="SUBMITTED", broker_order_no="0001569139",
                           broker_org_no="91252", raw_response={})
    filled = SimpleNamespace(status="FILLED", filled_qty=1.0, avg_fill_price=21_700.0, raw={})

    def status_fn(order_no, *, ticker=None, org_no=None):
        return filled

    trader._resolve_open_orders(conn, market=MARKET, ticker=SYMBOL, status_fn=status_fn, cancel_fn=None)
    pos = db.get_position(conn, SYMBOL, MARKET)
    assert pos["status"] == "OPEN" and float(pos["qty"]) == 1.0 and float(pos["avg_price"]) == 21_700.0
    _assert_entered_just_now(pos["entry_time"])
    assert pos["peak_price_since_entry"] is None
    assert pos["entry_signal"] is None


def test_adding_to_an_open_position_keeps_the_first_entry(conn):
    _seed(conn, qty=1.0, status="OPEN", avg=21_700.0)
    _buy(conn, qty=1.0, price=21_900.0, prev_qty=1.0, prev_avg=21_700.0, signal={"rsi": 29.0})
    pos = db.get_position(conn, SYMBOL, MARKET)
    assert float(pos["qty"]) == 2.0 and float(pos["avg_price"]) == 21_800.0
    assert pos["entry_time"] == OLD_TIME
    assert pos["peak_price_since_entry"] == OLD_PEAK
    assert pos["entry_signal"] == OLD_SIGNAL


def test_partial_sell_keeps_the_entry_metadata(conn):
    _seed(conn, qty=2.0, status="OPEN")
    trader._apply_fill_to_position(conn, market=MARKET, ticker=SYMBOL, side="SELL", qty=1.0, price=None,
                                   prev_qty=2.0, prev_avg=21_700.0)
    pos = db.get_position(conn, SYMBOL, MARKET)
    assert pos["status"] == "OPEN" and float(pos["qty"]) == 1.0
    assert pos["entry_time"] == OLD_TIME
    assert pos["peak_price_since_entry"] == OLD_PEAK
    assert pos["entry_signal"] == OLD_SIGNAL


def test_external_holding_keeps_its_row_metadata_when_bought_into(conn):
    # 외부 보유 위의 매수는 `_may_buy_into` 가 막는다. 이 시험은 체결 반영 함수의 현재 동작(이어받음)을 고정할 뿐이고,
    # 그 동작을 바꾸는 것은 이 Task 의 범위 밖이다(T12).
    _seed(conn, qty=3.0, status="EXTERNAL", avg=21_000.0)
    _buy(conn, qty=1.0, price=21_400.0, prev_qty=3.0, prev_avg=21_000.0)
    pos = db.get_position(conn, SYMBOL, MARKET)
    assert pos["status"] == "OPEN" and float(pos["qty"]) == 4.0
    assert pos["entry_time"] == OLD_TIME
    assert pos["peak_price_since_entry"] == OLD_PEAK
