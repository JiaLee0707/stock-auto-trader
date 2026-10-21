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
# 모의/실전 공통으로 1초 이상 간격을 두고, 조회는 rate limit이면 짧게 대기 후 재시도한다.
_MIN_CALL_INTERVAL_SEC = 1.0
_RETRY_SLEEP_SEC = 1.5
_REQUEST_TIMEOUT_SEC = 10
_last_call_ts = 0.0


class OrderRequestError(RuntimeError):
    """
    주문 요청의 결과를 확정할 수 없다(타임아웃·네트워크 오류·HTTP 500).

    주문이 나갔을 수도, 안 나갔을 수도 있는 상태다. **재시도하면 중복 주문이 되므로**
    (절대 규칙 3) 다시 보내지 않고 이 예외를 올린다. 호출부는 주문 상태를 `UNKNOWN`
    으로 기록하고, 다음 사이클의 체결 조회로 확정될 때까지 그 종목의 신규 주문을 막는다.
    """


def _throttle() -> None:
    """호출 간 최소 간격을 보장해 초당 거래건수 제한을 회피한다."""
    since = time.monotonic() - _last_call_ts
    if since < _MIN_CALL_INTERVAL_SEC:
        time.sleep(_MIN_CALL_INTERVAL_SEC - since)


def request_read(method: str, url: str, tr_id: str, *, max_attempts: int = 3, **kwargs):
    """
    **조회용** 요청. 재시도·백오프·호출 간격 제한을 유지한다.

    조회는 멱등이라 재시도해도 부작용이 없다. 재시도가 허용되는 유일한 경로다.
    """
    global _last_call_ts

    last_error = None
    resp = None
    for attempt in range(max_attempts):
        _throttle()
        try:
            resp = requests.request(
                method, url, headers=_headers(get_access_token(), tr_id),
                timeout=_REQUEST_TIMEOUT_SEC, **kwargs,
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


def request_order(method: str, url: str, tr_id: str, *, on_uncertain=None, **kwargs):
    """
    **주문·취소용** 요청. **재시도하지 않는다**(절대 규칙 3).

    타임아웃·네트워크 오류·HTTP 500 은 "결과 미확정"이다. 이때 `on_uncertain(exc)` 를
    호출해 호출부가 주문 상태를 `UNKNOWN` 으로 기록하게 한 뒤 `OrderRequestError` 를 올린다.
    재시도하지 않는 이유: 타임아웃 후 재요청은 주문이 실제로 나갔을 경우 **중복 주문**이 된다.

    HTTP 500(rate limit 등)도 재시도하지 않는다. 원 응답이 "초당 거래건수 초과"라면
    주문이 거부된 것이지만, 응답을 받지 못한 경우와 구분할 수 없으므로 미확정으로 본다.
    """
    global _last_call_ts

    try:
        _throttle()
        try:
            resp = requests.request(
                method, url, headers=_headers(get_access_token(), tr_id),
                timeout=_REQUEST_TIMEOUT_SEC, **kwargs,
            )
        finally:
            _last_call_ts = time.monotonic()
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        _handle_uncertain(on_uncertain, e)
        raise OrderRequestError(
            f"주문 요청 결과 미확정(네트워크/타임아웃) — 재시도하지 않습니다: {e}"
        ) from e

    if resp.status_code == 500:
        body = ""
        try:
            body = str(resp.json().get("msg1", "")).strip()
        except Exception:
            pass
        error = RuntimeError(f"HTTP 500 (주문 결과 미확정): {body}")
        _handle_uncertain(on_uncertain, error)
        raise OrderRequestError(
            f"주문 요청이 HTTP 500 — 결과를 확정할 수 없어 재시도하지 않습니다: {body}"
        ) from error

    return resp


def _handle_uncertain(on_uncertain, exc) -> None:
    """결과 미확정 시 호출부 콜백을 부른다. 콜백 자체의 실패는 주문 예외를 가리면 안 된다."""
    if on_uncertain is None:
        return
    try:
        on_uncertain(exc)
    except Exception as e:
        logger.error(f"주문 UNKNOWN 기록 콜백 실패 — 수동 확인이 필요합니다: {e}")


def _request(method: str, url: str, tr_id: str, **kwargs):
    """
    하위 호환용 별칭. **조회 전용**이다.

    T04 이전에는 조회·주문이 이 함수를 공유해 주문에도 재시도가 걸려 있었다(절대 규칙 3 위반).
    새 코드는 용도에 따라 `request_read()` / `request_order()` 를 직접 쓴다.
    """
    return request_read(method, url, tr_id, **kwargs)


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


def get_all_holdings() -> list[dict]:
    """
    해외주식 **보유 전 종목**을 돌려준다 (T03 대조용).

    `get_balance()` 는 `config.TARGET_TICKER` 한 종목만 보지만, 브로커와 DB 를 대조하려면
    전체 보유가 필요하다. 엔드포인트·TR_ID·파라미터는 `get_balance()` 와 동일한 것을 쓴다.
    사용하는 필드도 이미 확인된 것만 쓴다: ovrs_pdno, ovrs_cblc_qty, pchs_avg_pric.

    반환: [{"symbol": str, "market": "overseas", "qty": float, "avg_price": float}, ...]
    """
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
    holdings = resp.json().get("output1") or []

    result = []
    for h in holdings:
        qty = _to_float(h.get("ovrs_cblc_qty"))
        if qty <= 0:
            continue    # 보유 0건 행은 대조 대상이 아니다
        result.append({
            "symbol": h.get("ovrs_pdno"),
            "market": "overseas",
            "qty": qty,
            "avg_price": _to_float(h.get("pchs_avg_pric")),
        })
    return result


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

def order_body(quantity, price_usd: float) -> dict:
    """
    해외 주문 공통 body (매수·매도가 같은 구조). 전송될 값 그대로.

    T04: 호출부(trader)가 주문 전에 이 body 를 `orders.raw_request` 에 기록한다.
    """
    account_no, account_suffix = config.KIS_ACCOUNT_NO.split("-")
    return {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_suffix,
        "OVRS_EXCG_CD": "NAS",
        "PDNO": config.TARGET_TICKER,
        "ORD_DVSN": "00",           # 지정가 (미국 시장 기준)
        "ORD_QTY": str(int(quantity)),
        "OVRS_ORD_UNPR": str(round(price_usd, 2)),
        "CTAC_TLNO": "",
        "MGCO_APTM_ODNO": "",
        "ORD_SVR_DVSN_CD": "0",
    }


def _order_tr_id(side: str) -> str:
    """해외 주문 TR_ID (모의/실전 분기). 모의가 다르므로 반드시 config.IS_MOCK 로 고른다."""
    if side == "BUY":
        # 모의: VTTT1002U, 실전: TTTT1002U (해외주식 매수주문)
        return "VTTT1002U" if config.IS_MOCK else "TTTT1002U"
    # 모의: VTTS0308U, 실전: TTTS0308U (해외주식 매도주문)
    return "VTTS0308U" if config.IS_MOCK else "TTTS0308U"


def send_order(body: dict, *, side: str, on_uncertain=None) -> dict:
    """
    만들어 둔 body 를 실제로 전송한다. **재시도하지 않는다**(절대 규칙 3).

    KIS 해외주식 정규 주문 API (/uapi/overseas-stock/v1/trading/order) 사용.
    `on_uncertain`: 결과 미확정(타임아웃·500) 시 호출될 콜백.
    """
    # 실전 가드: 이 함수가 실제 전송 지점이므로 여기에도 반드시 있어야 한다 (T01b).
    config.assert_order_allowed()

    if side not in ("BUY", "SELL"):
        raise ValueError(f"허용되지 않는 주문 방향: {side!r}")

    url = f"{config.KIS_BASE_URL}/uapi/overseas-stock/v1/trading/order"
    resp = request_order("POST", url, _order_tr_id(side), json=body,
                         on_uncertain=on_uncertain)
    resp.raise_for_status()
    result = resp.json()
    if str(result.get("rt_cd")) != "0":
        action = "해외 매수 주문" if side == "BUY" else "해외 매도 주문"
        raise RuntimeError(
            f"{action} 실패 [{result.get('msg_cd')}] {result.get('msg1', '').strip()}"
        )
    logger.info(
        f"해외 {'매수' if side == 'BUY' else '매도'} 주문 접수: "
        f"{body.get('PDNO')} {body.get('ORD_QTY')}주 "
        f"(${body.get('OVRS_ORD_UNPR')}) → {result.get('msg1')}"
    )
    return result


def buy_shares(quantity: int, price_usd: float, *, on_uncertain=None) -> dict:
    """
    해외주식 현금 매수 (정수 수량 기준). (하위 호환 래퍼 — 실제 전송은 `send_order()`)

    `on_uncertain`: 결과 미확정(타임아웃·500) 시 호출될 콜백. 호출부(trader)가
    주문 상태를 `UNKNOWN` 으로 기록하는 데 쓴다. **재시도하지 않는다.**
    """
    # 실전 가드: main() 을 거치지 않고 호출돼도 실전 오발주를 막는다 (T01b).
    # 다른 사전 검증보다 **먼저** 와야 한다.
    config.assert_order_allowed()

    if quantity <= 0:
        raise ValueError(f"매수 수량은 1주 이상이어야 합니다: {quantity}")

    return send_order(order_body(quantity, price_usd), side="BUY", on_uncertain=on_uncertain)


def sell_all(quantity: float, current_price_usd: float, *, on_uncertain=None) -> dict:
    """
    보유 수량 전량 매도. (하위 호환 래퍼 — 실제 전송은 `send_order()`)

    `on_uncertain`: 결과 미확정 시 콜백. **재시도하지 않는다** — 타임아웃 후 재요청은
    주문이 실제로 나갔을 경우 중복 매도가 된다.
    """
    # 실전 가드: 다른 사전 검증보다 먼저 (T01b)
    config.assert_order_allowed()

    if quantity <= 0:
        raise ValueError(f"매도 수량은 1주 이상이어야 합니다: {quantity}")

    return send_order(order_body(quantity, current_price_usd), side="SELL",
                      on_uncertain=on_uncertain)


def get_order_status(order_no: str, *, symbol: str | None = None) -> "OrderStatus":
    """
    해외주식 주문 1건의 체결 상태를 조회한다 (T04).

    **미구현(확인 필요).** 해외 주문체결 조회의 엔드포인트·TR_ID·응답 필드명을
    공식 문서·공식 샘플에서 확인하지 못했다(절대 규칙 4). 추측 구현을 하지 않는다.
    """
    raise NotImplementedError(
        "해외주식 주문체결 조회의 엔드포인트/TR_ID/응답 필드명이 공식 문서·공식 샘플에서 "
        "확인되지 않았습니다. 확인 전에는 추측 구현하지 않습니다. "
        "TODO: 공식 문서 확인 후 구현. 그 전까지 해외 주문은 체결 확인이 불가하므로 "
        "미체결 취소·부분체결 반영도 동작하지 않습니다."
    )


def cancel_order(order_no: str, *, quantity: float, symbol: str | None = None) -> dict:
    """
    해외주식 미체결 주문을 취소한다 (T04).

    **미구현(확인 필요).** 취소 엔드포인트·TR_ID·필수 파라미터(원주문번호, 전량/부분 구분 등)를
    공식 문서·공식 샘플에서 확인하지 못했다(절대 규칙 4).
    """
    # 실전 가드는 유지한다: 나중에 구현될 때 가드 없이 나가지 않도록.
    config.assert_order_allowed()
    raise NotImplementedError(
        "해외주식 주문취소의 엔드포인트/TR_ID/필수 파라미터가 공식 문서·공식 샘플에서 "
        "확인되지 않았습니다. 확인 전에는 추측 구현하지 않습니다."
    )