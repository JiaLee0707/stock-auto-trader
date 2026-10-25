"""
T08: `strategy.decide()` — 판단 순수 함수 테스트.

두 종류를 담는다.

1. **특성화 테스트** (`test_characterization_*`)
   T07 까지 `trader._run_cycle` 이 내리던 결정을 표로 고정하고, `decide()` 가
   **같은 결정**을 내는지 확인한다. 확정 봉(요구사항 3) 때문에 달라지는 경우는
   별도 테스트(`test_changed_*`)로 분리해 "무엇이 왜 달라졌는지"를 코드에 남긴다.

2. **순수성 테스트** (`test_purity_*`, `test_determinism_*`)
   I/O·시간 조회가 없고, 입력을 변경하지 않으며, 같은 입력에 같은 결과를 낸다.

## T07 → T08 에서 결정이 **달라지는** 경우 (보고서 3절과 짝)

| # | 상황 | T07 (`trader._run_cycle`) | T08 (`decide`) | 왜 |
|---|---|---|---|---|
| 1 | 지표 설정인데 일봉을 못 얻음 | `fetch_closes` 가 준 값으로 계산 | `HOLD_INDICATOR_UNAVAILABLE` | 확정 봉(`history`)으로 출처를 통일. 조회 실패를 "신호 없음"으로 바꾸지 않는다 |
| 2 | 장중(당일 봉 미확정) | 당일 진행 중인 종가로 RSI·BB 계산 | **전일 확정 종가**로 계산 | 요구사항 3. 같은 날 신호가 뒤집히지 않는다 |
| 3 | 동시 보유 상한 초과 | `sizing` 안에서 걸려 `0주` | 진입 **제한** 단계에서 `MAX_OPEN_POSITIONS` | 사유가 명확해짐. 결정(BUY/HOLD)은 같다 |
| 4 | 추세 필터 켜짐 + SMA 계산 불가 | (기능 없음) | `HOLD_TREND_FILTER` | 신규 기능 — `None` 을 매수 허용으로 보지 않는다 |
| 5 | AI 필터 켜짐 + 점수 없음 | `ai_score` 가 필수 int 인자 | `HOLD_AI_FILTER` | 점수를 모르는 채 사지 않는다 |
| 6 | 매수 간격 기준 시각 | `datetime.now(KST)` (직접 조회) | `snapshot.now` (인자) | 순수 함수 — 시간도 입력이다 |

나머지(RSI 30·볼린저 2σ·AI 하한·일일 한도·간격·수량 계산)는 **그대로**다.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import config
import exit_rules
import history
import models
import sizing
import strategy

KST = ZoneInfo("Asia/Seoul")

# 매수 신호가 나오는 종가 시계열 (RSI 25.5 ≤ 30 AND 종가 95 ≤ BB 하단 100.50).
# `tests/test_sizing.py` 와 같은 값을 쓴다 — 조건 자체는 실제 지표 함수로 통과시킨다.
_BUY_CLOSES = [100 + i * 0.5 for i in range(29)] + [95.0]
# 신호가 나오지 않는 시계열(우상향: RSI 높음, 종가가 상단 근처).
_NO_SIGNAL_CLOSES = [100 + i * 0.5 for i in range(30)]

_TODAY = datetime(2026, 10, 23, 10, 0, tzinfo=KST)


def _bars(closes) -> pd.DataFrame:
    """종가 리스트 → 확정 일봉 DataFrame(과거 → 최신)."""
    rows = []
    base = _TODAY.date() - timedelta(days=len(closes))
    for i, close in enumerate(closes):
        rows.append({
            "date": (base + timedelta(days=i)).isoformat(),
            "open": close, "high": close, "low": close,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows, columns=history.COLUMNS)


def _snapshot(closes=_BUY_CLOSES, price=95.0, now=_TODAY) -> models.MarketSnapshot:
    return models.MarketSnapshot(symbol="QQQ", bars=_bars(closes),
                                 last_price=price, now=now)


def _position(qty=0.0, avg=0.0, entry=None, peak=None) -> models.PositionView:
    return models.PositionView(qty=qty, avg_price=avg, entry_date=entry, peak_price=peak)


def _account(equity=1_000_000.0, cash=1_000_000.0) -> models.AccountView:
    return models.AccountView(equity=equity, cash=cash)


def _params(**over) -> dict:
    """
    테스트용 파라미터.

    **`.env` 값에 의존하지 않도록 사이징 수치를 명시한다** — 개발자 환경에
    `CAPITAL_CAP_KRW` 가 설정돼 있으면 자본이 잘려 수량이 달라진다.
    """
    base = {
        # `decide()` 는 **완전한** 파라미터를 요구한다(빠진 키를 설정에서 채우지 않는다).
        # 그래서 여기서 요구 키를 전부 명시한다 — `.env` 값에 의존하지 않게 하려는 것이기도 하다.
        "use_indicators": True,
        "rsi_period": 14,
        "rsi_oversold": 30.0,
        "rsi_overbought": 70.0,
        "bb_period": 20,
        "bb_num_std": 2.0,
        "market": "overseas",
        "fx_rate": 1.0,
        "capital_cap_krw": 0,
        "ai_filter_enabled": True,
        "ai_score_floor": -50,
        "max_buys_per_day": 2,
        "min_buy_interval_hours": 2.0,
        "entry_trend_filter_enabled": False,
        "entry_trend_sma_period": 200,
        "dip_threshold": -1.5,
        "sizing": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 45.0,
            "max_open_positions": 2,
            "max_total_exposure_pct": 90.0,
        },
        "exit": {
            "take_profit_pct": 3.0,
            "stop_loss_pct": -3.0,
            "stop_loss_atr_mult": 0.0,
            "max_hold_trading_days": 7,
            "trailing_stop_enabled": False,
            "trailing_activate_pct": 2.0,
            "trailing_gap_pct": 1.0,
            "overbought_exit_enabled": False,
        },
    }
    for key, value in over.items():
        if key in ("sizing", "exit"):
            base[key] = dict(base[key], **value)
        else:
            base[key] = value
    return base


# ══ 1. 특성화: T07 결정 표 ═══════════════════════════════
#
# (상황, 기대 action, 기대 reason) — T07 `trader._run_cycle` 의 분기와 1:1 대응한다.
#   매수 신호            : entry_ok(지표) and ai_ok          → BUY
#   신호 없음            : not entry_ok                      → HOLD
#   일일 한도 초과       : buy_count >= MAX_BUYS_PER_DAY     → HOLD
#   매수 간격 미경과     : elapsed < MIN_BUY_INTERVAL_HOURS  → HOLD
#   AI 점수 미달         : ai_score < AI_SCORE_FLOOR         → HOLD
#   자본 부족            : plan.qty < 1                      → HOLD
#   보유 중 청산(손절)   : evaluate_exit STOP_LOSS           → SELL
#   보유 중 청산(목표가) : evaluate_exit TAKE_PROFIT         → SELL
#   HOLD                 : 청산·매수 모두 아님               → HOLD

def test_characterization_buy_signal():
    """매수 신호 + AI 통과 + 자본 충분 → BUY."""
    d = strategy.decide(_snapshot(), _position(), _account(), _params(), models.DayCounters(),
                        ai_score=0)
    assert d.action == models.BUY
    assert d.qty > 0
    # 위험 1% ÷ 손절 3% = 자산의 33% ≈ 3,300주... 이 아니라 자산 100만원 기준
    # 위험예산 1만원 ÷ 손절폭(95×3%=2.85원) = 3,508주, 현금·비중 기준 4,736주 → 3,508주
    assert d.qty == 3508


def test_characterization_no_signal():
    """지표 매수 조건 미충족 → HOLD(NO_SIGNAL)."""
    d = strategy.decide(_snapshot(closes=_NO_SIGNAL_CLOSES, price=115.0), _position(),
                        _account(), _params(), models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_NO_SIGNAL


def test_characterization_daily_limit():
    """당일 매수 한도 도달 → HOLD(DAILY_BUY_LIMIT). 보유·노출을 보지 않는다."""
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(buy_count_today=2), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_DAILY_LIMIT
    assert d.meta["buy_count"] == 2


def test_characterization_buy_interval():
    """직전 매수 후 최소 간격 미경과 → HOLD(BUY_INTERVAL)."""
    d = strategy.decide(
        _snapshot(), _position(), _account(), _params(),
        models.DayCounters(buy_count_today=1,
                           last_buy_time=_TODAY - timedelta(hours=1)),
        ai_score=0,
    )
    assert d.is_hold
    assert d.reason == models.HOLD_BUY_INTERVAL
    assert d.meta["elapsed_hours"] == 1.0


def test_characterization_buy_interval_elapsed_allows_entry():
    """간격을 넘겼으면 진입 판단으로 진행한다(간격 규칙이 과하게 막지 않는지)."""
    d = strategy.decide(
        _snapshot(), _position(), _account(), _params(),
        models.DayCounters(buy_count_today=1,
                           last_buy_time=_TODAY - timedelta(hours=3)),
        ai_score=0,
    )
    assert d.action == models.BUY


def test_characterization_ai_below_floor():
    """AI 점수가 하한 미달 → HOLD(AI_SCORE_FILTER)."""
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(), ai_score=-100)
    assert d.is_hold
    assert d.reason == models.HOLD_AI_FILTER


def test_characterization_ai_at_floor_is_allowed():
    """경계: 점수 == 하한이면 통과한다(T07 의 `>=` 와 같다)."""
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(), ai_score=-50)
    assert d.action == models.BUY


def test_characterization_insufficient_capital():
    """
    자본 부족으로 0주 → HOLD(INSUFFICIENT_CAPITAL). 최소 1주 강제 매수는 없다.

    위험 기준이 0주를 내는 조건을 만든다: 자산 3,000원 × 위험 1% = 30원이고
    손절폭이 95 × 3% = 2.85원이면 10주가 나오므로, **위험 %를 0.01%** 로 낮춰
    0.3원 / 2.85원 → 0주로 만든다. (T07 의 `INSUFFICIENT_CAPITAL` 경로 재현)
    """
    d = strategy.decide(
        _snapshot(), _position(),
        _account(equity=1_000_000.0, cash=1_000_000.0),
        _params(sizing={"risk_per_trade_pct": 0.0001}),  # 위험예산 1원 < 손절폭 2.85원
        models.DayCounters(), ai_score=0,
    )
    assert d.is_hold
    assert d.reason == sizing.INSUFFICIENT_CAPITAL
    assert d.qty == 0
    # "왜 못 사는지"와 "1주에 얼마 필요한지"를 남긴다 — 규칙을 조용히 완화하지 않는다.
    assert d.meta["detail"] == sizing.SizingResult(0, sizing.INSUFFICIENT_CAPITAL, 0.0).message


def test_characterization_zero_risk_pct_blocks_buy():
    """위험 %가 0이면 위험예산이 0이라 0주다(강제 1주 매수 금지)."""
    d = strategy.decide(_snapshot(), _position(), _account(),
                        _params(sizing={"risk_per_trade_pct": 0.0}),
                        models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert d.reason == sizing.INSUFFICIENT_CAPITAL


@pytest.mark.parametrize("avg,price,expected", [
    (100.0, 90.0, exit_rules.STOP_LOSS),       # -10% ≤ -3%
    (100.0, 104.0, exit_rules.TAKE_PROFIT),    # +4% ≥ +3%
])
def test_characterization_exit(avg, price, expected):
    """보유 중 청산 — 사유별로 SELL(전량)."""
    d = strategy.decide(_snapshot(price=price),
                        _position(qty=10.0, avg=avg, entry=_TODAY - timedelta(days=1)),
                        _account(), _params(), models.DayCounters(), ai_score=0)
    assert d.action == models.SELL
    assert d.reason == expected
    assert d.qty == 10.0, "청산은 전량이어야 한다(T06)"


def test_characterization_hold_when_holding_and_no_exit():
    """
    보유 중 청산 사유가 없으면 **진입 제한에서** 멈춘다 — T07 과 같은 결정(HOLD).

    T07: `_check_sell` 이 False 를 돌려주고, 매수 블록에서 `buy_count >= MAX_BUYS_PER_DAY`
    에 걸려 `매수 스킵` 로그를 남기고 끝났다. T08 도 같은 단계에서 멈춘다.
    """
    d = strategy.decide(_snapshot(price=101.0), _position(qty=10.0, avg=100.0), _account(),
                        _params(max_buys_per_day=0), models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_DAILY_LIMIT


def test_characterization_holding_with_buy_limit_remaining_still_buys():
    """
    T07 과 같은 결정: 일일 한도가 남아 있으면 **보유 중에도 추가 매수**가 나간다.

    T07 의 매수 블록은 `avg_price > 0` 이어도 지표 매수신호가 맞으면 진입을 허용했다
    (`entry_ok = signal["buy_signal"]`, 보유 여부와 무관). T08 도 같다 —
    여기서 "이미 보유 중이면 사지 않는다"를 새로 추가하면 동작이 달라진다.
    """
    d = strategy.decide(_snapshot(price=101.0), _position(qty=10.0, avg=100.0), _account(),
                        _params(), models.DayCounters(), ai_score=0)
    assert d.action == models.BUY


def test_characterization_stop_loss_beats_take_profit_signal():
    """우선순위: 보유 중이면 청산이 매수보다 먼저다(진입 제한이 청산을 가리지 않는다)."""
    # 당일 한도를 이미 채웠어도 손절은 나가야 한다.
    d = strategy.decide(_snapshot(price=90.0),
                        _position(qty=10.0, avg=100.0), _account(),
                        _params(), models.DayCounters(buy_count_today=99), ai_score=0)
    assert d.action == models.SELL
    assert d.reason == exit_rules.STOP_LOSS


# ══ 2. 달라지는 경우 (요구사항 3·4) ══════════════════════

def test_changed_indicator_unavailable_holds_buy():
    """
    #1: 지표 설정인데 확정 일봉을 못 얻으면 **매수를 보류**한다.

    T07 은 `fetch_closes` 가 준 값으로 계산했다. T08 은 확정 봉만 쓰므로, 없으면
    하락률 폴백으로 흘러가지 않고 사유를 남긴다(T04 의 "조회 실패 ≠ 신호 없음").
    """
    snapshot = models.MarketSnapshot(symbol="QQQ", bars=pd.DataFrame(columns=history.COLUMNS),
                                    last_price=95.0, now=_TODAY)
    d = strategy.decide(snapshot, _position(avg=0.0), _account(), _params(),
                        models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_INDICATOR_UNAVAILABLE


def test_changed_indicator_unavailable_still_allows_exit():
    """같은 상황에서도 **손절은 나간다** — 지표가 없다고 손실을 방치하지 않는다."""
    snapshot = models.MarketSnapshot(symbol="QQQ", bars=pd.DataFrame(columns=history.COLUMNS),
                                    last_price=90.0, now=_TODAY)
    d = strategy.decide(snapshot, _position(qty=10.0, avg=100.0), _account(), _params(),
                        models.DayCounters(), ai_score=0)
    assert d.action == models.SELL
    assert d.reason == exit_rules.STOP_LOSS


def test_changed_bars_received_false_is_reported_separately():
    """조회 실패(`bars_received=False`)와 캐시 없음은 사유 문자열이 다르다."""
    snapshot = models.MarketSnapshot(symbol="QQQ", bars=pd.DataFrame(columns=history.COLUMNS),
                                    last_price=95.0, now=_TODAY)
    d = strategy.decide(snapshot, _position(), _account(),
                        _params(bars_received=False,
                                bars_unavailable_detail="확정 일봉 조회 실패 — 매수 보류"),
                        models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert "조회 실패" in d.meta["detail"]


def test_changed_max_positions_has_explicit_reason():
    """
    #3: 동시 보유 상한 초과는 진입 단계에서 `MAX_OPEN_POSITIONS` 로 남는다.

    T07 은 `sizing` 안에서 0주가 되어 `MAX_POSITIONS_REACHED` 가 났다. 결정은 같고
    사유가 명확해졌다(`DayCounters.open_positions` 는 자기 자신을 제외한 수다).
    """
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(open_positions=2), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_MAX_POSITIONS


def test_changed_trend_filter_when_enabled():
    """#4 신규: 추세 필터가 켜지고 종가 > SMA 면 통과, 아니면 HOLD."""
    # 우상향 → 종가 > SMA → 지표 매수신호가 없어 HOLD(NO_SIGNAL)
    up = _snapshot(closes=_NO_SIGNAL_CLOSES, price=115.0)
    d = strategy.decide(up, _position(), _account(),
                        _params(entry_trend_filter_enabled=True,
                                entry_trend_sma_period=20),
                        models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_NO_SIGNAL

    # 하락 → 종가 ≤ SMA → 추세 필터에서 걸린다
    down = _snapshot(closes=_BUY_CLOSES, price=95.0)
    d2 = strategy.decide(down, _position(), _account(),
                         _params(entry_trend_filter_enabled=True,
                                 entry_trend_sma_period=20),
                         models.DayCounters(), ai_score=0)
    assert d2.is_hold
    assert d2.reason == models.HOLD_NO_SIGNAL


def test_changed_trend_filter_unavailable_blocks_buy():
    """
    #4 핵심: 추세 필터가 켜졌는데 SMA 를 계산할 수 없으면 **매수를 보류**한다.

    None 을 "매수 허용"으로 취급하면 필터를 켠 의미가 없다. 사유는
    `TREND_FILTER_UNAVAILABLE` 이며, 봉 부족(INDICATOR_UNAVAILABLE)과 구분된다.
    """
    d = strategy.decide(_snapshot(), _position(), _account(),
                        _params(entry_trend_filter_enabled=True,
                                entry_trend_sma_period=200),  # 봉 30개 < 200
                        models.DayCounters(), ai_score=0)
    assert d.is_hold
    assert d.reason == models.HOLD_TREND_FILTER


def test_changed_ai_filter_disabled_skips_score_check():
    """`ai_filter_enabled=False` 면 점수를 못 받아도 매수한다(T10 에서 기본값 변경 예정)."""
    d = strategy.decide(_snapshot(), _position(), _account(),
                        _params(ai_filter_enabled=False),
                        models.DayCounters(), ai_score=None)
    assert d.action == models.BUY


def test_changed_ai_filter_enabled_without_score_holds():
    """#5: 필터가 켜져 있는데 점수를 못 받으면 **매수를 보류**한다."""
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(), ai_score=None)
    assert d.is_hold
    assert d.reason == models.HOLD_AI_FILTER


