"""
모의투자 수동 주문 확인 도구 (T09c).

왜 있는가: 새 TR_ID 와 지정가(00) 주문을 모의 서버가 받는지는 실전 전환 전에 반드시 확인해야 한다(docs/manual_checks.md 5절).
그런데 봇은 매수 신호가 나야만 주문을 내므로 신호를 기다리면 확인이 오래 걸린다. 이 도구는 신호와 상관없이
**사람이 직접** 1주짜리 모의 주문을 낸다. 봇을 대신하지 않는다.

지키는 것
  - 모의투자(`config.IS_MOCK`)에서만 동작한다. 아니면 DB·API 에 닿기 전에 종료한다.
  - 새 주문 경로를 만들지 않는다. 전송은 `trader._submit_order` 한 곳으로만 하고(실전 가드 → 킬스위치 →
    중복 확인 → SUBMITTED 선기록 → 전송), 이 모듈은 전송 함수를 그 인자로만 넘긴다.
  - 기본은 미리보기다. 보내려면 `--send` 를 붙이고 화면에 보인 확인 문장을 직접 입력해야 한다.
  - 종목은 `config.DOMESTIC_TICKER`(국내), `config.MANUAL_ORDER_MAX_QTY`(1주) 이하만. 주문은 지정가이고, 시장가는 `sell --market` 에서만 쓴다.
  - 재시도하지 않는다. 결과가 불확실하면(UNKNOWN) 다시 보내지 말고 KIS 모의 앱에서 확인한다.
  - 요청 본문(계좌번호 포함)과 응답 원문은 출력하지 않는다.

사용: python -m manual_order buy [--send]|sell [--market] [--send]   /   python -m manual_order settle
종료 코드: 0 정상, 1 거부·실패(주문을 보내지 않음), 2 주문을 보냈으나 확정하지 못했거나 보냈는지 불확실
"""
import argparse
import logging
import math
import sys
import time
from urllib.parse import urlsplit

import config
import db
import kis_domestic as kd
import market_calendar
import risk
import trader
from errfmt import exc_summary

logger = logging.getLogger("bot")

MARKET = trader.MARKET_DOMESTIC
REASON = "MANUAL_CHECK"      # orders.reason 에 남겨 수동 주문임을 구분한다


def _clock() -> float:
    """확인 문장을 기다린 경과 시간을 재는 시계(시험에서 바꾼다). 날짜·시각이 아니라 경과 시간만 본다."""
    return time.monotonic()


def _is_tty() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _ask(prompt: str) -> str:
    return input(prompt)


