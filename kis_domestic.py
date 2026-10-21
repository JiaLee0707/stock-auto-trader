"""
KIS 국내주식 ETF API 모듈.

주요 기능:
  - get_domestic_price(): 국내 ETF 현재가 조회 (원화)
  - get_domestic_balance(): 국내 보유 잔고 및 예수금 조회
  - buy_domestic(): 국내 ETF 지정가 매수
  - sell_domestic_all(): 국내 ETF 전량 지정가 매도

모의투자 운영규정 참고:
  - 시간외매매 불가 (실전투자는 가능)
  - 매매제한종목: 현재가 1,000원 미만, 총발행주식수 100,000주 미만, 거래정지 등
"""
import logging

import config
import db
from kis_overseas import _request, request_order

logger = logging.getLogger("bot")

# 국내 주문 TR_ID (모의/실전)
_TR_BUY  = ("VTTC0802U", "TTTC0802U")   # 현금 매수 주문
_TR_SELL = ("VTTC0801U", "TTTC0801U")   # 현금 매도 주문
_TR_BALANCE = ("VTTC8434R", "TTTC8434R")  # 잔고 조회
_TR_PSBL_ORDER = ("VTTC8908R", "TTTC8908R")  # 매수가능조회

# 주문 구분 코드 (ORD_DVSN). 저장소가 이미 쓰던 값만 사용한다.
#   01 = 지정가 (기존 buy_domestic/sell_domestic_all 이 쓰던 값)
#   00 = 시장가 (기존 코드에서 확인되지 않음 → 확인 필요)
# VERIFY: 국내 시장가(00)는 공식 문서로 확인하지 않았다. ORDER_TYPE=market 을 쓰려면
#         공식 문서 확인이 먼저다. 확인 전에는 지정가만 동작한다.
_DVSN_LIMIT = "01"
_DVSN_MARKET = "00"

