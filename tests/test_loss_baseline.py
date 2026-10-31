"""
T09b-2b: 봇 자산 계산, 손실 기준선, 한도 도달 시 RISK 중단, 재개 시 기준선 재설정.

  - 기준은 CAPITAL_CAP_KRW 이고 봇 자산은 DB 행으로 계산한다(EXTERNAL 보유가 섞이지 않는다)
  - 당일 시작 자산은 전일 마지막 평가 값이다(시가 갭하락도 일일 손실에 들어간다)
  - 손실 한도 중단을 재개하면 다음 평가가 기준선을 다시 잡는다. 다른 사유의 재개는 건드리지 않는다
  - 읽을 수 없는 기준선은 조용히 새로 만들지 않고 중단한다
실제 API·알림은 쓰지 않는다.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import db
import reconcile
import risk

KST = ZoneInfo("Asia/Seoul")
CAP = 200_000.0


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(reconcile.notifier, "notify",
                        lambda level, title, message="": calls.append((level, title, message)) or True)
    return calls


def _at(day, hour=11):
    return datetime(2026, 11, day, hour, 0, tzinfo=KST)


def _eval(conn, equity, *, day=2, hour=11, unknown=0, **over):
    kw = dict(equity=equity, unknown_pnl_count=unknown, now=_at(day, hour), mode="MOCK",
              capital_cap_krw=CAP, max_drawdown_pct=5.0, daily_loss_limit_pct=2.0)   # 한도 10,000원 / 4,000원
    kw.update(over)
    return risk.evaluate_and_enforce(conn, **kw)


def _baseline(conn):
    return json.loads(db.get_kv(conn, risk.KV_LOSS_BASELINE))


# ── 봇 자산 계산 ──────────────────────────────────────────

def _pos(symbol="379800", market="domestic", qty=2.0, avg=23_000.0):
    return {"symbol": symbol, "market": market, "qty": qty, "avg_price": avg, "status": "OPEN"}


def _equity(positions, prices, realized=0.0):
    return risk.compute_bot_equity(capital_cap_krw=CAP, realized_pnl_krw=realized,
                                   open_positions=positions, prices=prices)


def test_equity_without_positions_is_cap_plus_realized():
    assert _equity([], {}, realized=-1_500.0) == pytest.approx(198_500.0)


def test_equity_adds_unrealized_from_the_database_position():
    assert _equity([_pos(qty=2.0, avg=23_000.0)], {("379800", "domestic"): 22_500.0}) == pytest.approx(199_000.0)


def test_zero_quantity_rows_are_ignored_without_a_price():
    assert _equity([_pos(qty=0.0)], {}) == CAP


def test_missing_price_makes_equity_unavailable():
    with pytest.raises(risk.EquityUnavailable):
        _equity([_pos()], {})


@pytest.mark.parametrize("price", [None, 0.0, -1.0, float("nan")], ids=["none", "zero", "negative", "nan"])
def test_unusable_price_makes_equity_unavailable(price):
    with pytest.raises(risk.EquityUnavailable):
        _equity([_pos()], {("379800", "domestic"): price})


def test_overseas_position_cannot_be_valued_in_krw():
    with pytest.raises(risk.EquityUnavailable):
        _equity([_pos(market="overseas")], {("379800", "overseas"): 100.0})


def test_unknown_average_cost_makes_equity_unavailable():
    with pytest.raises(risk.EquityUnavailable):
        _equity([_pos(avg=0.0)], {("379800", "domestic"): 22_500.0})


# ── 기준선 ────────────────────────────────────────────────

def test_first_evaluation_creates_the_baseline_and_logs_equity(conn, notes):
    out = _eval(conn, CAP)
    assert out.reasons == () and not out.halted_now and not out.rebaselined
    assert out.check.peak == CAP and out.check.day_start == CAP
    row = db.latest_equity(conn)
    assert row["total_equity"] == CAP and row["mode"] == "MOCK"
    assert _baseline(conn) == {"peak": CAP, "day": "2026-11-02", "day_start": CAP,
                               "last_equity": CAP, "unknown_ack": 0}
    assert notes == []


def test_peak_follows_gains_and_never_falls(conn):
    _eval(conn, 205_000.0)
    out = _eval(conn, 203_000.0)
    assert out.check.peak == 205_000.0
    assert _baseline(conn)["peak"] == 205_000.0


def test_overnight_gap_counts_as_the_next_days_loss(conn):
    _eval(conn, 200_000.0, day=2, hour=14)            # 전일 마지막 평가
    out = _eval(conn, 195_900.0, day=3, hour=11)      # 다음 날 첫 평가: 전일 마지막 값 대비 -4,100원
    assert out.check.day_start == 200_000.0
    assert out.reasons == (risk.DAILY_LOSS,)


def test_day_start_is_not_moved_by_later_evaluations_of_the_same_day(conn):
    _eval(conn, 200_000.0, day=2)
    _eval(conn, 199_000.0, day=3, hour=11)
    out = _eval(conn, 198_000.0, day=3, hour=14)
    assert out.check.day_start == 200_000.0
    assert out.check.daily_loss_krw == pytest.approx(2_000.0)


# ── 중단 ──────────────────────────────────────────────────

def test_drawdown_breach_halts_with_risk_kind_exactly_once(conn, notes):
    _eval(conn, 205_000.0, daily_loss_limit_pct=50.0)
    out = _eval(conn, 195_000.0, daily_loss_limit_pct=50.0)         # 낙폭 10,000원 = 한도
    assert out.reasons == (risk.DRAWDOWN,) and out.halted_now
    assert reconcile.halt_kind(conn) == "RISK"
    assert reconcile.halt_reason(conn).startswith("[손실 한도]")
    assert db.get_kv(conn, risk.KV_LOSS_HALT) == "1"
    again = _eval(conn, 195_000.0, daily_loss_limit_pct=50.0)       # 같은 사유가 계속돼도 이력·알림은 한 번
    assert again.reasons == (risk.DRAWDOWN,) and not again.halted_now
    assert len(db.list_halt_events(conn)) == 1 and len(notes) == 1


def test_unconfirmed_realized_pnl_halts_with_risk_kind(conn):
    _eval(conn, CAP, unknown=0)
    out = _eval(conn, CAP, unknown=1)
    assert out.reasons == (risk.PNL_UNKNOWN,) and out.halted_now
    assert reconcile.halt_kind(conn) == "RISK"
    assert "손익을 계산하지 못한" in reconcile.halt_reason(conn)
    assert _baseline(conn)["unknown_ack"] == 0


def test_loss_breach_during_a_state_halt_is_not_marked_as_a_loss_halt(conn):
    _eval(conn, 205_000.0, daily_loss_limit_pct=50.0)
    reconcile.halt_trading(conn, "불일치", "STATE")
    out = _eval(conn, 195_000.0, daily_loss_limit_pct=50.0)
    assert out.reasons == (risk.DRAWDOWN,) and not out.halted_now
    assert db.get_kv(conn, risk.KV_LOSS_HALT) in (None, "0")
    assert reconcile.halt_kind(conn) == "STATE"
    assert len(db.list_halt_events(conn)) == 1


def test_halt_failure_propagates_but_the_baseline_is_already_saved(conn, monkeypatch):
    _eval(conn, 205_000.0, daily_loss_limit_pct=50.0)
    real = reconcile.halt_trading

    def boom(*args, **kwargs):
        raise RuntimeError("db locked (test)")

    monkeypatch.setattr(reconcile, "halt_trading", boom)
    with pytest.raises(RuntimeError):
        _eval(conn, 195_000.0, daily_loss_limit_pct=50.0)
    assert _baseline(conn)["last_equity"] == 195_000.0 and _baseline(conn)["peak"] == 205_000.0
    monkeypatch.setattr(reconcile, "halt_trading", real)
    assert _eval(conn, 195_000.0, daily_loss_limit_pct=50.0).halted_now      # 다음 평가가 다시 감지한다


# ── 재개 ──────────────────────────────────────────────────

def test_resuming_a_loss_halt_rebaselines_at_the_next_evaluation(conn):
    _eval(conn, 205_000.0, daily_loss_limit_pct=50.0)
    _eval(conn, 195_000.0, daily_loss_limit_pct=50.0)
    assert reconcile.is_trading_halted(conn)
    assert risk.main(["resume", "--reason", "확인함"], conn=conn) == 0
    assert db.get_kv(conn, risk.KV_LOSS_RESET) == "1" and db.get_kv(conn, risk.KV_LOSS_HALT) == "0"
    out = _eval(conn, 195_000.0, daily_loss_limit_pct=50.0)
    assert out.rebaselined and out.reasons == ()
    assert out.check.peak == 195_000.0 and out.check.day_start == 195_000.0
    assert db.get_kv(conn, risk.KV_LOSS_RESET) == "0"
    assert not reconcile.is_trading_halted(conn)


def test_rebaseline_acknowledges_the_unconfirmed_pnl_count(conn):
    _eval(conn, CAP, unknown=0)
    _eval(conn, CAP, unknown=2)
    assert risk.main(["resume", "--reason", "수동 확인"], conn=conn) == 0
    out = _eval(conn, CAP, unknown=2)
    assert out.reasons == () and _baseline(conn)["unknown_ack"] == 2
    assert _eval(conn, CAP, unknown=3).reasons == (risk.PNL_UNKNOWN,)         # 새로 늘면 다시 걸린다


def test_resuming_a_non_loss_halt_does_not_touch_the_baseline(conn):
    _eval(conn, CAP)
    reconcile.halt_trading(conn, "불일치", "STATE")
    assert risk.main(["resume", "--reason", "정리함"], conn=conn) == 0
    assert db.get_kv(conn, risk.KV_LOSS_RESET) in (None, "0")


# ── 기준선 손상 ───────────────────────────────────────────

@pytest.mark.parametrize("raw", ["not json", "[]", '{"peak": 1}'], ids=["garbage", "list", "missing_keys"])
def test_corrupt_baseline_halts_and_keeps_the_evidence(conn, raw):
    db.set_kv(conn, risk.KV_LOSS_BASELINE, raw)
    out = _eval(conn, CAP)
    assert out.reasons == (risk.BASELINE_CORRUPT,) and out.halted_now and out.check is None
    assert reconcile.halt_kind(conn) == "RISK"
    assert db.get_kv(conn, risk.KV_LOSS_BASELINE) == raw
    assert db.latest_equity(conn) is None


def test_corrupt_baseline_is_rebuilt_after_a_manual_resume(conn):
    db.set_kv(conn, risk.KV_LOSS_BASELINE, "not json")
    _eval(conn, CAP)
    assert risk.main(["resume", "--reason", "기준선 손상 확인"], conn=conn) == 0
    out = _eval(conn, CAP)
    assert out.rebaselined and out.reasons == ()
    assert risk._parse_baseline(db.get_kv(conn, risk.KV_LOSS_BASELINE))["peak"] == CAP


# ── 입력 검증 ─────────────────────────────────────────────

def test_naive_time_is_rejected_before_anything_is_written(conn):
    with pytest.raises(ValueError):
        _eval(conn, CAP, now=datetime(2026, 11, 2, 11, 0))
    assert db.get_kv(conn, risk.KV_LOSS_BASELINE) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf")], ids=["nan", "inf"])
def test_non_finite_equity_is_rejected_before_anything_is_written(conn, bad):
    with pytest.raises(ValueError):
        _eval(conn, bad)
    assert db.get_kv(conn, risk.KV_LOSS_BASELINE) is None and db.latest_equity(conn) is None


# ── 상태 출력 ─────────────────────────────────────────────

def test_status_shows_the_loss_baseline(conn, capsys):
    _eval(conn, CAP)
    assert risk.main(["status"], conn=conn) == 0
    out = capsys.readouterr().out
    assert "손실 기준선" in out and "200,000" in out


def test_status_before_any_evaluation_says_so(conn, capsys):
    assert risk.main(["status"], conn=conn) == 0
    assert "아직 없음" in capsys.readouterr().out
