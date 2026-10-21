"""
T04: 주문 실행 안전성 검증.

검증 대상(요구사항 그대로)
  1. 주문 전 `SUBMITTED` 사전 기록 (write-ahead)
  2. 타임아웃/네트워크 오류 → **재시도 없음** + `UNKNOWN` 기록 + 그 종목 신규 주문 차단
  3. 미체결이 `ORDER_TIMEOUT_MINUTES` 초과 → 취소 호출 + 상태 갱신
  4. 부분 체결은 체결된 만큼만 포지션에 반영
  5. 중복 주문 차단
  6. **접수만으로 포지션을 갱신하지 않는다** (체결 확인 후에만)
  7. raw_request/raw_response 저장 + 계좌번호 마스킹

mock 브로커만 쓴다. 실제 KIS API 를 호출하지 않는다(main.py 도 실행하지 않는다).
"""
import json

import pytest

import config
import db
import indicators
import trader


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def mock_broker(monkeypatch, conn):
    """주문 전송을 가로채는 가짜 브로커. 실제 전송은 일어나지 않는다."""
    sent = []

    class Broker:
        def __init__(self):
            self.sent = sent
            self.on_uncertain = None

    broker = Broker()

    def send(body, on_uncertain=None):
        broker.on_uncertain = on_uncertain
        sent.append(body)
        return {"rt_cd": "0", "msg1": "주문 완료"}

    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    return broker, send


def _submit(conn, send, *, side="BUY", qty=3, price=100.0, ticker="QQQ",
            market="overseas"):
    """`_submit_order` 를 테스트용으로 감싼다."""
    return trader._submit_order(
        conn, market=market, ticker=ticker, side=side, qty=qty, price=price,
        order_type="LIMIT", make_body=lambda: {"PDNO": ticker, "ORD_QTY": str(qty)},
        send=send,
    )


# ── 1. write-ahead ────────────────────────────────────────

def test_order_is_recorded_before_being_sent(conn):
    """주문을 보내기 **전에** SUBMITTED 행이 있어야 한다(프로세스 사망 대비)."""
    seen = {}

    def send(body, on_uncertain=None):
        # 전송 시점에 이미 기록이 있어야 한다
        seen["orders"] = db.list_orders(conn)
        return {"rt_cd": "0", "msg1": "완료"}

    _submit(conn, send)
    assert seen["orders"], "주문을 보내기 전에 기록이 없었다 (write-ahead 위반)"
    assert seen["orders"][0]["status"] == "SUBMITTED"


def test_raw_request_is_saved(conn):
    """무엇을 주문했는지 사후 확인할 수 있어야 한다."""
    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})
    raw = json.loads(db.list_orders(conn)[0]["raw_request"])
    assert raw["PDNO"] == "QQQ"


def test_account_numbers_are_masked_in_raw_request(conn):
    """계좌번호는 마스킹해서 저장한다 (절대 규칙 2)."""
    masked = trader._mask_payload({"CANO": "50000000", "ACNT_PRDT_CD": "01",
                                   "PDNO": "QQQ"})
    assert masked["CANO"] == config.mask("50000000")
    assert "50000000" not in json.dumps(masked)
    assert masked["PDNO"] == "QQQ", "종목코드까지 가리면 추적이 불가해진다"


def test_mask_payload_does_not_mutate_original():
    """마스킹은 원본을 건드리지 않아야 한다(주문 값이 바뀌면 안 된다)."""
    original = {"CANO": "50000000", "nested": {"ACNT_PRDT_CD": "01"}}
    trader._mask_payload(original)
    assert original["CANO"] == "50000000"
    assert original["nested"]["ACNT_PRDT_CD"] == "01"


# ── 2. 타임아웃 → 재시도 없음 + UNKNOWN ───────────────────

def test_timeout_records_unknown_and_does_not_retry(conn):
    """타임아웃이면 재시도하지 않고 UNKNOWN 으로 기록한다 (절대 규칙 3)."""
    attempts = []

    def send(body, on_uncertain=None):
        attempts.append(body)
        on_uncertain(TimeoutError("응답 없음"))
        raise trader.kis.OrderRequestError("결과 미확정")

    with pytest.raises(Exception):
        _submit(conn, send)

    assert len(attempts) == 1, f"주문을 재시도했다 ({len(attempts)}회) — 중복 주문 위험"
    order = db.list_orders(conn)[0]
    assert order["status"] == "UNKNOWN"


