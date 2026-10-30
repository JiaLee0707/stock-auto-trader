"""
T09b-1: 매도 체결의 실현손익 기록.
  - 손익 = (매도가 - 평단) × 수량, 같은 체결은 두 번 세지 않는다
  - 가격을 모르면 손익을 추측하지 않고 NULL 로 남긴다(시장가의 주문가 0 이 -100% 손실로 계산되면 안 된다)
  - 기록이 실패하면 포지션을 바꾸기 전에 멈춘다
실제 API 는 호출하지 않는다. 체결 조회 결과만 가짜로 바꾼다.
"""
import logging
from types import SimpleNamespace

import pytest

import db
import trader

SYMBOL, MARKET = "379800", "domestic"


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


def _tables(c):
    return {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _record(conn, *, order_id=1, cum=2.0, qty=2, sell=22000.0, cost=23220.0, market=MARKET):
    return db.record_realized_pnl(conn, order_id=order_id, cum_filled_qty=cum, symbol=SYMBOL,
                                  market=market, qty=qty, sell_price=sell, avg_cost=cost)


# ── db 계층 ───────────────────────────────────────────────

def test_new_db_has_realized_pnl_table(conn):
    assert db.SCHEMA_VERSION == db._MIGRATIONS[-1][0] >= 4
    assert "realized_pnl" in _tables(conn)


def test_v3_db_upgrades_and_keeps_data(tmp_path):
    path = tmp_path / "bot.db"
    c = db.open_db(path)
    db.upsert_position(c, SYMBOL, MARKET, qty=3.0, avg_price=100.0, status="OPEN")
    c.execute("DROP TABLE realized_pnl")
    c.execute("DELETE FROM schema_version")
    c.execute("INSERT INTO schema_version (version) VALUES (3)")
    c.commit()
    c.close()
    c = db.open_db(path)
    try:
        assert "realized_pnl" in _tables(c)
        assert c.execute("SELECT version FROM schema_version").fetchone()["version"] == db.SCHEMA_VERSION
        assert db.get_position(c, SYMBOL, MARKET)["qty"] == 3.0
    finally:
        c.close()


def test_loss_is_negative_and_profit_positive(conn):
    loss = _record(conn, order_id=1, cum=2.0, qty=2, sell=22000.0)
    gain = _record(conn, order_id=2, cum=3.0, qty=3, sell=24000.0)
    assert loss == pytest.approx(-2440.0)
    assert gain == pytest.approx(2340.0)
    assert db.sum_realized_pnl(conn) == pytest.approx(-100.0)


def test_same_key_is_recorded_once(conn):
    _record(conn)
    _record(conn)
    assert len(db.list_realized_pnl(conn)) == 1
    assert db.sum_realized_pnl(conn) == pytest.approx(-2440.0)


def test_partial_fills_use_distinct_keys(conn):
    _record(conn, order_id=1, cum=1.0, qty=1)
    _record(conn, order_id=1, cum=3.0, qty=2)
    assert len(db.list_realized_pnl(conn)) == 2


@pytest.mark.parametrize("qty,sell,cost,note", [
    (2, None, 23220.0, "sell_price_unknown"),
    (2, 0.0, 23220.0, "sell_price_unknown"),
    (2, 22000.0, 0.0, "avg_cost_unknown"),
    (0, 22000.0, 23220.0, "qty_not_positive"),
    (None, 22000.0, 23220.0, "qty_not_positive"),
], ids=["no_price", "zero_price", "zero_cost", "zero_qty", "none_qty"])
def test_unknown_inputs_record_null_not_a_guess(conn, qty, sell, cost, note):
    assert _record(conn, qty=qty, sell=sell, cost=cost) is None
    assert db.sum_realized_pnl(conn) == 0.0
    assert db.count_unknown_pnl(conn) == 1
    assert db.list_realized_pnl(conn)[0]["note"] == note


def test_overseas_pnl_is_not_mixed_into_krw_sum(conn):
    _record(conn, market="overseas", sell=90.0, cost=100.0, qty=2)
    assert db.sum_realized_pnl(conn) == 0.0
    assert db.sum_realized_pnl(conn, currency="USD") == pytest.approx(-20.0)


def test_empty_sum_is_zero(conn):
    assert db.sum_realized_pnl(conn) == 0.0
    assert db.count_unknown_pnl(conn) == 0


# ── _settle_order 연결 ────────────────────────────────────

def _new_order(conn, side, qty, price):
    order_id, _ref = trader._record_order_pending(
        conn, market=MARKET, ticker=SYMBOL, side=side, qty=qty, price=price,
        order_type="LIMIT", raw_request={}, reason=None)
    db.update_order_status(conn, order_id, status="SUBMITTED", broker_order_no="0000001",
                           broker_org_no="91252", raw_response={})
    return order_id


def _open_order(conn, order_id):
    return next(o for o in db.list_open_orders(conn) if o["id"] == order_id)


def _settle(conn, monkeypatch, order, *, status, filled, avg):
    st = SimpleNamespace(status=status, filled_qty=filled, avg_fill_price=avg, raw={})
    monkeypatch.setattr(trader, "_call_status_fn", lambda fn, o, refs: st)
    return trader._settle_order(conn, order, lambda *a, **k: None)


def _seed(conn, qty=3.0, avg=23220.0):
    db.upsert_position(conn, SYMBOL, MARKET, qty=qty, avg_price=avg, status="OPEN")


def test_sell_fill_records_realized_loss_and_closes_position(conn, monkeypatch):
    _seed(conn)
    oid = _new_order(conn, "SELL", 3, 22500.0)
    _settle(conn, monkeypatch, _open_order(conn, oid), status="FILLED", filled=3.0, avg=22500.0)
    rows = db.list_realized_pnl(conn)
    assert len(rows) == 1 and rows[0]["order_id"] == oid
    assert rows[0]["pnl"] == pytest.approx((22500.0 - 23220.0) * 3)
    assert db.list_positions(conn, status="OPEN") == []


def test_buy_fill_records_no_realized_pnl(conn, monkeypatch):
    oid = _new_order(conn, "BUY", 2, 23220.0)
    _settle(conn, monkeypatch, _open_order(conn, oid), status="FILLED", filled=2.0, avg=23220.0)
    assert db.list_realized_pnl(conn) == []
    assert db.get_position(conn, SYMBOL, MARKET)["qty"] == 2.0


def test_partial_sell_fills_record_each_increment(conn, monkeypatch):
    _seed(conn)
    oid = _new_order(conn, "SELL", 3, 22800.0)
    _settle(conn, monkeypatch, _open_order(conn, oid), status="PARTIAL", filled=1.0, avg=23000.0)
    _settle(conn, monkeypatch, _open_order(conn, oid), status="FILLED", filled=3.0, avg=22800.0)
    rows = sorted(db.list_realized_pnl(conn), key=lambda r: r["cum_filled_qty"])
    assert [(r["cum_filled_qty"], r["qty"]) for r in rows] == [(1.0, 1.0), (3.0, 2.0)]


def test_resettling_same_fill_does_not_double_count_pnl(conn, monkeypatch):
    """주문 상태 갱신이 실패해 같은 체결을 다시 처리하는 상황 — 손익은 한 번만 센다."""
    _seed(conn)
    oid = _new_order(conn, "SELL", 3, 22500.0)
    stale = _open_order(conn, oid)
    _settle(conn, monkeypatch, stale, status="FILLED", filled=3.0, avg=22500.0)
    _settle(conn, monkeypatch, stale, status="FILLED", filled=3.0, avg=22500.0)
    assert len(db.list_realized_pnl(conn)) == 1
    assert db.sum_realized_pnl(conn) == pytest.approx(-2160.0)


def test_pnl_record_failure_leaves_position_unchanged(conn, monkeypatch):
    _seed(conn)
    oid = _new_order(conn, "SELL", 3, 22500.0)

    def boom(*args, **kwargs):
        raise RuntimeError("db write failed (test)")

    monkeypatch.setattr(db, "record_realized_pnl", boom)
    with pytest.raises(RuntimeError):
        _settle(conn, monkeypatch, _open_order(conn, oid), status="FILLED", filled=3.0, avg=22500.0)
    assert db.get_position(conn, SYMBOL, MARKET)["qty"] == 3.0
    assert [p["symbol"] for p in db.list_positions(conn, status="OPEN")] == [SYMBOL]


def test_market_sell_without_fill_price_records_unknown_not_a_loss(conn, monkeypatch, caplog):
    _seed(conn)
    oid = _new_order(conn, "SELL", 3, 0.0)                  # 시장가: 주문가 0
    with caplog.at_level(logging.WARNING, logger="bot"):
        _settle(conn, monkeypatch, _open_order(conn, oid), status="FILLED", filled=3.0, avg=None)
    row = db.list_realized_pnl(conn)[0]
    assert row["pnl"] is None and row["note"] == "sell_price_unknown"
    assert db.sum_realized_pnl(conn) == 0.0 and db.count_unknown_pnl(conn) == 1
    assert "계산하지 못했습니다" in caplog.text
    assert db.list_positions(conn, status="OPEN") == []
