"""
매매 판단 및 실행 모듈 (스노우볼 전략).

해외(QQQ)와 국내(379800) ETF를 매 사이클마다 각각 판단해 동시 운용한다.

매수 조건 (AND):
  1. 진입 타점 (USE_INDICATORS=True면 지표, 아니면 하락률)
     - 지표: RSI(14) <= RSI_OVERSOLD(30) 이고 종가 <= 볼린저 하단
     - 하락률: 현재가 < 평균 단가 × (1 + DIP_THRESHOLD/100), 보유 없으면 최초 매수 허용
  2. AI 점수 >= AI_SCORE_FLOOR                      →  심각한 악재 없음
  3. 당일 매수 횟수 < MAX_BUYS_PER_DAY              →  분할 매수 상한
  4. 직전 매수 후 MIN_BUY_INTERVAL_HOURS 경과        →  매수 소진 방지

매도 조건 (T06: 부분 익절 폐기 — 청산은 **전량**, 사유는 **하나**)
  판단은 `exit_rules.evaluate_exit()` **순수 함수**가 한다(I/O 없음).
  우선순위: 손절 > 시간청산 > 목표가 > 트레일링 > 과매수.
  여기(`trader`)는 I/O 만 담당한다 — 포지션 조회, 브로커 수량 재확인, 주문.

  T06 이전에는 부분 익절(2/4/6% 단계별 분할)이었고, 소액(1~4주)에서 최소 수량
  보정 때문에 첫 단계에 전량이 나가는 결함이 있었다(CLAUDE.md 참고).
"""
import json
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import db
import exit_rules
import indicators
import kis_domestic as kd
import kis_overseas as kis
import reconcile
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


class _BudgetProbe:
    """
    `_run_cycle` 이 `buy_fn.get_orderable` 만 쓰는 경우를 위한 자리채움 콜러블 (T04).

    T04 부터 실제 주문은 `submit_buy` 가 내므로 `buy_fn` 은 "1주라도 살 수 있는가"
    판단에만 쓰인다. 계좌·DB 를 건드리지 않으므로 안전하다.
    """

    def __init__(self, qty_for_budget):
        self._qty_for_budget = qty_for_budget

    def __call__(self, buy_krw: float) -> bool:
        return self._qty_for_budget(buy_krw) >= 1

    def get_orderable(self, budget_krw: float) -> float:
        """해외는 잔고 API 의 통합증거금 값을 신뢰하므로 0(클램핑 없음)을 돌려준다."""
        return 0.0