def test_unknown_order_blocks_new_orders_for_that_symbol(conn):
    """UNKNOWN 주문이 남아 있으면 같은 종목·방향 신규 주문을 막는다."""
    def send(body, on_uncertain=None):
        on_uncertain(TimeoutError("응답 없음"))
        raise trader.kis.OrderRequestError("결과 미확정")

    with pytest.raises(Exception):
        _submit(conn, send)

    with pytest.raises(trader.DuplicateOrderBlocked):
        _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})


def test_unknown_only_blocks_same_symbol_and_side(conn):
    """다른 종목·다른 방향은 막지 않는다(과잉 차단은 매도 불가로 이어진다)."""
    def send(body, on_uncertain=None):
        on_uncertain(TimeoutError("응답 없음"))
        raise trader.kis.OrderRequestError("결과 미확정")

    with pytest.raises(Exception):
        _submit(conn, send, ticker="QQQ")

    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"},
            ticker="QQQ", side="SELL")
    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"}, ticker="SPY")
    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"},
            ticker="QQQ", market="domestic")


# ── 3. 미체결 타임아웃 → 취소 ─────────────────────────────

def _open_order_with_ref(conn, *, age_minutes=0.0, qty=3):
    """주문번호까지 확정된 주문을 하나 만든다(취소·체결조회 테스트용)."""
    order_id, _ref = trader._record_order_pending(
        conn, market="overseas", ticker="QQQ", side="BUY", qty=qty, price=100.0,
        order_type="LIMIT",
        raw_request={"PDNO": "QQQ"},
    )
    db.update_order_status(conn, order_id, status="SUBMITTED",
                           broker_order_no="B123",
                           raw_response={"rt_cd": "0"})
    if age_minutes:
        old = db.now_kst().timestamp() - age_minutes * 60
        from datetime import datetime
        stamp = datetime.fromtimestamp(old, db.KST).isoformat(timespec="seconds")
        with db.transaction(conn):
            conn.execute("UPDATE orders SET created_at = ? WHERE id = ?", (stamp, order_id))
    return db.get_order(conn, order_id)


@pytest.fixture
def fake_refs(monkeypatch):
    """주문번호 필드가 아직 확인되지 않았으므로(T05), 테스트에서만 채워 넣는다."""
    monkeypatch.setattr(
        trader, "_order_refs_from_response",
        lambda result: ({"order_no": "B123", "org_no": None, "order_time": None}
                        if result and str(result.get("rt_cd")) == "0" else None),
    )


def test_stale_unfilled_order_is_canceled(conn, fake_refs):
    """ORDER_TIMEOUT_MINUTES 를 넘긴 미체결 주문은 취소하고 상태를 갱신한다."""
    order = _open_order_with_ref(conn, age_minutes=config.ORDER_TIMEOUT_MINUTES + 5)
    canceled = []

    st = trader._settle_order(
        conn, order,
        status_fn=lambda no, **kw: db.OrderStatus("SUBMITTED", filled_qty=0,
                                            avg_fill_price=None, raw={"odno": no}),
        cancel_fn=lambda qty: canceled.append(qty) or {"rt_cd": "0", "msg1": "취소 완료"},
    )

    assert canceled == [3.0], "미체결 주문을 취소하지 않았다"
    assert st.status == "SUBMITTED"
    assert db.get_order(conn, order["id"])["status"] == "CANCELED"


def test_fresh_unfilled_order_is_not_canceled(conn, fake_refs):
    """아직 기한이 안 지난 주문은 취소하지 않는다(성급한 취소는 체결 기회를 없앤다)."""
    order = _open_order_with_ref(conn, age_minutes=0)
    canceled = []

    trader._settle_order(
        conn, order,
        status_fn=lambda no, **kw: db.OrderStatus("SUBMITTED"),
        cancel_fn=lambda qty: canceled.append(qty) or {"rt_cd": "0"},
    )
    assert canceled == []
    assert db.get_order(conn, order["id"])["status"] == "SUBMITTED"


