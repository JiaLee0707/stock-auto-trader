"""
T06 청산 규칙 테스트 — 순수 함수 `exit_rules.evaluate_exit` 의 경계값 고정.

T06 은 부분 익절(2/4/6% 단계별 분할)을 폐기하고 다음 규칙으로 바꿨다.
  손절 > 시간청산 > 목표가 > 트레일링 > 과매수  (한 사이클에 사유는 **하나**)

여기서 고정하는 것:
  1. 경계값 — 임계값 **정확히**에서 발동하는가 (`<=` / `>=` 방향)
  2. 갭 — 임계값을 크게 넘어서도(갭 하락) 판단이 서는가
  3. 우선순위 — 손절과 시간청산이 동시에 성립하면 손절이 이긴다
  4. 트레일링 — 활성선 전/후, 고점 없는 경우
  5. ATR 옵션 on/off 와 **더 넓은 쪽** 선택
  6. I/O 가 없다는 것 — DB·API 를 부르지 않고 dict 만으로 판단한다
  7. 보유 거래일을 모를 때 시간청산이 **발동하지 않는다**(0 으로 대체하지 않음)
  8. `trader` 의 배선 — 사유가 `orders.reason` 에 남고, 주문 유형이 사유별로 다르다
  9. 부분 익절 코드·설정이 실제로 사라졌는가

실제 API 를 호출하지 않는다. `main.py` 도 실행하지 않는다.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import config
import db
import exit_rules as er
import kis_domestic as kd
import kis_overseas as kis
import state
import trader

KST = ZoneInfo("Asia/Seoul")


def _params(**overrides) -> dict:
    """기본 파라미터. 테스트가 전역 설정과 무관하게 경계값을 검증할 수 있게 한다."""
    base = {
        "take_profit_pct": 3.0,
        "stop_loss_pct": -3.0,
        "stop_loss_atr_mult": 0.0,
        "max_hold_trading_days": 7,
        "trailing_stop_enabled": False,
        "trailing_activate_pct": 2.0,
        "trailing_gap_pct": 1.0,
        "overbought_exit_enabled": False,
    }
    base.update(overrides)
    return base


def _pos(price, entry=100.0, qty=10, **kw) -> dict:
    """판단용 포지션 dict. `entry_time` 을 주지 않으면 시간청산은 계산되지 않는다."""
    base = {
        "qty": qty,
        "avg_price": entry,
        "current_price": price,
        "entry_time": None,
        "peak_price_since_entry": None,
        # 시장을 주지 않으면 보유 거래일을 셀 수 없어 시간청산이 발동하지 않는다.
        "market": None,
    }
    base.update(kw)
    return base


# 기준 시각: 2026-10-23(금) 11:00 KST — 거래일 계산 테스트에서 쓴다.
NOW = dt.datetime(2026, 10, 23, 11, 0, tzinfo=KST)


# ── 1. 손절 경계값 ───────────────────────────────────────

def test_stop_loss_triggers_exactly_at_threshold():
    """정확히 -3.00% 에서 발동한다 (`<=` 방향)."""
    sig = er.evaluate_exit(_pos(97.0), None, _params(), NOW)
    assert sig is not None and sig.reason == er.STOP_LOSS


def test_stop_loss_does_not_trigger_just_above_threshold():
    """-2.99% 에서는 발동하지 않는다 — 경계가 한 푼이라도 어긋나면 안 된다."""
    assert er.evaluate_exit(_pos(97.01), None, _params(), NOW) is None


def test_stop_loss_triggers_on_gap_down_far_below_threshold():
    """
    갭 하락(-10%)에서도 판단은 선다.

    **주의**: 판단이 선다는 것과 그 가격에 체결된다는 것은 다르다. 갭으로 시가가
    손절선 아래에서 시작하면 손절선 가격에는 팔리지 않는다(모듈 docstring 참고).
    """
    sig = er.evaluate_exit(_pos(90.0), None, _params(), NOW)
    assert sig is not None and sig.reason == er.STOP_LOSS
    assert "-10.00%" in sig.detail


def test_disabled_stop_loss_never_triggers():
    """`stop_loss_pct` 가 0 이면 비활성 — 큰 손실에도 팔지 않는다."""
    assert er.evaluate_exit(_pos(50.0), None, _params(stop_loss_pct=0.0), NOW) is None


# ── 2. 목표가 경계값 ─────────────────────────────────────

def test_take_profit_triggers_exactly_at_threshold():
    """정확히 +3.00% 에서 전량 청산 판단이 선다 (`>=` 방향)."""
    sig = er.evaluate_exit(_pos(103.0), None, _params(), NOW)
    assert sig is not None and sig.reason == er.TAKE_PROFIT


def test_take_profit_does_not_trigger_just_below_threshold():
    assert er.evaluate_exit(_pos(102.99), None, _params(), NOW) is None


def test_take_profit_triggers_on_gap_up():
    sig = er.evaluate_exit(_pos(110.0), None, _params(), NOW)
    assert sig is not None and sig.reason == er.TAKE_PROFIT


# ── 3. 우선순위 ──────────────────────────────────────────

def test_stop_loss_beats_time_exit():
    """
    손절과 시간청산이 **동시에** 성립하면 손절이 이긴다.

    진입 2026-10-12(월), 기준 2026-10-23(금) → 거래일 9일 ≥ 7일.
    가격은 -10% 라 손절도 성립한다. 사유는 하나뿐이므로 손절이어야 한다.
    """
    sig = er.evaluate_exit(
        _pos(90.0, entry_time="2026-10-12T10:00:00+09:00", market="overseas"),
        None, _params(), NOW,
    )
    assert sig is not None and sig.reason == er.STOP_LOSS


def test_stop_loss_beats_take_profit_is_impossible_but_priority_is_total():
    """우선순위 표가 모든 사유를 덮는다 — 사유가 하나뿐임을 코드로 보장한다."""
    assert set(er.PRIORITY) == {
        er.STOP_LOSS, er.TIME_EXIT, er.TAKE_PROFIT, er.TRAILING, er.OVERBOUGHT,
    }
    assert er.PRIORITY[er.STOP_LOSS] < er.PRIORITY[er.TIME_EXIT] \
        < er.PRIORITY[er.TAKE_PROFIT] < er.PRIORITY[er.TRAILING] \
        < er.PRIORITY[er.OVERBOUGHT]


def test_unknown_reason_is_rejected():
    """모르는 사유를 만들면 즉시 예외 — 오타가 조용히 새 사유가 되지 않는다."""
    with pytest.raises(ValueError):
        er.ExitSignal("SOMETHING_ELSE", "설명")


def test_only_one_signal_is_returned_per_cycle():
    """반환은 단일 신호이거나 None 이다 — 리스트가 아니다(사유 하나 보장)."""
    sig = er.evaluate_exit(_pos(90.0), None, _params(), NOW)
    assert isinstance(sig, er.ExitSignal)
    assert not isinstance(sig, (list, tuple))


# ── 4. 시간청산 (거래일) ─────────────────────────────────

def test_time_exit_triggers_at_max_hold_days():
    """
    보유 7거래일이면 시간청산한다 (기준일 **미포함**, 진입일 포함).

    진입 2026-10-14(수) ~ 기준 2026-10-23(금): 14,15,16,19,20,21,22 = 7일
    (17·18 은 주말, 23 일은 기준일이라 제외).
    """
    assert er.trading_days_held(
        _pos(100.0, entry_time="2026-10-14T10:00:00+09:00", market="overseas"), NOW
    ) == 7
    sig = er.evaluate_exit(
        _pos(100.0, entry_time="2026-10-14T10:00:00+09:00", market="overseas"),
        None, _params(max_hold_trading_days=7), NOW,
    )
    assert sig is not None and sig.reason == er.TIME_EXIT


def test_time_exit_does_not_trigger_one_day_early():
    """7거래일 상한에서 1거래일 모자라면 팔지 않는다."""
    sig = er.evaluate_exit(
        _pos(100.0, entry_time="2026-10-14T10:00:00+09:00", market="overseas"),
        None, _params(max_hold_trading_days=8), NOW,
    )
    assert sig is None


def test_weekend_is_not_counted_as_trading_days():
    """금요일 진입 → 월요일은 1거래일이다. 주말을 세면 조기에 팔린다."""
    monday = dt.datetime(2026, 10, 19, 11, 0, tzinfo=KST)
    sig = er.evaluate_exit(
        _pos(100.0, entry_time="2026-10-16T10:00:00+09:00", market="overseas"),
        None, _params(max_hold_trading_days=2), monday,
    )
    assert sig is None, "금→월은 1거래일인데 2일로 세어졌다"


def test_missing_entry_time_does_not_trigger_time_exit():
    """
    진입 시각을 모르면 시간청산을 **발동시키지 않는다**. 0 으로 대체하면
    "오늘 샀다"가 되어 시간청산이 영원히 안 걸린다 — 추측하지 않는다.
    """
    sig = er.evaluate_exit(
        _pos(100.0, entry_time=None, market="overseas"),
        None, _params(max_hold_trading_days=1), NOW,
    )
    assert sig is None


def test_naive_entry_time_is_refused():
    """naive 진입 시각은 KST 로 승격하지 않는다(절대 규칙 9)."""
    assert er.trading_days_held(
        _pos(100.0, entry_time="2026-10-14T10:00:00", market="overseas"), NOW
    ) is None


def test_unknown_market_does_not_trigger_time_exit():
    """시장을 모르면 거래일을 셀 수 없다 — 다른 시장을 가정하지 않는다."""
    assert er.trading_days_held(
        _pos(100.0, entry_time="2026-10-14T10:00:00+09:00", market="krx"), NOW
    ) is None


def test_disabled_max_hold_never_triggers():
    sig = er.evaluate_exit(
        _pos(100.0, entry_time="2026-01-02T10:00:00+09:00", market="overseas"),
        None, _params(max_hold_trading_days=0), NOW,
    )
    assert sig is None


# ── 5. 트레일링 스톱 ─────────────────────────────────────

def test_trailing_disabled_by_default_does_not_trigger():
    """고점 대비 크게 밀려도 기본은 꺼져 있어 팔지 않는다."""
    assert er.evaluate_exit(
        _pos(101.0, peak_price_since_entry=110.0), None, _params(), NOW
    ) is None


def test_trailing_not_active_before_threshold():
    """
    고점이 활성선(+2%)에 못 미치면 트레일링은 켜지지 않는다.

    고점 101.9(+1.9%) 까지 올랐다가 100.0 으로 밀려도(고점 대비 -1.9%),
    활성선 미달이라 발동하지 않는다.
    """
    sig = er.evaluate_exit(
        _pos(100.0, peak_price_since_entry=101.9), None,
        _params(trailing_stop_enabled=True), NOW,
    )
    assert sig is None


def test_trailing_activates_exactly_at_threshold_and_gap():
    """
    활성선(+2%)과 이탈 기준(-1%)이 **정확히** 성립하면 발동한다.

    고점 102.0(+2.00%), 현재 100.98(고점 대비 **정확히** -1.00%).
    목표가(+3%)·손절(-3%)에는 닿지 않으므로 트레일링만 성립한다.
    """
    sig = er.evaluate_exit(
        _pos(100.98, peak_price_since_entry=102.0), None,
        _params(trailing_stop_enabled=True), NOW,
    )
    assert sig is not None and sig.reason == er.TRAILING


def test_trailing_does_not_trigger_before_gap_is_hit():
    """고점 대비 -0.99% 는 이탈 기준(-1%) 미달이라 발동하지 않는다."""
    assert er.evaluate_exit(
        _pos(101.0, peak_price_since_entry=102.0), None,
        _params(trailing_stop_enabled=True), NOW,
    ) is None


def test_trailing_ignored_without_peak():
    """고점을 모르면 트레일링을 판단하지 않는다(추측하지 않는다)."""
    assert er.evaluate_exit(
        _pos(101.0, peak_price_since_entry=None), None,
        _params(trailing_stop_enabled=True), NOW,
    ) is None


def test_trailing_loses_to_take_profit():
    """목표가(+3%)에 닿으면 트레일링보다 목표가가 먼저다(우선순위)."""
    sig = er.evaluate_exit(
        _pos(103.0, peak_price_since_entry=110.0), None,
        _params(trailing_stop_enabled=True, trailing_activate_pct=2.0,
                trailing_gap_pct=1.0),
        NOW,
    )
    assert sig is not None and sig.reason == er.TAKE_PROFIT


# ── 6. 과매수 청산 ───────────────────────────────────────

def test_overbought_disabled_by_default():
    assert er.evaluate_exit(
        _pos(101.0), {"atr": None, "overbought": True}, _params(), NOW
    ) is None


def test_overbought_triggers_when_enabled_and_profitable():
    sig = er.evaluate_exit(
        _pos(101.0), {"atr": None, "overbought": True},
        _params(overbought_exit_enabled=True), NOW,
    )
    assert sig is not None and sig.reason == er.OVERBOUGHT


def test_overbought_ignored_when_not_profitable():
    """손실 중 과매수는 손절과 구분되지 않으므로 청산 사유로 인정하지 않는다."""
    assert er.evaluate_exit(
        _pos(99.0), {"atr": None, "overbought": True},
        _params(overbought_exit_enabled=True), NOW,
    ) is None


def test_overbought_loses_to_trailing():
    """둘 다 성립하면 트레일링이 이긴다(우선순위)."""
    sig = er.evaluate_exit(
        _pos(100.98, peak_price_since_entry=102.0),
        {"atr": None, "overbought": True},
        _params(trailing_stop_enabled=True, overbought_exit_enabled=True), NOW,
    )
    assert sig is not None and sig.reason == er.TRAILING


# ── 7. ATR 손절 옵션 ─────────────────────────────────────

def test_atr_stop_loss_converts_price_to_percent():
    """ATR 은 가격 단위다 — 진입가로 나눠 % 로 바꾼다."""
    assert er.atr_stop_loss_pct(100.0, 2.0, 2.0) == pytest.approx(-4.0)


def test_atr_option_off_uses_fixed_percent():
    """배수가 0 이면 ATR 을 줘도 고정 % 를 쓴다."""
    assert er.effective_stop_loss_pct(100.0, 5.0, -3.0, 0.0) == -3.0


def test_atr_option_picks_wider_of_two():
    """
    고정 -3% / ATR -4% → **더 넓은** -4% 를 쓴다.
    변동성이 큰 종목에서 정상적인 흔들림에 손절당하는 것을 줄이기 위해서다.
    """
    assert er.effective_stop_loss_pct(100.0, 2.0, -3.0, 2.0) == pytest.approx(-4.0)


def test_atr_option_keeps_fixed_when_atr_is_narrower():
    """ATR -1% 가 고정 -3% 보다 좁으면 고정을 유지한다(손절을 좁히지 않는다)."""
    assert er.effective_stop_loss_pct(100.0, 0.5, -3.0, 2.0) == pytest.approx(-3.0)


def test_atr_option_with_missing_atr_falls_back_to_fixed(caplog):
    """
    배수를 켰는데 ATR 을 계산할 수 없으면 고정 % 로 되돌아가되 **경고를 남긴다**
    (설정을 켰는데 동작이 다른 것을 모르면 안 된다).
    """
    with caplog.at_level("WARNING"):
        result = er.effective_stop_loss_pct(100.0, None, -3.0, 2.0)
    assert result == -3.0
    assert any("ATR" in r.message for r in caplog.records)


def test_atr_stop_loss_changes_widening_threshold_in_evaluate():
    """
    배선 확인: ATR 옵션을 켜면 -3% 에서 발동하지 않고 ATR 손절선(-6%)까지 버틴다.
    """
    params = _params(stop_loss_atr_mult=3.0)
    # ATR 2.0 / 진입가 100 → -6%
    assert er.evaluate_exit(_pos(97.0), {"atr": 2.0}, params, NOW) is None
    sig = er.evaluate_exit(_pos(94.0), {"atr": 2.0}, params, NOW)
    assert sig is not None and sig.reason == er.STOP_LOSS


# ── 8. 순수성 (I/O 없음) ─────────────────────────────────

def test_evaluate_exit_does_no_io(monkeypatch):
    """
    판단 함수는 I/O 를 하지 않는다. DB·API·상태를 모두 막아도 동작해야 한다.
    (I/O 가 섞여 있으면 경계값을 테스트로 고정할 수 없다.)
    """
    def boom(*args, **kwargs):
        raise AssertionError("순수 함수가 I/O 를 시도했다")

    monkeypatch.setattr(db, "get_position", boom)
    monkeypatch.setattr(db, "open_db", boom)
    monkeypatch.setattr(kis, "get_balance", boom)
    monkeypatch.setattr(kd, "get_domestic_balance", boom)
    monkeypatch.setattr(state, "get_bot_state", boom)

    sig = er.evaluate_exit(_pos(90.0), None, _params(), NOW)
    assert sig is not None and sig.reason == er.STOP_LOSS


def test_no_position_or_zero_qty_returns_none():
    assert er.evaluate_exit(None, None, _params(), NOW) is None
    assert er.evaluate_exit({}, None, _params(), NOW) is None
    assert er.evaluate_exit(_pos(90.0, qty=0), None, _params(), NOW) is None


def test_unknown_entry_price_returns_none():
    """단가를 모르면 수익률을 계산할 수 없다 — 추측해서 팔지 않는다."""
    assert er.evaluate_exit(_pos(90.0, entry=0.0), None, _params(), NOW) is None


def test_params_from_config_reads_every_setting():
    """`params_from_config()` 가 실제 설정 키를 모두 담는다(키 오타 방지)."""
    p = er.params_from_config()
    assert set(p) == set(_params())


# ── 9. 부분 익절 제거 확인 ───────────────────────────────

@pytest.mark.parametrize("name", [
    "TAKE_PROFIT_STEPS", "TAKE_PROFIT_THRESHOLD", "STOP_LOSS_THRESHOLD",
])
def test_partial_profit_settings_are_gone(name):
    """부분 익절 관련 설정이 config 에서 사라졌다."""
    assert not hasattr(config, name), f"{name} 이 아직 남아 있다"


@pytest.mark.parametrize("name", ["profit_stage", "set_profit_stage"])
def test_profit_stage_api_is_gone(name):
    """`state` 의 익절 단계 API 가 사라졌다 — 청산 판단이 그것을 쓰지 않는다."""
    assert not hasattr(state.BotState, name), f"BotState.{name} 이 아직 남아 있다"


def test_trader_has_no_partial_sell_code():
    """
    `trader` 소스에 부분 익절 **식별자**가 남아 있지 않다 (grep 대신 코드로 고정).

    문장("부분 익절을 폐기했다")은 남아도 된다 — 그건 이력 설명이다.
    찾는 것은 실제로 동작에 쓰이는 이름들이다.
    """
    import inspect
    src = inspect.getsource(trader)
    for token in ("TAKE_PROFIT_STEPS", "TAKE_PROFIT_THRESHOLD", "STOP_LOSS_THRESHOLD",
                  "profit_stage", "sold_any", "block_pct"):
        assert token not in src, f"trader.py 에 아직 남아 있다: {token}"


# ── 10. trader 배선: 사유·수량·주문 유형 ─────────────────

@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def bot(conn):
    st = state.reset_bot_state(conn)
    yield st
    state.reset_bot_state(None)


def _open(conn, ticker="QQQ", market="overseas", qty=10, avg=100.0, **kw):
    kw.setdefault("status", "OPEN")
    db.upsert_position(conn, ticker, market, qty=qty, avg_price=avg, **kw)


def test_sell_reason_is_recorded_on_order(conn, bot, monkeypatch):
    """청산 사유가 `orders.reason` 에 그대로 남는다 (요구사항 4)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, avg=100.0)
    captured = {}

    def submit_sell(reason, qty):
        captured["reason"] = reason
        captured["qty"] = qty
        return trader._record_order_pending(
            conn, market="overseas", ticker="QQQ", side="SELL", qty=qty,
            price=95.0, order_type="LIMIT", raw_request=None, reason=reason,
        )[0]

    trader._check_sell("해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
                       market="overseas", avg_price=100.0,
                       current_price=95.0, submit_sell=submit_sell)

    assert captured["reason"] == er.STOP_LOSS
    assert db.list_orders(conn)[0]["reason"] == er.STOP_LOSS