# ══ 3. 순수성: I/O·시간·전역 설정 ════════════════════════

def test_purity_no_io_or_clock_in_decide():
    """
    `decide()` 소스에 I/O·시간 조회가 없어야 한다.

    `params_from_config`(설정 읽기)와 `evaluate_entry_signal` 등 보조 함수는
    같은 모듈에 있지만, `decide` 본문에는 없어야 한다 — 그래야 백테스트가
    설정·시계를 바꿔가며 같은 함수를 돌릴 수 있다.
    """
    import inspect

    src = inspect.getsource(strategy.decide)
    for token in ("config.", "db.", "kis_", "kis.", "bot_state", "datetime.now",
                  "now_kst", "request", "open("):
        assert token not in src, f"decide() 에 I/O 흔적이 남아 있다: {token}"


def test_purity_config_change_does_not_affect_decide(monkeypatch):
    """
    파라미터를 만든 뒤 `config` 를 바꿔도 결과가 같아야 한다 — `decide()` 가
    전역 설정을 **읽지 않는다**는 증거다.
    """
    params = _params()
    before = strategy.decide(_snapshot(), _position(), _account(), params,
                             models.DayCounters(), ai_score=0)

    monkeypatch.setattr(config, "RSI_OVERSOLD", 0.1)
    monkeypatch.setattr(config, "BB_NUM_STD", 5.0)
    monkeypatch.setattr(config, "AI_SCORE_FLOOR", 999)
    monkeypatch.setattr(config, "TAKE_PROFIT_PCT", 999.0)
    monkeypatch.setattr(config, "RISK_PER_TRADE_PCT", 90.0)

    after = strategy.decide(_snapshot(), _position(), _account(), params,
                            models.DayCounters(), ai_score=0)
    assert after == before, "decide() 가 전역 설정을 읽고 있다"