def _as_float(value):
    """유한한 숫자면 float, 아니면 None (bool·NaN·무한대·변환 불가는 모두 None)."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _won(value) -> str:
    number = _as_float(value)
    return "알 수 없음" if number is None else f"{number:,.0f}원"


def _masked(value) -> str:
    return config.mask(value, keep=4) if value else "없음"


def confirm_phrase(side: str, symbol: str, qty: int, limit_price: str, *, market: bool = False) -> str:
    """사람이 그대로 입력해야 하는 확인 문장. 방향·종목·수량·주문 단가(시장가는 MARKET)가 모두 들어 있다."""
    if market:
        return f"MOCK {side} {symbol} {qty} MARKET"
    return f"MOCK {side} {symbol} {qty} @ {limit_price}"


def _gate_refusal(gate, detail, side):
    """킬스위치 상태가 이 방향의 주문을 막으면 사유, 아니면 None. 모르는 상태는 막는다."""
    if gate == risk.FULL_STOP:
        return f"전면 중단 중입니다 — {detail}"
    if gate == risk.ENTRIES_BLOCKED:
        if side == "SELL":
            return None          # RISK 중단은 신규 매수만 막는다(청산은 계속 — `_submit_order` 의 게이트와 같다)
        return f"신규 매수 중단 중입니다 — {detail}"
    if gate == risk.OPEN:
        return None
    return "중단 상태를 알 수 없어 주문하지 않습니다"


def refusal_reasons(*, side, qty, is_mock, db_mode_error, in_order_window, gate, gate_detail,
                    price, position, broker_qty, cash_krw, open_orders, market=False) -> list:
    """
    주문을 보내면 안 되는 이유 목록(순수 함수). 빈 목록이어야 진행한다.
    모든 입력은 호출부가 읽어 온 값이다 — 모르는 값(None·NaN)은 거부 사유다(모르면 주문하지 않는다).
    """
    reasons = []
    if is_mock is not True:
        reasons.append("모의투자 모드가 아닙니다 — 이 도구는 모의투자에서만 동작합니다")
    if db_mode_error:
        reasons.append(f"DB 모드 확인이 거부됐습니다: {db_mode_error}")
    if side not in ("BUY", "SELL"):
        reasons.append("방향은 BUY 또는 SELL 이어야 합니다")
    if market and side != "SELL":
        reasons.append("시장가는 매도(sell --market)에서만 쓸 수 있습니다")
    cap = config.MANUAL_ORDER_MAX_QTY
    q = qty if (isinstance(qty, int) and not isinstance(qty, bool)) else None
    if q is None or not 1 <= q <= cap:
        reasons.append(f"수량은 1 이상 {cap} 이하의 정수여야 합니다")
    if not in_order_window:
        reasons.append("지금은 주문 구간이 아닙니다(장 시간 밖이거나 마감 직전)")
    gate_text = _gate_refusal(gate, gate_detail, side)
    if gate_text:
        reasons.append(gate_text)
    p = _as_float(price)
    if p is None or p <= 0:
        reasons.append("현재가를 얻지 못했거나 올바르지 않습니다")
    if open_orders:
        reasons.append(f"미확정 주문이 {len(open_orders)}건 남아 있습니다 — 먼저 settle 로 확정하거나 KIS 앱에서 정리하세요")

    status = position.get("status") if isinstance(position, dict) else None
    pos_qty = _as_float(position.get("qty")) if isinstance(position, dict) else None
    held = _as_float(broker_qty)
    cash = _as_float(cash_krw)
    if side == "BUY":
        if status == "EXTERNAL":
            reasons.append("외부 보유(EXTERNAL) 종목입니다 — 체결되면 외부 보유분이 봇 소유로 바뀝니다")
        if status == "OPEN" and (pos_qty is None or pos_qty > 0):
            reasons.append("봇이 관리하는 보유(OPEN)가 이미 있습니다 — 이 도구는 보유가 없을 때만 매수합니다")
        if held is None or held != 0:
            reasons.append("계좌에 이미 보유가 있거나 잔고를 확인하지 못했습니다")
        if cash is None:
            reasons.append("예수금을 확인하지 못했습니다")
        elif p is not None and p > 0 and q is not None and cash < p * q:
            reasons.append("예수금이 부족합니다")
    elif side == "SELL":
        if status != "OPEN":
            reasons.append("봇이 관리하는 보유(OPEN)가 없습니다 — 봇이 산 포지션만 팔 수 있습니다")
        elif pos_qty is None or q is None or pos_qty < q:
            reasons.append("DB 포지션 수량이 부족합니다")
        if held is None or q is None or held < q:
            reasons.append("계좌 보유 수량이 부족하거나 잔고를 확인하지 못했습니다")
    return reasons


def body_error(body, symbol: str, qty: int, *, market: bool = False):
    """전송할 주문 본문이 의도한 것(해당 종목, 수량, 지정가와 양의 단가. market=True 면 시장가와 단가 0)인지 확인한다. 문제가 있으면 사유, 없으면 None."""
    if not isinstance(body, dict):
        return "주문 본문이 올바르지 않습니다"
    if body.get("PDNO") != symbol:
        return "종목 코드가 다릅니다"
    if body.get("ORD_QTY") != str(qty):
        return "수량이 다릅니다"
    if market:
        if body.get("ORD_DVSN") != kd._DVSN_MARKET:
            return "시장가 주문구분이 아닙니다"
        if str(body.get("ORD_UNPR", "")) != "0":
            return "시장가의 주문 단가는 0 이어야 합니다"
        return None
    if body.get("ORD_DVSN") != kd._DVSN_LIMIT:
        return "지정가 주문구분이 아닙니다"
    unpr = str(body.get("ORD_UNPR", ""))
    if not unpr.isdigit() or int(unpr) <= 0:
        return "주문 단가가 올바르지 않습니다(지정가는 양의 정수여야 합니다)"
    return None


def _late_refusal(conn, side):
    """확인 문장을 입력한 뒤, 전송 직전에 장 시간과 킬스위치를 다시 확인한다. 막혀 있으면 사유, 아니면 None."""
    try:
        in_window = market_calendar.is_order_window(market_calendar.MARKET_KRX, db.now_kst())
        gate, detail = risk.gate_status(conn)
    except Exception as e:
        return f"전송 직전 확인에 실패했습니다({exc_summary(e)})"
    if not in_window:
        return "지금은 주문 구간이 아닙니다(확인하는 사이에 장이 닫혔을 수 있습니다)"
    return _gate_refusal(gate, detail, side)


def _print_refusal(reasons) -> None:
    print("거부: 아래 이유로 주문을 보내지 않았습니다.")
    for reason in reasons:
        print(f"  - {reason}")


def _print_preview(*, side, symbol, qty, price, limit_price, ord_dvsn, tr_id, in_window, gate,
                   open_count, cash, broker_qty, position, market=False) -> None:
    host = urlsplit(config.KIS_BASE_URL).hostname or "(unknown)"
    label = "매수(BUY)" if side == "BUY" else "매도(SELL)"
    print("=== 모의투자 수동 주문 미리보기 ===")
    print(f"모드: 모의 | 서버: {host}")
    print(f"방향: {label} | 종목: {symbol} | 수량: {qty}주")
    if market:
        print(f"현재가: {_won(price)} → 시장가 주문 (가격 지정 없음, ORD_DVSN={ord_dvsn}, ORD_UNPR={limit_price})")
        print("주의: 체결가는 현재가와 다를 수 있습니다. 서버가 체결 평균가를 돌려주는지가 이 시험의 확인 대상입니다.")
    else:
        print(f"현재가: {_won(price)} → 주문 단가: {int(limit_price):,}원 (지정가, ORD_DVSN={ord_dvsn})")
    print(f"TR_ID: {tr_id}")
    print(f"주문 구간: {'가능' if in_window else '아님'} | 중단 상태: {gate} | 미확정 주문: {open_count}건")
    pos_text = "없음" if position is None else f"{position.get('status')} {position.get('qty')}주"
    print(f"예수금: {_won(cash)} | 계좌 보유: {broker_qty}주 | DB 포지션: {pos_text}")


def _report_state(conn, symbol, order_id=None):
    """DB 포지션과 미확정 주문을 출력한다. 방금 낸 주문(order_id)이 아직 미확정이면 그 행을 돌려준다."""
    position = db.get_position(conn, symbol, MARKET)
    if position is None:
        print("DB 포지션: 없음")
    else:
        print(f"DB 포지션: {position.get('status')} | {position.get('qty')}주 | 평단 {position.get('avg_price')}")
    open_orders = db.list_open_orders(conn, symbol=symbol, market=MARKET)
    print(f"미확정 주문: {len(open_orders)}건")
    mine = None
    for order in open_orders:
        print(f"  #{order.get('id')} {order.get('side')} {order.get('qty')}주 @ {order.get('price')} | "
              f"상태 {order.get('status')} | 체결 {order.get('filled_qty')}주 | "
              f"주문번호 {_masked(order.get('broker_order_no'))}")
        if order_id is not None and order.get("id") == order_id:
            mine = order
    return mine


def _settle(conn, symbol) -> None:
    """미확정 주문을 한 번 조회해 체결된 만큼 DB 에 반영한다 — 봇 사이클과 같은 함수를 쓴다. 주문은 내지 않는다."""
    def status_fn(order_no, *, ticker=None, org_no=None):
        return kd.get_order_status(order_no, ticker=ticker, org_no=org_no)

    trader._resolve_open_orders(conn, market=MARKET, ticker=symbol, status_fn=status_fn, cancel_fn=None)


def _print_followup(market: bool = False) -> None:
    print("--- 다음 확인 (docs/manual_checks.md 5절) ---")
    if market:
        print("KIS 모의 앱의 주문 내역에서 확인하세요: 접수됨 / 시장가로 보임 / 수량이 위와 같음 / 체결가가 표시됨 / 거절 응답 없음")
    else:
        print("KIS 모의 앱의 주문 내역에서 확인하세요: 접수됨 / 지정가로 보임 / 가격·수량이 위와 같음 / 거절 응답 없음")
    print("체결되지 않았다면 앱에서 직접 취소하세요(이 봇에는 국내 취소가 구현돼 있지 않습니다).")
    print("취소하거나 체결된 뒤에는 python -m manual_order settle 로 DB 를 맞추고, 봇(main.py)은 그 뒤에 켜세요.")


def _after_send(conn, symbol, order_id, market: bool = False) -> int:
    print(f"주문 기록 #{order_id}: 전송했고 서버가 접수 응답을 줬습니다(체결 여부는 별개입니다).")
    try:
        _settle(conn, symbol)
    except Exception as e:
        print(f"체결 조회·반영을 끝내지 못했습니다({exc_summary(e)}) — 나중에 python -m manual_order settle 로 다시 하세요")
    try:
        mine = _report_state(conn, symbol, order_id)
    except Exception as e:
        print(f"주문은 전송됐지만 상태를 읽지 못했습니다({exc_summary(e)}) — KIS 모의 앱에서 확인하세요. 다시 보내지 마세요.")
        return 2
    _print_followup(market=market)
    if mine is not None and (mine.get("status") == "UNKNOWN" or not mine.get("broker_order_no")):
        print("주의: 주문번호가 없거나 상태가 UNKNOWN 입니다 — KIS 모의 앱에서 직접 확인하세요. 다시 보내지 마세요.")
        return 2
    return 0


def _run(args, conn) -> int:
    symbol = config.DOMESTIC_TICKER
    mode_error = db.verify_bot_mode(conn, config.IS_MOCK)
    if args.command == "settle":
        if mode_error:
            print(f"거부: DB 모드 확인이 거부됐습니다: {mode_error}")
            return 1
        _settle(conn, symbol)
        _report_state(conn, symbol)
        return 0

    side = "BUY" if args.command == "buy" else "SELL"
    market = bool(getattr(args, "market", False))
    qty = config.MANUAL_ORDER_MAX_QTY
    in_window = market_calendar.is_order_window(market_calendar.MARKET_KRX, db.now_kst())
    gate, gate_detail = risk.gate_status(conn)
    price = kd.get_domestic_price(symbol)
    fetched_at = _clock()
    balance = kd.get_domestic_balance(symbol)
    position = db.get_position(conn, symbol, MARKET)
    open_orders = db.list_open_orders(conn, symbol=symbol, market=MARKET)

    reasons = refusal_reasons(
        side=side, qty=qty, is_mock=config.IS_MOCK, db_mode_error=mode_error, in_order_window=in_window,
        gate=gate, gate_detail=gate_detail, price=price, position=position,
        broker_qty=balance.get("quantity"), cash_krw=balance.get("cash_krw"), open_orders=open_orders, market=market,
    )
    if reasons:
        _print_refusal(reasons)
        return 1

    # 주문 본문은 한 번만 만든다 — 화면에 보인 것이 그대로 전송된다. 전역 ORDER_TYPE 은 따르지 않는다. --market 이 없으면 항상 지정가다.
    order_type = "market" if market else "limit"
    body = kd.order_body(symbol, qty, float(price), side=side, order_type=order_type)
    problem = body_error(body, symbol, qty, market=market)
    if problem:
        print(f"거부: 주문 본문 검증 실패 — {problem}. 아무것도 보내지 않았습니다.")
        return 1
    limit_price = body["ORD_UNPR"]
    tr_id = kd._tr(kd._TR_BUY if side == "BUY" else kd._TR_SELL)
    _print_preview(side=side, symbol=symbol, qty=qty, price=price, limit_price=limit_price,
                   ord_dvsn=body["ORD_DVSN"], tr_id=tr_id, in_window=in_window, gate=gate,
                   open_count=len(open_orders), cash=balance.get("cash_krw"),
                   broker_qty=balance.get("quantity"), position=position, market=market)

    if not args.send:
        print("미리보기입니다. 아무것도 보내지 않았습니다.")
        flag = " --market" if market else ""
        print(f"보내려면: python -m manual_order {args.command}{flag} --send")
        return 0

    if not _is_tty():
        print("거부: 터미널에서 직접 실행해야 합니다(입력이 터미널에 연결돼 있지 않습니다). 아무것도 보내지 않았습니다.")
        return 1
    phrase = confirm_phrase(side, symbol, qty, limit_price, market=market)
    timeout = config.MANUAL_ORDER_CONFIRM_TIMEOUT_SEC
    print("봇(main.py)이 실행 중이면 먼저 끄세요. 이 주문은 재시도되지 않습니다.")
    print(f"보내려면 {timeout}초 안에 아래 문장을 그대로 입력하세요(Ctrl+C 로 취소):")
    print(f"  {phrase}")
    try:
        typed = _ask("> ")
    except (KeyboardInterrupt, EOFError):
        print("취소했습니다. 아무것도 보내지 않았습니다.")
        return 1
    if _clock() - fetched_at > timeout:
        print("거부: 시세를 가져온 뒤 시간이 너무 지났습니다. 다시 실행하세요. 아무것도 보내지 않았습니다.")
        return 1
    if typed.strip() != phrase:
        print("거부: 입력한 문장이 일치하지 않습니다. 아무것도 보내지 않았습니다.")
        return 1
    late = _late_refusal(conn, side)
    if late:
        print(f"거부: {late}. 아무것도 보내지 않았습니다.")
        return 1

    price_text = "시장가" if market else f"@ {limit_price}"
    logger.info(f"[수동주문] 전송 요청 {side} {symbol} {qty}주 {price_text} (확인 문장 입력됨)")
    try:
        order_id = trader._submit_order(
            conn, market=MARKET, ticker=symbol, side=side, qty=qty, price=float(limit_price),
            order_type="MARKET" if market else "LIMIT", reason=REASON,
            make_body=lambda: body,
            send=lambda b, on_uncertain: kd.send_order(b, side=side, on_uncertain=on_uncertain),
        )
    except risk.TradingHalted:
        print("거부: 킬스위치가 주문을 막았습니다. python -m risk status 로 확인하세요. 아무것도 보내지 않았습니다.")
        return 1
    except trader.DuplicateOrderBlocked:
        print("거부: 같은 종목·방향의 미확정 주문이 있어 보내지 않았습니다.")
        return 1
    except Exception as e:
        print(f"실패: 주문 전송 중 오류가 났습니다({exc_summary(e)}). 서버가 받았는지 알 수 없습니다 — "
              "재전송하지 말고 KIS 모의 앱과 python -m manual_order settle 로 확인하세요.")
        return 2
    return _after_send(conn, symbol, order_id, market=market)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m manual_order",
        description="모의투자 수동 주문 확인 도구 (기본은 미리보기이고 아무것도 보내지 않는다)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("buy", "1주 지정가 매수 시험 주문"), ("sell", "봇이 산 포지션 1주 지정가 매도 시험 주문")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("--send", action="store_true", help="미리보기 뒤 확인 문장을 입력하면 실제로 전송한다")
        sp.add_argument("--market", action="store_true",
                        help="시장가로 낸다(sell 에서만 허용, 가격 지정 없음). 없으면 항상 지정가")
    sub.add_parser("settle", help="미확정 주문을 한 번 조회해 체결을 DB 에 반영한다(주문은 내지 않는다)")
    return parser


def main(argv=None, conn=None) -> int:
    args = _build_parser().parse_args(argv)
    if getattr(args, "market", False) and args.command != "sell":
        print("거부: 시장가(--market)는 sell 에서만 쓸 수 있습니다. 아무것도 보내지 않았습니다.")
        return 1
    if config.IS_MOCK is not True:
        print("거부: 모의투자 모드가 아닙니다 — 이 도구는 모의투자에서만 동작합니다. DB·API 에 접속하지 않았습니다.")
        return 1
    own = conn is None
    try:
        if own:
            conn = db.open_db()
        return _run(args, conn)
    except Exception as e:
        print(f"실패: 주문을 보내기 전에 오류가 났습니다({exc_summary(e)}). 아무것도 보내지 않았습니다.")
        return 1
    finally:
        if own and conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
