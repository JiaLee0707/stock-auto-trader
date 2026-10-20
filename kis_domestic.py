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
from kis_overseas import _request

logger = logging.getLogger("bot")

# 국내 주문 TR_ID (모의/실전)
_TR_BUY  = ("VTTC0802U", "TTTC0802U")   # 현금 매수 주문
_TR_SELL = ("VTTC0801U", "TTTC0801U")   # 현금 매도 주문
_TR_BALANCE = ("VTTC8434R", "TTTC8434R")  # 잔고 조회
_TR_PSBL_ORDER = ("VTTC8908R", "TTTC8908R")  # 매수가능조회


def _tr(pair: tuple) -> str:
    """IS_MOCK에 따라 (모의, 실전) TR_ID 중 하나를 선택."""
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


def buy_domestic(ticker: str, quantity: int, price: float) -> dict:
    """국내 ETF 지정가 매수."""
    if quantity <= 0:
        raise ValueError(f"매수 수량은 1주 이상이어야 합니다: {quantity}")

    # 실전 가드: main() 을 거치지 않고 호출돼도 실전 오발주를 막는다 (T01b)
    config.assert_order_allowed()

    cano, prdt = _account()
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "PDNO": ticker,
        "ORD_DVSN": "01",              # 지정가
        "ORD_QTY": str(quantity),
        "ORD_UNPR": str(int(price)),
    }
    resp = _request("POST", url, _tr(_TR_BUY), json=body)
    resp.raise_for_status()
    result = _check_rt_cd(resp.json(), "국내 매수 주문")
    logger.info(
        f"국내 매수 주문 완료: {ticker} {quantity}주 ({price:,.0f}원) → {result.get('msg1')}"
    )
    return result


def sell_domestic_all(ticker: str, quantity: int, price: float) -> dict:
    """국내 ETF 전량 지정가 매도."""
    if quantity <= 0:
        raise ValueError(f"매도 수량은 1주 이상이어야 합니다: {quantity}")

    # 실전 가드: main() 을 거치지 않고 호출돼도 실전 오발주를 막는다 (T01b)
    config.assert_order_allowed()

    cano, prdt = _account()
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt,
        "PDNO": ticker,
        "ORD_DVSN": "01",              # 지정가
        "ORD_QTY": str(int(quantity)),
        "ORD_UNPR": str(int(price)),
    }
    resp = _request("POST", url, _tr(_TR_SELL), json=body)
    resp.raise_for_status()
    result = _check_rt_cd(resp.json(), "국내 매도 주문")
    logger.info(f"국내 전량 매도 주문 완료: {ticker} {quantity:.0f}주 → {result.get('msg1')}")
    return result
