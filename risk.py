"""
risk.py — 킬스위치의 게이트 판단과 수동 재개 CLI (T09a).

중단 종류
  STATE      상태를 믿을 수 없다(브로커↔DB 불일치 등). 모든 주문(매수·매도) 차단.
  RISK       손실 한도. 신규 매수만 차단하고 청산(손절)은 계속한다.
  HALT 파일  사람이 거는 비상정지. 모든 주문 차단. 파일을 직접 지워야 풀린다.

실패 시 닫힘: 중단 상태나 HALT 파일을 읽다가 예외가 나면 모든 주문을 막는다.

재개는 사람만 한다(자동 재개 금지):
    python -m risk status
    python -m risk resume --reason "무엇을 확인하고 해제하는지"
"""
import argparse
import json
import logging
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import config
import db
import reconcile

logger = logging.getLogger("bot")

FULL_STOP = "FULL_STOP"              # 모든 주문 차단 (사이클 자체를 건너뛴다)
ENTRIES_BLOCKED = "ENTRIES_BLOCKED"  # 신규 매수만 차단 (청산은 계속)
OPEN = "OPEN"

_PROJECT_ROOT = Path(__file__).resolve().parent


class TradingHalted(Exception):
    """
    킬스위치가 이 주문을 막았다.

    일반 예외로 삼켜지면 "왜 주문이 안 나갔는지"가 사라진다. 주문을 감싸는 호출부는
    `except Exception` 보다 **앞에서** 이 예외를 잡아 CRITICAL 로 남겨야 한다.
    """


def halt_file_path() -> Path:
    """HALT 파일 경로. 작업 디렉터리와 무관하게 프로젝트 루트 기준이다."""
    raw = str(getattr(config, "HALT_FILE", "") or "").strip()
    if not raw:
        return _PROJECT_ROOT / "HALT"
    path = Path(raw).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def _halt_file_present() -> bool:
    return halt_file_path().exists()


def gate_status(conn) -> tuple[str, str]:
    """
    (게이트, 설명). 설명은 로그와 예외 메시지에 쓴다.

    `reconcile.is_trading_halted` 를 모듈 속성으로 부른다 — 기존 테스트가 이 함수를
    패치해 중단 상태를 바꾸기 때문이다. 읽다가 예외가 나면 FULL_STOP (실패 시 닫힘).
    """
    try:
        if _halt_file_present():
            return FULL_STOP, (f"HALT 파일이 있습니다({halt_file_path().name}) — "
                               "직접 삭제하기 전에는 모든 주문이 막힙니다")
        if not reconcile.is_trading_halted(conn):
            return OPEN, ""
        kind = reconcile.halt_kind(conn)
        reason = reconcile.halt_reason(conn) or "사유 미기록"
    except Exception as e:
        logger.critical(
            f"🚨 중단 상태를 읽지 못했습니다({type(e).__name__}: {e}) — 안전을 위해 모든 주문을 막습니다"
        )
        return FULL_STOP, "중단 상태를 읽지 못해 모든 주문을 막습니다"
    if kind == "RISK":
        return ENTRIES_BLOCKED, f"[RISK] {reason}"
    return FULL_STOP, f"[STATE] {reason}"


def cycle_gate(conn) -> str:
    """사이클 게이트: FULL_STOP | ENTRIES_BLOCKED | OPEN."""
    return gate_status(conn)[0]


def assert_order_permitted(conn, side: str, reason: str | None = None) -> None:
    """
    주문 직전 게이트. 막혀 있으면 `TradingHalted`.

    FULL_STOP 은 매수·매도 모두, ENTRIES_BLOCKED 는 매수만 막는다.
    **orders 기록보다 앞에서** 불러야 한다 — 거부된 주문이 SUBMITTED 행을 남기면 안 된다.
    """
    side = str(side).upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"알 수 없는 주문 방향: {side!r}")
    gate, detail = gate_status(conn)
    if gate == FULL_STOP or (gate == ENTRIES_BLOCKED and side == "BUY"):
        suffix = f" (사유: {reason})" if reason else ""
        raise TradingHalted(f"{side} 주문 차단{suffix} — {detail}")


