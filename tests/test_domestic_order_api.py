"""
T04b: 국내 주문 3개 API(주문현금 / 일별체결조회 / 정정취소) 검증.

근거는 **오직** `docs/kis_api_fields.md` 다. 필드명·TR_ID·값은 그 문서에 적힌 것만 쓴다.
가짜 응답은 문서의 Response Example 을 그대로 옮긴 것이라, 문서가 바뀌면 이 테스트도 바뀐다.

실제 KIS API 를 호출하지 않는다. main.py 도 실행하지 않는다.
"""
import pytest

import config
import db
import kis_domestic as kd
import trader


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


# ── 문서의 Response Example 그대로 (§1 주식주문(현금)) ─────

ORDER_CASH_RESPONSE = {
    "rt_cd": "0",
    "msg_cd": "APBK0013",
    "msg1": "주문 전송 완료 되었습니다.",
    "output": {
        "KRX_FWDG_ORD_ORGNO": "06010",
        "ODNO": "0001569157",
        "ORD_TMD": "155211",
    },
}


# ── 1. 주문번호 추출 ──────────────────────────────────────

def test_order_refs_reads_odno_and_org_no_from_document_example():
    """§1 Response Body: output.ODNO(주문번호), output.KRX_FWDG_ORD_ORGNO(계좌관리점코드)."""
    refs = trader._order_refs_from_response(ORDER_CASH_RESPONSE)
    assert refs["order_no"] == "0001569157"
    assert refs["org_no"] == "06010"
    assert refs["order_time"] == "155211"


def test_order_refs_are_case_insensitive():
    """문서가 대소문자 불일치를 명시하고 있으므로 어느 쪽이 와도 읽어야 한다."""
    lower = {"rt_cd": "0", "output": {"odno": "0001569157",
                                      "krx_fwdg_ord_orgno": "06010"}}
    upper = {"rt_cd": "0", "output": {"ODNO": "0001569157",
                                      "KRX_FWDG_ORD_ORGNO": "06010"}}
    mixed = {"rt_cd": "0", "output": {"Odno": "0001569157",
                                      "Krx_Fwdg_Ord_Orgno": "06010"}}
    for payload in (lower, upper, mixed):
        refs = trader._order_refs_from_response(payload)
        assert refs == {"order_no": "0001569157", "org_no": "06010",
                        "order_time": None}, payload


def test_order_refs_returns_none_without_odno():
    """주문번호가 없으면 None — 이후 단계가 '조회 불가'를 알 수 있어야 한다."""
    assert trader._order_refs_from_response({"rt_cd": "0", "output": {}}) is None
    assert trader._order_refs_from_response({"rt_cd": "0"}) is None
    assert trader._order_refs_from_response(None) is None


def test_order_refs_returns_none_on_rejected_order():
    """거부된 주문(rt_cd != 0)에는 조회할 주문번호가 없다."""
    payload = dict(ORDER_CASH_RESPONSE, rt_cd="1")
    assert trader._order_refs_from_response(payload) is None


def test_org_no_may_be_absent_without_losing_order_no():
    """KRX_FWDG_ORD_ORGNO 가 비어도 주문번호는 살린다(체결 조회는 주문번호로 한다)."""
    refs = trader._order_refs_from_response(
        {"rt_cd": "0", "output": {"ODNO": "0001569157", "KRX_FWDG_ORD_ORGNO": ""}}
    )
    assert refs["order_no"] == "0001569157"
    assert refs["org_no"] == ""


# ── 주문 기록에 org_no 가 저장되고 복원되는지 ──────────────

