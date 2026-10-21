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
from kis_overseas import _ci_get, _request, request_order

logger = logging.getLogger("bot")

# 국내 주문 TR_ID (모의, 실전). T04b 에서 **신 TR_ID 로 교체**했다.
# 근거: docs/kis_api_fields.md §1 주식주문(현금)
#   실전 매수 TTTC0012U / 매도 TTTC0011U, 모의 매수 VTTC0012U / 매도 VTTC0011U
#   구 TR_ID(TTTC0802U/TTTC0801U 등)에 대해 문서가
#   "사전 고지 없이 막힐 수 있음, 반드시 신TR로 변경"이라 명시하고 있다.
_TR_BUY  = ("VTTC0012U", "TTTC0012U")   # 현금 매수 주문 (신 TR_ID)
_TR_SELL = ("VTTC0011U", "TTTC0011U")   # 현금 매도 주문 (신 TR_ID)
# 체결 조회 (3개월 이내). 근거: docs/kis_api_fields.md §2 주식일별주문체결조회
_TR_CCLD = ("VTTC0081R", "TTTC0081R")
# 정정취소. 근거: docs/kis_api_fields.md §3 주식주문(정정취소)
_TR_RVSECNCL = ("VTTC0013U", "TTTC0013U")
_TR_BALANCE = ("VTTC8434R", "TTTC8434R")  # 잔고 조회
_TR_PSBL_ORDER = ("VTTC8908R", "TTTC8908R")  # 매수가능조회

# 주문 구분 코드 (ORD_DVSN). **T04b 에서 바로잡았다.**
# 근거: docs/kis_api_fields.md §1 Request Body ORD_DVSN 행 —
#       "[KRX] 00 지정가, 01 시장가, 02 조건부지정가, ..."
# T04 까지 코드는 지정가에 "01" 을 보내고 있었다. 문서 기준으로는 "01" 이 **시장가**라
# 지정가 주문이 시장가로 나갈 수 있었다(게다가 시장가는 ORD_UNPR="0" 이어야 하는데
# 실제 단가를 함께 보내는 모순된 요청이었다).
_DVSN_LIMIT = "00"    # 지정가 (주문단가를 문자열로 지정)
_DVSN_MARKET = "01"   # 시장가 (ORD_UNPR = "0")

# 국내 주식/ETF 호가 단위(틱) 구간표.
# VERIFY: 이 표가 **일반 주식 호가표인지 ETF 호가표인지** docs/kis_api_fields.md 에 없다.
#         ("이 파일에 없는 것" 목록에 "ETF 호가 단위표"가 명시돼 있다.)
#         값이 틀리면 지정가가 호가 단위에 맞지 않아 주문이 거부될 수 있다.
#         근거가 확인될 때까지 config.ORDER_PRICE_TICKS 기본값은 0(현재가 그대로)이다 —
#         틱을 옮기지 않으면 이 표가 틀려도 주문에 영향이 없다.
#         주문이 거부되면 로그의 msg1 을 보고 이 표를 고쳐야 한다.
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
    """
    설정된 주문 유형에 맞는 ORD_DVSN 코드.

    근거: docs/kis_api_fields.md §1 Request Body — 00 지정가 / 01 시장가.
    `ORDER_TYPE` 이 limit/market 이외 값이면 조용히 넘어가지 않고 예외로 막는다
    (모르는 값을 지정가로 해석하면 의도와 다른 주문이 나갈 수 있다).
    """
    if config.ORDER_TYPE == "market":
        return _DVSN_MARKET
    if config.ORDER_TYPE == "limit":
        return _DVSN_LIMIT
    raise ValueError(
        f"ORDER_TYPE 은 limit 또는 market 이어야 합니다: {config.ORDER_TYPE!r}"
    )


def _order_price(price: float, *, side: str) -> str:
    """
    주문단가(ORD_UNPR) 문자열. **지정가일 때만** 단가를 계산한다.

    시장가는 문서가 `ORD_UNPR = "0"` 이라고 명시하므로(§1 ORD_UNPR 행) 단가를 넣지 않는다.
    지정가는 매수 현재가 + N틱, 매도 현재가 − N틱.
    `ORDER_PRICE_TICKS=0` 이면 현재가 그대로다(틱 표가 검증되지 않았을 때 안전한 선택).
    """
    if _order_dvsn() == _DVSN_MARKET:
        return "0"      # 시장가: 문서 기준 ORD_UNPR = "0"
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