# ── 손실 한도 (T09b) ──────────────────────────────────────

_EPS_KRW = 1e-6     # 부동소수점 오차 허용(원). 한도와 정확히 같으면 걸린 것으로 본다.

DRAWDOWN = "DRAWDOWN"
DAILY_LOSS = "DAILY_LOSS"


@dataclass(frozen=True)
class LossCheck:
    """손실 한도 평가 결과. 금액은 원화, 퍼센트는 CAPITAL_CAP_KRW 대비다."""

    equity: float
    peak: float                 # 이번 값을 반영한 고점(내려가지 않는다). 다음 호출에 넘긴다
    day_start: float
    drawdown_krw: float         # 고점 대비 하락액(0 이상)
    daily_loss_krw: float       # 당일 시작 대비 하락액(0 이상)
    drawdown_pct: float
    daily_loss_pct: float
    drawdown_limit_krw: float
    daily_limit_krw: float
    breaches: tuple             # (DRAWDOWN, DAILY_LOSS) 중 해당하는 것

    @property
    def breached(self) -> bool:
        return bool(self.breaches)

    def describe(self) -> str:
        return (
            f"자산 {self.equity:,.0f}원 | 낙폭 {self.drawdown_krw:,.0f}원 "
            f"({self.drawdown_pct:.2f}%, 한도 {self.drawdown_limit_krw:,.0f}원) | "
            f"일일 손실 {self.daily_loss_krw:,.0f}원 "
            f"({self.daily_loss_pct:.2f}%, 한도 {self.daily_limit_krw:,.0f}원)"
        )


def evaluate_loss_limits(*, equity, peak, day_start, capital_cap_krw,
                         max_drawdown_pct, daily_loss_limit_pct) -> LossCheck:
    """
    고점·당일 시작 대비 손실이 한도에 닿았는지 평가한다. **순수 함수** — I/O·시계·설정을 읽지 않는다.

    기준은 계좌 총자산이 아니라 `capital_cap_krw` 다. 모의계좌 잔고가 수천만 원이면 계좌 기준 %는 영원히 걸리지 않는다.
    `equity` 는 봇이 쓴 자본 기준 자산(CAPITAL_CAP_KRW + 누적 실현손익 + 미실현손익)이며 계산은 호출부의 일이다.
    고점은 내려가지 않는다. 한도와 정확히 같으면 걸린 것으로 본다. 입력이 올바르지 않으면 추측하지 않고 ValueError.
    """
    cap = float(capital_cap_krw)
    if not (math.isfinite(cap) and cap > 0):
        raise ValueError(f"capital_cap_krw 는 양수여야 합니다: {capital_cap_krw!r}")
    limits = []
    for name, value in (("max_drawdown_pct", max_drawdown_pct),
                        ("daily_loss_limit_pct", daily_loss_limit_pct)):
        v = float(value)
        if not (math.isfinite(v) and 0 < v <= 100):
            raise ValueError(f"{name} 는 0 초과 100 이하여야 합니다: {value!r}")
        limits.append(v)
    eq, pk, ds = float(equity), float(peak), float(day_start)
    for name, v in (("equity", eq), ("peak", pk), ("day_start", ds)):
        if not math.isfinite(v):
            raise ValueError(f"{name} 가 유한한 숫자가 아닙니다: {v!r}")

    new_peak = max(pk, eq)
    drawdown_krw = max(0.0, new_peak - eq)
    daily_loss_krw = max(0.0, ds - eq)
    dd_limit = cap * limits[0] / 100.0
    daily_limit = cap * limits[1] / 100.0

    breaches = []
    if drawdown_krw + _EPS_KRW >= dd_limit:
        breaches.append(DRAWDOWN)
    if daily_loss_krw + _EPS_KRW >= daily_limit:
        breaches.append(DAILY_LOSS)
    return LossCheck(
        equity=eq, peak=new_peak, day_start=ds,
        drawdown_krw=drawdown_krw, daily_loss_krw=daily_loss_krw,
        drawdown_pct=drawdown_krw / cap * 100.0, daily_loss_pct=daily_loss_krw / cap * 100.0,
        drawdown_limit_krw=dd_limit, daily_limit_krw=daily_limit,
        breaches=tuple(breaches),
    )