def test_purity_uses_snapshot_now_not_wall_clock():
    """매수 간격 판정의 기준 시각은 `snapshot.now` 다 — 벽시계가 아니다."""
    params = _params(min_buy_interval_hours=2.0)
    counters = models.DayCounters(buy_count_today=1,
                                  last_buy_time=_TODAY - timedelta(hours=1))
    d = strategy.decide(_snapshot(now=_TODAY), _position(), _account(), params,
                        counters, ai_score=0)
    assert d.reason == models.HOLD_BUY_INTERVAL

    # 스냅샷 시각만 3시간 뒤로 옮기면 통과한다 — 시간이 입력임을 보인다.
    d2 = strategy.decide(_snapshot(now=_TODAY + timedelta(hours=3)), _position(),
                         _account(), params, counters, ai_score=0)
    assert d2.action == models.BUY


def test_purity_naive_now_is_rejected():
    """naive datetime 은 받지 않는다 (절대 규칙 9)."""
    with pytest.raises(ValueError):
        models.MarketSnapshot(symbol="QQQ", bars=None, last_price=1.0,
                              now=datetime(2026, 10, 23, 10, 0))


def test_purity_naive_last_buy_time_is_rejected():
    """직전 매수 시각도 tz-aware 만 받는다."""
    with pytest.raises(ValueError):
        models.DayCounters(last_buy_time=datetime(2026, 10, 23, 10, 0))