@pytest.mark.parametrize("price,profit,expected", [
    (95.0, -5.0, "STOP_LOSS"),
    (104.0, 4.0, "TAKE_PROFIT"),
])
def test_reason_strings_match_constants(conn, bot, monkeypatch, price, profit, expected):
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn)
    captured = []
    trader._check_sell("해외", "QQQ", 10, profit_rate=profit, sell_fn=None,
                       market="overseas", avg_price=100.0, current_price=price,
                       submit_sell=lambda r, q: captured.append(r))
    assert captured == [expected]


def test_full_exit_quantity_when_no_broker_recheck(conn, bot, monkeypatch):
    """잔고 재확인 콜러블이 없으면 DB 수량을 그대로 전량 매도한다(하위 호환)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10)
    captured = []
    trader._check_sell("해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
                       market="overseas", avg_price=100.0, current_price=95.0,
                       submit_sell=lambda r, q: captured.append(q))
    assert captured == [10], "부분 매도가 났다 — 청산은 전량이어야 한다"


def test_broker_quantity_smaller_reduces_order(conn, bot, monkeypatch):
    """브로커 보유가 DB 보다 적으면 **브로커 수량만** 매도한다 (요구사항 5)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10)
    captured = []
    trader._check_sell(
        "해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
        market="overseas", avg_price=100.0, current_price=95.0,
        submit_sell=lambda r, q: captured.append(q),
        refresh_balance=lambda: {"quantity": 4},
    )
    assert captured == [4], "보유하지 않은 수량으로 주문했다"