# ── 손실 한도 평가·기록 (T09b) ─────────────────────────────

KV_LOSS_BASELINE = "loss_baseline"      # JSON 한 키: peak, day, day_start, last_equity, unknown_ack (한 번에 써서 반쯤 쓰인 상태를 막는다)
KV_LOSS_RESET = "loss_reset_pending"    # "1" 이면 다음 평가에서 기준선을 현재 값으로 다시 잡는다(손실 한도 중단을 재개한 직후)
KV_LOSS_HALT = "loss_halt_active"       # "1" 이면 현재 중단이 손실 한도 때문이다 — 재개할 때 기준선 재설정 대상

PNL_UNKNOWN = "PNL_UNKNOWN"             # 손익을 계산하지 못한 매도 체결이 확인된 수보다 늘었다
BASELINE_CORRUPT = "BASELINE_CORRUPT"   # 저장된 기준선을 읽을 수 없다

_REASON_LABEL = {
    DRAWDOWN: "고점 대비 낙폭 한도",
    DAILY_LOSS: "일일 손실 한도",
    PNL_UNKNOWN: "손익을 계산하지 못한 매도 체결",
    BASELINE_CORRUPT: "손실 기준선 손상",
}
_KST = ZoneInfo("Asia/Seoul")


class EquityUnavailable(Exception):
    """봇 자산을 계산할 수 없다(시세를 모르는 보유 등). 호출부는 신규 매수만 보류하고 청산은 계속한다."""


def compute_bot_equity(*, capital_cap_krw, realized_pnl_krw, open_positions, prices) -> float:
    """
    봇이 쓴 자본 기준 자산 = CAPITAL_CAP_KRW + 누적 실현손익 + DB 의 OPEN 보유 미실현손익. **순수 함수**.

    브로커 잔고가 아니라 DB 행(`open_positions`, status=OPEN 만 넘긴다)으로 계산한다 — 잔고 수량에는 봇이 만들지 않은
    EXTERNAL 보유가 섞인다. 수수료·세금은 반영하지 않는다(VERIFY: T11 비용 모델).
    평가할 수 없는 보유(시세·평단을 모름, 해외 보유)가 있으면 추측하지 않고 EquityUnavailable.
    `prices` 는 {(symbol, market): 현재가(원)}.
    """
    equity = float(capital_cap_krw) + float(realized_pnl_krw)
    for pos in open_positions:
        qty = float(pos.get("qty") or 0.0)
        if qty <= 0:
            continue
        symbol, market = str(pos.get("symbol")), str(pos.get("market"))
        if market != "domestic":
            raise EquityUnavailable(f"{market} {symbol} 보유는 원화로 평가할 수 없습니다")
        price = prices.get((symbol, market))
        if price is None or not math.isfinite(float(price)) or float(price) <= 0:
            raise EquityUnavailable(f"{market} {symbol} 시세를 몰라 평가할 수 없습니다")
        avg = float(pos.get("avg_price") or 0.0)
        if not (math.isfinite(avg) and avg > 0):
            raise EquityUnavailable(f"{market} {symbol} 평단을 몰라 평가할 수 없습니다")
        equity += (float(price) - avg) * qty
    return equity


def _parse_baseline(raw: str) -> dict:
    """저장된 기준선(JSON)을 읽는다. 읽을 수 없으면 ValueError — 추측해서 채우지 않는다."""
    try:
        data = json.loads(raw)
        parsed = {
            "peak": float(data["peak"]),
            "day": str(data["day"]),
            "day_start": float(data["day_start"]),
            "last_equity": float(data["last_equity"]),
            "unknown_ack": int(data["unknown_ack"]),
        }
        date.fromisoformat(parsed["day"])
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"손실 기준선을 읽을 수 없습니다({type(e).__name__})") from e
    for key in ("peak", "day_start", "last_equity"):
        if not math.isfinite(parsed[key]):
            raise ValueError(f"손실 기준선 값이 유한하지 않습니다({key})")
    return parsed