def test_cancel_failure_blocks_symbol_without_fake_cancel(conn, fake_refs):
    """
    취소가 실패하면 CANCELED 로 바꾸지 않는다(거짓 확정 금지). 대신 그 주문을
    UNKNOWN 으로 두어 **이 종목의 신규 매수를 차단**한다 (T04b 요구사항 3).
    """
    order = _open_order_with_ref(conn, age_minutes=config.ORDER_TIMEOUT_MINUTES + 5)

    def boom(qty):
        raise RuntimeError("취소 거부")

    trader._settle_order(conn, order,
                         status_fn=lambda no, **kw: db.OrderStatus("SUBMITTED"),
                         cancel_fn=boom)
    row = db.get_order(conn, order["id"])
    assert row["status"] == "UNKNOWN", "취소 실패인데 CANCELED 로 확정했다"
    assert "취소 거부" in (row["raw_response"] or "")

    # 차단 확인: 같은 종목 신규 매수가 막힌다
    with pytest.raises(trader.DuplicateOrderBlocked):
        _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})
    # 매도는 막지 않는다 (손절이 막히면 손실이 커진다)
    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"}, side="SELL")


def test_unfilled_cancel_failure_does_not_halt_whole_bot(conn, fake_refs):
    """
    종목 단위로 차단하고 **전역 중단 플래그는 세우지 않는다** (T04b).
    전역 플래그를 세우면 손절까지 막혀 손실을 방치하게 된다.
    """
    import reconcile

    order = _open_order_with_ref(conn, age_minutes=config.ORDER_TIMEOUT_MINUTES + 5)
    trader._settle_order(conn, order,
                         status_fn=lambda no, **kw: db.OrderStatus("SUBMITTED"),
                         cancel_fn=lambda q: (_ for _ in ()).throw(
                             NotImplementedError("취소 미구현")))
    assert reconcile.is_trading_halted(conn) is False, "미체결 하나로 전역 매매가 멈췄다"


def test_no_partial_cancel_when_nothing_remains(conn, fake_refs):
    """이미 전량 체결된 주문은 취소하지 않는다."""
    order = _open_order_with_ref(conn, age_minutes=config.ORDER_TIMEOUT_MINUTES + 5)
    canceled = []
    trader._settle_order(
        conn, order,
        status_fn=lambda no, **kw: db.OrderStatus("PARTIAL", filled_qty=3.0,
                                            avg_fill_price=100.0),
        cancel_fn=lambda qty: canceled.append(qty) or {"rt_cd": "0"},
    )
    assert canceled == [], "체결 수량이 주문 수량과 같은데 취소를 시도했다"


# ── 4. 부분 체결 ──────────────────────────────────────────

def test_partial_fill_reflects_only_filled_quantity(conn, fake_refs):
    """부분 체결은 **체결된 만큼만** 포지션에 반영한다."""
    order = _open_order_with_ref(conn, qty=3)
    trader._settle_order(
        conn, order,
        status_fn=lambda no, **kw: db.OrderStatus("PARTIAL", filled_qty=1.0,
                                            avg_fill_price=101.0),
    )
    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 1.0, "미체결 수량까지 포지션에 들어갔다"
    assert pos["avg_price"] == 101.0, "평단가가 실제 체결가가 아니다"
    assert db.get_order(conn, order["id"])["status"] == "PARTIAL"


def test_repeated_inquiry_does_not_double_count_fill(conn, fake_refs):
    """같은 체결을 두 번 더하면 수량이 부풀어 reconcile 이 깨진다."""
    order = _open_order_with_ref(conn, qty=3)
    st = lambda no, **kw: db.OrderStatus("PARTIAL", filled_qty=1.0, avg_fill_price=101.0)

    trader._settle_order(conn, order, status_fn=st)
    trader._settle_order(conn, db.get_order(conn, order["id"]), status_fn=st)

    assert db.get_position(conn, "QQQ", "overseas")["qty"] == 1.0


def test_extra_fill_is_added_incrementally(conn, fake_refs):
    """추가 체결분은 증분만 반영된다."""
    order = _open_order_with_ref(conn, qty=3)
    trader._settle_order(conn, order,
                         status_fn=lambda no, **kw: db.OrderStatus("PARTIAL", 1.0, 100.0))
    trader._settle_order(conn, db.get_order(conn, order["id"]),
                         status_fn=lambda no, **kw: db.OrderStatus("FILLED", 3.0, 110.0))

    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 3.0
    assert pos["avg_price"] == pytest.approx((1 * 100.0 + 2 * 110.0) / 3)


# ── 5·6. 접수만으로는 포지션을 갱신하지 않는다 ────────────