# 국내 주식/ETF 호가 단위(틱) 구간표.
# VERIFY: 아래 구간은 **공식 자료로 확인하지 못했다.** 저장소 코드에도 틱 계산이 없었다.
#         값이 틀리면 지정가가 호가 단위에 맞지 않아 주문이 거부될 수 있다.
#         주문이 거부되면 로그의 msg1 을 보고 이 표를 고쳐야 한다.
#         확인 전까지는 _order_price() 가 "현재가 그대로"를 쓰도록 TICKS=0 을 권장한다.
_TICK_TABLE = (
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
_TICK_TABLE_ABOVE = 1000


def tick_size(price: float) -> int:
    """
    국내 주식/ETF 호가 단위(원). 가격대별 구간표를 따른다.

    ETF 는 주식과 같은 호가 단위를 쓴다고 보고 있으나(확인 필요), 이 표 자체가
    공식 자료로 검증되지 않았다. `_TICK_TABLE` 주석 참고.
    """
    for upper, tick in _TICK_TABLE:
        if price < upper:
            return tick
    return _TICK_TABLE_ABOVE


def _order_dvsn() -> str:
    """설정된 주문 유형에 맞는 ORD_DVSN 코드. market 은 확인 전이라 예외로 막는다."""
    if config.ORDER_TYPE == "market":
        raise NotImplementedError(
            "ORDER_TYPE=market(국내 시장가, ORD_DVSN=00)은 공식 문서로 확인되지 않았습니다. "
            "확인 전에는 지정가(ORDER_TYPE=limit)만 사용하세요."
        )
    return _DVSN_LIMIT


def _order_price(price: float, *, side: str) -> str:
    """
    지정가 주문가. 매수는 현재가 + N틱, 매도는 현재가 - N틱.

    `ORDER_PRICE_TICKS=0` 이면 현재가 그대로다(틱 표가 검증되지 않았을 때 안전한 선택).
    가격은 호가 단위에 맞춰 반올림한다 — 호가 단위에 맞지 않으면 주문이 거부된다.
    """
    ticks = max(0, int(config.ORDER_PRICE_TICKS))
    unit = tick_size(price)
    if ticks == 0:
        raw = int(price)
    else:
        # 매수는 위로, 매도는 아래로 → 체결 가능성을 높이는 방향
        offset = ticks * unit
        raw = int(price + offset) if side == "BUY" else int(price - offset)
    # 호가 단위에 맞춤
    snapped = int(round(raw / unit) * unit) if unit > 0 else raw
    return str(max(unit, snapped))


def _account() -> tuple:
    """계좌번호를 (CANO, ACNT_PRDT_CD)로 분리."""
    return config.KIS_ACCOUNT_NO.split("-")


def _num(v) -> float:
    """KIS 응답은 미사용 필드를 None으로 주므로 안전하게 0으로 변환."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _check_rt_cd(result: dict, action: str) -> dict:
    """주문 응답의 rt_cd를 확인. 0이 아니면 실패로 간주해 예외 발생."""
    if str(result.get("rt_cd")) != "0":
        raise RuntimeError(
            f"{action} 실패 [{result.get('msg_cd')}] {result.get('msg1', '').strip()}"
        )
    return result


def get_domestic_price(ticker: str) -> float:
    """국내 ETF 현재가 조회 (원화)."""
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",   # 주식·ETF
        "FID_INPUT_ISCD": ticker,
    }
    resp = _request("GET", url, "FHKST01010100", params=params)
    resp.raise_for_status()
    output = resp.json().get("output", {})
    price = _num(output.get("stck_prpr"))   # 현재가
    if price <= 0:
        raise ValueError(f"국내 현재가 조회 실패: {ticker} → {price}")
    return price


def get_daily_closes(ticker: str, count: int = 60) -> list[float]:
    """
    국내주식 일봉 종가를 과거 → 최신 순으로 반환 (보조지표 계산용).
    """
    from datetime import date, timedelta

    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=count * 2)).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",     # 수정주가 반영
    }
    resp = _request("GET", url, "FHKST03010100", params=params)
    resp.raise_for_status()
    rows = resp.json().get("output2") or []
    # API는 최신순으로 주므로 역순 정렬
    closes = [_num(r.get("stck_clpr")) for r in reversed(rows)]
    return [c for c in closes if c > 0][-count:]


def get_domestic_balance(ticker: str | None = None) -> dict:
    """
    국내주식 잔고 및 예수금 조회.

    반환: {
        "quantity": float,          # 보유 수량
        "avg_price_krw": float,     # 평균 단가 (원화)
        "eval_amt_krw": float,      # 평가 금액 (원화)
        "profit_rate": float,       # 수익률 (%)
        "cash_krw": float,          # 주문가능금 (원화)
        "total_asset_krw": float,   # 총자산 (원화)
    }
    """
    ticker = ticker or config.DOMESTIC_TICKER
    cano, prdt = _account()
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    resp = _request("GET", url, _tr(_TR_BALANCE), params=params)
    resp.raise_for_status()
    result = resp.json()

    holdings = result.get("output1") or []
    output2 = result.get("output2") or {}
    if isinstance(output2, list):
        output2 = output2[0] if output2 else {}

    # 대상 종목 찾기 (코스피/코스닥 ETF는 pdno = 종목코드)
    target = next((h for h in holdings if h.get("pdno") == ticker), {})

    quantity    = _num(target.get("hldg_qty"))
    avg_price   = _num(target.get("pchs_avg_pric"))
    eval_amt    = _num(target.get("evlu_amt"))
    profit_rate = _num(target.get("evlu_pfls_rt"))
    cash_krw    = _num(output2.get("dnca_tot_amt"))       # 예수금 총금액
    total_krw   = _num(output2.get("tot_evlu_amt"))       # 총평가금액

    # 총자산이 0으로 오면 보유 평가액 + 예수금으로 보정
    if total_krw <= 0:
        total_krw = eval_amt + cash_krw

    logger.info(
        f"[국내잔고] {ticker} {quantity:.0f}주 | 평가 {eval_amt:,.0f}원 | "
        f"수익률 {profit_rate:.2f}% | 주문가능 {cash_krw:,.0f}원 | 총자산 {total_krw:,.0f}원"
    )

    return {
        "quantity": quantity,
        "avg_price_krw": avg_price,
        "eval_amt_krw": eval_amt,
        "profit_rate": profit_rate,
        "cash_krw": cash_krw,
        "total_asset_krw": total_krw,
    }


def get_all_holdings() -> list[dict]:
    """
    국내주식 **보유 전 종목**을 돌려준다 (T03 대조용).

    `get_domestic_balance()` 는 한 종목만 보지만, 브로커와 DB 를 대조하려면 전체가 필요하다.
    엔드포인트·TR_ID·파라미터는 `get_domestic_balance()` 와 동일한 것을 쓴다.
    사용하는 필드도 이미 확인된 것만 쓴다: pdno, hldg_qty, pchs_avg_pric.

    반환: [{"symbol": str, "market": "domestic", "qty": float, "avg_price": float}, ...]
    """
    cano, prdt = _account()
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    resp = _request("GET", url, _tr(_TR_BALANCE), params=params)
    resp.raise_for_status()
    holdings = resp.json().get("output1") or []

    result = []
    for h in holdings:
        qty = _num(h.get("hldg_qty"))
        if qty <= 0:
            continue    # 보유 0건 행은 대조 대상이 아니다
        result.append({
            "symbol": h.get("pdno"),
            "market": "domestic",
            "qty": qty,
            "avg_price": _num(h.get("pchs_avg_pric")),
        })
    return result


def get_orderable_cash(ticker: str, price: float) -> float:
    """국내 주문가능금액 조회 (지정가 기준)."""
    cano, prdt = _account()
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "PDNO": ticker,
        "ORD_UNPR": str(int(price)),
        "ORD_DVSN": "01",              # 지정가
        "CMA_EVLU_AMT_ICLD_YN": "N",
        "OVRS_ICLD_YN": "N",
    }
    resp = _request("GET", url, _tr(_TR_PSBL_ORDER), params=params)
    resp.raise_for_status()
    output = resp.json().get("output", {})
    return _num(output.get("ord_psbl_cash"))


def order_body(ticker: str, quantity: int, price: float, *, side: str) -> dict:
    """
    국내 주문 **요청 body**를 만든다 (전송될 값 그대로).

    T04: 호출부(trader)가 주문을 보내기 **전에** 이 body 를 `orders.raw_request` 에
    기록해야 "무엇을 주문했는지"를 사후에 확인할 수 있다. 그래서 body 생성을
    전송과 분리해 노출한다.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"허용되지 않는 주문 방향: {side!r}")
    cano, prdt = _account()
    return {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "PDNO": ticker,
        "ORD_DVSN": _order_dvsn(),
        "ORD_QTY": str(int(quantity)),
        "ORD_UNPR": _order_price(price, side=side),
    }