def test_broker_quantity_larger_sells_only_db_quantity(conn, bot, monkeypatch):
    """봇이 모르는 수량이 있으면 DB 가 아는 만큼만 팔고 reconcile 로 넘긴다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10)
    captured = []
    checked = []
    monkeypatch.setattr(trader, "_trigger_reconcile_check", checked.append)
    trader._check_sell(
        "해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
        market="overseas", avg_price=100.0, current_price=95.0,
        submit_sell=lambda r, q: captured.append(q),
        refresh_balance=lambda: {"quantity": 25},
    )
    assert captured == [10], "봇이 만들지 않은 보유까지 팔았다"
    assert checked, "불일치를 reconcile 로 넘기지 않았다"


def test_broker_zero_quantity_blocks_order(conn, bot, monkeypatch):
    """브로커 보유가 0 이면 주문하지 않는다 — 보유하지 않은 수량을 팔 수 없다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10)
    captured = []
    monkeypatch.setattr(trader, "_trigger_reconcile_check", lambda what: None)
    trader._check_sell(
        "해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
        market="overseas", avg_price=100.0, current_price=95.0,
        submit_sell=lambda r, q: captured.append(q),
        refresh_balance=lambda: {"quantity": 0},
    )
    assert captured == [], "보유 0 인데 매도 주문이 나갔다"


