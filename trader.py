"""
매매 **실행** 모듈 (T08: 판단은 `strategy.py` 로 옮겼다).

해외(QQQ)와 국내(379800) ETF를 매 사이클마다 각각 판단해 동시 운용한다.

## T08 이후 역할 분담

  **판단은 이 모듈에 없다.** 매수·청산·수량은 모두 순수 함수다.
    - `strategy.decide()`   매수/매도/대기 + 수량 (I/O 없음)
    - `exit_rules`          청산 사유 (T06)
    - `sizing`              수량 (T07)

  이 모듈은 **오케스트레이션만** 한다:
    브로커에서 데이터 수집 → `reconcile` → `decide()` → 주문(T04) → DB 갱신.

  판단을 순수 함수로 옮긴 이유는 **백테스트와 실전이 같은 판단을 쓰게** 하기 위함이다.
  그전에는 판단이 `_run_cycle` 안에 `config`·`bot_state` 전역과 섞여 있어 백테스트에서
  재현할 수 없었다(CLAUDE.md "백테스트와 실전 판단 로직이 별개일 가능성").

## 판단을 옮기지 **않은** 것 (의도)

  부작용이 있거나 "판단 이전/이후"의 사실에 의존하는 것들은 여기 남는다.
    - `config.assert_order_allowed()`   주문 함수 맨 앞의 실전 가드 (절대 규칙 1)
    - T03 `EXTERNAL` 매도 차단 + reconcile 중단 플래그 확인
    - T04 주문 직전 **중복 확인**(`db.has_open_order`)·미확정 주문 확정
    - T06 주문 직전 **브로커 잔고 재확인**(`_confirm_sell_quantity`)
  이것들을 `decide()` 로 끌어오면 "모르는 값을 판단 입력으로" 넣게 되어,
  조회 실패 시 추측한 수량으로 주문이 나갈 수 있다.

## 지표는 확정 봉만 쓴다 (T08 — 동작이 바뀐 부분)

  `_run_cycle` 은 확정 일봉(`history.get_confirmed_bars`, T02)으로 지표를 계산해
  `MarketSnapshot.bars` 로 넘긴다. 그래서 **당일 미확정 봉은 신호에 들어가지 않는다** —
  장중에 오늘 봉이 다시 그려지면서 신호가 뒤집히지 않고, 판단이 하루 동안 고정된다.
  T07 까지는 KIS 일봉(당일 봉 포함)으로 계산했다.

  체결 가정은 `config.BACKTEST_FILL_MODEL`(기본 `next_open`)로 문서화했다.
"""
import json
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import broker as broker_module
import config
import db
import exit_rules
import history
import kis_domestic as kd
import kis_overseas as kis
import models
import reconcile
import sizing
import strategy
from state import bot_state, get_bot_state

KST = ZoneInfo("Asia/Seoul")

logger = logging.getLogger("bot")

# 시장 구분 값. `positions`/`orders` 의 market 컬럼에 그대로 저장된다.
MARKET_OVERSEAS = "overseas"
MARKET_DOMESTIC = "domestic"


def _state_conn():
    """상태 DB 연결. 실패는 삼키지 않고 올린다(호출부가 사이클을 건너뛴다)."""
    return get_bot_state().conn


def _order_type_label() -> str:
    """`orders.order_type` 에 남길 주문 유형 문자열(설정값 그대로)."""
    return "MARKET" if config.ORDER_TYPE == "market" else "LIMIT"


def _refresh_peak(conn, ticker: str, market: str, price: float) -> float | None:
    """
    진입 후 고점(`positions.peak_price_since_entry`)을 현재가로 갱신한다 (T06, 요구사항 2).

    트레일링 스톱이 쓰는 값이다. 고점은 **내려가지 않는다** — 매 사이클 현재가로
    덮어쓰면 고점이 아니라 그냥 현재가가 되어 트레일링이 동작하지 않는다.

    포지션이 없거나(청산됨) 상태를 못 읽으면 아무것도 하지 않고 None 을 돌려준다.
    고점을 갱신하지 못해도 청산 판단은 계속된다(가격 기반 규칙이 우선).
    """
    pos = db.get_position(conn, ticker, market)
    if not pos or pos.get("status") != "OPEN":
        return None

    prev = pos.get("peak_price_since_entry")
    try:
        prev_val = float(prev) if prev is not None else None
    except (TypeError, ValueError):
        prev_val = None
    peak = price if prev_val is None else max(prev_val, float(price))
    if prev_val is not None and peak <= prev_val:
        return prev_val      # 갱신 없음 — DB 쓰기를 줄인다

    db.upsert_position(
        conn, ticker, market, qty=float(pos.get("qty") or 0.0),
        avg_price=float(pos.get("avg_price") or 0.0),
        entry_time=pos.get("entry_time"),
        entry_signal=pos.get("entry_signal"),
        peak_price_since_entry=peak,
        status="OPEN",
    )
    return peak


def _order_price_for(module, price: float, *, side: str) -> float:
    """
    지정가 주문 가격. 국내는 호가 단위(틱)를 적용하고 해외는 그대로 쓴다 (T04).

    해외 지정가를 현재가에서 N틱 옮기려면 미국 호가 단위 표가 필요한데 확인되지
    않았다(절대 규칙 4). 그래서 해외는 현재가 그대로 낸다.
    # VERIFY: 미국 가격대별 호가 단위 표를 확인하면 해외도 N틱 조정을 적용할 것.
    """
    if module is kd:
        return float(module._order_price(price, side=side))
    return round(float(price), 2)


def _exposure_snapshot(market: str | None, ticker: str,
                       fx_rate: float = 1.0) -> tuple[int, float]:
    """
    신규 진입 판단에 쓸 (보유 종목 수, 현재 총 노출 원화) 를 읽는다 (T07).

    **자기 자신(이번에 사려는 종목)은 세지 않는다** — 이미 보유 중이면 "추가 매수"이고
    신규 진입이 아니다. 자기 자신을 세면 상한 2종목에서 다른 시장 1종목만 있어도
    2로 세어 영영 못 산다.

    노출 계산은 `positions.qty × avg_price` 다(원가 기준). 평가금액이 아니라
    **투입한 돈**을 보는 이유는, 총 노출 상한이 "얼마나 실었는가"를 제한하는
    규칙이기 때문이다(평가금액은 가격이 오르면 상한을 넘겨 계산된다).

    Args:
        fx_rate: 해외 포지션의 `avg_price` 는 **달러**이므로 원화로 환산한다.
            환산하지 않으면 1달러가 1원으로 계산돼 노출이 실제의 1/1,400 이 되고,
            상한(90%)이 사실상 사라진다.

    T06 이전에는 이 계산 자체가 없었다(노출 상한 미도입) — 비교할 기준선이 없다.
    """
    conn = _state_conn()
    if conn is None:
        # DB 미사용(`market is None`) 경로에서만 나온다 — 노출을 모르므로 0(제한 없음)으로 둔다.
        # `market` 이 있는데 DB 가 없으면 **모르는 채로 매수하게 되므로** 실패로 올린다.
        # 이 예외는 `_run_cycle` 이 잡아 `counters=None` → 매수만 보류로 이어진다(청산은 계속).
        if market is not None:
            raise RuntimeError(f"DB 연결 없음 — {market} 노출·보유 종목 수를 알 수 없습니다")
        return 0, 0.0

    rate = float(fx_rate or 1.0)
    count = 0
    exposure = 0.0
    for pos in db.list_positions(conn, status="OPEN"):
        if (str(pos.get("symbol")) == str(ticker)
                and str(pos.get("market")) == str(market)):
            continue      # 자기 자신 — 추가 매수이지 신규 진입이 아니다
        count += 1
        cost = float(pos.get("qty") or 0.0) * float(pos.get("avg_price") or 0.0)
        exposure += cost * rate if pos.get("market") == MARKET_OVERSEAS else cost
    return count, exposure


class _BudgetProbe:
    """
    `_run_cycle` 이 `buy_fn.get_orderable` 만 쓰는 경우를 위한 자리채움 콜러블 (T04).

    T04 부터 실제 주문은 `submit_buy` 가 내므로 `buy_fn` 은 "1주라도 살 수 있는가"
    판단에만 쓰인다. 계좌·DB 를 건드리지 않으므로 안전하다.

    **T07 부터 이 경로는 `market=None`(DB 미사용) 일 때만 쓰인다** — 실제 사이클은
    `sizing_fn` 이 수량을 정한다. 비중 설정이 사라져 "예산" 개념도 함께 사라졌으므로
    인자 이름을 `spend_krw` 로 둔다(수량 × 가격).
    """

    def __init__(self, qty_for_spend):
        self._qty_for_spend = qty_for_spend

    def __call__(self, spend_krw: float) -> bool:
        return self._qty_for_spend(spend_krw) >= 1

    def get_orderable(self, spend_krw: float) -> float:
        """해외는 잔고 API 의 통합증거금 값을 신뢰하므로 0(클램핑 없음)을 돌려준다."""
        return 0.0


def _budget_probe(qty_for_spend):
    """`_BudgetProbe` 생성 별칭(가독용)."""
    return _BudgetProbe(qty_for_spend)


def _client_ref(market: str, side: str, ticker: str) -> str:
    """
    주문 식별자. `orders.client_ref` 의 UNIQUE 제약으로 중복 기록을 막는다.
    같은 초에 두 주문이 나가도 충돌하지 않도록 임의 접미사를 붙인다.
    """
    stamp = db.now_kst().strftime("%Y%m%d%H%M%S")
    return f"{market}-{side}-{ticker}-{stamp}-{uuid.uuid4().hex[:8]}"


