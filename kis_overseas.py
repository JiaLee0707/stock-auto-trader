"""
KIS 해외주식 API 모듈 (Phase 1: 소수점 주문 포함).

주요 기능:
  - get_access_token(): 토큰 발급 및 파일 캐싱 (24시간 유효)
  - get_current_price(): 현재가 조회
  - get_balance(): 보유 잔고 및 평균 단가 조회
  - get_usd_krw_rate(): USD/KRW 환율 조회
  - buy_fractional(): 소수점 매수 주문 (원화 금액 기준)
  - sell_all(): 전량 매도 주문
"""
import json
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests

import config

logger = logging.getLogger("bot")

# ─────────────────────────────────────────────
# 토큰 관리
# ─────────────────────────────────────────────

def _load_cached_token() -> dict | None:
    """캐시 파일에서 토큰 로드. 만료 시 None 반환."""
    cache = Path(config.TOKEN_CACHE_FILE)
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text())
        expire_dt = datetime.fromisoformat(data["expires_at"])
        if datetime.now() < expire_dt - timedelta(minutes=30):  # 30분 여유
            return data
    except Exception:
        pass
    return None


def _save_token(token: str, expires_in_sec: int = 86400):
    cache = Path(config.TOKEN_CACHE_FILE)
    data = {
        "access_token": token,
        "expires_at": (datetime.now() + timedelta(seconds=expires_in_sec)).isoformat(),
    }
    cache.write_text(json.dumps(data, ensure_ascii=False))


def get_access_token() -> str:
    """토큰 캐시 우선 사용, 만료 시 재발급."""
    cached = _load_cached_token()
    if cached:
        return cached["access_token"]

    url = f"{config.KIS_BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
    }
    resp = requests.post(url, json=body, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    token = result["access_token"]
    _save_token(token, int(result.get("expires_in", 86400)))
    logger.info("KIS 토큰 신규 발급 완료")
    return token


def _headers(token: str, tr_id: str) -> dict:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": config.KIS_APP_KEY,
        "appsecret": config.KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }


# KIS 모의투자는 초당 거래건수 제한이 있어 0.5초 간격으로 호출하면
# "초당 거래건수를 초과하였습니다" (HTTP 500) 오류가 간헐 발생한다.
# 모의/실전 공통으로 1초 이상 간격을 두고, rate limit이면 짧게 대기 후 재시도한다.
_MIN_CALL_INTERVAL_SEC = 1.0
_RETRY_SLEEP_SEC = 1.5
_last_call_ts = 0.0


def _request(method: str, url: str, tr_id: str, *, max_attempts: int = 3, **kwargs):
    """
    KIS API 공통 호출 래퍼.
    - 호출 간 최소 간격을 보장해 초당 거래건수 제한을 회피
    - HTTP 500(rate limit 등)이면 재시도
    """
    global _last_call_ts

    last_error = None
    for attempt in range(max_attempts):
        since = time.monotonic() - _last_call_ts
        if since < _MIN_CALL_INTERVAL_SEC:
            time.sleep(_MIN_CALL_INTERVAL_SEC - since)

        try:
            resp = requests.request(
                method, url, headers=_headers(get_access_token(), tr_id),
                timeout=10, **kwargs,
            )
        finally:
            _last_call_ts = time.monotonic()

        if resp.status_code != 500:
            return resp

        last_error = resp
        body = ""
        try:
            body = str(resp.json().get("msg1", ""))
        except Exception:
            pass
        logger.debug(f"KIS 500 응답 ({attempt+1}/{max_attempts}) {body.strip()}")
        if attempt < max_attempts - 1:
            time.sleep(_RETRY_SLEEP_SEC)

    return last_error if last_error is not None else resp


# ─────────────────────────────────────────────
# 시세 / 잔고
# ─────────────────────────────────────────────

def get_current_price(ticker: str) -> float:
    """해외주식 현재가(USD) 조회."""
    url = f"{config.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/price"
    params = {
        "AUTH": "",
        "EXCD": "NAS",          # 나스닥
        "SYMB": ticker,
    }
    try:
        resp = _request("GET", url, "HHDFS00000300", params=params)
        resp.raise_for_status()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        logger.warning(f"KIS API 네트워크 오류 (get_current_price): {e}")
        # 모의투자 모드에서는 기본값 반환
        if config.IS_MOCK:
            logger.info("모의투자 모드 - 기본값 100.00으로 반환")
            return 100.0
        raise
    except requests.exceptions.HTTPError as e:
        logger.error(f"KIS 시세 조회 최종 실패: {e}")
        raise
    data = resp.json()
    return float(data["output"]["last"])