def test_submit_order_stores_org_no(conn, monkeypatch):
    """취소에 필요한 KRX_FWDG_ORD_ORGNO 를 orders 에 저장한다 (T04b)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    order_id = trader._submit_order(
        conn, market="domestic", ticker="379800", side="BUY", qty=3, price=23000.0,
        order_type="LIMIT", make_body=lambda: {"PDNO": "379800"},
        send=lambda body, on_uncertain: ORDER_CASH_RESPONSE,
    )
    order = db.get_order(conn, order_id)
    assert order["broker_order_no"] == "0001569157"
    assert order["broker_org_no"] == "06010"


def test_stored_refs_use_saved_columns(conn, monkeypatch):
    """저장된 컬럼에서 주문번호·org_no 를 다시 꺼낸다(raw_response 없이도)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    order_id = trader._submit_order(
        conn, market="domestic", ticker="379800", side="BUY", qty=3, price=23000.0,
        order_type="LIMIT", make_body=lambda: {}, send=lambda b, u: ORDER_CASH_RESPONSE,
    )
    refs = trader._stored_order_refs(db.get_order(conn, order_id))
    assert refs["order_no"] == "0001569157"
    assert refs["org_no"] == "06010"


# ── 2. 체결 조회 (주식일별주문체결조회) ────────────────────

# §2 Response Example 의 output1 행 (문서 원문)
CCLD_ROW = {
    "ord_dt": "20220103", "ord_gno_brno": "06010", "odno": "0001568197",
    "orgn_odno": "", "ord_dvsn_name": "Limit",
    "sll_buy_dvsn_cd": "02", "sll_buy_dvsn_cd_name": "BUY REJECT",
    "pdno": "009150", "prdt_name": "삼성전기",
    "ord_qty": "10", "ord_unpr": "150000", "ord_tmd": "170100",
    "tot_ccld_qty": "0", "avg_prvs": "0", "cncl_yn": "",
    "tot_ccld_amt": "0", "cncl_cfrm_qty": "0", "rmn_qty": "0", "rjct_qty": "10",
    "ord_dvsn_cd": "00", "prdt_type_cd": "300", "excg_dvsn_cd": "02",
    "ord_orgno": "00000",
}