def _budget_probe(qty_for_budget):
    """`_BudgetProbe` 생성 별칭(가독용)."""
    return _BudgetProbe(qty_for_budget)


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
    청산 판단 및 실행 (T06). 매도했으면 True.

    **판단은 `exit_rules.evaluate_exit()` 가 한다**(순수 함수, I/O 없음). 여기서는
    I/O 만 한다 — 포지션·현재가 조회, 브로커 수량 재확인, 주문.

    Args:
        sell_fn: (수량) -> 주문 실행 콜러블 (DB 기록 없는 하위 호환 경로)
        signal: 지표 신호(indicators.evaluate). 과매수 청산에만 쓰고 값은 바뀌지 않는다.
        market: 포지션 시장 구분(DB 기록용). None이면 DB 기록을 건너뛴다(하위 호환).
        avg_price: 평균 단가(DB 기록용). None이면 DB 값을 쓴다.
        submit_sell: (사유, 수량) -> order_id. write-ahead 주문 콜러블 (T04/T06)
        position: 진입 시각·고점 등이 담긴 포지션 dict. None 이면 DB 에서 읽는다.
        current_price: 현재가. None 이면 `avg_price × (1+수익률)` 로 역산한다.
        bar_info: {"atr": float|None, "overbought": bool}. 없으면 ATR·과매수 판단을 건너뛴다.
        refresh_balance: () -> 잔고 dict. 주면 **주문 직전에** 실제 보유 수량을 재확인한다
                (요구사항 5). 없으면 DB 수량을 그대로 쓴다는 뜻이며, 그때는 경고를 남긴다.
        now: 판단 기준 시각(tz-aware). None 이면 `db.now_kst()`.

    사유 문자열(`STOP_LOSS` 등)은 `orders.reason` 과 로그에 그대로 남는다(요구사항 4).
    """
    if qty <= 0:
        logger.info(f"⏩ {prefix} 매도 조건 미충족 (보유 수량 없음)")
        return False

    # ── 0. 외부 보유는 매도하지 않는다 (T03) ──────────────
    # 매도 수량은 **브로커 잔고**에서 오므로, DB 상태를 보지 않으면 봇이 만들지 않은
    # 보유까지 손절·익절 규칙으로 팔아버린다. `EXTERNAL` 은 "봇이 만든 기록이 없는 보유"이므로
    # 봇의 관리 대상이 아니다. 사람이 직접 정리해야 한다.
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

    # ── 1. 청산 판단 (순수 함수, I/O 없음) ────────────────
    # 가격은 **한 출처에서만** 가져온다.
    # `avg_price` 와 `profit_rate` 는 같은 잔고 응답에서 온 값이라 서로 모순되지 않는다.
    # 별도 시세 API 의 현재가를 섞으면 두 값이 어긋날 때(-10% 라는데 계산은 0%)
    # 손절이 발동하지 않는 상태가 생긴다 — 실제로 이 사이클에서 그런 불일치가 있었다.
    # 그래서 현재가는 수익률에서 **역산**한다(비율만 맞으면 되므로 수익률 계산에 충분하다).
    entry_price = float(avg_price or (pos or {}).get("avg_price") or 0.0)
    current_price = entry_price * (1.0 + profit_rate / 100.0) if entry_price > 0 else 0.0

    judgment = {
        "qty": qty,
        "avg_price": entry_price,
        "current_price": current_price,
        "entry_time": (pos or {}).get("entry_time"),
        "peak_price_since_entry": (pos or {}).get("peak_price_since_entry"),
        # 시장을 모르면 보유 거래일을 셀 수 없어 시간청산이 발동하지 않는다
        # (exit_rules 가 경고를 남긴다). 임의로 다른 시장을 가정하지 않는다.
        "market": market,
    }
    exit_signal = exit_rules.evaluate_exit(
        judgment, bar_info, exit_rules.params_from_config(),
        now or db.now_kst(),
    )
    if exit_signal is None:
        logger.info(
            f"⏩ {prefix} 매도 조건 미충족 (수익률: {profit_rate:.2f}%, "
            f"기준: 목표 +{config.TAKE_PROFIT_PCT}% / 손절 {config.STOP_LOSS_PCT}% / "
            f"최대 {config.MAX_HOLD_TRADING_DAYS}거래일)"
        )
        return False

    reason = exit_signal.reason
    label = exit_rules.LABELS[reason]
    logger.info(
        f"📤 {prefix} 청산 판단 — {label}({reason}): {exit_signal.detail} "
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


def _run_cycle(prefix: str, ticker: str, balance: dict, current_price: float,
               ai_score: int, ratio: float, price_fmt: str, sell_fn, buy_fn,
               fetch_closes=None, market: str | None = None,
               submit_buy=None, submit_sell=None, status_fn=None, cancel_fn=None,
               refresh_balance=None, refresh_peak=None, now: datetime | None = None):
    """
    해외·국내 공통 매매 사이클 로직.

    Args:
        ratio: 총자산 대비 1회 매수 비중
        price_fmt: 로그용 가격 포맷 함수
        sell_fn / buy_fn: (수량) / (수량, 가격) 주문 실행 콜러블.
                T04 부터 실제 주문은 `submit_*` 가 내므로, `buy_fn` 은 예산으로
                1주라도 살 수 있는지 판단하는 데만 쓰인다(`_BudgetProbe`).
        market: 시장 구분(DB 기록용). None이면 DB 기록을 건너뛰고 예전 경로를 쓴다.
        submit_buy: (예산) -> order_id. write-ahead 기록 + 전송 (T04)
        submit_sell: (사유, 수량) -> order_id. **T06 에서 사유가 추가됐다.**
        status_fn / cancel_fn: 체결 조회 / 미체결 취소 (T04)
        refresh_balance: () -> 잔고 dict. 청산 직전 실제 보유 수량 재확인 (요구사항 5)
        refresh_peak: () -> float|None. 진입 후 고점 갱신 (트레일링용, 요구사항 2)
        now: 판단 기준 시각(tz-aware). None 이면 `db.now_kst()`.
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
            logger.error(f"⛔ [{prefix}] 미확정 주문 조회 실패 — 신규 주문을 건너뜁니다: {e}")
            return

    # ── 보조지표 신호 (RSI + 볼린저밴드) ──────────────────
    signal = None
    if config.USE_INDICATORS and fetch_closes is not None:
        try:
            signal = indicators.evaluate_or_none(fetch_closes)
            logger.info(f"[{prefix} 지표] {signal['reason']}")
        except indicators.IndicatorDataUnavailable as e:
            # 조회 실패를 "신호 없음"으로 바꾸면 하락률 폴백 매수로 이어진다 (T04).
            # 데이터를 모를 때는 사지 않는다. 청산 판단은 가격 기반 규칙으로 계속한다.
            logger.error(f"⛔ [{prefix}] 지표 데이터 없음 — 이번 사이클 매수를 보류합니다: {e}")
            _check_sell(prefix, ticker, qty, profit_rate, sell_fn, None,
                        market=market, avg_price=avg_price,
                        submit_sell=submit_sell, current_price=current_price,
                        now=now)
            return

    # ── 청산 (순수 함수 판단 → I/O 실행, T06) ─────────────
    # 과매수 청산은 T06 에서 기본 꺼짐이라 `sell_signal` 을 그대로 쓰지 않고
    # 설정이 켜져 있을 때만 켠다(검증되지 않은 신호를 청산 사유에 섞지 않는다).
    bar_info = {
        "atr": None,
        "overbought": bool(signal and signal.get("sell_signal"))
                      and config.OVERBOUGHT_EXIT_ENABLED,
    }
    if _check_sell(prefix, ticker, qty, profit_rate, sell_fn, signal,
                   market=market, avg_price=avg_price, submit_sell=submit_sell,
                   current_price=current_price, bar_info=bar_info,
                   refresh_balance=refresh_balance, now=now):
        return

    # ── 매수 ────────────────────────────────────────────
    buy_count = bot_state.buy_count_today(ticker)
    if buy_count >= config.MAX_BUYS_PER_DAY:
        logger.info(f"⏩ [{prefix}] 당일 매수 상한 도달 ({buy_count}/{config.MAX_BUYS_PER_DAY}회) — 매수 스킵")
        return

    # 직전 매수 후 최소 간격 체크 (2회차 이상부터 적용)
    if buy_count > 0:
        last_t = bot_state.last_buy_time(ticker)
        if last_t:
            elapsed_h = (datetime.now(KST) - last_t).total_seconds() / 3600
            if elapsed_h < config.MIN_BUY_INTERVAL_HOURS:
                logger.info(
                    f"⏩ [{prefix}] 직전 매수 후 {elapsed_h:.1f}h 경과 "
                    f"(최소 {config.MIN_BUY_INTERVAL_HOURS:.1f}h 필요) — 매수 스킵"
                )
                return

    # 조건 1: 진입 타점 (지표가 있으면 지표, 없으면 평단 대비 하락률)
    if avg_price > 0:
        dip_pct = (current_price - avg_price) / avg_price * 100
    else:
        dip_pct = 0

    if signal is not None:
        entry_ok = signal["buy_signal"]
        entry_desc = f"지표 매수신호 (RSI {signal['rsi']:.1f} ≤ {config.RSI_OVERSOLD:g} + BB 하단 이탈)"
    else:
        # 보유 없으면 최초 매수 허용 (기존 하락률 기준)
        entry_ok = (avg_price == 0) or (dip_pct <= config.DIP_THRESHOLD)
        entry_desc = f"딥 하락률 {dip_pct:.2f}% (기준: {config.DIP_THRESHOLD}%)"

    # 조건 2: AI 점수 체크
    ai_ok = ai_score >= config.AI_SCORE_FLOOR

    logger.info(
        f"[{prefix} 매수 조건] {entry_desc} → {'✅' if entry_ok else '❌'} | "
        f"AI점수: {ai_score} (기준: ≥{config.AI_SCORE_FLOOR}) "
        f"→ {'✅' if ai_ok else '❌'} | "
        f"매수 횟수: {buy_count}/{config.MAX_BUYS_PER_DAY}"
    )

    if not (entry_ok and ai_ok):
        logger.info(f"❌ [{prefix}] 매수 조건 미충족 — 대기")
        return

    # 매수 금액 = 총자산 × 1회 비중, 예수금 한도 내
    buy_krw = total_krw * ratio
    buy_krw = min(buy_krw, cash_krw * 0.99)

    if buy_krw <= 0:
        logger.warning(f"⚠️ [{prefix}] 주문가능금 부족({cash_krw:,.0f}원) — 매수 불가")
        return

    # 현금 조회에 지연이 있으므로 실제 주문가능금으로 재확인 (클램핑 역할)
    try:
        orderable = buy_fn.get_orderable(buy_krw)
        if orderable > 0:
            buy_krw = min(buy_krw, orderable)
    except Exception as e:
        logger.debug(f"[{prefix}] 주문가능금 재확인 실패, 계산값 사용: {e}")

    logger.info(
        f"🚀 [{prefix}] 매수 실행 ({buy_count + 1}/{config.MAX_BUYS_PER_DAY}회차): "
        f"예산 {buy_krw:,.0f}원 (총자산의 {ratio * 100:.0f}%)"
    )
    if market:
        # T04: 주문은 사전 기록 후 전송하고, **체결이 확인된 뒤에만** 포지션에 반영한다.
        # `qty`/`current_price` 는 현황 로그와 매수 직전 판단에만 쓰인다.
        if submit_buy is None:
            logger.error(f"⛔ [{prefix}] 주문 제출 경로가 없어 매수를 건너뜁니다")
            return
        try:
            submit_buy(buy_krw)
        except DuplicateOrderBlocked as e:
            logger.warning(f"⏩ [{prefix}] 중복 주문 차단 — {e}")
            return
        except Exception as e:
            logger.error(f"❌ [{prefix}] 매수 주문 실패 — 체결 확인 전이므로 횟수 미기록: {e}")
            return
        bot_state.record_buy(ticker)
        logger.info(
            f"✅ [{prefix}] 매수 주문 접수 — 당일 {bot_state.buy_count_today(ticker)}회차 "
            f"(포지션은 체결 확인 후 반영)"
        )
    elif buy_fn(buy_krw):
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

    def _qty_for_budget(buy_krw: float) -> int:
        """원화 예산을 달러로 환산한 정수 수량. 0이면 살 수 없다."""
        stock_krw = current_price * rate
        return int(buy_krw // stock_krw) if stock_krw > 0 else 0

    def submit_buy(buy_krw: float) -> int:
        """사전 기록 후 매수 주문을 낸다. 반환: orders.id."""
        target_qty = _qty_for_budget(buy_krw)
        if target_qty < 1:
            stock_krw = current_price * rate
            raise ValueError(
                f"[해외] 예산({buy_krw:,.0f}원)이 1주 가격({stock_krw:,.0f}원)보다 작습니다"
            )
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
        config.BUY_RATIO, lambda p: f"${p:,.2f}", None, buy_fn,
        lambda: kis.get_daily_closes(ticker),
        market=MARKET_OVERSEAS, submit_buy=submit_buy, submit_sell=submit_sell,
        status_fn=status_fn, cancel_fn=cancel_fn,
        # 청산 직전 실제 보유 수량 재확인 (요구사항 5). 잔고 API 를 다시 부른다.
        refresh_balance=kis.get_balance,
        refresh_peak=lambda: _refresh_peak(conn, ticker, MARKET_OVERSEAS, current_price),
    )