def get_daily_closes(ticker: str, count: int = 60) -> list[float]:
    """
    해외주식 일봉 종가를 과거 → 최신 순으로 반환 (보조지표 계산용).
    """
    url = f"{config.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/dailyprice"
    params = {
        "AUTH": "",
        "EXCD": "NAS",
        "SYMB": ticker,
        "GUBN": "0",        # 0=일봉
        "BYMD": "",
        "MODP": "1",        # 수정주가 반영
    }
    resp = _request("GET", url, "HHDFS76240000", params=params)
    resp.raise_for_status()
    rows = resp.json().get("output2") or []
    # API는 최신순으로 주므로 역순 정렬
    closes = [_to_float(r.get("clos")) for r in reversed(rows)]
    return [c for c in closes if c > 0][-count:]


def _to_float(v) -> float:
    """KIS 응답 문자열을 float로 변환 (실패 시 0)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def get_balance() -> dict:
    """
    해외주식 잔고 조회.
    반환: {
        "quantity": float,          # 보유 수량
        "avg_price_usd": float,     # 평균 단가 (USD)
        "eval_amt_krw": float,      # 평가 금액 (원화)
        "profit_rate": float,       # 수익률 (%)
        "cash_krw": float,          # 주문가능금 (원화) = 외화 예수금 + 국내 원화 예수금
        "total_asset_krw": float,   # 총자산 (원화) = 평가금액 + 주문가능금
    }
    """
    # 모의/실전 TR_ID 분기
    tr_id = "VTTS3012R" if config.IS_MOCK else "TTTS3012R"
    url = f"{config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
    account_no, account_suffix = config.KIS_ACCOUNT_NO.split("-")
    params = {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_suffix,
        "OVRS_EXCG_CD": "",
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }
    resp = _request("GET", url, tr_id, params=params)
    resp.raise_for_status()
    result = resp.json()

    output2 = result.get("output2", {})
    holdings = result.get("output1", [])

    # 대상 종목 찾기
    target = next(
        (h for h in holdings if h.get("ovrs_pdno") == config.TARGET_TICKER), {}
    )

    rate = get_usd_krw_rate()

    def _num(v) -> float:
        """KIS 응답은 미사용 필드를 None으로 주므로 안전하게 0으로 변환."""
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    quantity     = _num(target.get("ovrs_cblc_qty"))
    avg_price    = _num(target.get("pchs_avg_pric"))
    # 보유분 평가금액: USD(ovrs_stck_evlu_amt) × 환율
    eval_amt_krw = _num(target.get("ovrs_stck_evlu_amt")) * rate
    profit_rate  = _num(target.get("evlu_pfls_rt"))

    # 통합증거금 계좌: 외화 예수금과 국내 원화 예수금을 합산해 주문가능금으로 사용
    cash_frcr_krw = _num(output2.get("frcr_dncl_amt_2")) * rate
    cash_dom_krw  = 0.0

    try:
        dom_tr_id = "VTTC8434R" if config.IS_MOCK else "TTTC8434R"
        dom_url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
        dom_params = {
            "CANO": account_no,
            "ACNT_PRDT_CD": account_suffix,
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
        dom_resp = _request("GET", dom_url, dom_tr_id, params=dom_params)
        if dom_resp.status_code == 200:
            dom_data = dom_resp.json()
            dom_output2 = dom_data.get("output2", {})
            if isinstance(dom_output2, list):
                if dom_output2:
                    dom_output2 = dom_output2[0]
                else:
                    logger.warning("국내잔고 output2 빈 리스트 — dnca=0으로 처리")
                    dom_output2 = {}
            cash_dom_krw = _num(dom_output2.get("dnca_tot_amt"))
            logger.debug(f"국내잔고 원화 예수금(dnca): {cash_dom_krw:,.0f}원")
    except Exception as e:
        logger.warning(f"원화 예수금(통합증거금) 조회 실패: {e}")

    # 통합증거금: 주문가능금 = 외화 예수금(원화환산) + 국내 원화 예수금
    # KIS 모의 서버는 frcr_dncl_amt_2를 내려주지 않는 경우가 있어, 외화가 0이면
    # 국내 원화 예수금으로 대체한다 (자동환전 계좌는 원화만 존재).
    if cash_frcr_krw <= 0:
        cash_krw = cash_dom_krw
    else:
        cash_krw = cash_frcr_krw + cash_dom_krw

    # 총자산 = 보유 평가금액 + 주문가능금
    total_krw = eval_amt_krw + cash_krw

    logger.info(
        f"[통합증거금] 외화 {cash_frcr_krw:,.0f}원 + 국내 {cash_dom_krw:,.0f}원 "
        f"= 주문가능 {cash_krw:,.0f}원 | 총자산 {total_krw:,.0f}원"
    )

    if cash_krw <= 0:
        logger.warning("⚠️ 예수금 조회 최종 실패 — 0원으로 처리됨 (API 응답 확인 필요)")

    return {
        "quantity": quantity,
        "avg_price_usd": avg_price,
        "eval_amt_krw": eval_amt_krw,
        "profit_rate": profit_rate,
        "cash_krw": cash_krw,
        "total_asset_krw": total_krw,
    }


def get_usd_krw_rate() -> float:
    """USD/KRW 환율 조회 (KIS 해외시세 환율 엔드포인트)."""
    url = f"{config.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
    params = {
        "FID_COND_MRKT_DIV_CODE": "X",
        "FID_INPUT_ISCD": "FX@KRW",     # 원/달러
        "FID_INPUT_DATE_1": "",
        "FID_INPUT_DATE_2": "",
        "FID_PERIOD_DIV_CODE": "D",
    }
    try:
        resp = _request("GET", url, "FHKST03030100", params=params)
        resp.raise_for_status()
        output = resp.json().get("output1") or {}
        rate = float(output.get("ovrs_nmix_prpr"))   # 현재 환율
        if rate <= 0:
            raise ValueError(f"비정상 환율: {rate}")
        return rate
    except Exception as e:
        logger.warning(f"환율 조회 실패({e}), 기본값 1350 사용")
        return 1350.0


# ─────────────────────────────────────────────
# 주문
# ─────────────────────────────────────────────

def buy_shares(quantity: int, price_usd: float) -> dict:
    """
    해외주식 현금 매수 (정수 수량 기준).
    KIS 해외주식 정규 주문 API (/uapi/overseas-stock/v1/trading/order) 사용.
    """
    if quantity <= 0:
        raise ValueError(f"매수 수량은 1주 이상이어야 합니다: {quantity}")

    # 모의: VTTT1002U, 실전: TTTT1002U (해외주식 매수주문)
    tr_id = "VTTT1002U" if config.IS_MOCK else "TTTT1002U"
    url = f"{config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"

    account_no, account_suffix = config.KIS_ACCOUNT_NO.split("-")

    body = {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_suffix,
        "OVRS_EXCG_CD": "NAS",
        "PDNO": config.TARGET_TICKER,
        "ORD_DVSN": "00",           # 지정가 (미국 시장 기준)
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": str(round(price_usd, 2)),
        "CTAC_TLNO": "",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
    }
    resp = _request("POST", url, tr_id, json=body)
    resp.raise_for_status()
    result = resp.json()
    if str(result.get("rt_cd")) != "0":
        raise RuntimeError(
            f"해외 매수 주문 실패 [{result.get('msg_cd')}] {result.get('msg1', '').strip()}"
        )
    logger.info(f"매수 주문 완료: {config.TARGET_TICKER} {quantity}주 (${price_usd:.2f}) → {result.get('msg1')}")
    return result


def sell_all(quantity: float, current_price_usd: float) -> dict:
    """보유 수량 전량 시장가 매도."""
    tr_id = "VTTS0308U" if config.IS_MOCK else "TTTS0308U"
    url = f"{config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"

    account_no, account_suffix = config.KIS_ACCOUNT_NO.split("-")

    body = {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_suffix,
        "OVRS_EXCG_CD": "NAS",
        "PDNO": config.TARGET_TICKER,
        "ORD_DVSN": "00",
        "ORD_QTY": str(quantity),
        "OVRS_ORD_UNPR": str(current_price_usd),
        "CTAC_TLNO": "",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
    }
    resp = _request("POST", url, tr_id, json=body)
    resp.raise_for_status()
    result = resp.json()
    if str(result.get("rt_cd")) != "0":
        raise RuntimeError(
            f"해외 매도 주문 실패 [{result.get('msg_cd')}] {result.get('msg1', '').strip()}"
        )
    logger.info(f"전량 매도 주문 완료: {quantity}주 → {result}")
    return result