# 주문 raw_request/raw_response 에서 마스킹할 계좌 관련 키 (절대 규칙 2)
_ACCOUNT_KEYS = ("CANO", "ACNT_PRDT_CD")


def _mask_payload(payload):
    """
    주문 요청/응답에서 계좌 관련 값을 마스킹한 **사본**을 만든다.

    원본 dict 는 건드리지 않는다(주문 자체에 영향을 주면 안 된다).
    로그·DB 에 원문 계좌번호가 남지 않게 하기 위한 것이다(절대 규칙 2).
    """
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return payload
    out = {}
    for key, value in payload.items():
        if key in _ACCOUNT_KEYS:
            out[key] = config.mask(value)
        elif isinstance(value, dict):
            out[key] = _mask_payload(value)
        else:
            out[key] = value
    return out


# KIS 응답 키를 대소문자 구분 없이 읽는 헬퍼. 정의는 `kis_overseas` 에 있다
# (kis_domestic 도 같은 것을 쓴다). 근거는 그쪽 docstring 참고.
_ci_get = kis._ci_get


def _order_refs_from_response(result: dict | None) -> dict | None:
    """
    주문 응답에서 **체결 조회·취소에 필요한 주문 식별자**를 꺼낸다.

    반환: {"order_no": str, "org_no": str|None, "order_time": str|None} 또는 None.

    근거: `docs/kis_api_fields.md` §1 주식주문(현금) Response Body.

        output.KRX_FWDG_ORD_ORGNO  계좌관리점코드 (string(5))
        output.ODNO                주문번호       (string(10))
        output.ORD_TMD             주문시간       (string(6))

    응답 예시(문서 원문):
        {"rt_cd": "0", "output": {"KRX_FWDG_ORD_ORGNO": "06010",
                                  "ODNO": "0001569157", "ORD_TMD": "155211"}}

    **주문번호(ODNO)가 없으면 None 을 돌려준다.** 주문번호 없이는 체결 조회도
    취소도 할 수 없으므로, 이후 단계가 "주문번호 없음"을 명확히 알 수 있어야 한다.
    `KRX_FWDG_ORD_ORGNO` 는 취소(order-rvsecncl)의 필수 파라미터라 함께 돌려준다.
    """
    if not result or str(result.get("rt_cd")) != "0":
        return None
    output = result.get("output")
    order_no = _ci_get(output, "ODNO")
    if not order_no:
        return None
    return {
        "order_no": order_no,
        "org_no": _ci_get(output, "KRX_FWDG_ORD_ORGNO"),
        "order_time": _ci_get(output, "ORD_TMD"),
    }


def _stored_order_refs(order: dict) -> dict | None:
    """
    DB 에 저장된 주문에서 주문 식별자를 꺼낸다.

    먼저 `orders.broker_order_no`(T04b 부터 저장됨)를 본다. 없으면 raw_response 를
    다시 해석한다(스키마 v2 이전에 기록된 주문 호환).
    """
    order_no = order.get("broker_order_no")
    if order_no:
        return {
            "order_no": str(order_no),
            "org_no": order.get("broker_org_no"),
            "order_time": None,
        }

    raw = order.get("raw_response")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.error(f"[주문] raw_response 해석 실패 (#{order.get('id')}): {e}")
        return None
    return _order_refs_from_response(payload)


