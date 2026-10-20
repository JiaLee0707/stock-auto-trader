"""
T03: trader.py 가 주문 결과를 DB 에 기록하는지 검증한다.

배경(중요): `positions`/`orders` 를 채우는 코드가 없으면 reconciliation 이
브로커의 실제 보유를 전부 "외부 보유"로 판정해 **첫 사이클부터 매매를 영구 정지**시킨다.
그래서 "매수 → 기록 → 대조 일치" 흐름을 테스트로 고정한다.

임시 DB 만 쓴다. 실제 KIS API 를 호출하지 않는다(mock).
"""
import pytest

import db
import reconcile
import trader


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


def _holding(symbol, qty, market="overseas", avg_price=100.0):
    return {"symbol": symbol, "market": market, "qty": qty, "avg_price": avg_price}


# ── 주문 기록 ─────────────────────────────────────────────

def test_record_order_stores_submitted_status(conn):
    """접수만 확인했으므로 SUBMITTED 다(체결 확인은 T04/T05 범위)."""
    order_id = trader._record_order(
        conn, market="overseas", ticker="QQQ", side="BUY", qty=3, price=100.0,
        order_type="LIMIT", raw_response={"rt_cd": "0"},
    )
    order = db.get_order(conn, order_id)
    assert order["status"] == "SUBMITTED"
    assert order["side"] == "BUY"
    assert order["qty"] == 3.0
    assert order["market"] == "overseas"
    assert order["config_hash"], "설정 지문이 남아야 어떤 설정으로 낸 주문인지 추적된다"


def test_broker_order_no_is_left_null_not_guessed(conn):
    """
    KIS 주문 응답의 주문번호 필드명은 확인되지 않았다(절대 규칙 4).
    추측해서 채우지 않고 NULL 로 둔다.
    """
    order_id = trader._record_order(
        conn, market="overseas", ticker="QQQ", side="BUY", qty=1, price=100.0,
        order_type="LIMIT", raw_response={"rt_cd": "0", "msg1": "주문 완료"},
    )
    assert db.get_order(conn, order_id)["broker_order_no"] is None


def test_client_ref_is_unique_per_order(conn):
    """같은 초에 두 주문이 나가도 client_ref 가 충돌하지 않아야 한다."""
    ids = [
        trader._record_order(conn, market="overseas", ticker="QQQ", side="BUY",
                             qty=1, price=100.0, order_type="LIMIT",
                             raw_response=None)
        for _ in range(5)
    ]
    refs = {db.get_order(conn, i)["client_ref"] for i in ids}
    assert len(refs) == 5, "client_ref 가 중복됐다(UNIQUE 위반 또는 충돌)"


# ── 포지션 반영 ───────────────────────────────────────────

def test_buy_creates_position(conn):
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=3, price=100.0, prev_qty=0, prev_avg=0.0,
                                   entry_signal={"rsi": 25.0})
    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 3.0
    assert pos["avg_price"] == 100.0
    assert pos["status"] == "OPEN"
    assert pos["entry_signal"] == {"rsi": 25.0}
    assert pos["entry_time"], "진입 시각이 기록돼야 한다"


def test_buy_averages_price_on_second_entry(conn):
    """추가 매수 시 평균 단가가 가중평균으로 갱신된다."""
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=2, price=100.0, prev_qty=0, prev_avg=0.0)
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=2, price=200.0, prev_qty=2, prev_avg=100.0)
    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 4.0
    assert pos["avg_price"] == 150.0


def test_partial_sell_keeps_remaining_position(conn):
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=6, price=100.0, prev_qty=0, prev_avg=0.0)
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="SELL",
                                   qty=2, price=None, prev_qty=6, prev_avg=100.0)
    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 4.0
    assert pos["status"] == "OPEN"


def test_full_sell_closes_position(conn):
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=3, price=100.0, prev_qty=0, prev_avg=0.0)
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="SELL",
                                   qty=3, price=None, prev_qty=3, prev_avg=100.0)
    assert db.list_positions(conn, status="OPEN") == []
    assert len(db.list_positions(conn, status="CLOSED")) == 1


def test_position_survives_restart(tmp_path):
    """포지션과 주문은 재시작해도 유지돼야 한다(메모리였다면 사라진다)."""
    path = tmp_path / "bot.db"
    c1 = db.open_db(path)
    trader._apply_fill_to_position(c1, market="overseas", ticker="QQQ", side="BUY",
                                   qty=3, price=100.0, prev_qty=0, prev_avg=0.0)
    trader._record_order(c1, market="overseas", ticker="QQQ", side="BUY", qty=3,
                         price=100.0, order_type="LIMIT", raw_response=None)
    c1.close()

    c2 = db.open_db(path)
    assert db.get_position(c2, "QQQ", "overseas")["qty"] == 3.0
    assert len(db.list_orders(c2)) == 1
    c2.close()


# ── 핵심: 기록이 있어야 대조가 일치한다 ────────────────────

def test_recorded_buy_makes_reconcile_consistent(conn):
    """
    이 테스트가 실패하면 봇이 첫 사이클부터 영구 정지한다.
    매수 기록이 없으면 브로커 보유가 '외부 보유'로 잡혀 중단 플래그가 선다.
    """
    trader._record_order(conn, market="overseas", ticker="QQQ", side="BUY", qty=3,
                         price=100.0, order_type="LIMIT", raw_response=None)
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=3, price=100.0, prev_qty=0, prev_avg=0.0)

    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 3)], autofix=False)
    assert result["is_consistent"] is True
    assert result["halted"] is False
    assert reconcile.is_trading_halted(conn) is False