def test_receipt_alone_does_not_create_position(conn, fake_refs):
    """접수(rt_cd=0)만으로 포지션을 만들면 미체결 주문이 반영돼 reconcile 이 멈춘다."""
    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0", "msg1": "접수"})
    assert db.get_position(conn, "QQQ", "overseas") is None
    # 주문 기록 자체는 남아야 한다
    assert len(db.list_orders(conn)) == 1


def test_duplicate_order_is_blocked_before_sending(conn, fake_refs):
    """미확정 주문이 있으면 전송 자체를 하지 않는다."""
    attempts = []
    _submit(conn, lambda body, on_uncertain=None: attempts.append(body) or {"rt_cd": "0"})

    with pytest.raises(trader.DuplicateOrderBlocked):
        _submit(conn, lambda body, on_uncertain=None: attempts.append(body) or {"rt_cd": "0"})
    assert len(attempts) == 1, "중복 주문이 브로커로 나갔다"


def test_filled_order_allows_next_order(conn, fake_refs):
    """체결이 확정된 주문은 더 이상 신규 주문을 막지 않는다."""
    order_id = _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})
    db.update_order_status(conn, order_id, status="FILLED", filled_qty=3)

    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})
    assert len(db.list_orders(conn)) == 2


# ── 조회 실패는 상태를 바꾸지 않는다 ──────────────────────

def test_inquiry_failure_leaves_order_untouched(conn, fake_refs):
    """체결 조회가 실패하면 추측하지 않는다 — 상태를 그대로 두고 로그만 남긴다."""
    order = _open_order_with_ref(conn, qty=3)

    def boom(no, **kw):
        raise RuntimeError("조회 실패")

    assert trader._settle_order(conn, order, status_fn=boom) is None
    assert db.get_order(conn, order["id"])["status"] == "SUBMITTED"
    assert db.get_position(conn, "QQQ", "overseas") is None


def test_not_implemented_inquiry_is_reported_not_crashed(conn, fake_refs):
    """미구현(주문번호 필드 미확인) 조회는 예외를 올리지 않고 로그로 알린다."""
    order = _open_order_with_ref(conn, qty=3)

    def not_impl(no, **kw):
        raise NotImplementedError("TR_ID 미확인")

    assert trader._settle_order(conn, order, status_fn=not_impl) is None


def test_stale_order_without_order_no_cannot_be_canceled(conn):
    """주문번호가 없으면 취소할 수 없다 — 조용히 넘어가지 않고 ERROR 로 남긴다."""
    order_id, _ref = trader._record_order_pending(
        conn, market="overseas", ticker="QQQ", side="BUY", qty=3, price=100.0,
        order_type="LIMIT", raw_request=None,
    )
    order = db.get_order(conn, order_id)
    canceled = []
    # 주문번호를 못 꺼내므로(status_fn 호출 전에 None 반환) 취소도 시도되지 않는다
    assert trader._settle_order(conn, order,
                                status_fn=lambda no, **kw: db.OrderStatus("SUBMITTED"),
                                cancel_fn=lambda q: canceled.append(q)) is None
    assert canceled == []


# ── 접수 직후 reconcile 이 멈추지 않는지 ──────────────────

def _holding(symbol, qty, market="overseas", avg_price=100.0):
    return {"symbol": symbol, "market": market, "qty": qty, "avg_price": avg_price}


def test_reconcile_does_not_halt_right_after_order_submitted(conn, fake_refs):
    """
    T04 핵심: 포지션은 체결 후에만 반영되므로 접수 직후에는 브로커에만 보유가 있다.
    이걸 불일치로 보면 매수 직후 매매가 멈춘다 — 미확정 주문으로 유예해야 한다.
    """
    import reconcile

    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 3)], autofix=False)

    assert result["is_consistent"] is True, "접수 직후를 불일치로 판정했다"
    assert result["halted"] is False
    assert result["pending_orders"], "유예 내역이 남지 않았다"


def test_reconcile_halts_when_pending_order_is_too_old(conn, fake_refs):
    """유예에는 상한이 있다 — 오래 미확정이면 체결조회/취소가 안 되는 것이므로 멈춘다."""
    import reconcile

    _open_order_with_ref(conn, age_minutes=config.ORDER_TIMEOUT_MINUTES + 5, qty=3)
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 3)], autofix=False)

    assert result["is_consistent"] is False
    assert result["halted"] is True
    assert result["stale_orders"], "만료된 미확정 주문이 보고되지 않았다"