def send_order(body: dict, *, side: str, on_uncertain=None) -> dict:
    """
    만들어 둔 body 를 실제로 전송한다. **재시도하지 않는다**(절대 규칙 3).

    `on_uncertain`: 결과 미확정(타임아웃·500) 시 호출될 콜백. 호출부가 `UNKNOWN` 으로
    기록하는 데 쓴다.
    """
    # 실전 가드: 이 함수가 실제 전송 지점이므로 여기에도 반드시 있어야 한다 (T01b)
    config.assert_order_allowed()

    tr_id = _tr(_TR_BUY if side == "BUY" else _TR_SELL)
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    resp = request_order("POST", url, tr_id, json=body, on_uncertain=on_uncertain)
    resp.raise_for_status()
    action = "국내 매수 주문" if side == "BUY" else "국내 매도 주문"
    result = _check_rt_cd(resp.json(), action)
    logger.info(f"{action} 접수: {body.get('PDNO')} {body.get('ORD_QTY')}주 → {result.get('msg1')}")
    return result


def buy_domestic(ticker: str, quantity: int, price: float, *, on_uncertain=None) -> dict:
    """
    국내 ETF 지정가 매수. (하위 호환 래퍼 — 실제 전송은 `send_order()`)

    `on_uncertain`: 결과 미확정(타임아웃·500) 시 호출될 콜백. 호출부가 `UNKNOWN` 으로
    기록하는 데 쓴다. **재시도하지 않는다**(절대 규칙 3).
    """
    # 실전 가드: 다른 사전 검증보다 **먼저** 와야 한다 (T01b)
    config.assert_order_allowed()

    if quantity <= 0:
        raise ValueError(f"매수 수량은 1주 이상이어야 합니다: {quantity}")

    body = order_body(ticker, quantity, price, side="BUY")
    return send_order(body, side="BUY", on_uncertain=on_uncertain)


def sell_domestic_all(ticker: str, quantity: int, price: float, *, on_uncertain=None) -> dict:
    """
    국내 ETF 전량 매도. (하위 호환 래퍼 — 실제 전송은 `send_order()`)

    `on_uncertain`: 결과 미확정 시 콜백. **재시도하지 않는다.**
    """
    # 실전 가드: 다른 사전 검증보다 먼저 (T01b)
    config.assert_order_allowed()

    if quantity <= 0:
        raise ValueError(f"매도 수량은 1주 이상이어야 합니다: {quantity}")

    body = order_body(ticker, quantity, price, side="SELL")
    return send_order(body, side="SELL", on_uncertain=on_uncertain)


def get_order_status(order_no: str, *, ticker: str | None = None,
                     order_date: str | None = None) -> "db.OrderStatus":
    """
    국내주식 주문 1건의 체결 상태를 조회한다 (T04).

    **미구현(확인 필요).** 국내 주문체결 조회의 엔드포인트·TR_ID·응답 필드명을
    공식 문서·공식 샘플에서 확인하지 못했다(절대 규칙 4). 추측 구현을 하지 않는다.
    """
    raise NotImplementedError(
        "국내주식 주문체결 조회의 엔드포인트/TR_ID/응답 필드명이 공식 문서·공식 샘플에서 "
        "확인되지 않았습니다. 확인 전에는 추측 구현하지 않습니다. "
        "TODO: 공식 문서 확인 후 구현."
    )


def cancel_order(order_no: str, *, quantity: int, ticker: str) -> dict:
    """
    국내주식 미체결 주문을 취소한다 (T04).

    **미구현(확인 필요).** 취소 엔드포인트·TR_ID·필수 파라미터(원주문번호, 전량/부분 구분)를
    공식 문서·공식 샘플에서 확인하지 못했다(절대 규칙 4).
    """
    # 실전 가드는 유지한다: 나중에 구현될 때 가드 없이 나가지 않도록.
    config.assert_order_allowed()
    raise NotImplementedError(
        "국내주식 주문취소의 엔드포인트/TR_ID/필수 파라미터가 공식 문서·공식 샘플에서 "
        "확인되지 않았습니다. 확인 전에는 추측 구현하지 않습니다."
    )