# ══ 4. 결정성: 같은 입력 → 같은 출력, 입력 불변 ═══════════

def test_determinism_repeated_calls_are_identical():
    snapshot, position = _snapshot(), _position()
    params, counters = _params(), models.DayCounters()
    results = [strategy.decide(snapshot, position, _account(), params, counters,
                               ai_score=0) for _ in range(5)]
    assert all(r == results[0] for r in results), "같은 입력에 다른 결과가 나왔다"


def test_determinism_input_dataframe_is_not_mutated():
    """입력 DataFrame 을 변경하지 않는다 — 백테스트가 같은 봉을 재사용할 수 있어야 한다."""
    snapshot = _snapshot()
    before = snapshot.bars.copy(deep=True)
    strategy.decide(snapshot, _position(), _account(), _params(),
                    models.DayCounters(), ai_score=0)
    pd.testing.assert_frame_equal(snapshot.bars, before)


def test_determinism_input_params_are_not_mutated():
    """
    `params` 를 변경하지 않는다.

    `merge_params` 가 **사본**을 만들기 때문이다 — 원본을 고치면 호출부의 다음 종목
    판단이 오염된다(두 시장을 한 사이클에 돌린다).
    """
    params = _params()
    snapshot = params["sizing"].copy()
    strategy.decide(_snapshot(), _position(), _account(), params,
                    models.DayCounters(), ai_score=0)
    assert params["sizing"] == snapshot
    assert "exit_price" not in params, "merge_params 가 원본을 오염시켰다"