def test_reconcile_still_halts_on_unexplained_mismatch(conn, fake_refs):
    """미확정 주문으로 설명되지 않는 불일치는 그대로 중단 사유다(유예가 구멍이 되면 안 된다)."""
    import reconcile

    _submit(conn, lambda body, on_uncertain=None: {"rt_cd": "0"})   # 3주
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 99)], autofix=False)

    assert result["is_consistent"] is False
    assert result["halted"] is True


def test_reconcile_halts_on_holding_with_no_order(conn):
    """주문 기록 없이 브로커에만 있는 보유는 여전히 외부 보유로 중단된다."""
    import reconcile

    result = reconcile.reconcile(conn, holdings=[_holding("SPY", 5)], autofix=False)
    assert result["halted"] is True


# ── 사이클 배선: 매도도 접수만으로 포지션을 줄이지 않는다 ──

def _cycle_balance(qty=3.0, avg=100.0, profit=50.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": 1_000_000.0, "cash_krw": 500_000.0}


def test_sell_via_submit_does_not_touch_position_on_receipt(conn, fake_refs, monkeypatch):
    """
    매도 주문을 내도 **체결 확인 전에는** 포지션이 줄지 않아야 한다.
    (접수만으로 줄이면 미체결 매도가 반영돼 reconcile 이 어긋난다.)
    """
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    db.upsert_position(conn, "QQQ", "overseas", qty=3, avg_price=100.0, status="OPEN")

    trader._check_sell(
        "해외", "QQQ", 3, profit_rate=-10.0,   # 손절 조건
        sell_fn=None,
        market="overseas", avg_price=100.0,
        submit_sell=lambda qty: _submit(conn, lambda b, on_uncertain=None: {"rt_cd": "0"},
                                        side="SELL", qty=qty),
    )

    assert db.get_position(conn, "QQQ", "overseas")["qty"] == 3.0, \
        "접수만으로 포지션을 줄였다"
    assert db.list_orders(conn)[0]["side"] == "SELL"


def test_sell_position_is_reduced_after_fill(conn, fake_refs, monkeypatch):
    """체결이 확인되면 그때 포지션이 줄어든다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    db.upsert_position(conn, "QQQ", "overseas", qty=3, avg_price=100.0, status="OPEN")

    trader._check_sell(
        "해외", "QQQ", 3, profit_rate=-10.0, sell_fn=None,
        market="overseas", avg_price=100.0,
        submit_sell=lambda qty: _submit(conn, lambda b, on_uncertain=None: {"rt_cd": "0"},
                                        side="SELL", qty=qty),
    )
    order = db.list_orders(conn)[0]
    trader._settle_order(conn, order,
                         status_fn=lambda no, **kw: db.OrderStatus("FILLED", 3.0, 110.0))

    assert db.get_position(conn, "QQQ", "overseas")["status"] == "CLOSED"


def test_indicator_unavailable_holds_off_buy_but_still_allows_sell(conn, fake_refs,
                                                                   monkeypatch):
    """
    지표 조회 실패 시 **매수는 보류**하되, 손절은 계속 동작해야 한다.
    (조회 실패로 손절까지 막으면 손실이 무한정 커진다.)
    """
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    db.upsert_position(conn, "QQQ", "overseas", qty=3, avg_price=100.0, status="OPEN")
    submitted = []

    def boom_closes():
        raise ConnectionError("network")

    trader._run_cycle(
        "해외", "QQQ", _cycle_balance(profit=-10.0), 100.0, 0, 0.15,
        lambda p: f"${p}", sell_fn=None, buy_fn=None,
        fetch_closes=boom_closes, market="overseas",
        submit_buy=lambda krw: submitted.append("BUY"),
        submit_sell=lambda qty: submitted.append(("SELL", qty)),
        status_fn=None, cancel_fn=None,
    )
    assert ("SELL", 3.0) in submitted, "지표 조회 실패로 손절까지 막혔다"
    assert "BUY" not in submitted, "지표 데이터가 없는데 매수했다"


# ── 지표 조회 실패 시 매수 보류 (T01 보고 4-2) ─────────────

def test_indicator_fetch_failure_holds_off_buying():
    """조회 실패를 '신호 없음'으로 바꾸면 하락률 폴백 매수로 이어진다 (T04)."""
    def boom():
        raise ConnectionError("network")

    with pytest.raises(indicators.IndicatorDataUnavailable):
        indicators.evaluate_or_none(boom)