@dataclass(frozen=True)
class LimitOutcome:
    """한 번의 평가 결과. check 가 None 이면 기준선이 손상돼 평가하지 못했다."""

    check: "LossCheck | None"
    reasons: tuple          # DRAWDOWN / DAILY_LOSS / PNL_UNKNOWN / BASELINE_CORRUPT 중 해당하는 것
    halted_now: bool        # 이번 평가가 새로 RISK 중단을 걸었는가
    rebaselined: bool       # 재개 직후라 기준선을 다시 잡았는가


def _halt_for_loss(conn, text: str, details: dict) -> bool:
    """RISK 중단을 건다(신규 매수만 차단·청산 계속). 새로 걸렸을 때만 '손실 한도 중단' 표시를 남긴다."""
    changed = reconcile.halt_trading(conn, f"[손실 한도] {text}", "RISK", details=details)
    if changed:
        db.set_kv(conn, KV_LOSS_HALT, "1")
    return changed


def evaluate_and_enforce(conn, *, equity, unknown_pnl_count, now, mode, capital_cap_krw,
                         max_drawdown_pct, daily_loss_limit_pct) -> LimitOutcome:
    """
    봇 자산으로 손실 한도를 평가하고, 기준선·자산 기록을 남기고, 한도에 닿으면 RISK 중단을 건다.

    기준선(kv 한 키)
      - 고점: 내려가지 않는다. 기준선이 없으면 CAPITAL_CAP_KRW 에서 시작한다.
        (VERIFY: DB 가 사라지면 고점이 낮아져 낙폭이 줄어 보인다.)
      - 당일 시작 자산: 그날 첫 평가 때 **전일 마지막 평가 자산**으로 정한다. 첫 사이클 값을 쓰면 시가 갭하락이
        일일 손실에서 빠진다. 기록이 없으면 현재 값에서 시작한다. 날짜는 KST.
      - 손익 미확인 매도 건수: 확인된 수(unknown_ack)보다 늘면 한도 계산이 낙관적일 수 있어 중단한다.
    재개(손실 한도 중단 뒤): CLI 가 재설정 표시를 남기고, 다음 평가가 고점·당일 시작을 현재 값으로 다시 잡고
    미확인 건수를 확인 처리한다. 그대로 두면 다음 평가에서 같은 한도에 바로 걸려 다시 멈춘다.
    기준선이 손상됐으면 새로 만들지 않고 RISK 중단을 건다(사람이 재개하면 새로 만든다).

    순서: 검증 → 평가 → 자산 기록·기준선 저장 → 중단. 중단이 실패해도 기준선은 이미 저장돼 있어 다음 평가가 다시 감지한다.
    중단 기록이 실패하면 예외를 그대로 올린다(호출부가 신규 매수를 보류한다).
    """
    if now.tzinfo is None:
        raise ValueError("now 는 tz-aware 여야 합니다")
    equity = float(equity)
    if not math.isfinite(equity):
        raise ValueError(f"equity 가 유한한 숫자가 아닙니다: {equity!r}")
    unknown = int(unknown_pnl_count)
    today = now.astimezone(_KST).date().isoformat()

    reset = db.get_kv(conn, KV_LOSS_RESET) == "1"
    raw = db.get_kv(conn, KV_LOSS_BASELINE)
    base = None
    if raw is not None and not reset:
        try:
            base = _parse_baseline(raw)
        except ValueError as e:
            logger.critical(f"🚨 [손실 한도] {e} — 신규 매수를 중단합니다. 원인을 확인한 뒤 "
                            "python -m risk resume --reason ... 로 기준선을 다시 만드세요")
            changed = _halt_for_loss(conn, str(e), {"reasons": [BASELINE_CORRUPT]})
            return LimitOutcome(None, (BASELINE_CORRUPT,), changed, False)

    rebaselined = False
    if reset:
        peak, day_start, ack = equity, equity, unknown
        rebaselined = True
        logger.warning(f"[손실 한도] 재개 후 첫 평가 — 기준선을 현재 자산 {equity:,.0f}원으로 다시 잡습니다 "
                       f"(고점·당일 시작 재설정, 손익 미확인 {unknown}건 확인 처리)")
    elif base is None:
        peak, day_start, ack = float(capital_cap_krw), equity, 0
        logger.info(f"[손실 한도] 기준선이 없어 새로 시작합니다 (고점 {float(capital_cap_krw):,.0f}원, "
                    f"당일 시작 {equity:,.0f}원)")
    else:
        peak = base["peak"]
        day_start = base["day_start"] if base["day"] == today else base["last_equity"]
        ack = base["unknown_ack"]

    check = evaluate_loss_limits(
        equity=equity, peak=peak, day_start=day_start, capital_cap_krw=capital_cap_krw,
        max_drawdown_pct=max_drawdown_pct, daily_loss_limit_pct=daily_loss_limit_pct,
    )
    reasons = list(check.breaches)
    if unknown > ack:
        reasons.append(PNL_UNKNOWN)

    db.log_equity(conn, equity, None, mode)
    db.set_kv(conn, KV_LOSS_BASELINE, json.dumps({
        "peak": check.peak,
        "day": today,
        "day_start": day_start,
        "last_equity": equity,
        "unknown_ack": ack,
    }, sort_keys=True, allow_nan=False))
    if reset:
        db.set_kv(conn, KV_LOSS_RESET, "0")

    halted = False
    if reasons:
        names = ", ".join(_REASON_LABEL[r] for r in reasons)
        text = f"{names} — {check.describe()}"
        if PNL_UNKNOWN in reasons:
            text += f" | 손익 미확인 매도 {unknown}건(확인 완료 {ack}건)"
        halted = _halt_for_loss(conn, text, {
            "reasons": reasons, "equity": equity, "peak": check.peak, "day_start": day_start,
            "unknown_pnl": unknown, "unknown_ack": ack,
        })
    return LimitOutcome(check, tuple(reasons), halted, rebaselined)


