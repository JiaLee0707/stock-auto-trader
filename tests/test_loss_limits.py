"""
T09b-2a: 손실 한도 — 시작 시 설정 검증과 순수 평가 함수.

  - 기준 자본이 없거나 한도·중단 동작 설정이 위험하면 어떤 API 호출보다 먼저 시작을 거부한다
  - 한도 판정은 CAPITAL_CAP_KRW 기준이고, 한도와 정확히 같으면 걸린다
  - 고점은 내려가지 않는다
실제 API 는 호출하지 않는다.
"""
import logging

import pytest

import config
import main
import risk

CAP = 200_000.0


def _cfg(**over):
    base = dict(capital_cap_krw=CAP, max_drawdown_pct=10.0, daily_loss_limit_pct=3.0,
                halt_action="hold", overseas_enabled=False)
    base.update(over)
    return config.risk_config_error(**base)


def _set_valid(monkeypatch):
    monkeypatch.setattr(config, "CAPITAL_CAP_KRW", CAP)
    monkeypatch.setattr(config, "MAX_DRAWDOWN_PCT", 10.0)
    monkeypatch.setattr(config, "DAILY_LOSS_LIMIT_PCT", 3.0)
    monkeypatch.setattr(config, "HALT_ACTION", "hold")
    monkeypatch.setattr(config, "IS_OVERSEAS_ENABLED", False)


# ── 시작 시 설정 검증 ─────────────────────────────────────

def test_valid_risk_config_has_no_error():
    assert _cfg() is None


_BAD = {
    "cap_zero": {"capital_cap_krw": 0.0},
    "cap_negative": {"capital_cap_krw": -1.0},
    "cap_nan": {"capital_cap_krw": float("nan")},
    "drawdown_zero": {"max_drawdown_pct": 0.0},
    "drawdown_over_100": {"max_drawdown_pct": 100.1},
    "drawdown_nan": {"max_drawdown_pct": float("nan")},
    "daily_zero": {"daily_loss_limit_pct": 0.0},
    "daily_negative": {"daily_loss_limit_pct": -3.0},
    "halt_liquidate": {"halt_action": "liquidate"},
    "halt_unknown": {"halt_action": "close_all"},
    "halt_empty": {"halt_action": ""},
    "overseas_on": {"overseas_enabled": True},
}


@pytest.mark.parametrize("over", list(_BAD.values()), ids=list(_BAD))
def test_invalid_risk_config_is_rejected(over):
    error = _cfg(**over)
    assert isinstance(error, str) and error


def test_error_messages_name_the_problem():
    assert "CAPITAL_CAP_KRW" in _cfg(capital_cap_krw=0.0)
    assert "MAX_DRAWDOWN_PCT" in _cfg(max_drawdown_pct=0.0)
    assert "구현되지 않았" in _cfg(halt_action="liquidate")
    assert "IS_OVERSEAS_ENABLED" in _cfg(overseas_enabled=True)


def test_enforce_risk_config_exits_with_a_critical_log(monkeypatch, caplog):
    _set_valid(monkeypatch)
    monkeypatch.setattr(config, "CAPITAL_CAP_KRW", 0.0)
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        with pytest.raises(SystemExit) as exc:
            config.enforce_risk_config()
    assert exc.value.code == 1
    assert "CAPITAL_CAP_KRW" in caplog.text


def test_enforce_risk_config_passes_for_valid_settings(monkeypatch):
    _set_valid(monkeypatch)
    assert config.enforce_risk_config() is None


def test_main_refuses_to_start_before_any_api_call(monkeypatch):
    """main() 이 설정 검증을 API 호출 단계(자본 점검·첫 사이클)보다 먼저 한다."""
    _set_valid(monkeypatch)
    monkeypatch.setattr(config, "CAPITAL_CAP_KRW", 0.0)
    monkeypatch.setattr(config, "enforce_trading_mode", lambda: None)

    def reached(*args, **kwargs):
        raise AssertionError("설정 검증 없이 API 호출 단계에 도달했다")

    monkeypatch.setattr(main, "log_capital_summary", reached)
    monkeypatch.setattr(main, "job", reached)
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 1


# ── 순수 평가 함수 ────────────────────────────────────────

def _check(**over):
    base = dict(equity=CAP, peak=CAP, day_start=CAP, capital_cap_krw=CAP,
                max_drawdown_pct=10.0, daily_loss_limit_pct=3.0)
    base.update(over)
    return risk.evaluate_loss_limits(**base)


def test_no_loss_updates_peak_and_does_not_breach():
    c = _check(equity=205_000.0)
    assert c.peak == 205_000.0
    assert c.breaches == () and not c.breached
    assert c.drawdown_krw == 0.0 and c.daily_loss_krw == 0.0


def test_peak_never_decreases():
    c = _check(equity=190_000.0, peak=210_000.0, day_start=190_000.0)
    assert c.peak == 210_000.0
    assert c.drawdown_krw == pytest.approx(20_000.0)


def test_drawdown_exactly_at_limit_breaches():
    c = _check(equity=180_000.0, peak=200_000.0, day_start=180_000.0)      # 낙폭 20,000원 = 10%
    assert c.breaches == (risk.DRAWDOWN,)


def test_one_won_below_drawdown_limit_does_not_breach():
    c = _check(equity=180_001.0, peak=200_000.0, day_start=180_001.0)
    assert c.breaches == ()


def test_daily_loss_exactly_at_limit_breaches_only_daily():
    c = _check(equity=194_000.0, peak=200_000.0, day_start=200_000.0)      # 일일 손실 6,000원 = 3%
    assert c.breaches == (risk.DAILY_LOSS,)


def test_one_won_below_daily_limit_does_not_breach():
    c = _check(equity=194_001.0, peak=200_000.0, day_start=200_000.0)
    assert c.breaches == ()


def test_both_limits_are_listed_drawdown_first():
    c = _check(equity=150_000.0, peak=200_000.0, day_start=200_000.0)
    assert c.breaches == (risk.DRAWDOWN, risk.DAILY_LOSS)


def test_a_gain_today_is_not_reported_as_a_loss_but_drawdown_still_counts():
    c = _check(equity=195_000.0, peak=200_000.0, day_start=190_000.0)
    assert c.daily_loss_krw == 0.0
    assert c.drawdown_krw == pytest.approx(5_000.0)
    assert c.breaches == ()


def test_percentages_are_relative_to_the_capital_cap():
    c = _check(equity=190_000.0, peak=200_000.0, day_start=200_000.0)
    assert c.drawdown_pct == pytest.approx(5.0)
    assert c.daily_loss_pct == pytest.approx(5.0)


def test_five_stop_losses_on_200k_reach_the_daily_limit():
    """20만원·2주(23,220원)·손절 -3% → 1회 약 1,393원. 4회는 한도 아래, 5회째에 일일 3%(6,000원)에 닿는다."""
    one = 23_220 * 2 * 0.03
    four = _check(equity=CAP - 4 * one)
    five = _check(equity=CAP - 5 * one)
    assert four.breaches == ()
    assert five.breaches == (risk.DAILY_LOSS,)


@pytest.mark.parametrize("over", [
    {"capital_cap_krw": 0.0},
    {"capital_cap_krw": float("nan")},
    {"max_drawdown_pct": 0.0},
    {"daily_loss_limit_pct": 101.0},
    {"equity": float("nan")},
    {"equity": float("inf")},
], ids=["cap_zero", "cap_nan", "drawdown_zero", "daily_over_100", "equity_nan", "equity_inf"])
def test_invalid_inputs_raise_instead_of_guessing(over):
    with pytest.raises(ValueError):
        _check(**over)