def supports_order_inquiry() -> bool:
    """
    이 모듈이 **주문 체결 조회를 실제로 구현했는지** 알려준다 (T04b).

    근거: `docs/kis_api_fields.md` §2 주식일별주문체결조회 — 구현 완료.
    호출부가 "구현됐는지"를 알려고 함수를 실제로 불러 보게 하지 않기 위한 플래그다
    (그러면 조회 요청이 진짜로 나가고 실패 원인도 구분되지 않는다).
    """
    return True


def _tr(pair: tuple) -> str:
    """
    (모의 TR_ID, 실전 TR_ID) 에서 현재 모드의 값을 고른다.

    # VERIFY: 이 함수는 T04b 이전까지 **정의되지 않은 채** 여러 곳에서 호출되고 있었다
    #         (조회 3곳 + 주문 1곳). 즉 해당 경로는 실행되면 NameError 로 죽었다.
    #         모드 판정은 절대 규칙 1에 따라 `config.IS_MOCK` 만 쓴다
    #         (`TRADING_MODE` 는 표시용이라 쓰지 않는다).
    """
    return pair[0] if config.IS_MOCK else pair[1]


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


def _find_order_row(rows: list[dict], order_no: str) -> dict | None:
    """output1 행들에서 주문번호가 일치하는 행을 찾는다(대소문자 무시)."""
    target = str(order_no).lstrip("0") or "0"
    for row in rows:
        if not isinstance(row, dict):
            continue
        got = _ci_get(row, "odno")
        if got and (got.lstrip("0") or "0") == target:
            return row
    return None


def get_order_status(order_no: str, *, ticker: str | None = None,
                     order_date: str | None = None,
                     org_no: str | None = None) -> "db.OrderStatus":
    """
    국내주식 주문 1건의 체결 상태를 조회한다 (T04b).

    근거: `docs/kis_api_fields.md` §2 주식일별주문체결조회
      - `GET /uapi/domestic-stock/v1/trading/inquire-daily-ccld`
      - 모의 3개월 이내 `VTTC0081R` / 실전 `TTTC0081R`
      - **한 번 호출 최대 건수: 실전 100건, 모의 15건.** 그래서 `ODNO`(주문번호)로
        좁혀 조회하고, 결과에서 그 주문번호를 **못 찾으면 조회 실패**로 처리한다
        (다른 주문의 체결을 이 주문 것으로 오인하지 않기 위함).
      - 응답은 `output1` 배열(소문자 키)만 쓴다. `output2` 는 문서 안에서 필드명과
        한글명이 어긋나 있어 신뢰하지 않는다(문서가 그렇게 적고 있다).

    사용 필드(문서 §2 output1 표): `odno`, `ord_qty`, `tot_ccld_qty`, `avg_prvs`,
    `rmn_qty`, `rjct_qty`, `cncl_yn`.

    상태 판정 (문서에 상태 코드표가 없어 **확인된 필드만으로** 판정한다)
      - `cncl_yn` 이 "Y"      → CANCELED
      - `rjct_qty` > 0        → REJECTED
      - `tot_ccld_qty` >= 주문수량 → FILLED
      - 0 < `tot_ccld_qty`    → PARTIAL
      - 그 외                 → SUBMITTED
    # VERIFY: 문서에 주문 상태 코드 필드가 없어 위 규칙은 **해석**이다. 실제 상태 코드가
    #         있는 응답을 보게 되면 그 값으로 교체해야 한다.

    Args:
        order_date: 조회 기준일(YYYYMMDD). None 이면 오늘(KST).
        org_no: 주문채번지점번호(`ORD_GNO_BRNO`). None 이면 빈 값으로 조회한다.
    """
    if not order_no:
        raise ValueError("주문번호(order_no) 없이 체결 조회를 할 수 없습니다")

    day = order_date or db.now_kst().strftime("%Y%m%d")
    cano, prdt = _account()
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "INQR_STRT_DT": day,
        "INQR_END_DT": day,
        "SLL_BUY_DVSN_CD": "00",     # 전체 (매도·매수)
        "PDNO": ticker or "",
        "ORD_GNO_BRNO": org_no or "",
        "ODNO": str(order_no),       # 모의 15건 제한 → 주문번호로 좁힌다
        "CCLD_DVSN": "00",           # 전체 (체결·미체결)
        "INQR_DVSN": "00",           # 역순
        "INQR_DVSN_1": "",
        "INQR_DVSN_3": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    resp = _request("GET", url, _tr(_TR_CCLD), params=params)
    resp.raise_for_status()
    body = resp.json()

    rows = body.get("output1") or []
    row = _find_order_row(rows, order_no)
    if row is None:
        # 못 찾으면 조회 실패다. 0건을 "미체결"로 해석하면 실제 체결을 놓친다.
        raise LookupError(
            f"주문번호 {order_no} 를 체결 조회 결과에서 찾지 못했습니다 "
            f"(조회일 {day}, {len(rows)}건). 주문 직후이거나 조회 기간이 다를 수 있습니다."
        )

    return _status_from_row(row)


