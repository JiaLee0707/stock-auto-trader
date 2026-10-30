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
import logging
import math
import sys
from dataclasses import dataclass
from pathlib import Path

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
    reconcile.resume_trading(conn, text, actor="cli")
    print(f"해제했습니다 (종류 {kind}). 다음 사이클부터 반영됩니다. 사유: {text}")
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