class _Resp:
    """requests.Response 최소 대역."""

    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def _patch_ccld(monkeypatch, rows, *, capture=None):
    """일별체결조회 응답을 가짜로 바꾼다. 실제 요청은 나가지 않는다."""
    def fake_request(method, url, tr_id, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture["tr_id"] = tr_id
            capture["params"] = kwargs.get("params")
        return _Resp({"rt_cd": "0", "msg1": "조회가 완료되었습니다",
                      "output1": rows, "output2": {}})
    monkeypatch.setattr(kd, "_request", fake_request)


def test_get_order_status_uses_documented_endpoint_and_tr_id(monkeypatch):
    """§2: GET /trading/inquire-daily-ccld, 모의 VTTC0081R / 실전 TTTC0081R."""
    cap = {}
    _patch_ccld(monkeypatch, [dict(CCLD_ROW, odno="0001569157", rjct_qty="0")],
                capture=cap)

    kd.get_order_status("0001569157", ticker="009150")

    assert cap["url"].endswith("/uapi/domestic-stock/v1/trading/inquire-daily-ccld")
    assert cap["tr_id"] == ("VTTC0081R" if config.IS_MOCK else "TTTC0081R")


def test_get_order_status_narrows_by_odno(monkeypatch):
    """모의는 1회 15건 제한이므로 주문번호로 좁혀 조회한다 (문서 §2)."""
    cap = {}
    _patch_ccld(monkeypatch, [dict(CCLD_ROW, odno="0001569157")], capture=cap)
    kd.get_order_status("0001569157", ticker="009150")

    assert cap["params"]["ODNO"] == "0001569157"
    for key in ("CANO", "ACNT_PRDT_CD", "INQR_STRT_DT", "INQR_END_DT",
                "SLL_BUY_DVSN_CD", "CCLD_DVSN", "INQR_DVSN", "INQR_DVSN_3",
                "CTX_AREA_FK100", "CTX_AREA_NK100"):
        assert key in cap["params"], f"문서 §2 필수 파라미터 누락: {key}"


def test_get_order_status_raises_when_order_not_found(monkeypatch):
    """주문번호를 못 찾으면 조회 실패다 — 0건을 '미체결'로 해석하면 체결을 놓친다."""
    _patch_ccld(monkeypatch, [dict(CCLD_ROW, odno="9999999999")])
    with pytest.raises(LookupError):
        kd.get_order_status("0001569157")


@pytest.mark.parametrize(
    "overrides,expected_status,expected_filled",
    [
        # 체결 전 (문서 예시 그대로: tot_ccld_qty=0)
        ({"tot_ccld_qty": "0", "cncl_yn": "", "rjct_qty": "0"}, "SUBMITTED", 0.0),
        # 전량 체결 (rjct_qty 는 0 이어야 한다 — 위 CCLD_ROW 는 거부 예시라 10 이다)
        ({"tot_ccld_qty": "10", "ord_qty": "10", "avg_prvs": "150300",
          "rjct_qty": "0"}, "FILLED", 10.0),
        # 부분 체결
        ({"tot_ccld_qty": "4", "ord_qty": "10", "avg_prvs": "150200",
          "rjct_qty": "0"}, "PARTIAL", 4.0),
        # 취소 (cncl_yn = "Y")
        ({"cncl_yn": "Y", "tot_ccld_qty": "0", "rjct_qty": "0"}, "CANCELED", 0.0),
        # 거부 (문서 예시: rjct_qty=10)
        ({"rjct_qty": "10", "tot_ccld_qty": "0"}, "REJECTED", 0.0),
    ],
)
def test_status_is_derived_from_documented_fields(monkeypatch, overrides,
                                                  expected_status, expected_filled):
    """사용 필드: odno, ord_qty, tot_ccld_qty, avg_prvs, rmn_qty, rjct_qty, cncl_yn."""
    row = dict(CCLD_ROW, odno="0001569157", **overrides)
    _patch_ccld(monkeypatch, [row])

    st = kd.get_order_status("0001569157")
    assert st.status == expected_status
    assert st.filled_qty == expected_filled


def test_avg_fill_price_is_none_when_zero(monkeypatch):
    """문서 예시는 미체결 시 avg_prvs="0" — 0을 체결가로 쓰면 평단가가 0이 된다."""
    _patch_ccld(monkeypatch, [dict(CCLD_ROW, odno="0001569157", avg_prvs="0")])
    assert kd.get_order_status("0001569157").avg_fill_price is None


def test_status_reads_uppercase_rows_too(monkeypatch):
    """output1 은 문서상 소문자지만, 대소문자가 뒤바뀌어 와도 읽어야 한다."""
    upper_row = {k.upper(): v for k, v in
                 dict(CCLD_ROW, odno="0001569157", ord_qty="10", rjct_qty="0",
                      tot_ccld_qty="10", avg_prvs="150300").items()}
    _patch_ccld(monkeypatch, [upper_row])

    st = kd.get_order_status("0001569157")
    assert st.status == "FILLED"
    assert st.filled_qty == 10.0


def test_odno_matching_ignores_leading_zeros(monkeypatch):
    """문서 예시 주문번호가 "0001568197" 처럼 0으로 시작한다."""
    _patch_ccld(monkeypatch, [dict(CCLD_ROW, odno="0001569157")])
    assert kd.get_order_status("1569157").filled_qty == 0.0   # 예외 없이 찾아낸다


def test_output2_is_ignored(monkeypatch):
    """output2 는 문서 내 필드명·한글명 불일치가 있어 쓰지 않는다 (§2 주석)."""
    def fake_request(method, url, tr_id, **kwargs):
        return _Resp({"rt_cd": "0", "output1": [dict(CCLD_ROW, odno="0001569157")],
                      "output2": {"tot_ccld_qty": "999", "pchs_avg_pric": "999"}})
    monkeypatch.setattr(kd, "_request", fake_request)

    st = kd.get_order_status("0001569157")
    assert st.filled_qty == 0.0, "output2 값을 썼다"


def test_order_no_is_required():
    """주문번호 없이 조회하면 다른 주문의 체결을 가져올 수 있다."""
    with pytest.raises(ValueError):
        kd.get_order_status("")


# ── 3. 취소 (미구현 유지) ─────────────────────────────────

def test_cancel_order_is_not_implemented(monkeypatch):
    """
    문서에 취소(02) 예시가 없고 ORD_QTY/ORD_UNPR 규칙이 "이 파일에 없는 것"이므로
    구현하지 않는다. 추측한 값을 넣으면 잘못된 주문이 나간다.
    """
    called = []
    monkeypatch.setattr(kd, "request_order", lambda *a, **k: called.append(1))
    monkeypatch.setattr(config, "IS_MOCK", True)

    with pytest.raises(NotImplementedError):
        kd.cancel_order("0001569157", quantity=3, ticker="379800", org_no="06010")
    assert called == [], "취소 요청이 브로커로 나갔다"


# ── 4. TR_ID / ORD_DVSN 정정 확인 ─────────────────────────

def test_domestic_tr_ids_are_the_new_ones():
    """§1: 신 TR_ID (모의 매수 VTTC0012U / 매도 VTTC0011U). 구 ...0802U/...0801U 금지."""
    assert kd._TR_BUY == ("VTTC0012U", "TTTC0012U")
    assert kd._TR_SELL == ("VTTC0011U", "TTTC0011U")
    assert kd._TR_CCLD == ("VTTC0081R", "TTTC0081R")
    assert kd._TR_RVSECNCL == ("VTTC0013U", "TTTC0013U")


def test_send_order_uses_new_buy_tr_id(monkeypatch):
    cap = {}
    monkeypatch.setattr(config, "IS_MOCK", True)
    monkeypatch.setattr(kd, "request_order",
                        lambda m, u, tr, **k: cap.update(tr_id=tr) or _Resp({"rt_cd": "0"}))
    kd.send_order({"PDNO": "379800", "ORD_QTY": "1", "ORD_UNPR": "23000"}, side="BUY")
    assert cap["tr_id"] == "VTTC0012U"


def test_send_order_uses_new_sell_tr_id(monkeypatch):
    cap = {}
    monkeypatch.setattr(config, "IS_MOCK", True)
    monkeypatch.setattr(kd, "request_order",
                        lambda m, u, tr, **k: cap.update(tr_id=tr) or _Resp({"rt_cd": "0"}))
    kd.send_order({"PDNO": "379800", "ORD_QTY": "1", "ORD_UNPR": "23000"}, side="SELL")
    assert cap["tr_id"] == "VTTC0011U"


def test_dvsn_limit_is_00_per_document():
    """§1 ORD_DVSN: 00 지정가 / 01 시장가. T04 까지 지정가에 01 을 보내고 있었다."""
    assert kd._DVSN_LIMIT == "00"
    assert kd._DVSN_MARKET == "01"


def test_limit_order_sends_dvsn_00_with_price(monkeypatch):
    """지정가는 ORD_DVSN=00 + 주문단가를 문자열로."""
    monkeypatch.setattr(config, "ORDER_TYPE", "limit")
    body = kd.order_body("379800", 3, 23000.0, side="BUY")
    assert body["ORD_DVSN"] == "00"
    assert body["ORD_UNPR"] == "23000"
    assert isinstance(body["ORD_UNPR"], str)


def test_market_order_sends_dvsn_01_with_zero_price(monkeypatch):
    """시장가는 ORD_DVSN=01 + ORD_UNPR="0" (문서 §1 ORD_UNPR 행)."""
    monkeypatch.setattr(config, "ORDER_TYPE", "market")
    body = kd.order_body("379800", 3, 23000.0, side="BUY")
    assert body["ORD_DVSN"] == "01"
    assert body["ORD_UNPR"] == "0"


def test_unknown_order_type_is_rejected(monkeypatch):
    """모르는 값을 지정가로 해석하면 의도와 다른 주문이 나간다."""
    monkeypatch.setattr(config, "ORDER_TYPE", "weird")
    with pytest.raises(ValueError):
        kd.order_body("379800", 1, 23000.0, side="BUY")


def test_all_body_fields_are_strings():
    """§1 개요: ORD_QTY, ORD_UNPR 등은 문자열로 전달한다."""
    body = kd.order_body("379800", 3, 23000.0, side="BUY")
    for key in ("CANO", "ACNT_PRDT_CD", "PDNO", "ORD_DVSN", "ORD_QTY", "ORD_UNPR"):
        assert isinstance(body[key], str), f"{key} 가 문자열이 아니다"


# ── TR_ID 선택 헬퍼 (_tr) ─────────────────────────────────

def test_tr_helper_selects_by_is_mock(monkeypatch):
    """
    `_tr()` 은 T04b 이전까지 **정의되지 않은 채** 조회·주문 경로에서 호출되고 있었다
    (해당 경로는 실행되면 NameError 로 죽었다). 모드 판정은 IS_MOCK 만 쓴다.
    """
    monkeypatch.setattr(config, "IS_MOCK", True)
    assert kd._tr(("MOCK", "REAL")) == "MOCK"
    monkeypatch.setattr(config, "IS_MOCK", False)
    assert kd._tr(("MOCK", "REAL")) == "REAL"


def test_balance_lookup_no_longer_raises_name_error(monkeypatch):
    """`_tr` 누락으로 죽던 잔고 조회 경로가 실제로 동작하는지 확인 (T04b 발견)."""
    monkeypatch.setattr(config, "IS_MOCK", True)
    monkeypatch.setattr(kd, "_request", lambda m, u, tr, **k: _Resp(
        {"rt_cd": "0", "output1": [], "output2": {"dnca_tot_amt": "0"}}))
    kd.get_all_holdings()    # NameError 없이 끝나야 한다


# ── 6. 체결 조회 미구현 시 주문 차단 (해외) ────────────────

def test_overseas_reports_inquiry_unimplemented():
    """
    해외는 체결 조회가 미구현이다 (docs/kis_api_fields.md 에 해외 문서가 없다).
    구현 여부는 **플래그**로 알린다 — 함수를 실제로 불러 보면 조회 요청이 나간다.
    """
    import kis_overseas as kis

    assert kis.supports_order_inquiry() is False


def test_overseas_order_is_blocked_when_inquiry_unimplemented(caplog):
    """
    그 상태로 주문을 내면 체결을 확인할 수 없어 포지션에 반영되지도,
    미체결을 정리하지도 못한다. CRITICAL 로 남기고 막는다.
    """
    import kis_overseas as kis
    import logging

    with caplog.at_level(logging.CRITICAL, logger="bot"):
        with pytest.raises(NotImplementedError):
            trader._require_settlement_support(kis, "해외")
    assert any("체결 조회가 구현되지 않아" in r.message for r in caplog.records)


def test_settlement_probe_does_not_call_the_api(monkeypatch):
    """
    구현 여부 확인이 실제 조회 호출이 되면 안 된다 (T04b 요구사항 6).
    조회 함수가 불리면 그 자체로 API 요청이 나가고, 실패 원인도 구분되지 않는다.
    """
    import kis_overseas as kis

    def boom(*a, **k):
        raise AssertionError("구현 여부 확인이 실제 조회를 호출했다")

    monkeypatch.setattr(kis, "get_order_status", boom)
    with pytest.raises(NotImplementedError):
        trader._require_settlement_support(kis, "해외")   # boom 이 불리면 실패


def test_require_settlement_support_passes_when_implemented():
    """구현돼 있으면 통과한다(국내는 T04b 에서 구현됨)."""
    trader._require_settlement_support(kd, "국내")   # 예외가 없어야 한다


def test_overseas_cycle_does_not_order_when_inquiry_unimplemented(monkeypatch):
    """run_overseas_cycle 이 주문 경로에 들어가기 전에 멈추는지 확인."""
    import kis_overseas as kis

    monkeypatch.setattr(config, "IS_MOCK", True)
    monkeypatch.setattr(kis, "get_balance", lambda: {
        "quantity": 0, "avg_price_usd": 0.0, "profit_rate": 0.0,
        "total_asset_krw": 1_000_000.0, "cash_krw": 500_000.0})
    monkeypatch.setattr(kis, "get_current_price", lambda t: 500.0)
    monkeypatch.setattr(kis, "get_usd_krw_rate", lambda: 1300.0)
    monkeypatch.setattr(kis, "supports_order_inquiry", lambda: False)
    ordered = []
    monkeypatch.setattr(kis, "send_order",
                        lambda *a, **k: ordered.append(1) or {"rt_cd": "0"})
    # 주문 기록도 나가면 안 된다(write-ahead 행이 남으면 사후에 혼란을 준다).
    recorded = []
    monkeypatch.setattr(trader, "_submit_order",
                        lambda *a, **k: recorded.append(1))

    with pytest.raises(NotImplementedError):
        trader.run_overseas_cycle(ai_score=0)
    assert ordered == [], "체결 조회가 미구현인데 해외 주문이 나갔다"
    assert recorded == [], "체결 조회가 미구현인데 주문 기록이 남았다"


def test_unfilled_cancel_failure_blocks_symbol(conn, monkeypatch, caplog):
    """
    취소가 안 되면 그 종목 신규 **매수**를 막고 CRITICAL 을 남긴다 (T04b 요구사항 3).
    미체결 주문이 살아 있는 채로 계속 주문하면 주문이 겹겹이 쌓인다.
    """
    import logging

    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    order_id, _ref = trader._record_order_pending(
        conn, market="domestic", ticker="379800", side="BUY", qty=3, price=23000.0,
        order_type="LIMIT", raw_request={"PDNO": "379800"})
    db.update_order_status(conn, order_id, status="SUBMITTED",
                           broker_order_no="0001569157", broker_org_no="06010")

    with caplog.at_level(logging.CRITICAL, logger="bot"):
        trader._cancel_stale_order(
            conn, db.get_order(conn, order_id),
            db.OrderStatus("SUBMITTED", filled_qty=0.0),
            cancel_fn=lambda qty: (_ for _ in ()).throw(NotImplementedError("미구현")),
        )

    row = db.get_order(conn, order_id)
    assert row["status"] == "UNKNOWN"
    assert "379800" in (row["raw_response"] or "") or "미구현" in (row["raw_response"] or "")
    assert any("취소 불가" in r.message for r in caplog.records), "CRITICAL 로그가 없다"

    with pytest.raises(trader.DuplicateOrderBlocked):
        trader._submit_order(
            conn, market="domestic", ticker="379800", side="BUY", qty=1,
            price=23000.0, order_type="LIMIT",
            make_body=lambda: {"PDNO": "379800"},
            send=lambda body, on_uncertain=None: {"rt_cd": "0"})


def test_fully_filled_order_is_not_canceled_and_not_halting(conn, monkeypatch):
    """이미 전량 체결된 주문은 취소할 것이 없으므로 중단시키지 않는다(과잉 차단 방지)."""
    import reconcile

    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    order_id, _ref = trader._record_order_pending(
        conn, market="domestic", ticker="379800", side="BUY", qty=3, price=23000.0,
        order_type="LIMIT", raw_request=None)

    trader._cancel_stale_order(
        conn, db.get_order(conn, order_id),
        db.OrderStatus("FILLED", filled_qty=3.0),
        cancel_fn=lambda qty: {"rt_cd": "0"})

    assert reconcile.is_trading_halted(conn) is False


def test_successful_cancel_marks_canceled_and_does_not_halt(conn, monkeypatch):
    """취소가 성공하면 CANCELED 로 바꾸고 중단하지 않는다."""
    import reconcile

    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    order_id, _ref = trader._record_order_pending(
        conn, market="domestic", ticker="379800", side="BUY", qty=3, price=23000.0,
        order_type="LIMIT", raw_request=None)

    trader._cancel_stale_order(
        conn, db.get_order(conn, order_id),
        db.OrderStatus("SUBMITTED", filled_qty=0.0),
        cancel_fn=lambda qty: {"rt_cd": "0", "output": {}})

    assert db.get_order(conn, order_id)["status"] == "CANCELED"
    assert reconcile.is_trading_halted(conn) is False