def test_balance_recheck_failure_blocks_order(conn, bot, monkeypatch):
    """잔고 재조회가 실패하면 모르는 채 팔지 않는다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10)
    captured = []

    def boom():
        raise ConnectionError("network")

    trader._check_sell(
        "해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
        market="overseas", avg_price=100.0, current_price=95.0,
        submit_sell=lambda r, q: captured.append(q), refresh_balance=boom,
    )
    assert captured == []


def test_external_position_is_never_sold(conn, bot, monkeypatch):
    """T03 의 EXTERNAL 매도 차단 가드가 그대로 동작한다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10, status="EXTERNAL")
    captured = []
    result = trader._check_sell(
        "해외", "QQQ", 10, profit_rate=-50.0, sell_fn=None,
        market="overseas", avg_price=100.0, current_price=50.0,
        submit_sell=lambda r, q: captured.append(q),
    )
    assert captured == []
    assert result is True


def test_full_exit_allows_reentry_same_day(conn, bot, monkeypatch):
    """전량 청산이면 당일 매수 플래그를 푼다 — 청산은 전량이므로 '부분 보유'가 없다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    _open(conn, qty=10)
    bot.record_buy("QQQ")
    trader._check_sell("해외", "QQQ", 10, profit_rate=-5.0, sell_fn=None,
                       market="overseas", avg_price=100.0, current_price=95.0,
                       submit_sell=lambda r, q: None)
    assert bot.bought_today_for("QQQ") is False


# ── 11. 사유별 주문 유형 (요구사항 2) ────────────────────

@pytest.mark.parametrize("reason,expected", [
    (er.STOP_LOSS, "market"),
    (er.TIME_EXIT, "market"),
    (er.TAKE_PROFIT, "limit"),
    (er.TRAILING, "market"),
    (er.OVERBOUGHT, "market"),
])
def test_exit_order_type_by_reason(reason, expected):
    """손절·시간청산은 체결이 우선이라 시장가, 목표가만 지정가 (기본값)."""
    assert trader._exit_order_type(reason) == expected


def test_unknown_exit_order_type_falls_back_to_market(monkeypatch):
    """설정이 잘못되면 체결이 우선인 시장가로 두고 크게 알린다."""
    monkeypatch.setattr(config, "EXIT_ORDER_TYPE_STOP_LOSS", "weird")
    assert trader._exit_order_type(er.STOP_LOSS) == "market"


def test_order_type_argument_overrides_global_setting(monkeypatch):
    """전역 `ORDER_TYPE` 과 무관하게 주문 단위로 유형을 덮어쓸 수 있다 (T06 핵심)."""
    monkeypatch.setattr(config, "ORDER_TYPE", "limit")
    body = kd.order_body("379800", 3, 23000.0, side="SELL", order_type="market")
    assert body["ORD_DVSN"] == "01"
    assert body["ORD_UNPR"] == "0"

    monkeypatch.setattr(config, "ORDER_TYPE", "market")
    body = kd.order_body("379800", 3, 23000.0, side="SELL", order_type="limit")
    assert body["ORD_DVSN"] == "00"
    assert body["ORD_UNPR"] != "0"


def test_order_type_none_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(config, "ORDER_TYPE", "market")
    assert kd.order_body("379800", 1, 23000.0, side="BUY")["ORD_DVSN"] == "01"


def test_invalid_per_order_type_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "ORDER_TYPE", "limit")
    with pytest.raises(ValueError):
        kd.order_body("379800", 1, 23000.0, side="SELL", order_type="weird")


def test_overseas_market_order_is_not_implemented():
    """
    해외 시장가는 문서가 없어 미구현이다(절대 규칙 4) — 추측 코드표를 만들지 않는다.
    """
    with pytest.raises(NotImplementedError):
        kis.order_body(1, 100.0, order_type="market")


def test_overseas_limit_order_still_builds_body(monkeypatch):
    monkeypatch.setattr(config, "KIS_ACCOUNT_NO", "12345678-01")
    body = kis.order_body(1, 100.0, order_type="limit")
    assert body["ORD_DVSN"] == "00"
