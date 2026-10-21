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

매도 조건 (우선순위 순):
  1. 손절: 수익률 <= STOP_LOSS_THRESHOLD            →  전량 매도
  2. 부분 익절: 아래 둘 중 하나라도 해당하면 다음 단계 실행 (수익 중일 때만)
     - 수익률이 TAKE_PROFIT_STEPS 단계 도달
     - 지표 과매수: RSI >= RSI_OVERBOUGHT(70) 이고 종가 >= 볼린저 상단
     단계는 당일 1회씩만 실행하고, 수익률이 떨어져도 재실행하지 않는다.
"""
import json
import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import db
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


def _order_refs_from_response(result: dict | None) -> dict | None:
    """
    주문 응답에서 **체결 조회·취소에 필요한 주문 식별자**를 꺼낸다.

    반환: {"order_no": str, "org_no": str|None, "order_time": str|None} 또는 None.

    **미구현(확인 필요) — 절대 규칙 4.** KIS 주문 응답의 주문번호 필드명이
    공식 문서·공식 샘플에서 확인되지 않았다. 추측한 키를 읽으면 "주문번호 없음"과
    "필드명이 틀림"을 구분할 수 없게 되므로 지금은 채우지 않는다
    (`orders.broker_order_no` 는 NULL 로 남는다).

    # VERIFY: 공식 문서로 주문번호 필드를 확인한 뒤 이 함수만 채우면
    #         체결 조회·미체결 취소가 동작한다(나머지 배선은 이미 준비돼 있다).
    #         그 전까지 주문은 SUBMITTED 로 남고, reconcile 이 "미확정 주문"으로
    #         유예하다가 ORDER_TIMEOUT_MINUTES 초과 시 매매를 중단시킨다.
    """
    if not result:
        return None
    return None


def _stored_order_refs(order: dict) -> dict | None:
    """DB 에 저장된 주문 raw_response 에서 주문 식별자를 다시 꺼낸다."""
    raw = order.get("raw_response")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as e:
        logger.error(f"[주문] raw_response 해석 실패 (#{order.get('id')}): {e}")
        return None
    if str(payload.get("rt_cd")) != "0":
        return None    # 거부된 주문은 조회할 주문번호가 없다
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
                          raw_request: dict | None) -> tuple[int, str]:
    """
    **주문을 보내기 전에** `SUBMITTED` 행을 먼저 기록한다 (write-ahead, T04).

    순서가 핵심이다: 주문을 보낸 뒤 기록하면, 기록 직전에 프로세스가 죽었을 때
    "브로커에는 주문이 있는데 DB 에는 없는" 상태가 된다. 먼저 기록하면 최악의 경우
    "DB 에는 주문이 있는데 브로커에는 없는" 상태가 되고, 이것은 reconcile 이 잡아낸다.

    반환: (order_id, client_ref)
    """
    client_ref = _client_ref(market, side, ticker)
    order_id = db.record_order(
        conn, symbol=ticker, market=market, side=side, qty=qty, price=price,
        order_type=order_type, status="SUBMITTED",
        client_ref=client_ref, broker_order_no=None,
        raw_request=raw_request,
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
                  price: float | None, order_type: str, make_body, send) -> int:
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
        order_type=order_type, raw_request=body,
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
        db.update_order_status(conn, order_id, status="SUBMITTED",
                               broker_order_no=refs["order_no"], raw_response=result)
    else:
        db.update_order_status(conn, order_id, status="SUBMITTED", raw_response=result)
        # TODO(T05): 주문번호 필드가 확인되면 이 경로는 사라진다.
        logger.warning(
            f"⚠️ 주문 #{order_id} 주문번호를 얻지 못했습니다(필드명 미확인) — "
            f"체결 조회·미체결 취소가 불가합니다. {market} {ticker} {side} {qty:g}주"
        )
    return order_id


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
        st = status_fn(refs["order_no"])
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
    """`ORDER_TIMEOUT_MINUTES` 를 넘긴 미체결 주문을 취소한다 (T04)."""
    if cancel_fn is None:
        logger.error(
            f"⛔ 주문 #{order['id']} 이 {config.ORDER_TIMEOUT_MINUTES:g}분 넘게 미체결인데 "
            f"취소 기능이 없습니다. 직접 취소하세요: {order['market']} {order['symbol']} "
            f"{order['side']} {order.get('qty'):g}주"
        )
        return

    remaining = float(order.get("qty") or 0.0) - float(st.filled_qty or 0.0)
    if remaining <= 0:
        return
    try:
        result = cancel_fn(remaining)
    except NotImplementedError as e:
        logger.error(f"[취소] {order['market']} {order['symbol']} 미구현 — 주문 #{order['id']}: {e}")
        return
    except Exception as e:
        logger.error(f"[취소] 실패 — 주문 #{order['id']} 을 그대로 둡니다: {e}")
        return

    db.update_order_status(conn, order["id"], status="CANCELED",
                           filled_qty=st.filled_qty, raw_response=result)
    logger.warning(
        f"🚫 주문 #{order['id']} 미체결 취소 — {order['market']} {order['symbol']} "
        f"{remaining:g}주 (체결 {st.filled_qty:g}주는 유지)"
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


def _check_sell(prefix: str, ticker: str, qty: float, profit_rate: float, sell_fn,
                signal: dict | None = None, market: str | None = None,
                avg_price: float | None = None, submit_sell=None) -> bool:
    """
    손절·부분 익절 판단 및 실행. 매도했으면 True.

    Args:
        sell_fn: (수량) -> 주문 실행 콜러블
        signal: 지표 신호 (indicators.evaluate 결과). None이면 수익률만으로 판단.
        market: 포지션 시장 구분(DB 기록용). None이면 DB 기록을 건너뛴다(하위 호환).
        avg_price: 평균 단가(DB 기록용). None이면 기존 값을 유지한다.
        submit_sell: (수량) -> order_id. 주문을 write-ahead 로 내는 콜러블 (T04).
                None 이면 `sell_fn` 을 그대로 부른다(DB 기록 없음 — 하위 호환 경로).
    """
    if qty <= 0:
        logger.info(f"⏩ {prefix} 매도 조건 미충족 (보유 수량 없음)")
        return False

    # ── 0. 외부 보유는 매도하지 않는다 (T03) ──────────────
    # 매도 수량은 **브로커 잔고**에서 오므로, DB 상태를 보지 않으면 봇이 만들지 않은
    # 보유까지 손절·익절 규칙으로 팔아버린다. `EXTERNAL` 은 "봇이 만든 기록이 없는 보유"이므로
    # 봇의 관리 대상이 아니다. 사람이 직접 정리해야 한다.
    if market:
        try:
            pos = db.get_position(_state_conn(), ticker, market)
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

    # ── 1. 손절 ──────────────────────────────────────────
    if config.STOP_LOSS_THRESHOLD < 0 and profit_rate <= config.STOP_LOSS_THRESHOLD:
        logger.info(
            f"🛑 {prefix} 손절 조건 충족 ({profit_rate:.2f}% ≤ {config.STOP_LOSS_THRESHOLD}%) "
            f"— {qty:.0f}주 전량 매도"
        )
        try:
            _place_sell(prefix, market, ticker, qty, sell_fn, submit_sell)
            bot_state.set_bought_today(ticker, False)
            bot_state.set_profit_stage(ticker, 0)
        except Exception as e:
            logger.error(f"{prefix} 손절 주문 실패: {e}")
            return True
        return True

    # ── 2. 부분 익절 ─────────────────────────────────────
    # 도달한 단계를 순서대로 모두 실행한다. (급등으로 여러 단계를 한 번에
    # 넘어선 경우에도 다음 사이클을 기다리지 않고 즉시 분할 매도)
    stage = bot_state.profit_stage(ticker)
    remaining = qty
    sold_any = False

    # 미충족 사유 로깅용 (첫 번째 미도달 단계)
    block_pct: float | None = None

    # 지표 과매수 신호: 수익 중일 때만 익절 트리거로 인정
    indicator_sell = bool(signal and signal.get("sell_signal") and profit_rate > 0)
    if signal and signal.get("sell_signal") and profit_rate <= 0:
        logger.info(f"ℹ️ {prefix} 지표 과매수 신호지만 수익 중이 아니어서 익절 보류 (수익률 {profit_rate:.2f}%)")

    while stage < len(config.TAKE_PROFIT_STEPS):
        target_pct, ratio = config.TAKE_PROFIT_STEPS[stage]
        # 수익률 도달이면 정상 진행 (여러 단계를 한 번에 넘어설 수 있음)
        by_profit = profit_rate >= target_pct
        # 수익률 미달이면 지표 과매수일 때만, 그리고 이번 단계 하나만 실행한다
        by_indicator = (not by_profit) and indicator_sell

        if not (by_profit or by_indicator):
            block_pct = target_pct
            break
        # 지표 신호로 진입한 경우에도 손실 중이면 매도하지 않음
        if profit_rate <= 0:
            block_pct = target_pct
            break

        # 마지막 단계(비중 100%)는 남은 수량 전량
        if ratio >= 100:
            sell_qty = remaining
        else:
            sell_qty = max(1, min(round(remaining * ratio / 100), int(remaining)))

        trigger = (
            f"수익률 {profit_rate:.2f}% ≥ {target_pct}%"
            if profit_rate >= target_pct
            else f"지표 과매수 (RSI {signal.get('rsi', 0):.1f} ≥ {config.RSI_OVERBOUGHT:g})"
        )
        logger.info(
            f"💰 {prefix} 부분 익절 {stage + 1}단계 ({trigger}) "
            f"— {sell_qty:.0f}주 매도 (보유 {remaining:.0f}주)"
        )
        try:
            _place_sell(prefix, market, ticker, sell_qty, sell_fn, submit_sell)
        except Exception as e:
            logger.error(f"{prefix} 부분 익절 주문 실패: {e}")
            break

        remaining -= sell_qty
        stage += 1
        sold_any = True
        bot_state.set_profit_stage(ticker, stage)

        if remaining <= 0:
            break
        # 지표 과매수만으로 진입한 경우엔 한 단계만 매도하고 종료
        # (급등으로 수익률이 여러 단계를 넘어선 경우에는 계속 진행)
        if by_indicator and not by_profit:
            break

    # 전량 매도한 경우에만 매수 플래그 초기화 (남은 수량이 있으면 유지)
    if sold_any and remaining <= 0:
        bot_state.set_bought_today(ticker, False)
        bot_state.set_profit_stage(ticker, 0)

    if not sold_any:
        if block_pct is not None:
            summary = "/".join(f"+{p:g}%" for p, _ in config.TAKE_PROFIT_STEPS)
            logger.info(
                f"⏩ {prefix} 매도 조건 미충족 (수익률: {profit_rate:.2f}%, "
                f"다음 목표: +{block_pct:g}%, 전체 단계: {summary})"
            )
        else:
            logger.info(
                f"⏩ {prefix} 매도 조건 미충족 (수익률: {profit_rate:.2f}%, "
                f"금일 익절 단계 모두 소진 {stage}/{len(config.TAKE_PROFIT_STEPS)})"
            )
    return sold_any


def _run_cycle(prefix: str, ticker: str, balance: dict, current_price: float,
               ai_score: int, ratio: float, price_fmt: str, sell_fn, buy_fn,
               fetch_closes=None, market: str | None = None,
               submit_buy=None, submit_sell=None, status_fn=None, cancel_fn=None):
    """
    해외·국내 공통 매매 사이클 로직.

    Args:
        ratio: 총자산 대비 1회 매수 비중
        price_fmt: 로그용 가격 포맷 함수
        sell_fn / buy_fn: (수량) / (수량, 가격) 주문 실행 콜러블.
                T04 부터 실제 주문은 `submit_*` 가 내므로, `buy_fn` 은 예산으로
                1주라도 살 수 있는지 판단하는 데만 쓰인다(`_BudgetProbe`).
        market: 시장 구분(DB 기록용). None이면 DB 기록을 건너뛰고 예전 경로를 쓴다.
        submit_buy / submit_sell: (수량) -> order_id. write-ahead 기록 + 전송 (T04)
        status_fn / cancel_fn: 체결 조회 / 미체결 취소 (T04)
    """
    qty         = balance["quantity"]
    avg_price   = balance["avg_price"]
    profit_rate = balance["profit_rate"]
    total_krw   = balance["total_asset_krw"]
    cash_krw    = balance["cash_krw"]

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
            # 데이터를 모를 때는 사지 않는다. 매도 판단은 수익률 기준으로 계속한다.
            logger.error(f"⛔ [{prefix}] 지표 데이터 없음 — 이번 사이클 매수를 보류합니다: {e}")
            _check_sell(prefix, ticker, qty, profit_rate, sell_fn, None,
                        market=market, avg_price=avg_price,
                        submit_sell=submit_sell)
            return

    # ── 매도 (손절 > 부분 익절) ──────────────────────────
    if _check_sell(prefix, ticker, qty, profit_rate, sell_fn, signal,
                   market=market, avg_price=avg_price, submit_sell=submit_sell):
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


def run_overseas_cycle(ai_score: int):
    """해외주식(QQQ) 매매 사이클 (T04: 지정가·체결 확인 기반)."""
    ticker = config.TARGET_TICKER
    balance = kis.get_balance()
    current_price = kis.get_current_price(ticker)
    rate = kis.get_usd_krw_rate()
    conn = _state_conn()

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

    def submit_sell(qty: float) -> int:
        order_price = _order_price_for(kis, current_price, side="SELL")
        return _submit_order(
            conn, market=MARKET_OVERSEAS, ticker=ticker, side="SELL", qty=qty,
            price=order_price, order_type="LIMIT",
            make_body=lambda: kis.order_body(qty, order_price),
            send=lambda body, on_uncertain: kis.send_order(body, side="SELL",
                                                           on_uncertain=on_uncertain),
        )

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
        status_fn=kis.get_order_status, cancel_fn=cancel_fn,
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

    def submit_sell(qty: float) -> int:
        body = kd.order_body(ticker, int(qty), current_price, side="SELL")
        return _submit_order(
            conn, market=MARKET_DOMESTIC, ticker=ticker, side="SELL", qty=qty,
            price=float(body["ORD_UNPR"]), order_type=_order_type_label(),
            make_body=lambda: body,
            send=lambda b, on_uncertain: kd.send_order(b, side="SELL",
                                                       on_uncertain=on_uncertain),
        )

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
        status_fn=kd.get_order_status, cancel_fn=cancel_fn,
    )