# ── 연속 사이클 오류·UNKNOWN 주문 시한 (T09b-2c) ─────────────────────────────
# 둘 다 RISK 중단(신규 매수만 차단·청산 계속)을 건다. STATE 로 걸면 손절까지 멈춘다.
# 손실 한도 중단이 아니므로 `KV_LOSS_HALT` 표시는 남기지 않는다 — 재개해도 손실 기준선을 다시 잡지 않는다.


class ErrorStreak:
    """연속으로 실패한 매매 사이클 수. 메모리에만 있다 — 재시작하면 0 에서 시작한다."""

    def __init__(self) -> None:
        self.count = 0

    def failure(self) -> int:
        self.count += 1
        return self.count

    def success(self) -> None:
        self.count = 0


def error_streak_reached(count: int, max_consecutive: int) -> bool:
    """연속 실패가 한도에 닿았는가. 한도와 같으면 걸린다. 순수 함수."""
    if int(max_consecutive) < 1:
        raise ValueError(f"max_consecutive 는 1 이상이어야 합니다: {max_consecutive!r}")
    return int(count) >= int(max_consecutive)


def halt_for_error_streak(conn, *, count: int, max_consecutive: int, label: str) -> bool:
    """연속 실패 한도에 닿았을 때 RISK 중단을 건다. 새로 걸렸으면 True. 기록에 실패하면 예외를 그대로 올린다."""
    text = f"[연속 오류] 매매 사이클이 {count}회 연속 실패했습니다(한도 {max_consecutive}회, 마지막 원인: {label})"
    return reconcile.halt_trading(
        conn, text, "RISK",
        details={"reasons": ["ERROR_STREAK"], "count": count, "max": max_consecutive, "last": label},
    )


