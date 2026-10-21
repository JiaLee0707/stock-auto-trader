"""
indicators.py 특성화 테스트 (T01).

목적: 현재 구현의 출력값을 고정한다. 로직을 바꾸려면 이 값이 왜 달라져야 하는지
      먼저 설명할 수 있어야 한다. 여기 값은 "정답"이 아니라 "현재 동작의 기준선"이다.
"""
import pytest

import indicators as ind


@pytest.fixture
def zigzag() -> list[float]:
    """고정 톱니형 종가 60개 (과거 → 최신). 재현 가능한 상수 시계열."""
    seq, p = [], 100.0
    for i in range(60):
        p += [0.5, -0.3, 0.8, -0.6, -0.2, 0.4][i % 6]
        seq.append(round(p, 4))
    return seq


# ── RSI (Wilder) ─────────────────────────────────────────

def test_rsi_rising_series_is_100(zigzag):
    """단조 상승이면 손실이 없어 RSI는 100.0."""
    up = [float(i) for i in range(1, 41)]
    assert ind.rsi(up, 14) == 100.0


def test_rsi_flat_series_is_100():
    """변동이 0이면 avg_loss=0 분기로 100.0을 반환한다(현재 동작)."""
    assert ind.rsi([5.0] * 20, 14) == 100.0


def test_rsi_falling_series_is_0():
    """단조 하락이면 avg_gain=0이라 RSI는 0.0."""
    down = [float(200 - i) for i in range(40)]
    assert ind.rsi(down, 14) == 0.0


def test_rsi_zigzag_value(zigzag):
    assert ind.rsi(zigzag, 14) == pytest.approx(59.86173710940455)


def test_rsi_returns_none_when_data_short():
    """period+1개 미만이면 None."""
    assert ind.rsi([1.0, 2.0], 14) is None
    assert ind.rsi([float(i) for i in range(14)], 14) is None


# ── 볼린저밴드 ───────────────────────────────────────────

def test_bollinger_simple_window():
    """[1..5], period=5, 2σ → 중심 3.0, 표준편차 sqrt(2)."""
    assert ind.bollinger([1.0, 2.0, 3.0, 4.0, 5.0], 5, 2.0) == pytest.approx(
        (3.0, 5.82842712474619, 0.1715728752538097)
    )


def test_bollinger_zigzag_value(zigzag):
    assert ind.bollinger(zigzag, 20, 2.0) == pytest.approx(
        (105.155, 106.47481059247151, 103.83518940752849)
    )


def test_bollinger_returns_none_when_data_short():
    assert ind.bollinger([1.0, 2.0], 20, 2.0) is None


def test_bollinger_uses_only_last_window(zigzag):
    """마지막 period개만 쓴다 — 앞에 값을 덧붙여도 결과가 같아야 한다."""
    shifted = [0.0] + zigzag
    assert ind.bollinger(shifted, 20, 2.0) == pytest.approx(
        ind.bollinger(zigzag, 20, 2.0)
    )


# ── SMA / ATR / 짧은 RSI (T02 추가: 순수 함수, 외부 의존성 없음) ──────

def test_sma_simple_window():
    """[1..5], period=3 → 마지막 3개 평균 = 4.0."""
    assert ind.sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)


def test_sma_zigzag_value(zigzag):
    assert ind.sma(zigzag, 20) == pytest.approx(105.155)
    assert ind.sma(zigzag, 60) == pytest.approx(103.18333333333334)


def test_sma_returns_none_when_data_short():
    """데이터가 period 미만이면 None (SMA(200) 필터가 이 경우를 조용히 통과시키지 않도록)."""
    assert ind.sma([1.0, 2.0], 20) is None
    assert ind.sma([1.0] * 60, 200) is None
    assert ind.sma([], 20) is None


def test_sma_rejects_nonpositive_period():
    assert ind.sma([1.0, 2.0, 3.0], 0) is None
    assert ind.sma([1.0, 2.0, 3.0], -5) is None


def test_sma_uses_only_last_window(zigzag):
    """마지막 period개만 쓴다 — 앞에 값을 덧붙여도 결과가 같아야 한다."""
    assert ind.sma([0.0] + zigzag, 20) == pytest.approx(ind.sma(zigzag, 20))


def test_atr_simple_case():
    """
    TR = max(high-low, |high-prev_close|, |low-prev_close|).
    합성 케이스: period=2, TR = [2, 2, 2] → 시드 평균 2.0, Wilder 평활 후에도 2.0.
    """
    assert ind.atr([2.0, 3.0, 4.0], [0.0, 1.0, 2.0], [1.0, 2.0, 3.0], 2) == pytest.approx(2.0)


def test_atr_zigzag_value(zigzag):
    """highs=close+1, lows=close-1 이면 모든 TR이 2.0 → ATR도 2.0."""
    highs = [c + 1.0 for c in zigzag]
    lows = [c - 1.0 for c in zigzag]
    assert ind.atr(highs, lows, zigzag, 14) == pytest.approx(2.0)


def test_atr_returns_none_when_data_short():
    assert ind.atr([2.0], [0.0], [1.0], 14) is None