def test_determinism_frozen_models_reject_mutation():
    """frozen dataclass 이므로 판단 함수가 입력을 바꾸려 하면 예외가 난다."""
    snapshot = _snapshot()
    with pytest.raises(Exception):
        snapshot.last_price = 1.0


# ══ 5. 로그 재현성 (요구사항 6) ══════════════════════════

def test_decision_log_line_has_no_account_info():
    """로그에 계좌번호·키가 들어가지 않는다 (절대 규칙 2)."""
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(), ai_score=7)
    line = d.log_line()
    assert "CANO" not in line
    assert "ACNT_PRDT_CD" not in line
    assert "12345678" not in line


def test_decision_meta_records_inputs_for_reproduction():
    """
    요구사항 6: 왜 이 주문이 나갔는지 재현할 수 있게 **입력 지표값을 함께** 남긴다.
    """
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(), ai_score=7)
    assert d.action == models.BUY
    for key in ("signal", "rsi", "close", "bb_lower", "ai_score", "sizing", "stop_pct"):
        assert key in d.meta, f"재현용 값이 빠졌다: {key}"
    assert d.meta["ai_score"] == 7
    assert d.meta["rsi"] is not None


def test_decision_log_line_includes_reason_and_qty():
    d = strategy.decide(_snapshot(), _position(), _account(), _params(),
                        models.DayCounters(), ai_score=0)
    line = d.log_line()
    assert "BUY" in line and "주" in line and d.reason in line


def test_sell_decision_meta_records_exit_inputs():
    """청산 결정도 근거(현재가·평단·상세)를 남긴다."""
    d = strategy.decide(_snapshot(price=90.0), _position(qty=10.0, avg=100.0),
                        _account(), _params(), models.DayCounters(), ai_score=0)
    assert d.action == models.SELL
    assert d.meta["detail"], "청산 상세가 비었다"
    assert d.meta["price"] and d.meta["avg_price"]