def _minutes_since(raw, now) -> float:
    """`raw`(ISO 문자열) 이후 지난 분. 읽을 수 없거나 tz 가 없으면 '아주 오래됨'으로 본다(추측하지 않는다)."""
    if not raw:
        return float("inf")
    try:
        created = type(now).fromisoformat(str(raw))
    except (TypeError, ValueError):
        return float("inf")
    if created.tzinfo is None:
        return float("inf")
    return (now - created).total_seconds() / 60.0


def find_stale_unknown_orders(orders, *, now, timeout_minutes) -> list:
    """UNKNOWN 주문 중 생성 후 timeout_minutes 이상 지난 것. 한도와 같으면 포함한다. 순수 함수."""
    if now.tzinfo is None:
        raise ValueError("now 는 tz-aware 여야 합니다")
    timeout = float(timeout_minutes)
    if not (0 < timeout < float("inf")):
        raise ValueError(f"timeout_minutes 는 0 보다 큰 유한한 숫자여야 합니다: {timeout_minutes!r}")
    stale = []
    for order in orders:
        if order.get("status") != "UNKNOWN":
            continue
        if _minutes_since(order.get("created_at"), now) >= timeout:
            stale.append(order)
    return stale


def enforce_unknown_deadline(conn, *, now, timeout_minutes) -> list:
    """
    시한을 넘긴 응답 불명(UNKNOWN) 주문이 있으면 RISK 중단을 걸고, 그 주문 목록을 돌려준다.

    UNKNOWN 은 주문이 나갔는지 모르는 상태다. 체결 조회로 확정되지 않은 채 오래 남으면 보유 수량을 믿을 수 없으므로
    신규 매수를 멈추고 사람이 브로커에서 확인하게 한다. 청산은 계속한다.
    시각은 주문 **생성** 시각 기준이다(`updated_at` 은 조회마다 바뀔 수 있어 시계가 밀린다).
    이미 같거나 더 강한 중단 중이면 `halt_trading` 이 아무것도 바꾸지 않는다(매 사이클 알림 반복 없음).
    중단 기록에 실패하면 예외를 그대로 올린다.
    """
    stale = find_stale_unknown_orders(db.list_open_orders(conn), now=now, timeout_minutes=timeout_minutes)
    if not stale:
        return []
    refs = ", ".join(f"#{o.get('id')} {o.get('symbol')} {o.get('side')}" for o in stale)
    text = (f"[UNKNOWN 시한] 응답 불명 주문 {len(stale)}건이 {float(timeout_minutes):g}분을 넘겼습니다({refs}) — "
            "체결 조회로 확정되지 않았습니다. 브로커에서 직접 확인하세요")
    reconcile.halt_trading(
        conn, text, "RISK",
        details={"reasons": ["UNKNOWN_TIMEOUT"], "orders": [o.get("id") for o in stale],
                 "timeout_minutes": float(timeout_minutes)},
    )
    return stale


def _loss_baseline_lines(conn) -> list:
    """`python -m risk status` 에 보여 줄 손실 기준선 요약. 읽기 실패도 화면에 그대로 알린다."""
    lines = []
    try:
        raw = db.get_kv(conn, KV_LOSS_BASELINE)
        pending = db.get_kv(conn, KV_LOSS_RESET) == "1"
        if raw is None:
            lines.append("손실 기준선: 아직 없음 (첫 평가 때 만들어집니다)")
        else:
            b = _parse_baseline(raw)
            lines.append(
                f"손실 기준선: 고점 {b['peak']:,.0f}원 | 당일 시작 {b['day_start']:,.0f}원 ({b['day']}) | "
                f"마지막 자산 {b['last_equity']:,.0f}원 | 손익 미확인 확인 완료 {b['unknown_ack']}건"
            )
        if pending:
            lines.append("  재개 직후입니다 — 다음 평가에서 기준선을 현재 값으로 다시 잡습니다")
    except Exception as e:
        lines.append(f"손실 기준선: 읽기 실패({type(e).__name__}) — 재개(resume)하면 새로 만들어집니다")
    return lines


