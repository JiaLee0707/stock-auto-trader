"""
브로커 잔고 ↔ DB 포지션 대조 (T03).

목적: 봇이 아는 상태와 브로커의 실제 보유가 어긋나면 **신규 매매를 멈춘다.**
      상태를 모르는 채 주문하면 중복 매수·유령 포지션·미관리 보유가 생긴다.

비교 대상은 두 시장(overseas / domestic)의 **모든 보유**다.
한 종목만 보면 다른 종목이 생겼을 때 눈치채지 못한다.

불일치 처리
- 기본(`RECONCILE_AUTOFIX=false`): **보정하지 않고** 중단 플래그를 세우고 ERROR 로그만 남긴다.
- `RECONCILE_AUTOFIX=true`: 브로커 값을 DB 에 덮어써 보정한다. 그래도 로그는 남긴다.
- **외부 보유**(브로커에만 있고 봇이 산 기록이 없는 것)는 `EXTERNAL` 로 표시한다.
  봇은 자기가 만든 포지션만 관리하므로 외부 보유는 **매도하지 않는다.**
  단 자동 보정이 꺼져 있으면(기본) 이것도 중단 사유다 — 사람이 확인해야 한다.

알림(T09)은 아직 없다. 중단 지점에 TODO 를 남겼다.
"""
import logging
from datetime import datetime

import config
import db
import notifier

logger = logging.getLogger("bot")

# 수량 비교 허용 오차. 대조·유예 판정에서 같은 기준을 쓴다.
_QTY_EPS = 1e-6


def _index_by_key(entries: list[dict]) -> dict:
    """(symbol, market) 을 키로 하는 dict 로 바꾼다. 중복 키는 마지막 값으로 경고."""
    out: dict = {}
    for e in entries:
        if not e.get("symbol"):
            logger.warning(f"[reconcile] symbol 없는 항목 무시: {e!r}")
            continue
        key = db.position_key(e["symbol"], e["market"])
        if key in out:
            logger.warning(f"[reconcile] 중복 키 {key} — 마지막 값으로 덮어씁니다")
        out[key] = e
    return out


def compare_positions(db_positions: list[dict], broker_holdings: list[dict]) -> dict:
    """
    DB 포지션과 브로커 보유를 비교한다(순수 함수 — 네트워크·DB 접근 없음).

    반환: {
        "matched":        [(key, db_pos, broker_pos), ...],   # 수량 일치
        "qty_mismatch":   [(key, db_pos, broker_pos), ...],   # 수량 다름
        "db_only":        [(key, db_pos), ...],               # 브로커에 없음 (봇은 있다고 앎)
        "broker_only":    [(key, broker_pos), ...],           # 외부 보유
        "is_consistent":  bool,                               # 전부 일치 여부
    }

    수량 비교는 부동소수 오차를 피하려고 `_QTY_EPS` 로 반올림해 비교한다.
    """
    db_idx = _index_by_key(db_positions)
    broker_idx = _index_by_key(broker_holdings)

    matched, qty_mismatch, db_only, broker_only = [], [], [], []

    for key, db_pos in db_idx.items():
        broker_pos = broker_idx.get(key)
        if broker_pos is None:
            db_only.append((key, db_pos))
            continue
        if _qty_equal(db_pos.get("qty", 0), broker_pos.get("qty", 0)):
            matched.append((key, db_pos, broker_pos))
        else:
            qty_mismatch.append((key, db_pos, broker_pos))

    for key, broker_pos in broker_idx.items():
        if key not in db_idx:
            broker_only.append((key, broker_pos))

    is_consistent = not (qty_mismatch or db_only or broker_only)
    return {
        "matched": matched,
        "qty_mismatch": qty_mismatch,
        "db_only": db_only,
        "broker_only": broker_only,
        "is_consistent": is_consistent,
    }


def _qty_equal(a, b, eps: float = _QTY_EPS) -> bool:
    """수량 비교. 주식 수량은 정수지만 부동소수 변환 오차를 허용한다."""
    try:
        return abs(float(a) - float(b)) < eps
    except (TypeError, ValueError):
        return False