def test_atr_rejects_mismatched_lengths():
    """길이가 다르면 조용히 넘어가지 않고 예외로 알린다."""
    with pytest.raises(ValueError):
        ind.atr([2.0, 3.0], [0.0], [1.0, 2.0], 2)


def test_rsi_short_period_is_supported():
    """
    rsi(2) 같은 짧은 기간도 같은 함수로 계산한다(별도 함수 없음).
    손 검산 (closes=[100, 101, 102, 101, 103, 102], period=2):
      i=2: seed diffs [1, 1] → avg_gain=1, avg_loss=0 → 손실 0 → 100.0
      i=3: diff=-1 → avg_gain=(1*1+0)/2=0.5, avg_loss=(0*1+1)/2=0.5 → RS=1 → 50.0
      i=4: diff=+2 → avg_gain=(0.5*1+2)/2=1.25, avg_loss=(0.5*1+0)/2=0.25 → RS=5 → 83.333…
      i=5: diff=-1 → avg_gain=(1.25*1+0)/2=0.625, avg_loss=(0.25*1+1)/2=0.625 → RS=1 → 50.0
    """
    closes = [100.0, 101.0, 102.0, 101.0, 103.0, 102.0]
    assert ind.rsi(closes, 2) == pytest.approx(50.0)
    assert ind.rsi_series(closes, 2) == [
        None, None, 100.0, 50.0, pytest.approx(83.33333333333333), 50.0,
    ]


def test_rsi_zigzag_short_period_value(zigzag):
    assert ind.rsi(zigzag, 2) == pytest.approx(65.73705179282865)


# ── evaluate ─────────────────────────────────────────────

def test_evaluate_empty_series():
    assert ind.evaluate([]) == {
        "close": 0.0,
        "rsi": None,
        "bb_mid": None,
        "bb_upper": None,
        "bb_lower": None,
        "buy_signal": False,
        "sell_signal": False,
        "reason": "데이터 없음",
    }


def test_evaluate_insufficient_data_reason():
    r = ind.evaluate([1.0, 2.0])
    assert r["buy_signal"] is False
    assert r["sell_signal"] is False
    assert r["rsi"] is None
    assert r["reason"] == "지표 계산 불가(데이터 부족)"
    assert r["close"] == 2.0


def test_evaluate_zigzag_snapshot(zigzag):
    """톱니 계열은 현재 매수·매도 신호가 모두 False다."""
    r = ind.evaluate(zigzag)
    assert r["close"] == 106.0
    assert r["rsi"] == pytest.approx(59.86173710940455)
    assert r["buy_signal"] is False
    assert r["sell_signal"] is False
    assert r["reason"] == (
        "RSI 59.9 (기준 ≤30/≥70) | 종가 106.00 | BB 하단 103.84 / 상단 106.47"
    )


def test_evaluate_buy_signal_requires_both_conditions(monkeypatch):
    """RSI ≤ 30 이고 종가 ≤ BB 하단일 때만 buy_signal=True (현재 경계 동작 고정)."""
    closes = [100.0] * 19 + [90.0]
    # RSI는 낮고 종가가 하단 이하인 상황을 인위적으로 만든다
    monkeypatch.setattr(ind, "rsi", lambda *a, **k: 20.0)
    monkeypatch.setattr(ind, "bollinger", lambda *a, **k: (100.0, 110.0, 95.0))
    r = ind.evaluate(closes)
    assert r["buy_signal"] is True
    assert r["sell_signal"] is False


def test_evaluate_sell_signal_requires_both_conditions(monkeypatch):
    monkeypatch.setattr(ind, "rsi", lambda *a, **k: 80.0)
    monkeypatch.setattr(ind, "bollinger", lambda *a, **k: (100.0, 105.0, 95.0))
    r = ind.evaluate([100.0] * 19 + [110.0])
    assert r["sell_signal"] is True
    assert r["buy_signal"] is False


# ── evaluate_or_none ─────────────────────────────────────

def test_evaluate_or_none_raises_on_empty():
    """종가가 비어 있으면 '신호 없음'이 아니라 '데이터 없음'으로 구분한다 (T04)."""
    with pytest.raises(ind.IndicatorDataUnavailable):
        ind.evaluate_or_none(lambda: [])


def test_evaluate_or_none_raises_on_fetch_failure():
    """조회 실패를 삼키지 않는다 — 삼키면 하락률 폴백 매수로 이어진다 (T04)."""
    def boom():
        raise ValueError("network")

    with pytest.raises(ind.IndicatorDataUnavailable):
        ind.evaluate_or_none(boom)


def test_evaluate_insufficient_data_is_no_signal_not_error():
    """데이터는 있고 계산만 불가하면 예외가 아니라 '신호 없음'(False)이다 (T04 경계)."""
    r = ind.evaluate_or_none(lambda: [100.0, 101.0, 102.0])
    assert r is not None
    assert r["buy_signal"] is False
    assert r["sell_signal"] is False


def test_evaluate_or_none_passes_through_result(zigzag):
    r = ind.evaluate_or_none(lambda: zigzag)
    assert r is not None
    assert r["rsi"] == pytest.approx(59.86173710940455)