def _age_minutes(order: dict) -> float:
    """주문이 생성된 뒤 얼마나 지났는지(분). 시각을 못 읽으면 '아주 오래됨'으로 본다."""
    raw = order.get("created_at")
    if not raw:
        return float("inf")
    try:
        return (db.now_kst() - datetime.fromisoformat(raw)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return float("inf")


def _record_order_pending(conn, *, market: str, ticker: str, side: str, qty: float,
                          price: float | None, order_type: str,
                          raw_request: dict | None,
                          reason: str | None = None) -> tuple[int, str]:
    """
    **주문을 보내기 전에** `SUBMITTED` 행을 먼저 기록한다 (write-ahead, T04).

    순서가 핵심이다: 주문을 보낸 뒤 기록하면, 기록 직전에 프로세스가 죽었을 때
    "브로커에는 주문이 있는데 DB 에는 없는" 상태가 된다. 먼저 기록하면 최악의 경우
    "DB 에는 주문이 있는데 브로커에는 없는" 상태가 되고, 이것은 reconcile 이 잡아낸다.

    `reason`(T06): 청산 사유(`STOP_LOSS` 등). 매수 주문은 None 이다.

    반환: (order_id, client_ref)
    """
    client_ref = _client_ref(market, side, ticker)
    order_id = db.record_order(
        conn, symbol=ticker, market=market, side=side, qty=qty, price=price,
        order_type=order_type, status="SUBMITTED",
        client_ref=client_ref, broker_order_no=None,
        reason=reason, raw_request=raw_request,
    )
    logger.info(f"[주문기록] #{order_id} {market} {side} {ticker} {qty:g}주 → SUBMITTED (사전 기록)")
    return order_id, client_ref


def _apply_fill_to_position(conn, *, market: str, ticker: str, side: str,
                            qty: float, price: float | None,
                            prev_qty: float, prev_avg: float,
                            entry_signal: dict | None = None) -> None:
    """
    **체결된** 수량을 포지션에 반영한다 (T04).

    T03 에서는 접수(rt_cd=="0")만 보고 이 함수를 불렀다. 그러면 미체결 주문이
    포지션에 반영돼 다음 사이클 reconcile 이 불일치로 중단 플래그를 세웠다.
    이제는 **체결이 확인된 뒤에만** 호출한다(`_settle_order` 참고).
    평단가는 실제 체결가(`avg_fill_price`)로 계산한다.

    `qty` 는 이번에 **새로 체결된** 수량이고, `prev_qty`/`prev_avg` 는 반영 전 포지션이다.
    """
    if side == "BUY":
        new_qty = prev_qty + qty
        if new_qty > 0 and price is not None:
            new_avg = (prev_qty * prev_avg + qty * price) / new_qty
        else:
            new_avg = prev_avg
    else:
        new_qty = max(0.0, prev_qty - qty)
        new_avg = prev_avg

    if new_qty <= 0:
        db.close_position(conn, ticker, market)
        logger.info(f"[포지션] {market} {ticker} 전량 청산 — CLOSED")
        return

    existing = db.get_position(conn, ticker, market)
    db.upsert_position(
        conn, ticker, market, qty=new_qty, avg_price=new_avg,
        entry_time=(existing or {}).get("entry_time") or db.now_iso(),
        entry_signal=(existing or {}).get("entry_signal") or entry_signal,
        peak_price_since_entry=(existing or {}).get("peak_price_since_entry"),
        status="OPEN",
    )
    logger.info(f"[포지션] {market} {ticker} {new_qty:g}주 @ {new_avg:,.2f} — OPEN (체결 반영)")


class DuplicateOrderBlocked(RuntimeError):
    """
    같은 종목·시장·방향의 **미확정 주문이 남아 있어** 새 주문을 내지 않았다 (T04).

    특히 `UNKNOWN`(타임아웃 등)은 주문이 나갔을 수도 있는 상태라, 확정되기 전에
    다시 내면 곧 **중복 주문**이다. 조회로 확정될 때까지 막는다.
    """


def _submit_order(conn, *, market: str, ticker: str, side: str, qty: float,
                  price: float | None, order_type: str, make_body, send,
                  reason: str | None = None) -> int:
    """
    주문 1건을 **사전 기록 → 전송 → 상태 확정** 순서로 낸다 (T04).

    순서가 안전성의 핵심이다.
      1. 같은 종목·시장·방향의 미확정 주문이 있으면 **내지 않는다**(중복 방지).
      2. 보내기 **전에** `SUBMITTED` 로 기록한다(write-ahead). 기록 후 전송 전에
         프로세스가 죽으면 "DB 에는 있고 브로커에는 없는" 상태가 되는데, 이건
         reconcile 이 잡아낸다. 반대 순서면 "브로커에는 있는데 DB 에는 없는" 상태가 된다.
      3. 전송 실패의 결과가 미확정이면 `UNKNOWN` 으로 기록한다(재시도하지 않음).
      4. 응답에서 주문번호를 얻으면 기록해 둔다(체결 조회는 다음 사이클의
         `_settle_order()` 가 한다). 아직 얻지 못하면 `SUBMITTED` 로 남기고 로그로 알린다.

    `reason`(T06): 청산 사유. `orders.reason` 에 남겨 "왜 이 주문이 나갔는지"를
    나중에 확인할 수 있게 한다(요구사항 4).

    반환: `orders.id`
    """
    if db.has_open_order(conn, symbol=ticker, market=market, side=side):
        existing = db.list_open_orders(conn, symbol=ticker, market=market, side=side)
        ids = ", ".join(f"#{o['id']}({o['status']})" for o in existing)
        raise DuplicateOrderBlocked(
            f"{market} {ticker} {side} 미확정 주문이 남아 있어 새 주문을 내지 않습니다: {ids}"
        )

    body = make_body()
    order_id, _client_ref = _record_order_pending(
        conn, market=market, ticker=ticker, side=side, qty=qty, price=price,
        order_type=order_type, raw_request=body, reason=reason,
    )

    def on_uncertain(exc):
        """결과 미확정 → UNKNOWN. 이 종목은 다음 사이클 조회로 확정될 때까지 신규 주문 금지."""
        db.update_order_status(conn, order_id, status="UNKNOWN",
                               raw_response={"error": str(exc)})
        logger.error(
            f"⚠️ 주문 #{order_id} 결과 미확정(UNKNOWN) — 재시도하지 않습니다. "
            f"{market} {ticker} 은 조회로 확정될 때까지 신규 주문이 막힙니다: {exc}"
        )

    try:
        result = send(body, on_uncertain)
    except Exception as e:
        logger.error(f"❌ 주문 #{order_id} 실패 — {market} {ticker} {side}: {e}")
        raise

    refs = _order_refs_from_response(result)
    if refs:
        # 취소에 필요한 KRX_FWDG_ORD_ORGNO 도 함께 저장한다 (T04b).
        db.update_order_status(conn, order_id, status="SUBMITTED",
                               broker_order_no=refs["order_no"],
                               broker_org_no=refs["org_no"], raw_response=result)
    else:
        db.update_order_status(conn, order_id, status="SUBMITTED", raw_response=result)
        logger.error(
            f"⛔ 주문 #{order_id} 응답에서 주문번호(ODNO)를 얻지 못했습니다 — "
            f"체결 조회·미체결 취소가 불가합니다. 이 주문은 조회로 확정되지 않으므로 "
            f"직접 확인해야 합니다: {market} {ticker} {side} {qty:g}주"
        )
    return order_id


def _call_status_fn(status_fn, order: dict, refs: dict):
    """
    체결 조회 함수를 부른다. `org_no`·`ticker` 를 함께 넘긴다 (T04b).

    국내 `get_order_status()` 는 `ODNO` 로 좁혀 조회하므로 주문번호만으로 충분하지만,
    `ticker` 를 주면 서버가 더 좁혀 준다. 미구현 시장은 애초에 주문이 나가지 않으므로
    (`_require_settlement_support`) 여기서 인자 호환을 걱정할 필요가 없다.
    """
    order_no = refs["order_no"]
    extras = {"ticker": order.get("symbol")}
    if refs.get("org_no"):
        extras["org_no"] = refs["org_no"]
    # `TypeError` 를 잡아 재시도하지 않는다. 그러면 조회 함수 **내부**의 TypeError
    # (예: 응답 파싱 버그)까지 "인자 문제"로 오인해 같은 실패를 두 번 부른다.
    # 인자 구성은 호출부(`status_fn` 래퍼)가 맞춰서 넘긴다.
    return status_fn(order_no, **extras)


def _settle_order(conn, order: dict, status_fn, cancel_fn=None) -> "db.OrderStatus | None":
    """
    미확정 주문 1건의 체결을 조회해 **체결된 만큼만** 포지션에 반영한다 (T04).

    반영 규칙
      - 이번 조회에서 **새로 체결된 수량**(직전 기록 대비 증가분)만 반영한다.
        같은 체결을 두 번 더하지 않기 위한 것이다.
      - 아직 체결 전이면 아무것도 반영하지 않는다(접수만으로 포지션을 만들지 않는다).
      - `ORDER_TIMEOUT_MINUTES` 를 넘겨 미체결이면 취소를 시도한다.

    조회 자체가 실패하면 상태를 바꾸지 않는다(추측하지 않는다). 그 주문은
    reconcile 이 "미확정 주문이 오래 남았다"로 잡아 사람이 보게 한다.

    반환: 조회 결과(`db.OrderStatus`) 또는 확정하지 못했으면 None.
    """
    refs = _stored_order_refs(order)
    if not refs or status_fn is None:
        return None

    symbol, market, side = order["symbol"], order["market"], order["side"]
    try:
        st = _call_status_fn(status_fn, order, refs)
    except NotImplementedError as e:
        logger.error(f"[체결조회] {market} {symbol} 미구현 — 주문 #{order['id']} 상태 미확정: {e}")
        return None
    except Exception as e:
        logger.error(f"[체결조회] 실패 — 주문 #{order['id']} 상태를 바꾸지 않습니다: {e}")
        return None

    prev_filled = float(order.get("filled_qty") or 0.0)
    delta = float(st.filled_qty) - prev_filled

    if delta > 0:
        prev = db.get_position(conn, symbol, market)
        _apply_fill_to_position(
            conn, market=market, ticker=symbol, side=side, qty=delta,
            price=st.avg_fill_price if st.avg_fill_price is not None else order.get("price"),
            prev_qty=float((prev or {}).get("qty") or 0.0),
            prev_avg=float((prev or {}).get("avg_price") or 0.0),
        )
        logger.info(
            f"[체결] 주문 #{order['id']} {market} {symbol} {side} "
            f"{delta:g}주 체결 @ {st.avg_fill_price} (누적 {st.filled_qty:g}주)"
        )

    db.update_order_status(conn, order["id"], status=st.status,
                           filled_qty=st.filled_qty,
                           avg_fill_price=st.avg_fill_price, raw_response=st.raw)

    # 미체결이 오래 남으면 취소한다. 부분 체결분은 이미 위에서 포지션에 반영했다.
    if st.status in ("SUBMITTED", "PARTIAL") and _age_minutes(order) > config.ORDER_TIMEOUT_MINUTES:
        _cancel_stale_order(conn, order, st, cancel_fn)

    return st


def _cancel_stale_order(conn, order: dict, st, cancel_fn) -> None:
    """
    `ORDER_TIMEOUT_MINUTES` 를 넘긴 미체결 주문을 취소한다 (T04/T04b).

    **취소하지 못하면 그 종목의 신규 매수를 차단한다.** 미체결 주문이 살아 있는데
    계속 신규 주문을 내면 같은 종목에 주문이 겹겹이 쌓이고, 취소도 안 되니 사람이
    손으로 정리할 때까지 상태를 알 수 없게 된다. 그래서 취소 실패(미구현·거부) 시
    `_halt_due_to_unfilled()` 로 차단하고 CRITICAL 로 남긴다.
    """
    remaining = float(order.get("qty") or 0.0) - float(st.filled_qty or 0.0)
    if remaining <= 0:
        return   # 이미 전량 체결 — 취소할 것이 없다

    if cancel_fn is None:
        _halt_due_to_unfilled(conn, order, remaining, "취소 기능이 연결되지 않았습니다")
        return

    try:
        result = cancel_fn(remaining)
    except NotImplementedError as e:
        _halt_due_to_unfilled(conn, order, remaining, f"취소 미구현: {e}")
        return
    except Exception as e:
        _halt_due_to_unfilled(conn, order, remaining, f"취소 실패: {e}")
        return

    db.update_order_status(conn, order["id"], status="CANCELED",
                           filled_qty=st.filled_qty, raw_response=result)
    logger.warning(
        f"🚫 주문 #{order['id']} 미체결 취소 — {order['market']} {order['symbol']} "
        f"{remaining:g}주 (체결 {st.filled_qty:g}주는 유지)"
    )


def _halt_due_to_unfilled(conn, order: dict, remaining: float, why: str) -> None:
    """
    미체결 주문을 취소하지 못했을 때 **그 종목의 신규 주문을 차단**한다 (T04b).

    취소되지 않은 주문이 살아 있는 채로 계속 주문하면 같은 종목에 주문이 쌓인다.

    **전역 중단(`reconcile.halt_trading`)을 쓰지 않는다.** 전역 플래그는 손절까지
    막아서, 미체결 하나 때문에 손실을 방치하게 된다. 차단은 **그 종목의 매수에만**
    건다(`_require_no_unfilled`). 매도(손절·익절)는 계속 나가야 한다.
    """
    reason = (
        f"{order['market']} {order['symbol']} 미체결 주문 #{order['id']} "
        f"({remaining:g}주) 취소 불가 — {why}"
    )
    logger.critical(
        f"🚨 {reason}\n"
        f"   이 주문이 살아 있는 채로 신규 주문을 내면 주문이 중복될 수 있어 "
        f"{order['market']} {order['symbol']} 의 신규 매수를 차단합니다(손절 매도는 계속 동작).\n"
        f"   KIS 앱에서 직접 취소한 뒤 확인하세요."
    )
    # 주문 행의 상태를 UNKNOWN 으로 두고 사유를 남긴다. UNKNOWN 은 `list_open_orders()`
    # 대상이라 그대로 남아 있는 한 이 종목의 신규 매수가 계속 차단된다
    # (전역 플래그와 달리 해제 명령이 필요 없고, 주문이 확정되면 자동으로 풀린다).
    try:
        db.update_order_status(conn, order["id"], status="UNKNOWN",
                               filled_qty=float(order.get("filled_qty") or 0.0),
                               raw_response={"halt_reason": reason})
    except Exception as e:
        # 상태 기록조차 못 하면 그 사실을 반드시 알린다(삼키지 않는다).
        logger.critical(f"🚨🚨 미체결 주문 상태 기록에 실패했습니다 — 즉시 확인하세요: {e}")


def get_cancelable_order(order_no: str, *, ticker: str) -> "db.OrderStatus":
    """
    주식정정취소가능주문조회 (T04b).

    **미구현(확인 필요) — 절대 규칙 4.** 이 API 는 `docs/kis_api_fields.md`
    "이 파일에 없는 것 (추측 금지)" 목록에 **명시적으로 빠져 있다.** 엔드포인트·
    TR_ID·응답 필드를 추측하면 취소 가능 수량(`psbl_qty`)을 잘못 읽어 잘못된 수량으로
    취소가 나갈 수 있다. 문서 §3 개요도 "호출 전에 이 API 로 정정취소가능수량을
    확인하라"고 권고하지만, 그 API 문서가 없으므로 구현하지 않는다.

    TODO: 공식 문서 확인 후 구현. 확인되면 `cancel_order()` 앞단에 연결한다.
    """
    raise NotImplementedError(
        "주식정정취소가능주문조회 API 는 docs/kis_api_fields.md 에 없어 구현하지 "
        "않았습니다. 엔드포인트/TR_ID/psbl_qty 필드를 추측하지 않습니다."
    )


def _resolve_open_orders(conn, *, market: str, ticker: str, status_fn, cancel_fn=None) -> None:
    """
    이 종목의 미확정 주문을 **주문을 내기 전에** 확정시킨다 (T04).

    `UNKNOWN` 주문이 남아 있으면 그 종목은 신규 주문이 막힌다. 매 사이클마다
    이 함수가 조회로 확정시켜 막힘이 영구화되지 않게 한다.
    """
    for order in db.list_open_orders(conn, symbol=ticker, market=market):
        _settle_order(conn, order, status_fn, cancel_fn)


def run_trading_cycle(ai_score: int, overseas_open: bool = True, domestic_open: bool = True):
    """
    1회 매매 사이클 실행 (해외 + 국내).

    Args:
        ai_score: AI 시장 감정 점수
        overseas_open: 해외(미국) 정규장 개장 여부
        domestic_open: 국내 정규장 개장 여부
    """
    # 신규 매매 중단 플래그 확인 (T03). 브로커 ↔ DB 불일치가 해소되지 않았으면
    # 상태를 모르는 채 주문하지 않도록 여기서 멈춘다.
    # TODO(T09): 중단 시 사용자 알림 연결.
    try:
        conn = get_bot_state().conn
    except Exception as e:
        logger.error(f"❌ 상태 저장소 접근 실패 — 매매 사이클을 건너뜁니다: {e}")
        return

    if reconcile.is_trading_halted(conn):
        # 매 사이클 CRITICAL 로 남긴다 — 조용히 지나가면 "봇은 도는데 매매만 안 되는"
        # 상태를 사용자가 눈치채지 못한다. 해제 방법은 실행 가능한 명령으로 적는다 (T04).
        logger.critical(
            f"🚨 신규 매매 중단 중 — 사유: {reconcile.halt_reason(conn) or '사유 미기록'}\n"
            "   해제: 불일치를 확인한 뒤 아래를 실행하세요.\n"
            "   .venv/bin/python -c \"import db, reconcile; c=db.open_db(); "
            "reconcile.resume_trading(c); c.close()\""
        )
        return

    if not config.IS_OVERSEAS_ENABLED:
        pass    # 해외 운용 비활성 (소액 자본 등)
    elif overseas_open:
        run_overseas_cycle(ai_score)
    else:
        logger.info("⏩ [해외] 정규장 아님 — 매매 스킵")

    if not config.IS_DOMESTIC_ENABLED:
        return
    if domestic_open:
        run_domestic_cycle(ai_score)
    else:
        logger.info("⏩ [국내] 정규장 아님 — 매매 스킵")


def _place_sell(prefix: str, market: str | None, ticker: str, qty: float, sell_fn,
                submit_sell) -> None:
    """
    매도 1건을 낸다 (T04).

    `submit_sell`(write-ahead 기록 포함)이 있으면 그쪽을 쓰고, 없으면 예전처럼
    `sell_fn` 만 부른다(DB 기록 경로가 없는 하위 호환).
    **체결 확인은 여기서 하지 않는다** — 포지션 반영은 `_settle_order()` 가
    체결을 확인한 뒤에 한다. 접수만으로 포지션을 줄이면 미체결 매도가 반영돼
    다음 사이클 reconcile 이 불일치로 매매를 멈춘다.
    """
    if submit_sell is not None and market:
        submit_sell(qty)
        return

    sell_fn(qty)
    logger.info(f"{prefix} 매도 주문 접수 {qty:.0f}주 (체결 확인은 다음 사이클)")


def _exit_order_type(reason: str) -> str:
    """
    청산 사유별 주문 유형(limit/market)을 정한다 (요구사항 2).

    `config.ORDER_TYPE` 은 전역 설정이라 청산마다 다르게 쓸 수 없다. 손절·시간청산은
    **반드시 체결되어야** 하므로 기본이 시장가다 — `cancel_order` 가 미구현(T04b)이라
    지정가가 미체결로 남으면 취소할 수 없고 포지션이 그대로 남는다.
    목표가 익절은 지정가를 허용한다(미체결이 손실은 아니다).
    """
    table = {
        exit_rules.STOP_LOSS: config.EXIT_ORDER_TYPE_STOP_LOSS,
        exit_rules.TIME_EXIT: config.EXIT_ORDER_TYPE_TIME_EXIT,
        exit_rules.TAKE_PROFIT: config.EXIT_ORDER_TYPE_TAKE_PROFIT,
        exit_rules.TRAILING: config.EXIT_ORDER_TYPE_TRAILING,
        exit_rules.OVERBOUGHT: config.EXIT_ORDER_TYPE_OVERBOUGHT,
    }
    value = str(table.get(reason, "market")).strip().lower()
    if value not in ("limit", "market"):
        # 모르는 값을 지정가로 해석하면 체결되지 않는 주문이 나갈 수 있다.
        # 체결이 우선인 청산이므로 안전한 쪽(시장가)으로 두고 크게 알린다.
        logger.critical(
            f"🚨 청산 주문 유형 설정이 잘못되었습니다 ({reason}: {value!r}) — "
            f"시장가(체결 우선)로 진행합니다."
        )
        return "market"
    return value


def _confirm_sell_quantity(prefix: str, market: str, ticker: str, qty: float,
                           refresh_balance) -> float | None:
    """
    매도 직전에 **브로커 잔고의 실제 보유 수량**으로 청산 수량을 재확인한다 (요구사항 5).

    DB 만 믿으면 안 된다 — 분할 매도·부분 체결·사람의 수동 매도로 DB 와 브로커가
    어긋날 수 있고, 그 상태로 전량 매도를 내면 **보유하지 않은 수량**을 팔려고 해
    주문이 거부되거나(운 좋은 경우) 초과 매도가 된다.

    반환: 주문할 수량, 또는 주문하면 안 되는 경우 None.
    """
    try:
        fresh = refresh_balance()
    except Exception as e:
        logger.error(f"⛔ {prefix} 매도 직전 잔고 재조회 실패 — 주문하지 않습니다: {e}")
        return None

    actual = float((fresh or {}).get("quantity") or 0.0)
    if actual <= 0:
        logger.critical(
            f"🚨 {prefix} 브로커 보유 수량이 0 인데 청산을 시도했습니다 "
            f"(DB {qty:.0f}주) — 주문하지 않고 reconcile 로 확인합니다."
        )
        _trigger_reconcile_check(f"{prefix} {ticker}: DB {qty:.0f}주 vs 브로커 0주")
        return None
    if actual < qty:
        # DB 가 실제보다 많다. 많은 쪽으로 팔면 초과 주문이므로 실제 수량으로 줄인다.
        logger.warning(
            f"⚠️ {prefix} 보유 수량 불일치 — DB {qty:.0f}주 > 브로커 {actual:.0f}주. "
            f"브로커 기준 {actual:.0f}주만 매도합니다."
        )
        _trigger_reconcile_check(f"{prefix} {ticker}: DB {qty:.0f}주 > 브로커 {actual:.0f}주")
        return actual
    if actual > qty:
        # 봇이 모르는 수량이 있다. 그 전부를 팔면 봇이 만들지 않은 보유까지 팔게 된다.
        logger.warning(
            f"⚠️ {prefix} 보유 수량 불일치 — 브로커 {actual:.0f}주 > DB {qty:.0f}주. "
            f"DB 가 아는 {qty:.0f}주만 매도하고 reconcile 로 확인합니다."
        )
        _trigger_reconcile_check(f"{prefix} {ticker}: 브로커 {actual:.0f}주 > DB {qty:.0f}주")
        return qty
    return qty


def _trigger_reconcile_check(what: str) -> None:
    """
    매도 직전 수량 불일치를 **reconcile 경로로 넘겨** 사람이 보게 한다 (요구사항 5).

    `reconcile.reconcile(conn)` 이 브로커 조회를 **다시** 하므로 여기서 만든 불일치
    사실과 어긋날 수 있지만, 그것을 확인하는 것이 목적이다. 이 함수는 대조를
    강제로 돌려 중단 플래그·CRITICAL 로그를 남기고, 실패해도 매도 판단 자체는
    이미 끝났으므로 삼키지 않고 경고만 남긴다(주문은 호출부가 이미 보류했다).
    """
    logger.critical(
        f"🚨 매도 전 수량 불일치 감지 — {what}\n"
        f"   reconcile 대조를 실행합니다. 불일치가 확인되면 신규 매매가 중단됩니다.\n"
        f"   해제: 불일치를 확인한 뒤 `reconcile.resume_trading(conn)` 을 실행하세요."
    )
    try:
        reconcile.reconcile(_state_conn())
    except Exception as e:
        logger.error(f"⛔ reconcile 대조 실행 실패 — 수동으로 확인하세요: {e}")


def _check_sell(prefix: str, ticker: str, qty: float, profit_rate: float, sell_fn,
                signal: dict | None = None, market: str | None = None,
                avg_price: float | None = None, submit_sell=None,
                *, position: dict | None = None, current_price: float | None = None,
                bar_info: dict | None = None, refresh_balance=None,
                now: datetime | None = None) -> bool:
    """
    청산 판단 및 실행 — **T08 부터는 `strategy.decide()` 위임 래퍼다.**

    T06~T07 에서는 이 함수가 `exit_rules.evaluate_exit()` 를 **직접** 불렀다. T08 에서
    매수·청산 판단을 `strategy.decide()` 하나로 모으면서, 이 함수는 다음만 한다.
      1. T03 의 **EXTERNAL 매도 차단 가드** (판단 이전의 안전 점검 — 순수 함수로 못 옮긴다)
      2. `strategy.decide()` 호출 (판단)
      3. 브로커 잔고 재확인 → 주문 (부작용)

    시그니처는 T06 그대로 유지한다 — `tests/test_exit_rules.py`·
    `tests/test_trader_state_wiring.py` 가 이 이름과 인자로 배선을 검증한다.

    Args:
        profit_rate: 잔고 응답의 수익률(%). `avg_price` 와 **같은 응답**에서 온 값이라
            서로 모순되지 않는다. 판단에 쓸 현재가는 여기서 **역산**한다 —
            별도 시세 API 의 현재가를 섞으면 두 값이 어긋날 때(-10% 라는데 계산은 0%)
            손절이 발동하지 않는 상태가 생긴다(T06 실측).
        signal: 지표 신호. 과매수 청산에만 쓰고 값은 바뀌지 않는다.
        refresh_balance: () -> 잔고 dict. 주면 **주문 직전에** 실제 보유 수량을 재확인한다
            (요구사항 5). 없으면 DB 수량을 그대로 쓴다.

    **`_run_cycle()` 은 T08 부터 이 함수를 부르지 않는다**(`strategy.decide` 를 직접
    쓴다). 남겨 둔 이유는 하위 호환과, EXTERNAL 가드·잔고 재확인이 배선된 상태로
    청산만 따로 검증하는 테스트가 있기 때문이다.
    """
    if qty <= 0:
        logger.info(f"⏩ {prefix} 매도 조건 미충족 (보유 수량 없음)")
        return False

    # 판단 기준 시각. tz-aware 만 쓴다(절대 규칙 9).
    now = now or db.now_kst()

    # ── 0. 외부 보유는 매도하지 않는다 (T03) ──────────────
    # 매도 수량은 **브로커 잔고**에서 오므로, DB 상태를 보지 않으면 봇이 만들지 않은
    # 보유까지 손절·익절 규칙으로 팔아버린다. `EXTERNAL` 은 "봇이 만든 기록이 없는 보유"이므로
    # 봇의 관리 대상이 아니다. **이 가드는 부작용(DB 조회)이 있으므로 `decide()` 가 아니라
    # 여기(오케스트레이션)에 남긴다**(요청 지침).
    pos = position
    if market:
        try:
            pos = pos if pos is not None else db.get_position(_state_conn(), ticker, market)
        except Exception as e:
            # 상태를 못 읽었는데 매도하면 위험하다. 모르면 팔지 않는다.
            logger.error(f"⛔ {prefix} 포지션 상태 조회 실패 — 안전을 위해 매도를 건너뜁니다: {e}")
            return True
        if pos and pos.get("status") == "EXTERNAL":
            logger.warning(
                f"⛔ {prefix} 외부 보유(EXTERNAL) — 봇이 만든 포지션이 아니므로 매도하지 않습니다 "
                f"({qty:.0f}주 보유). 직접 정리한 뒤 reconcile 로 상태를 맞추세요."
            )
            return True

    # ── 1. 청산 판단 (순수 함수 `strategy.decide` — I/O 없음) ─
    entry_price = float(avg_price or (pos or {}).get("avg_price") or 0.0)
    # 현재가는 **수익률에서 역산**한다. `current_price` 인자는 하위 호환이며 값이
    # 넘어와도 `profit_rate` 와 같은 잔고 응답에서 나온 값이라 결과가 같다.
    judged_price = entry_price * (1.0 + profit_rate / 100.0) if entry_price > 0 else 0.0

    decision = strategy.decide(
        models.MarketSnapshot(symbol=ticker, bars=None, last_price=judged_price, now=now),
        models.PositionView(
            qty=float(qty), avg_price=entry_price,
            entry_date=(pos or {}).get("entry_time"),
            peak_price=(pos or {}).get("peak_price_since_entry"),
        ),
        models.AccountView(),          # 청산 판단에는 자본이 쓰이지 않는다
        strategy.merge_params({
            "use_indicators": False,   # 지표는 `signal` 로 이미 계산돼 넘어온다
            "market": market,
            # 청산만 보는 경로다. 진입 제한이 먼저 걸려 청산이 가려지면 안 된다.
            "max_buys_per_day": 0,
            "overbought_signal": bool(signal and signal.get("sell_signal")),
            # bar_info 는 T06 의 ATR·과매수 통로다. 넘어온 값을 그대로 쓴다.
            "exit": {"overbought_exit_enabled": bool(bar_info and bar_info.get("overbought"))},
        }),
        models.DayCounters(),
        ai_score=None,
    )
    if not decision.is_sell:
        logger.info(
            f"⏩ {prefix} 매도 조건 미충족 (수익률: {profit_rate:.2f}%, "
            f"기준: 목표 +{config.TAKE_PROFIT_PCT}% / 손절 {config.STOP_LOSS_PCT}% / "
            f"최대 {config.MAX_HOLD_TRADING_DAYS}거래일)"
        )
        return False

    reason = decision.reason
    label = exit_rules.LABELS.get(reason, reason)
    logger.info(
        f"📤 {prefix} 청산 판단 — {label}({reason}): {decision.meta.get('detail')} "
        f"→ {qty:.0f}주 전량 매도"
    )

    # ── 2. 브로커 보유 수량 재확인 (요구사항 5) ───────────
    sell_qty = qty
    if market and refresh_balance is not None:
        confirmed = _confirm_sell_quantity(prefix, market, ticker, qty, refresh_balance)
        if confirmed is None:
            return True         # 불일치 — 주문하지 않는다(사람이 확인해야 한다)
        sell_qty = confirmed

    # ── 3. 주문 (사유를 함께 넘긴다) ─────────────────────
    try:
        if submit_sell is not None and market:
            submit_sell(reason, sell_qty)
        else:
            _place_sell(prefix, market, ticker, sell_qty, sell_fn, None)
    except Exception as e:
        logger.error(f"❌ {prefix} 청산({reason}) 주문 실패: {e}")
        return True     # 매도 시도는 했다 — 다음 사이클에 다시 판단한다

    # 청산은 전량이므로 당일 재진입을 허용한다(부분 매도 상태가 없다).
    bot_state.set_bought_today(ticker, False)
    return True


def _fetch_confirmed_bars(symbol: str, now: datetime):
    """
    **확정 일봉**을 DataFrame 으로 만든다 (T08 요구사항 3).

    `history.get_confirmed_bars()` 는 당일 봉을 제외한 일봉 목록(dict)을 준다.
    판단 함수는 DataFrame 을 받으므로 여기서 변환한다 — 변환(I/O·자료형)은
    오케스트레이션의 일이고, `strategy.decide` 는 순수하게 남는다.

    **조회 실패와 데이터 없음을 구분한다** (T04 원칙).
      - 캐시 파일이 없거나 비어 있으면 → 빈 DataFrame. "신호 없음"이며 매수하지 않는다.
      - 읽기 자체가 실패하면(권한·손상) → 호출부가 알 수 있게 예외를 올린다.
    pandas 는 `history`/`market_calendar` 가 이미 쓰는 전이 의존성이라 새로 추가하지 않는다.
    """
    import pandas as pd

    bars = history.get_confirmed_bars(symbol, as_of=now)
    if not bars:
        return pd.DataFrame(columns=history.COLUMNS)
    return pd.DataFrame(bars, columns=history.COLUMNS)


def _entry_signal_meta(symbol: str, bars, now: datetime) -> dict:
    """`MarketSnapshot` 에 남길 일봉 요약(로그용). 마지막 확정 봉 날짜와 건수."""
    try:
        count = int(len(bars)) if bars is not None else 0
    except TypeError:
        count = 0
    last_date = None
    if count:
        try:
            last_date = str(bars["date"].iloc[-1])
        except Exception as e:
            # 메타는 로그용이다 — 실패를 삼키지 않고 알리되 판단은 계속한다.
            logger.warning(f"⚠️ [{symbol}] 확정 봉 날짜를 읽지 못했습니다: {e}")
    return {"confirmed_bars": count, "last_confirmed_date": last_date}


def _run_cycle(prefix: str, ticker: str, balance: dict, current_price: float,
               ai_score: int, price_fmt: str, sell_fn, buy_fn,
               fetch_closes=None, market: str | None = None,
               submit_buy=None, submit_sell=None, status_fn=None, cancel_fn=None,
               refresh_balance=None, refresh_peak=None, now: datetime | None = None,
               sizing_fn=None, fx_rate: float = 1.0, params: dict | None = None,
               broker=None):
    """
    해외·국내 공통 매매 사이클 — **얇은 오케스트레이터** (T08).

    하는 일은 넷뿐이다. 판단은 하지 않는다.
      1. **데이터 수집** — 확정 일봉, 보유·자본·카운터 (I/O)
      2. **사전 정리** — 미확정 주문 확정, 진입 후 고점 갱신 (I/O)
      3. **판단** — `strategy.decide()` (순수, I/O 없음)
      4. **실행** — `submit_buy`/`submit_sell` 로 주문, 카운터 기록 (I/O)

    T07 까지 이 함수 안에 있던 RSI·볼린저·손익률 계산과 AI 점수 비교는 **전부
    `strategy.py` 로 옮겼다.** 여기에는 지표 계산도, 임계값 비교도 없다.

    Args:
        price_fmt: 로그용 가격 포맷 함수
        sell_fn / buy_fn: (수량) / (수량 × 가격) 주문 콜러블. T04 부터 실제 주문은
                `submit_*` 가 내므로 `buy_fn` 은 예전 경로(`market=None`)에만 쓰인다.
        fetch_closes: 하위 호환용 인자. **T08 부터 쓰지 않는다** — 지표는 확정 일봉
                (`history.get_confirmed_bars`)으로만 계산한다. 호출부(`main`)가
                당일 봉 포함 목록을 넘겨도 무시한다. 시그니처는 유지한다.
        market: 시장 구분(DB 기록용). None이면 DB 기록을 건너뛰고 예전 경로를 쓴다.
        sizing_fn: **T08 부터 쓰지 않는다.** 수량은 `strategy.decide()` 안에서
                `sizing.compute_qty` 로 정해진다. 시그니처만 남겨 둔다(하위 호환).
        submit_buy: (수량, 가격) -> order_id. write-ahead 기록 + 전송 (T04)
        submit_sell: (사유, 수량) -> order_id (T06)
        status_fn / cancel_fn: 체결 조회 / 미체결 취소 (T04)
        refresh_balance: () -> 잔고 dict. 청산 직전 실제 보유 수량 재확인 (요구사항 5)
        refresh_peak: () -> float|None. 진입 후 고점 갱신 (트레일링용)
        now: 판단 기준 시각(tz-aware). None 이면 `db.now_kst()`.
        params: `strategy.merge_params()` 로 채워질 파라미터. `market`·`fx_rate` 는
                여기서 채워 넣는다(호출부가 넘긴 값이 우선).
        broker: `broker.Broker` 어댑터. 현재는 로그·검증용으로만 받는다 —
                실제 호출은 기존 `kis_*` 함수가 그대로 한다(요청 지침: 별칭 정리는 범위 밖).

    반환: 없음. 결정과 입력 지표값은 로그에 남는다(재현용).
    """
    qty         = balance["quantity"]
    avg_price   = balance["avg_price"]
    profit_rate = balance["profit_rate"]
    total_krw   = balance["total_asset_krw"]
    cash_krw    = balance["cash_krw"]

    # 판단 기준 시각. tz-aware 만 쓴다(절대 규칙 9).
    now = now or db.now_kst()

    # ── 진입 후 고점 갱신 (트레일링 스톱, 요구사항 2) ─────
    # 청산 판단 **전에** 갱신한다 — 갱신이 늦으면 고점이 실제보다 낮게 잡혀
    # 트레일링이 늦게 발동한다. 갱신에 실패해도 청산 판단은 계속한다(가격 기반
    # 규칙이 고점 하나 때문에 멈추면 안 된다).
    if qty > 0 and market and refresh_peak is not None:
        try:
            refresh_peak()
        except Exception as e:
            logger.warning(f"⚠️ [{prefix}] 진입 후 고점 갱신 실패 — 청산 판단은 계속합니다: {e}")

    logger.info(
        f"[{prefix} 현황] 현재가: {price_fmt(current_price)} | 평균단가: {price_fmt(avg_price)} | "
        f"수익률: {profit_rate:.2f}% | 총자산: {total_krw:,.0f}원 | "
        f"예수금: {cash_krw:,.0f}원 | AI점수: {ai_score}"
    )

    # ── 미확정 주문 확정 (주문을 내기 전에 먼저) ──────────
    # UNKNOWN/SUBMITTED 주문이 남아 있으면 신규 주문이 막힌다. 조회로 확정시켜
    # 막힘이 영구화되지 않게 한다. 체결 확인 후에만 포지션에 반영된다.
    if market:
        try:
            _resolve_open_orders(_state_conn(), market=market, ticker=ticker,
                                 status_fn=status_fn, cancel_fn=cancel_fn)
        except Exception as e:
            logger.error(f"⛔ [{prefix}] 미확정 주문 조회 실패 — 이번 사이클 판단(청산 포함)을 건너뜁니다: {e}")
            return

    # ── 데이터 수집: 확정 일봉 (T08 요구사항 3) ───────────
    # 지표는 **확정 봉만** 쓴다. `bars_received=False` 는 "조회에 실패했다"는 뜻이고,
    # 빈 DataFrame 은 "캐시가 비어 있다"는 뜻이다 — `strategy` 는 두 경우 모두 매수를
    # 보류하되 사유를 구분해 남긴다. 어느 쪽이든 **청산은 계속된다**(가격 기반 규칙).
    import pandas as pd

    bars = pd.DataFrame(columns=history.COLUMNS)
    bars_meta = {"confirmed_bars": 0}
    bars_received = True
    if market:
        try:
            bars = _fetch_confirmed_bars(ticker, now)
            bars_meta = _entry_signal_meta(ticker, bars, now)
        except Exception as e:
            bars_received = False
            logger.error(f"⛔ [{prefix}] 확정 일봉 조회 실패 — 이번 사이클 매수를 보류합니다: {e}")

    # ── 카운터·자본·포지션 (I/O) ──────────────────────────
    # 노출·보유 종목 수를 못 읽으면 매수하지 않는다 — 노출을 모르는 채로 사면
    # 상한을 넘길 수 있다. 청산은 계속한다.
    counters = None
    try:
        open_positions, exposure_krw = _exposure_snapshot(market, ticker, fx_rate)
        counters = models.DayCounters(
            buy_count_today=int(bot_state.buy_count_today(ticker)),
            last_buy_time=bot_state.last_buy_time(ticker),
            open_positions=open_positions,
            exposure_krw=exposure_krw,
        )
    except Exception as e:
        # 조회 실패의 **원인을 남긴다** — 예외 타입까지 찍어야 RuntimeError(DB 없음)와
        # DB 쿼리 오류를 로그만 보고 구분할 수 있다. 매수만 보류되고 청산은 계속된다.
        logger.critical(f"❌ [{prefix}] 보유·노출·카운터 조회 실패 — 매수만 보류(청산은 계속): "
                        f"{type(e).__name__}: {e}")

    position = _position_view(ticker, market, qty, avg_price)

    # **자본 상한(`CAPITAL_CAP_KRW`)은 여기서 적용하지 않는다** — `strategy.decide()` 안의
    # `sizing.apply_capital_cap` 이 적용한다. 두 곳에서 자본을 제한하면 서로 어긋나고,
    # 백테스트가 파라미터로 상한을 바꿀 수 없다. 여기서는 로그용으로만 미리 계산한다.
    capped_total, capped_cash = sizing.apply_capital_cap(total_krw, cash_krw)
    if (capped_total, capped_cash) != (total_krw, cash_krw):
        logger.info(
            f"[{prefix}] 자본 상한 적용: 총자산 {total_krw:,.0f} → {capped_total:,.0f}원, "
            f"예수금 {cash_krw:,.0f} → {capped_cash:,.0f}원 (CAPITAL_CAP_KRW)"
        )

    # 청산 판단에 쓸 가격. **잔고 응답의 수익률에서 역산한다** (T06 단일 출처 원칙).
    # 시세 API 현재가를 섞으면 두 값이 어긋날 때(-10% 라는데 계산은 0%) 손절이
    # 발동하지 않는 상태가 생긴다. 진입·수량은 반대로 시세 현재가를 쓴다 — 실제로
    # 주문할 가격이기 때문이다. 두 값의 출처가 다르다는 사실을 여기서 명시한다.
    exit_price = None
    if qty > 0 and avg_price > 0:
        exit_price = float(avg_price) * (1.0 + float(profit_rate) / 100.0)

    cycle_params = strategy.merge_params({
        **(params or {}),
        "market": market,
        "fx_rate": fx_rate,
        "exit_price": exit_price,
        # 조회 실패(`False`)와 캐시 없음(빈 DataFrame)을 `strategy` 가 구분할 수 있게 알린다.
        "bars_received": bars_received,
        "bars_unavailable_detail": (
            "확정 일봉 조회 실패 — 매수 보류" if not bars_received
            else "확정 일봉 캐시 없음 — 매수 보류"
        ),
    })

    snapshot = models.MarketSnapshot(symbol=ticker, bars=bars,
                                    last_price=float(current_price), now=now)
    # **상한을 적용하지 않은 실제 자본**을 넘긴다 — 상한 적용은 `decide()` 안에서 한다.
    account = models.AccountView(equity=total_krw, cash=cash_krw)

    # ── 판단 (순수 함수) — 매수·청산·수량을 한 번에 ─────────
    # 청산이 매수보다 우선이므로, 지표를 못 얻었거나 진입 제한에 걸려도 **손절은
    # 그대로 판단된다**(`strategy.decide` 의 판단 순서 1번).
    #
    # 노출·보유·카운터를 못 읽었으면(`counters is None`) **청산은 계속, 매수만 보류**한다.
    # 판단은 `strategy.decide()` 안에서 한다 — `counters is None` 이면 청산 판단을 마친 뒤
    # `HOLD(EXPOSURE_UNAVAILABLE)` 를 돌려준다(판단 순서 2번). 여기서 조기 반환하면
    # 손절까지 막힌다(T04 의 "미체결 취소 실패" 차단과 같은 원칙 — 손절은 계속 나가야 한다).
    decision = strategy.decide(snapshot, position, account, cycle_params,
                               counters, ai_score=ai_score)

    logger.info(
        f"[{prefix} 판단 입력] {ticker} 확정봉 {bars_meta or {'confirmed_bars': 0}} | "
        f"현재가 {price_fmt(current_price)} | 평단 {price_fmt(avg_price)} | "
        f"수익률 {profit_rate:.2f}% | 자본 {capped_total:,.0f}원 | AI {ai_score}"
    )
    logger.info(f"🧭 [{prefix}] {decision.log_line()}")

    if decision.is_sell:
        _execute_sell(prefix, ticker, decision, market, qty, submit_sell, sell_fn,
                      refresh_balance)
        return
    if decision.is_buy:
        _execute_buy(prefix, ticker, decision, market, current_price, submit_buy,
                     buy_fn, fx_rate)


def _position_view(ticker: str, market: str | None, qty: float,
                   avg_price: float) -> "models.PositionView":
    """
    보유 상태를 판단용으로 읽는다 (I/O).

    잔고 응답의 수량(`qty`)과 평단(`avg_price`)을 기준으로 하고, DB 에서 진입 시각과
    고점을 가져온다 — 보유 거래일(시간청산)과 트레일링 스톱에 필요하다.

    **DB 조회 실패를 삼키지 않는다.** 다만 그 때문에 청산 판단 자체를 멈추지는 않는다:
    진입 시각이 없으면 `exit_rules` 가 시간청산을 건너뛰고 가격 기반 규칙만 쓴다
    (`exit_rules.trading_days_held` 가 경고를 남긴다).
    """
    view = models.PositionView(qty=float(qty or 0.0), avg_price=float(avg_price or 0.0))
    if not market or view.qty <= 0:
        return view
    try:
        row = db.get_position(_state_conn(), ticker, market) or {}
    except Exception as e:
        logger.error(f"⚠️ 포지션 상세 조회 실패 — 진입 시각·고점 없이 판단합니다: {e}")
        return view
    return models.PositionView(
        qty=view.qty, avg_price=view.avg_price,
        entry_date=row.get("entry_time"),
        peak_price=row.get("peak_price_since_entry"),
    )


def _hold_decision(ticker: str, reason: str, meta: dict) -> "models.Decision":
    """보류 결정을 만들어 로그에 남긴다(매수 경로만 막는다)."""
    decision = models.Decision(models.HOLD, reason=reason, meta=meta)
    logger.info(f"[판단] {ticker} {decision.log_line()}")
    return decision


def _may_sell_holding(prefix: str, ticker: str, market: str | None, qty: float) -> bool:
    """
    실제 청산 경로에서 이 보유를 팔아도 되는지 DB 로 확인한다 (T03, T08c).

    **허용 목록 방식**이다 — positions 행이 있고 status 가 OPEN 일 때만 True.
    행이 없거나 EXTERNAL·CLOSED 이거나 조회에 실패하면 False (모르면 팔지 않는다).
    매도 수량은 브로커 잔고에서 오므로, 이 확인이 없으면 봇이 만들지 않은 보유까지 판다.
    """
    if not market:
        return True      # DB 미사용 예전 경로 — 가드 대상이 아니다
    try:
        pos = db.get_position(_state_conn(), ticker, market)
    except Exception as e:
        logger.error(f"⛔ {prefix} 포지션 상태 조회 실패 — 안전을 위해 매도하지 않습니다: {e}")
        return False
    if pos is None:
        logger.error(
            f"⛔ {prefix} 브로커에는 {qty:.0f}주가 있으나 positions 기록이 없습니다 — "
            "봇 소유를 확인할 수 없어 매도하지 않습니다. reconcile 결과를 확인하세요."
        )
        return False
    status = pos.get("status")
    if status != "OPEN":
        logger.warning(
            f"⛔ {prefix} 보유 상태가 {status} 입니다 — 봇이 관리하는 포지션(OPEN)이 아니므로 "
            f"매도하지 않습니다({qty:.0f}주). 직접 정리한 뒤 reconcile 로 상태를 맞추세요."
        )
        return False
    return True


def _execute_sell(prefix: str, ticker: str, decision, market: str | None, qty: float,
                  submit_sell, sell_fn, refresh_balance) -> None:
    """
    청산 실행 (I/O). 판단은 이미 끝났다 — 여기서는 **주문 직전 안전 점검만** 한다.

    요구사항 5 의 브로커 잔고 재확인을 반드시 거친다: 판단에 쓴 수량(DB·잔고 응답)과
    실제 보유가 다르면 **보유하지 않은 수량을 팔려는 주문**이 나갈 수 있다.
    """
    if not _may_sell_holding(prefix, ticker, market, qty):
        return

    reason = decision.reason
    label = exit_rules.LABELS.get(reason, reason)
    logger.info(
        f"📤 {prefix} 청산 판단 — {label}({reason}): {decision.meta.get('detail')} "
        f"→ {qty:.0f}주 전량 매도"
    )

    sell_qty = qty
    if market and refresh_balance is not None:
        confirmed = _confirm_sell_quantity(prefix, market, ticker, qty, refresh_balance)
        if confirmed is None:
            return      # 불일치 — 주문하지 않는다(사람이 확인해야 한다)
        sell_qty = confirmed

    try:
        if submit_sell is not None and market:
            submit_sell(reason, sell_qty)
        else:
            _place_sell(prefix, market, ticker, sell_qty, sell_fn, None)
    except Exception as e:
        logger.error(f"❌ {prefix} 청산({reason}) 주문 실패: {e}")
        return

    # 청산은 전량이므로 당일 재진입을 허용한다(부분 매도 상태가 없다).
    bot_state.set_bought_today(ticker, False)


def _may_buy_into(prefix: str, ticker: str, market: str | None) -> bool:
    """
    매수가 봇이 관리하지 않는 보유(EXTERNAL) 위에 쌓이지 않게 확인한다 (T03, T08d).

    체결 반영(`_apply_fill_to_position`)은 기존 행의 status 를 보지 않고 OPEN 으로 덮어쓰므로,
    EXTERNAL 보유 위에 매수가 체결되면 외부 보유분까지 봇 소유가 된다.
    행이 없거나(첫 진입) OPEN·CLOSED(재진입)이면 True, EXTERNAL 이거나 조회에 실패하면 False.
    """
    if not market:
        return True
    try:
        pos = db.get_position(_state_conn(), ticker, market)
    except Exception as e:
        logger.error(f"⛔ {prefix} 포지션 상태 조회 실패 — 안전을 위해 매수하지 않습니다: {e}")
        return False
    if pos is not None and pos.get("status") == "EXTERNAL":
        logger.warning(
            f"⛔ {prefix} 외부 보유(EXTERNAL) 종목입니다 — 체결되면 외부 보유분이 봇 소유로 바뀌므로 "
            "매수하지 않습니다. 직접 정리한 뒤 reconcile 로 상태를 맞추세요."
        )
        return False
    return True


def _execute_buy(prefix: str, ticker: str, decision, market: str | None,
                 current_price: float, submit_buy, buy_fn, fx_rate: float) -> None:
    """
    매수 실행 (I/O). 수량은 `decision.qty`(=`sizing.compute_qty` 결과) 그대로 쓴다.

    **여기서 수량을 다시 계산하지 않는다** — 두 곳에서 계산하면 판단한 수량과 실제
    주문 수량이 어긋난다(T07 원칙).
    """
    if not _may_buy_into(prefix, ticker, market):
        return
    qty = int(decision.qty)
    buy_count = int(bot_state.buy_count_today(ticker))
    logger.info(
        f"🚀 [{prefix}] 매수 실행 ({buy_count + 1}/{config.MAX_BUYS_PER_DAY}회차): "
        f"{qty}주 × {current_price:,.0f}원 | {decision.meta.get('sizing', '')}"
    )

    if market:
        if submit_buy is None:
            logger.error(f"⛔ [{prefix}] 주문 제출 경로가 없어 매수를 건너뜁니다")
            return
        try:
            submit_buy(qty, float(current_price))
        except DuplicateOrderBlocked as e:
            logger.warning(f"⏩ [{prefix}] 중복 주문 차단 — {e}")
            return
        except Exception as e:
            logger.error(f"❌ [{prefix}] 매수 주문 실패 — 체결 확인 전이므로 횟수 미기록: {e}")
            return
        bot_state.record_buy(ticker)
        logger.info(
            f"✅ [{prefix}] 매수 주문 접수 — 당일 {bot_state.buy_count_today(ticker)}회차 "
            "(포지션은 체결 확인 후 반영)"
        )
    elif buy_fn and buy_fn(qty * float(current_price)):
        bot_state.record_buy(ticker)
        logger.info(f"✅ [{prefix}] 매수 완료 — 당일 {bot_state.buy_count_today(ticker)}회차")
    else:
        logger.warning(f"⚠️ [{prefix}] 매수 미체결 — 횟수 미기록")


def _require_settlement_support(module, label: str) -> None:
    """
    체결 조회가 구현되지 않은 시장에서는 **주문을 내지 않는다** (T04b).

    해외(`kis_overseas.get_order_status`)는 공식 문서가 없어 여전히 미구현이다.
    체결 조회가 없으면 주문이 나가도 포지션에 반영되지 않고(체결 확인 불가),
    미체결 주문이 계속 미확정으로 남아 아무도 정리하지 못한다(주문이 반복될 수 있다).
    그래서 조용히 삼키지 말고 여기서 **명시적으로 막고 CRITICAL 로 남긴다.**

    구현 여부는 `supports_order_inquiry()` 플래그로 본다. 조회 함수를 실제로 불러
    보는 방식은 쓰지 않는다 — 그러면 조회 요청이 진짜로 나가고, 실패 원인
    (미구현인지 네트워크 오류인지)이 구분되지 않는다.
    """
    probe = getattr(module, "supports_order_inquiry", None)
    if probe is None:
        # 플래그가 없는 모듈은 "구현 여부를 알 수 없다" → 모르면 주문하지 않는다.
        logger.critical(
            f"🚨 [{label}] 체결 조회 구현 여부를 확인할 수 없어 주문을 내지 않습니다 "
            f"({module.__name__} 에 supports_order_inquiry() 가 없습니다)."
        )
        raise NotImplementedError(
            f"{module.__name__} 에 supports_order_inquiry() 가 없어 "
            f"체결 조회 지원 여부를 알 수 없습니다"
        )

    if probe():
        return

    logger.critical(
        f"🚨 [{label}] 체결 조회가 구현되지 않아 주문을 내지 않습니다. "
        f"체결을 확인할 수 없으면 포지션에 반영할 수 없고, 미체결 주문도 정리할 수 없습니다.\n"
        f"   해외주식 체결조회/취소 API 는 공식 문서가 확인되지 않았습니다(T04b 보고 참고).\n"
        f"   운용을 멈추려면 IS_OVERSEAS_ENABLED=false 로 두세요."
    )
    raise NotImplementedError(
        f"[{label}] 체결 조회가 미구현이라 주문을 내지 않습니다"
    )


def run_overseas_cycle(ai_score: int):
    """해외주식(QQQ) 매매 사이클 (T04: 지정가·체결 확인 기반)."""
    ticker = config.TARGET_TICKER
    balance = kis.get_balance()
    current_price = kis.get_current_price(ticker)
    rate = kis.get_usd_krw_rate()
    conn = _state_conn()

    # 체결 조회가 없으면 주문을 내지 않는다 (T04b).
    _require_settlement_support(kis, "해외")

    def _qty_for_budget(spend_krw: float) -> int:
        """원화 금액을 달러로 환산한 정수 수량. 0이면 살 수 없다 (`market=None` 경로용)."""
        stock_krw = current_price * rate
        return int(spend_krw // stock_krw) if stock_krw > 0 else 0

    def submit_buy(qty: int, price: float) -> int:
        """
        사전 기록 후 매수 주문을 낸다. 반환: orders.id.

        **T07: 인자가 "예산"에서 "수량·가격"으로 바뀌었다.** 수량은 `sizing` 이
        정하고(위험·현금 기준) trader 는 그대로 쓴다 — 여기서 다시 나눗셈하지 않는다.
        두 곳에서 계산하면 `submit_buy` 가 받은 수량과 실제 주문 수량이 어긋난다.
        """
        target_qty = int(qty)
        if target_qty < 1:
            raise ValueError(f"[해외] 수량이 0 입니다 (qty={qty})")
        order_price = _order_price_for(kis, current_price, side="BUY")
        return _submit_order(
            conn, market=MARKET_OVERSEAS, ticker=ticker, side="BUY", qty=target_qty,
            price=order_price, order_type="LIMIT",
            make_body=lambda: kis.order_body(target_qty, order_price),
            send=lambda body, on_uncertain: kis.send_order(body, side="BUY",
                                                           on_uncertain=on_uncertain),
        )

    def submit_sell(reason: str, qty: float) -> int:
        """
        청산 매도 주문 (T06). `reason` 은 `orders.reason` 에 남긴다(요구사항 4).

        해외는 시장가 미구현(`kis.order_body`)이라 사유와 무관하게 지정가로 난다 —
        **갭 하락 시 손절이 체결되지 않을 수 있다**(T06 보고서 참고).
        """
        order_type = _exit_order_type(reason)
        order_price = _order_price_for(kis, current_price, side="SELL")
        return _submit_order(
            conn, market=MARKET_OVERSEAS, ticker=ticker, side="SELL", qty=qty,
            price=order_price, order_type="LIMIT", reason=reason,
            make_body=lambda: kis.order_body(qty, order_price, order_type=order_type),
            send=lambda body, on_uncertain: kis.send_order(body, side="SELL",
                                                           on_uncertain=on_uncertain),
        )

    def status_fn(order_no: str, *, ticker=None, org_no=None):
        return kis.get_order_status(order_no, symbol=ticker)

    def cancel_fn(qty: float) -> dict:
        return kis.cancel_order("", quantity=qty, symbol=ticker)

    buy_fn = _budget_probe(_qty_for_budget)

    balance_norm = {
        "quantity": balance["quantity"],
        "avg_price": balance["avg_price_usd"],
        "profit_rate": balance["profit_rate"],
        "total_asset_krw": balance["total_asset_krw"],
        "cash_krw": balance["cash_krw"],
    }
    _run_cycle(
        "해외", ticker, balance_norm, current_price, ai_score,
        lambda p: f"${p:,.2f}", None, buy_fn,
        # `fetch_closes` 는 T08 부터 쓰지 않는다(확정 봉만 쓴다). 시그니처 유지용이다.
        lambda: kis.get_daily_closes(ticker),
        market=MARKET_OVERSEAS, submit_buy=submit_buy, submit_sell=submit_sell,
        status_fn=status_fn, cancel_fn=cancel_fn,
        # 청산 직전 실제 보유 수량 재확인 (요구사항 5). 잔고 API 를 다시 부른다.
        refresh_balance=kis.get_balance,
        refresh_peak=lambda: _refresh_peak(conn, ticker, MARKET_OVERSEAS, current_price),
        # 해외 포지션의 avg_price 는 **달러**다. 사이징·노출 상한은 원화 기준이므로
        # 환율을 넘긴다(`strategy.decide` 가 `last_price` 를 원화로 환산한다).
        # 환산하지 않으면 1주 가격이 1/1400 로 보여 수량이 1400배가 된다(T07).
        fx_rate=rate,
        broker=broker_module.for_market(MARKET_OVERSEAS, ticker),
    )


def run_domestic_cycle(ai_score: int):
    """국내주식 ETF 매매 사이클 (지정가 주문, T04: 체결 확인 기반)."""
    ticker = config.DOMESTIC_TICKER
    balance = kd.get_domestic_balance(ticker)
    current_price = kd.get_domestic_price(ticker)
    conn = _state_conn()

    def _qty_for_budget(spend_krw: float) -> int:
        """`market=None` 경로용 (DB 미사용). 실제 사이클은 `sizing_fn` 이 정한다."""
        return int(spend_krw // current_price) if current_price > 0 else 0

    def submit_buy(qty: int, price: float) -> int:
        """사전 기록 후 매수 주문을 낸다. 반환: orders.id. **T07: (수량, 가격)**."""
        target_qty = int(qty)
        if target_qty < 1:
            raise ValueError(f"[국내] 수량이 0 입니다 (qty={qty})")
        body = kd.order_body(ticker, target_qty, current_price, side="BUY")
        return _submit_order(
            conn, market=MARKET_DOMESTIC, ticker=ticker, side="BUY", qty=target_qty,
            price=float(body["ORD_UNPR"]), order_type=_order_type_label(),
            make_body=lambda: body,
            send=lambda b, on_uncertain: kd.send_order(b, side="BUY",
                                                       on_uncertain=on_uncertain),
        )

    def submit_sell(reason: str, qty: float) -> int:
        """
        청산 매도 주문 (T06). 사유별로 주문 유형이 다르다 (`config.EXIT_ORDER_TYPE_*`).

        손절·시간청산은 시장가로 낸다 — 지정가가 미체결로 남으면 `cancel_order` 가
        미구현(T04b)이라 취소할 수 없고 포지션이 그대로 남는다.
        """
        order_type = _exit_order_type(reason)
        body = kd.order_body(ticker, int(qty), current_price, side="SELL",
                             order_type=order_type)
        return _submit_order(
            conn, market=MARKET_DOMESTIC, ticker=ticker, side="SELL", qty=qty,
            price=float(body["ORD_UNPR"]), order_type=order_type.upper(), reason=reason,
            make_body=lambda: body,
            send=lambda b, on_uncertain: kd.send_order(b, side="SELL",
                                                       on_uncertain=on_uncertain),
        )

    def status_fn(order_no: str, *, ticker=None, org_no=None):
        return kd.get_order_status(order_no, ticker=ticker, org_no=org_no)

    def cancel_fn(qty: float) -> dict:
        return kd.cancel_order("", quantity=int(qty), ticker=ticker)

    buy_fn = _budget_probe(_qty_for_budget)

    balance_norm = {
        "quantity": balance["quantity"],
        "avg_price": balance["avg_price_krw"],
        "profit_rate": balance["profit_rate"],
        "total_asset_krw": balance["total_asset_krw"],
        "cash_krw": balance["cash_krw"],
    }
    _run_cycle(
        "국내", ticker, balance_norm, current_price, ai_score,
        lambda p: f"{p:,.0f}원", None, buy_fn,
        # `fetch_closes` 는 T08 부터 쓰지 않는다(확정 봉만 쓴다). 시그니처 유지용이다.
        lambda: kd.get_daily_closes(ticker),
        market=MARKET_DOMESTIC, submit_buy=submit_buy, submit_sell=submit_sell,
        status_fn=status_fn, cancel_fn=cancel_fn,
        # 청산 직전 실제 보유 수량 재확인 (요구사항 5). 잔고 API 를 다시 부른다.
        refresh_balance=lambda: kd.get_domestic_balance(ticker),
        refresh_peak=lambda: _refresh_peak(conn, ticker, MARKET_DOMESTIC, current_price),
        # 국내는 가격이 이미 원화라 환산이 필요 없다(fx_rate 기본 1.0).
        # ATR(`STOP_LOSS_ATR_MULT`)은 켜지 않는다 — 일봉 OHLC 조회가 미구현이라
        # ATR 값의 출처가 없다(`history.kis_rows_to_bars` NotImplementedError).
        # 기본 0 이므로 고정 `STOP_LOSS_PCT` 가 쓰인다.
        # VERIFY: 일봉 OHLC 를 확보하면 ATR 기반 손절폭을 넘겨야 한다(T06 보고 참고).
        broker=broker_module.for_market(MARKET_DOMESTIC, ticker),
    )
