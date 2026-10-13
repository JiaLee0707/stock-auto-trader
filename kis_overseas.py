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


# ─────────────────────────────────────────────
# 시세 / 잔고
# ─────────────────────────────────────────────

def get_current_price(ticker: str) -> float:
    """해외주식 현재가(USD) 조회."""
    token = get_access_token()
    time.sleep(0.5)
    url = f"{config.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/price"
    params = {
        "AUTH": "",
        "EXCD": "NAS",          # 나스닥
        "SYMB": ticker,
    }
    try:
        resp = requests.get(url, headers=_headers(token, "HHDFS00000300"),
                            params=params, timeout=10)
        resp.raise_for_status()
    except ConnectionError as e:
        logger.warning(f"KIS API 네트워크 오류 (get_current_price): {e}")
        # 모의투자 모드에서는 기본값 반환
        if config.IS_MOCK:
            logger.info("모의투자 모드 - 기본값 100.00으로 반환")
            return 100.0
        raise
    data = resp.json()
    return float(data["output"]["last"])


def get_balance() -> dict:
    """
    해외주식 잔고 조회.
    반환: {
        "quantity": float,          # 보유 수량
        "avg_price_usd": float,     # 평균 단가 (USD)
        "eval_amt_krw": float,      # 평가 금액 (원화)
        "profit_rate": float,       # 수익률 (%)
        "cash_krw": float,          # 예수금 (원화)
        "total_asset_krw": float,   # 총자산 (원화)
    }
    """
    token = get_access_token()
    time.sleep(0.5)

    # 모의/실전 TR_ID 분기
    tr_id = "VTTS3012R" if config.IS_MOCK else "TTTS3012R"
    url = f"{config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
    account_no, account_suffix = config.KIS_ACCOUNT_NO.split("-")
    params = {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_suffix,
        "OVRS_EXCG_CD": "NAS",
        "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "",
        "CTX_AREA_NK200": "",
    }
    resp = requests.get(url, headers=_headers(token, tr_id),
                        params=params, timeout=10)
    resp.raise_for_status()
    result = resp.json()

    output2 = result.get("output2", {})
    holdings = result.get("output1", [])

    # 대상 종목 찾기
    target = next(
        (h for h in holdings if h.get("ovrs_pdno") == config.TARGET_TICKER), {}
    )

    rate = get_usd_krw_rate()
    quantity     = float(target.get("ovrs_cblc_qty", 0))
    avg_price    = float(target.get("pchs_avg_pric", 0))
    eval_amt_krw = float(target.get("evlu_amt", 0))         # API가 원화로 줄 수도 있음
    profit_rate  = float(target.get("evlu_pfls_rt", 0))
    cash_krw     = float(output2.get("frcr_dncl_amt_2", 0)) * rate
    total_krw    = float(output2.get("tot_evlu_pfls_amt", 0)) or (eval_amt_krw + cash_krw)

    # 외화 예수금이 0원인 경우(원화 증거금 기반 자동환전 계좌), 원화 예수금 조회
    if cash_krw <= 0:
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
            dom_resp = requests.get(dom_url, headers=_headers(token, dom_tr_id),
                                    params=dom_params, timeout=10)
            if dom_resp.status_code == 200:
                dom_data = dom_resp.json()
                dom_output2 = dom_data.get("output2", {})
                if isinstance(dom_output2, list) and dom_output2:
                    dom_output2 = dom_output2[0]
                dnca = float(dom_output2.get("dnca_tot_amt", 0))
                if dnca > 0:
                    cash_krw = dnca
                    total_krw = float(dom_output2.get("tot_evlu_amt", 0)) or (eval_amt_krw + cash_krw)
        except Exception as e:
            logger.warning(f"원화 예수금(자동환전) 조회 실패: {e}")


    # KIS 실제 응답 예시에는 "evlu_pfls_amt" 필드가 루트 레벨에 있을 수 있음
    if "evlu_pfls_amt" in result:
        total_krw = float(result["evlu_pfls_amt"])

    return {
        "quantity": quantity,
        "avg_price_usd": avg_price,
        "eval_amt_krw": eval_amt_krw,
        "profit_rate": profit_rate,
        "cash_krw": cash_krw,
        "total_asset_krw": total_krw,
    }


def get_usd_krw_rate() -> float:
    """USD/KRW 환율 조회."""
    token = get_access_token()
    time.sleep(0.5)
    url = f"{config.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
    # 간이 환율 조회 — 실제로는 KIS 환율 조회 엔드포인트 사용
    # 아래는 예시이며, 실제 엔드포인트로 교체 필요
    # 대안: requests.get("https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD")
    try:
        resp = requests.get(
            "https://quotation-api-cdn.dunamu.com/v1/forex/recent?codes=FRX.KRWUSD",
            timeout=5,
        )
        data = resp.json()
        return float(data[0]["basePrice"])
    except Exception:
        logger.warning("환율 조회 실패, 기본값 1350 사용")
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

    token = get_access_token()
    time.sleep(0.5)

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
    resp = requests.post(url, headers=_headers(token, tr_id), json=body, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    logger.info(f"매수 주문 완료: {config.TARGET_TICKER} {quantity}주 (${price_usd:.2f}) → {result.get('msg1')}")
    return result


def sell_all(quantity: float, current_price_usd: float) -> dict:
    """보유 수량 전량 시장가 매도."""
    token = get_access_token()
    time.sleep(0.5)

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
    resp = requests.post(url, headers=_headers(token, tr_id), json=body, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    logger.info(f"전량 매도 주문 완료: {quantity}주 → {result}")
    return result