def fetch_broker_holdings() -> list[dict]:
    """
    브로커의 전체 보유를 모아 돌려준다. 운용이 꺼진 시장은 조회하지 않는다.

    조회 실패는 **삼키지 않고** 올린다. 대조를 못 했는데 "이상 없음"으로 넘어가면
    상태가 어긋난 채로 매매가 계속된다.
    """
    import kis_domestic as kd
    import kis_overseas as kis

    holdings: list[dict] = []
    if config.IS_OVERSEAS_ENABLED:
        holdings.extend(kis.get_all_holdings())
    else:
        logger.info("[reconcile] 해외 운용 비활성 — 해외 잔고 조회 생략")
    if config.IS_DOMESTIC_ENABLED:
        holdings.extend(kd.get_all_holdings())
    else:
        logger.info("[reconcile] 국내 운용 비활성 — 국내 잔고 조회 생략")
    return holdings


def _open_orders_by_key(conn) -> dict:
    """미확정 주문(`SUBMITTED`/`PARTIAL`/`UNKNOWN`)을 (symbol, market) 키로 묶는다."""
    out: dict = {}
    for o in db.list_open_orders(conn):
        out.setdefault(db.position_key(o["symbol"], o["market"]), []).append(o)
    return out


def _order_age_minutes(order: dict, now) -> float:
    """주문이 얼마나 오래 미확정 상태로 남았는지(분). 시각을 못 읽으면 아주 오래된 것으로 본다."""
    raw = order.get("created_at")
    if not raw:
        return float("inf")
    try:
        return (now - datetime.fromisoformat(raw)).total_seconds() / 60.0
    except (TypeError, ValueError):
        return float("inf")


def _pending_qty(orders: list[dict], side: str) -> float:
    """아직 확정되지 않은 주문의 수량 합계(방향별)."""
    return sum(float(o.get("qty") or 0.0) for o in orders if o.get("side") == side)


def _split_pending(result: dict, open_orders: dict, *, now,
                   max_age_minutes: float) -> tuple[dict, list, list]:
    """
    불일치를 "미확정 주문으로 설명되는 것"과 "설명되지 않는 것"으로 나눈다 (T04).

    T04 부터 포지션은 **체결 확인 후에만** 반영되므로, 주문 접수 직후에는
    "브로커에는 있고 DB 에는 없는" 상태가 정상적으로 존재한다. 이걸 그대로 불일치로
    보면 매수 직후 매 사이클마다 매매가 멈춘다. 그래서 **미확정 주문 수량만큼**은
    불일치로 세지 않고 WARNING 으로만 남긴다.

    단 그 유예에는 상한이 있다. 미확정 상태로 `max_age_minutes` 를 넘긴 주문은
    유예하지 않는다(체결 조회·취소가 동작하지 않는다는 뜻이므로 사람이 봐야 한다).

    반환: (설명되지 않은 불일치만 남긴 result, 유예 목록, 만료된 주문 목록)
    """
    now_orders: dict = {}
    stale: list[dict] = []
    for key, orders in open_orders.items():
        for o in orders:
            (stale if _order_age_minutes(o, now) > max_age_minutes else now_orders.setdefault(key, [])).append(o)

    pending: list[tuple] = []
    remaining = {"matched": result["matched"], "qty_mismatch": [], "db_only": [],
                 "broker_only": []}

    for key, db_pos, broker_pos in result["qty_mismatch"]:
        orders = now_orders.get(key, [])
        expected = (float(db_pos.get("qty", 0)) + _pending_qty(orders, "BUY")
                    - _pending_qty(orders, "SELL"))
        if _qty_equal(expected, broker_pos.get("qty", 0)):
            pending.append(("수량불일치", key, db_pos, broker_pos, orders))
        else:
            remaining["qty_mismatch"].append((key, db_pos, broker_pos))

    for key, db_pos in result["db_only"]:
        orders = now_orders.get(key, [])
        expected = float(db_pos.get("qty", 0)) - _pending_qty(orders, "SELL")
        if expected <= _QTY_EPS:
            pending.append(("DB만", key, db_pos, {"qty": 0}, orders))
        else:
            remaining["db_only"].append((key, db_pos))

    for key, broker_pos in result["broker_only"]:
        orders = now_orders.get(key, [])
        expected = _pending_qty(orders, "BUY")
        if _qty_equal(expected, broker_pos.get("qty", 0)):
            pending.append(("외부보유", key, {"qty": 0}, broker_pos, orders))
        else:
            remaining["broker_only"].append((key, broker_pos))

    remaining["is_consistent"] = not (
        remaining["qty_mismatch"] or remaining["db_only"] or remaining["broker_only"]
    )
    return remaining, pending, stale