# ── CLI ───────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m risk",
                                     description="킬스위치 상태 확인과 수동 재개")
    sub = parser.add_subparsers(dest="command", required=True)
    resume = sub.add_parser("resume", help="신규 매매 중단을 수동으로 해제한다")
    resume.add_argument("--reason", required=True, help="재개 사유(필수, 이력에 기록)")
    sub.add_parser("status", help="현재 중단 상태를 보여준다")
    return parser


def _resume(conn, reason: str) -> int:
    text = (reason or "").strip()
    if not text:
        print("재개 사유가 비어 있습니다. --reason 에 무엇을 확인하고 해제하는지 적어 주세요. (상태 변경 없음)")
        return 2
    try:
        present = _halt_file_present()
    except Exception as e:
        print(f"HALT 파일 상태를 확인하지 못했습니다({type(e).__name__}). 해제하지 않습니다. (상태 변경 없음)")
        return 3
    if present:
        print(f"HALT 파일이 있습니다: {halt_file_path()}\n"
              "사람이 직접 삭제해야 합니다(이 명령은 지우지 않습니다). 삭제한 뒤 다시 실행하세요. (상태 변경 없음)")
        return 3
    if not reconcile.is_trading_halted(conn):
        print("중단 상태가 아닙니다. 할 일이 없습니다. (이력 없음)")
        return 0
    kind = reconcile.halt_kind(conn)
    was_loss = db.get_kv(conn, KV_LOSS_HALT) == "1"
    reconcile.resume_trading(conn, text, actor="cli")
    print(f"해제했습니다 (종류 {kind}). 다음 사이클부터 반영됩니다. 사유: {text}")
    if was_loss:
        # 손실 한도 때문에 멈췄다면 기준선을 현재 값으로 다시 잡아야 한다. 그대로 두면 다음 평가에서
        # 같은 한도에 바로 걸려 다시 멈춘다. 해제 다음에 표시한다 — 중간에 실패하면 다시 멈추는 안전한 쪽이다.
        db.set_kv(conn, KV_LOSS_RESET, "1")
        db.set_kv(conn, KV_LOSS_HALT, "0")
        print("손실 한도 기준선(고점·당일 시작 자산·손익 미확인 건수)은 다음 평가에서 현재 값으로 다시 잡힙니다. "
              "이 시점부터 한도만큼 다시 잃을 수 있습니다.")
    return 0


def _file_state() -> str:
    try:
        return "있음" if _halt_file_present() else "없음"
    except Exception as e:
        return f"확인 실패({type(e).__name__})"


def _status(conn) -> int:
    gate, detail = gate_status(conn)
    print(f"게이트: {gate}" + (f" — {detail}" if detail else ""))
    if reconcile.is_trading_halted(conn):
        print(f"DB 중단: 예 (종류 {reconcile.halt_kind(conn)})")
        print(f"  사유: {reconcile.halt_reason(conn) or '사유 미기록'}")
        print(f"  시작: {reconcile.halt_since(conn) or '기록 없음'}")
    else:
        print("DB 중단: 아니오")
    print(f"HALT 파일: {_file_state()} ({halt_file_path()})")
    for line in _loss_baseline_lines(conn):
        print(line)
    events = db.list_halt_events(conn, limit=5)
    if events:
        print("최근 이력(최신순):")
        for e in events:
            print(f"  {e['ts']} {e['event']:6s} {e['kind'] or '-':5s} {e['actor'] or '-'}: {e['reason'] or ''}")
    return 0


def main(argv=None, conn=None) -> int:
    args = _build_parser().parse_args(argv)
    own_conn = conn is None
    if own_conn:
        try:
            conn = db.open_db()
        except db.DatabaseUnavailable as e:
            print(f"DB 를 열 수 없습니다: {e}")
            return 1
    try:
        if args.command == "resume":
            return _resume(conn, args.reason)
        return _status(conn)
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