def test_unrecorded_buy_halts_trading(conn):
    """
    반대 방향 확인: 기록 없이 브로커에만 보유가 있으면 중단된다.
    (이 동작 자체가 안전장치이며, 위 배선이 이를 피하기 위해 존재한다.)
    """
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 3)], autofix=False)
    assert result["is_consistent"] is False
    assert result["halted"] is True


def test_recorded_full_sell_makes_reconcile_consistent(conn):
    """전량 매도 후에는 브로커에 없어도 불일치가 아니어야 한다."""
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="BUY",
                                   qty=3, price=100.0, prev_qty=0, prev_avg=0.0)
    trader._apply_fill_to_position(conn, market="overseas", ticker="QQQ", side="SELL",
                                   qty=3, price=None, prev_qty=3, prev_avg=100.0)

    result = reconcile.reconcile(conn, holdings=[], autofix=False)
    assert result["is_consistent"] is True
    assert result["halted"] is False


# ── 외부 보유는 매도하지 않는다 (요구사항 3) ────────────────

def test_external_holding_is_never_sold(conn, monkeypatch):
    """
    봇이 만들지 않은 보유(EXTERNAL)는 손절·익절 조건이 맞아도 매도하지 않는다.
    매도 수량은 브로커 잔고에서 오므로 DB 상태를 보지 않으면 남의 보유를 팔게 된다.
    """
    db.upsert_position(conn, "SPY", "overseas", qty=10, avg_price=100.0,
                       status="EXTERNAL")

    sold = []
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)

    # 손절 조건(-10% ≤ -5%)을 충족시켜도 매도 콜러블이 호출되면 안 된다
    handled = trader._check_sell(
        "해외", "SPY", 10, profit_rate=-10.0,
        sell_fn=lambda q: sold.append(q),
        market="overseas", avg_price=100.0,
    )
    assert handled is True, "매도 판단을 봇이 처리한 것으로 표시해야 한다"
    assert sold == [], "외부 보유를 매도했다"


def test_external_holding_is_not_sold_by_partial_take_profit(conn, monkeypatch):
    """익절 단계로도 외부 보유를 팔지 않는다."""
    db.upsert_position(conn, "SPY", "overseas", qty=10, avg_price=100.0,
                       status="EXTERNAL")
    sold = []
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)

    handled = trader._check_sell(
        "해외", "SPY", 10, profit_rate=50.0,
        sell_fn=lambda q: sold.append(q),
        market="overseas", avg_price=100.0,
    )
    assert handled is True
    assert sold == [], "외부 보유를 익절로 매도했다"


def test_bot_owned_position_is_still_sold(conn, monkeypatch):
    """봇이 만든(OPEN) 포지션은 정상적으로 매도된다 — 가드가 과하게 막지 않는지 확인."""
    db.upsert_position(conn, "QQQ", "overseas", qty=3, avg_price=100.0, status="OPEN")
    sold = []
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)

    trader._check_sell(
        "해외", "QQQ", 3, profit_rate=-10.0,
        sell_fn=lambda q: sold.append(q),
        market="overseas", avg_price=100.0,
    )
    assert sold == [3], "봇 소유 포지션의 손절이 막혔다"


def test_no_position_row_is_still_sold(conn, monkeypatch):
    """
    DB 에 포지션 행이 없으면(예: 기록 전 단계) 매도를 막지 않는다.
    여기서 막으면 기록 누락이 영구 매도 불가로 이어진다 — 불일치는 reconcile 이 잡는다.
    """
    sold = []
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    trader._check_sell(
        "해외", "QQQ", 3, profit_rate=-10.0,
        sell_fn=lambda q: sold.append(q),
        market="overseas", avg_price=100.0,
    )
    assert sold == [3]


def test_sell_skipped_when_position_lookup_fails(conn, monkeypatch):
    """
    포지션 상태를 읽지 못하면 **팔지 않는다**(모르면 주문하지 않는다).
    상태 조회 실패를 '포지션 없음'으로 처리하면 외부 보유를 팔 수 있다.
    """
    sold = []

    def boom():
        raise RuntimeError("DB 잠김")

    monkeypatch.setattr(trader, "_state_conn", boom)
    handled = trader._check_sell(
        "해외", "QQQ", 3, profit_rate=-10.0,
        sell_fn=lambda q: sold.append(q),
        market="overseas", avg_price=100.0,
    )
    assert handled is True
    assert sold == [], "상태를 모르는데 매도했다"


def test_both_markets_tracked_separately(conn):
    """해외·국내를 동시에 보유해도 각각 기록돼 대조가 일치한다."""
    for market, symbol, qty in (("overseas", "QQQ", 3), ("domestic", "379800", 7)):
        trader._apply_fill_to_position(conn, market=market, ticker=symbol, side="BUY",
                                       qty=qty, price=100.0, prev_qty=0, prev_avg=0.0)

    result = reconcile.reconcile(
        conn,
        holdings=[_holding("QQQ", 3), _holding("379800", 7, market="domestic")],
        autofix=False,
    )
    assert result["is_consistent"] is True
