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
import sys
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