def _status_from_row(row: dict) -> "db.OrderStatus":
    """체결 조회 output1 행 1개를 `db.OrderStatus` 로 바꾼다 (T04b)."""
    ord_qty = _num(_ci_get(row, "ord_qty"))
    ccld_qty = _num(_ci_get(row, "tot_ccld_qty"))
    rjct_qty = _num(_ci_get(row, "rjct_qty"))
    cncl_yn = (_ci_get(row, "cncl_yn") or "").strip().upper()
    avg_prvs = _num(_ci_get(row, "avg_prvs"))

    if cncl_yn == "Y":
        status = "CANCELED"
    elif rjct_qty > 0:
        status = "REJECTED"
    elif ord_qty > 0 and ccld_qty >= ord_qty:
        status = "FILLED"
    elif ccld_qty > 0:
        status = "PARTIAL"
    else:
        status = "SUBMITTED"

    return db.OrderStatus(
        status=status, filled_qty=ccld_qty,
        avg_fill_price=(avg_prvs if avg_prvs > 0 else None),
        raw=dict(row),
    )


def cancel_order(order_no: str, *, quantity: int, ticker: str,
                 org_no: str | None = None) -> dict:
    """
    국내주식 미체결 주문을 취소한다 (T04b).

    근거: `docs/kis_api_fields.md` §3 주식주문(정정취소)
      - `POST /uapi/domestic-stock/v1/trading/order-rvsecncl`
      - 모의 `VTTC0013U` / 실전 `TTTC0013U`
      - `RVSE_CNCL_DVSN_CD`: **01 정정 / 02 취소**
      - `QTY_ALL_ORD_YN`: Y 전량 / N 일부
      - `KRX_FWDG_ORD_ORGNO`(필수, 주문 응답의 같은 이름 값) / `ORGN_ODNO`(원주문번호)

    **미구현(확인 필요) — 절대 규칙 4.** 문서에 **취소(02) 예시가 없고**, 취소 시
    `ORD_QTY`·`ORD_UNPR` 에 어떤 값을 넣어야 하는지가 "이 파일에 없는 것" 목록에
    명시돼 있다. 추측한 값을 넣으면 잘못된 수량·가격으로 정정/취소가 나갈 수 있다.
    그래서 요청을 보내지 않고 여기서 막는다.

    문서 §3 개요는 호출 전에 "주식정정취소가능주문조회"로 `psbl_qty` 를 확인하라고
    권고하지만, 그 API 문서도 이 파일에 없다(`trader.get_cancelable_order()` 가
    `NotImplementedError`). 그래서 **가능 수량 검증 없이** 취소를 보내게 된다.

    TODO: 공식 문서에서 취소(02) 예시와 정정취소가능주문조회 API 를 확인하면 구현한다.
          확인 전에는 호출부(`trader._cancel_stale_order`)가 이 예외를 잡아
          **해당 종목 신규 매수를 차단하고 CRITICAL 로그**를 남긴다.
    """
    # 실전 가드는 유지한다: 구현될 때 가드 없이 나가지 않도록.
    config.assert_order_allowed()
    raise NotImplementedError(
        "국내주식 주문취소는 공식 문서에 취소(02) 예시가 없고 ORD_QTY/ORD_UNPR 규칙이 "
        "확인되지 않아 구현하지 않았습니다(docs/kis_api_fields.md). "
        "추측한 값을 넣으면 잘못된 수량·가격으로 주문이 나갈 수 있습니다. "
        "TODO: 취소 예시 확인 후 구현."
    )