def _report_pending(pending: list, stale: list) -> None:
    """유예 중인 불일치(미확정 주문)와 만료된 주문을 로그로 남긴다."""
    for kind, key, db_pos, broker_pos, orders in pending:
        ids = ", ".join(f"#{o['id']}({o['side']} {o.get('qty'):g} {o['status']})" for o in orders)
        logger.warning(
            f"[reconcile] {kind} {key} 유예 — 미확정 주문 {ids} 으로 설명됩니다 "
            f"(DB {db_pos.get('qty')} / 브로커 {broker_pos.get('qty')}). 체결 확정 시 해소됩니다."
        )
    for o in stale:
        logger.error(
            f"[reconcile] 주문 #{o['id']} 이 {o['status']} 상태로 "
            f"{config.ORDER_TIMEOUT_MINUTES:g}분을 넘겼습니다 — 체결 조회/취소가 동작하지 않습니다. "
            f"{o['symbol']} {o['side']} {o.get('qty'):g}주, 주문번호 {o.get('broker_order_no') or '없음'}"
        )


def _report(result: dict) -> None:
    """불일치 내역을 로그로 남긴다. 값은 마스킹 대상이 아니라 그대로 둔다."""
    for key, db_pos, broker_pos in result["qty_mismatch"]:
        logger.error(
            f"[reconcile] 수량 불일치 {key}: DB {db_pos.get('qty')} vs "
            f"브로커 {broker_pos.get('qty')}"
        )
    for key, db_pos in result["db_only"]:
        logger.error(
            f"[reconcile] DB 에만 있음(브로커에 없음) {key}: "
            f"DB 수량 {db_pos.get('qty')} — 체결 누락 또는 매도 미반영 의심"
        )
    for key, broker_pos in result["broker_only"]:
        logger.error(
            f"[reconcile] 외부 보유 {key}: 브로커 수량 {broker_pos.get('qty')} — "
            "봇이 만든 기록이 없어 매도하지 않습니다"
        )


def _apply_autofix(conn, result: dict) -> None:
    """
    브로커 값으로 DB 를 덮어쓴다(`RECONCILE_AUTOFIX=true` 일 때만 호출).

    외부 보유는 `EXTERNAL` 상태로 넣어 봇이 매도 대상으로 잡지 않게 한다.
    """
    for key, db_pos, broker_pos in result["qty_mismatch"]:
        logger.warning(f"[reconcile] 보정 {key}: DB 수량을 브로커 값으로 덮어씁니다")
        db.upsert_position(
            conn, key[0], key[1],
            qty=broker_pos["qty"], avg_price=broker_pos.get("avg_price", 0.0),
            entry_time=db_pos.get("entry_time"),
            entry_signal=db_pos.get("entry_signal"),
            peak_price_since_entry=db_pos.get("peak_price_since_entry"),
            status="OPEN",
        )

    for key, _broker_pos in result["db_only"]:
        logger.warning(f"[reconcile] 보정 {key}: 브로커에 없어 포지션을 CLOSED 처리")
        db.close_position(conn, key[0], key[1])

    for key, broker_pos in result["broker_only"]:
        logger.warning(f"[reconcile] 보정 {key}: 외부 보유를 EXTERNAL 로 기록(매도 대상 아님)")
        db.upsert_position(
            conn, key[0], key[1],
            qty=broker_pos["qty"], avg_price=broker_pos.get("avg_price", 0.0),
            entry_time=None, entry_signal=None, peak_price_since_entry=None,
            status="EXTERNAL",
        )


HALT_KINDS = ("STATE", "RISK")
# STATE: 상태를 믿을 수 없다(브로커↔DB 불일치 등) → 모든 주문 차단.
# RISK : 손실 한도 → 신규 매수만 차단하고 청산(손절)은 계속한다.


def _notify(level: str, title: str, message: str) -> None:
    """알림을 보낸다. 알림이 실패해도 중단 처리는 이미 끝나 있어야 하므로 예외를 올리지 않는다."""
    try:
        notifier.notify(level, title, message)
    except Exception as e:
        # str(e) 는 쓰지 않는다 — 예외 메시지에 URL(토큰)이 들어 있을 수 있다.
        logger.error(f"[알림] 알림 호출 실패 — {type(e).__name__}")


