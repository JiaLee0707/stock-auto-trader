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

import config
import db

logger = logging.getLogger("bot")


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


def _qty_equal(a, b, eps: float = 1e-6) -> bool:
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


def halt_trading(conn, reason: str) -> None:
    """
    신규 매매 중단 플래그를 세운다. 매매 판단은 시작 전에 이 값을 봐야 한다.

    TODO(T09): 사용자 알림(텔레그램 등) 연결 지점. 지금은 로그만 남긴다.
    """
    db.set_kv(conn, db.KV_TRADING_HALTED, "1")
    db.set_kv(conn, db.KV_HALT_REASON, reason)
    logger.error(f"🚨 신규 매매 중단 플래그 설정 — {reason}")
    # TODO(T09): 여기서 알림을 보낸다 (텔레그램/슬랙 등)


def resume_trading(conn, reason: str = "수동 해제") -> None:
    """중단 플래그를 해제한다. 불일치를 사람이 확인한 뒤 호출한다."""
    db.set_kv(conn, db.KV_TRADING_HALTED, "0")
    db.set_kv(conn, db.KV_HALT_REASON, None)
    logger.warning(f"✅ 신규 매매 중단 플래그 해제 — {reason}")


def is_trading_halted(conn) -> bool:
    """중단 플래그가 서 있으면 True."""
    return db.get_kv(conn, db.KV_TRADING_HALTED, "0") == "1"


def halt_reason(conn) -> str | None:
    """중단 사유(없으면 None)."""
    return db.get_kv(conn, db.KV_HALT_REASON)


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

    db_positions = db.list_positions(conn, status="OPEN")
    # EXTERNAL 은 봇이 관리하지 않는 보유지만 대조에서는 "브로커에 있음"으로 봐야
    # 매 사이클마다 외부 보유로 다시 잡히지 않는다.
    db_positions += db.list_positions(conn, status="EXTERNAL")

    if holdings is None:
        holdings = fetch_broker_holdings()

    result = compare_positions(db_positions, holdings)
    result["halted"] = False
    result["autofixed"] = False

    if result["is_consistent"]:
        logger.info(f"[reconcile] 브로커 ↔ DB 일치 ({len(result['matched'])}건)")
        # 이전에 세운 중단 플래그가 있고 사유가 불일치였다면 자동 해제하지 않는다.
        # 사람이 확인했는지 알 수 없기 때문이다. 상태만 로그로 알린다.
        if is_trading_halted(conn):
            logger.warning(
                f"[reconcile] 불일치는 해소됐지만 중단 플래그가 남아 있습니다 "
                f"(사유: {halt_reason(conn)}). 확인 후 resume_trading() 으로 해제하세요."
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