def run_domestic_cycle(ai_score: int):
    """국내주식 ETF 매매 사이클 (지정가 주문, T04: 체결 확인 기반)."""
    ticker = config.DOMESTIC_TICKER
    balance = kd.get_domestic_balance(ticker)
    current_price = kd.get_domestic_price(ticker)
    conn = _state_conn()

    def _qty_for_budget(buy_krw: float) -> int:
        return int(buy_krw // current_price) if current_price > 0 else 0

    def submit_buy(buy_krw: float) -> int:
        """사전 기록 후 매수 주문을 낸다. 반환: orders.id."""
        target_qty = _qty_for_budget(buy_krw)
        if target_qty < 1:
            raise ValueError(
                f"[국내] 예산({buy_krw:,.0f}원)이 1주 가격({current_price:,.0f}원)보다 작습니다"
            )
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
    buy_fn.get_orderable = lambda budget: kd.get_orderable_cash(ticker, current_price)

    balance_norm = {
        "quantity": balance["quantity"],
        "avg_price": balance["avg_price_krw"],
        "profit_rate": balance["profit_rate"],
        "total_asset_krw": balance["total_asset_krw"],
        "cash_krw": balance["cash_krw"],
    }
    _run_cycle(
        "국내", ticker, balance_norm, current_price, ai_score,
        config.DOMESTIC_BUY_RATIO, lambda p: f"{p:,.0f}원", None, buy_fn,
        lambda: kd.get_daily_closes(ticker),
        market=MARKET_DOMESTIC, submit_buy=submit_buy, submit_sell=submit_sell,
        status_fn=status_fn, cancel_fn=cancel_fn,
        # 청산 직전 실제 보유 수량 재확인 (요구사항 5). 잔고 API 를 다시 부른다.
        refresh_balance=lambda: kd.get_domestic_balance(ticker),
        refresh_peak=lambda: _refresh_peak(conn, ticker, MARKET_DOMESTIC, current_price),
    )