def halt_trading(conn, reason: str, kind: str = "STATE", *, actor: str = "bot",
                 details: dict | None = None) -> bool:
    """
    신규 매매 중단 플래그를 세운다. 매매 판단은 시작 전에 이 값을 봐야 한다.

    kind: "STATE"(기본, 기존 동작 — 모든 주문 차단) 또는 "RISK"(신규 매수만 차단).

    상태가 **바뀔 때만** 이력(halt_events)과 알림을 남긴다.
      - 중단 중이 아니었다 → 새 중단
      - RISK 중단 중에 STATE 발생 → STATE 로 격상 (더 강한 쪽이 이긴다)
    이미 같거나 더 강한 중단 중이면 아무것도 바꾸지 않는다(매 사이클 알림 반복 방지).

    순서: 상태 기록 → 로그 → 알림. 알림이 실패해도 상태 기록은 이미 끝나 있다.
    반환: 상태가 바뀌었으면 True.
    """
    if kind not in HALT_KINDS:
        raise ValueError(f"허용되지 않는 중단 종류: {kind!r} (허용: {HALT_KINDS})")
    reason = str(reason or "").strip() or "사유 미기록"

    current = halt_kind(conn)       # 중단 중이 아니면 None
    if current is not None and (current == "STATE" or kind == "RISK"):
        logger.warning(
            f"[중단] 이미 {current} 중단 중이라 상태를 바꾸지 않습니다 — 추가 사유: [{kind}] {reason}"
        )
        return False

    try:
        db.set_kv(conn, db.KV_HALT_KIND, kind)
        db.set_kv(conn, db.KV_HALT_REASON, reason)
        db.set_kv(conn, db.KV_HALT_AT, db.now_iso())
        db.set_kv(conn, db.KV_TRADING_HALTED, "1")
        db.record_halt_event(conn, "HALT", kind=kind, reason=reason, actor=actor, details=details)
    except Exception as e:
        logger.critical(
            f"🚨 중단 상태를 DB 에 기록하지 못했습니다 ({type(e).__name__}: {e}) — [{kind}] {reason}"
        )
        _notify("CRITICAL", "중단 기록 실패",
                f"[{kind}] {reason}\n중단 상태를 DB 에 기록하지 못했습니다({type(e).__name__}). 직접 확인이 필요합니다.")
        raise

    logger.critical(f"🚨 신규 매매 중단 플래그 설정 — [{kind}] {reason}")
    scope = "모든 주문(매수·매도) 차단" if kind == "STATE" else "신규 매수만 차단(청산은 계속)"
    title = f"신규 매매 중단 [{kind}]" if current is None else f"중단 격상 [{current}→{kind}]"
    _notify("CRITICAL", title,
            f"{reason}\n- {scope}\n- 확인: python -m risk status\n"
            '- 해제: python -m risk resume --reason "확인한 내용"')
    return True


def is_trading_halted(conn) -> bool:
    """중단 플래그가 서 있으면 True."""
    return db.get_kv(conn, db.KV_TRADING_HALTED, "0") == "1"


def halt_reason(conn) -> str | None:
    """중단 사유(없으면 None)."""
    return db.get_kv(conn, db.KV_HALT_REASON)


def halt_kind(conn) -> str | None:
    """
    중단 종류. 중단 중이 아니면 None.

    종류가 기록돼 있지 않거나 알 수 없는 값이면 "STATE" 로 본다 — T09a 이전에 세워진
    플래그나 손상된 값은 가장 엄격한 쪽(모든 주문 차단)으로 해석한다(실패 시 닫힘).
    """
    if not is_trading_halted(conn):
        return None
    kind = db.get_kv(conn, db.KV_HALT_KIND)
    return kind if kind in HALT_KINDS else "STATE"


def halt_since(conn) -> str | None:
    """중단 시작 시각(ISO). 중단 중이 아니거나 기록이 없으면 None."""
    if not is_trading_halted(conn):
        return None
    return db.get_kv(conn, db.KV_HALT_AT)


def resume_trading(conn, reason: str | None = None, *, actor: str = "manual") -> bool:
    """
    중단 플래그를 해제한다. **사람이 확인한 뒤에만 호출한다** — 자동 호출 금지
    (테스트가 소스를 스캔해 risk.py 밖의 호출을 막는다).

    이력(halt_events)에 RESUME 을 남긴다. 사유가 없으면 WARNING 을 남기고 "사유 미기록" 으로
    기록한다(CLI 는 사유가 없으면 아예 거부한다). 중단 중이 아니면 아무것도 하지 않고 False.
    """
    text = str(reason or "").strip()
    if not text:
        logger.warning(
            '⚠️ 재개 사유가 없습니다 — "사유 미기록"으로 기록합니다. '
            'CLI 를 쓰면 사유가 필수입니다: python -m risk resume --reason "..."'
        )
        text = "사유 미기록"

    kind = halt_kind(conn)
    if kind is None:
        logger.info("[중단] 중단 상태가 아니라 재개할 것이 없습니다")
        return False

    # 이력을 먼저 남긴다 — 기록 없이 해제만 되는 상태가 생기면 안 된다.
    db.record_halt_event(conn, "RESUME", kind=kind, reason=text, actor=actor)
    db.set_kv(conn, db.KV_TRADING_HALTED, "0")
    db.set_kv(conn, db.KV_HALT_REASON, None)
    db.set_kv(conn, db.KV_HALT_KIND, None)
    db.set_kv(conn, db.KV_HALT_AT, None)
    logger.warning(f"✅ 신규 매매 중단 플래그 해제 — [{kind}] {text}")
    return True


def reconcile(conn, *, holdings: list[dict] | None = None,
              autofix: bool | None = None) -> dict:
    """
    브로커와 DB 를 대조하고 필요하면 중단 플래그를 세운다.

    Args:
        conn: 열린 DB 연결
        holdings: 브로커 보유(테스트용 주입). None 이면 실제 조회.
        autofix: None 이면 `config.RECONCILE_AUTOFIX` 를 따른다.

    반환: `compare_positions()` 결과 + {"halted": bool, "autofixed": bool}

    불일치가 있으면 **자동 보정 여부와 무관하게 중단 플래그를 세운다.**
    (보정했다고 해서 조용히 계속하면, 보정 자체가 틀렸을 때 알아챌 기회가 없다.)
    """
    if autofix is None:
        autofix = config.RECONCILE_AUTOFIX

    # 중단 플래그가 서 있으면 **매 사이클** CRITICAL 로 사유와 해제 방법을 남긴다 (T04).
    # 조용히 지나가면 봇이 돌고 있는데 매매만 안 되는 상태를 사용자가 눈치채지 못한다.
    if is_trading_halted(conn):
        logger.critical(
            f"🚨 신규 매매 중단 중 [{halt_kind(conn)}] — 사유: {halt_reason(conn) or '사유 미기록'}\n"
            f"   확인: python -m risk status\n"
            f"   해제: 원인을 확인한 뒤 python -m risk resume --reason \"확인한 내용\""
        )

    db_positions = db.list_positions(conn, status="OPEN")
    # EXTERNAL 은 봇이 관리하지 않는 보유지만 대조에서는 "브로커에 있음"으로 봐야
    # 매 사이클마다 외부 보유로 다시 잡히지 않는다.
    db_positions += db.list_positions(conn, status="EXTERNAL")

    if holdings is None:
        holdings = fetch_broker_holdings()

    result = compare_positions(db_positions, holdings)
    result["halted"] = False
    result["autofixed"] = False

    # 미확정 주문으로 설명되는 불일치는 유예한다 (T04). 상한을 넘긴 주문은 유예하지 않는다.
    open_orders = _open_orders_by_key(conn)
    if open_orders:
        result, pending, stale = _split_pending(
            result, open_orders, now=db.now_kst(),
            max_age_minutes=config.ORDER_TIMEOUT_MINUTES,
        )
        result["pending_orders"] = pending
        result["stale_orders"] = stale
        # _split_pending 이 돌려주는 dict 에는 이 키들이 없으므로 다시 채운다.
        result["halted"] = False
        result["autofixed"] = False
        _report_pending(pending, stale)

    if result["is_consistent"]:
        logger.info(f"[reconcile] 브로커 ↔ DB 일치 ({len(result['matched'])}건)")
        # 이전에 세운 중단 플래그가 있고 사유가 불일치였다면 자동 해제하지 않는다.
        # 사람이 확인했는지 알 수 없기 때문이다. 상태만 로그로 알린다.
        if is_trading_halted(conn):
            logger.warning(
                f"[reconcile] 불일치는 해소됐지만 중단 플래그가 남아 있습니다 "
                f"(사유: {halt_reason(conn)}). 확인 후 python -m risk resume --reason \"...\" 로 해제하세요."
            )
        return result

    _report(result)

    if autofix:
        _apply_autofix(conn, result)
        result["autofixed"] = True
        logger.warning("[reconcile] RECONCILE_AUTOFIX=true — 브로커 값으로 보정했습니다")

    reason = (
        f"브로커 ↔ DB 불일치: 수량불일치 {len(result['qty_mismatch'])}건, "
        f"DB만 {len(result['db_only'])}건, 외부보유 {len(result['broker_only'])}건"
        + (" (자동 보정함)" if autofix else " (자동 보정 안 함)")
    )
    halt_trading(conn, reason)
    result["halted"] = True
    return result
