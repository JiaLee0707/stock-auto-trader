"""
T02: 자체 구현 지표를 외부 라이브러리(pandas-ta) 값과 대조 검증한다.

- 이 파일은 dev 의존성(pandas, pandas-ta)이 없으면 자동으로 skip 된다.
- 허용 오차를 명시적으로 고정하고, 왜 그 오차가 필요한지(시드 차이)를 주석으로 남긴다.
- 라이브러리 값이 없어도 자체 구현이 옳다는 증거가 되지 않는다. 서로 다른 구현이
  같은 값에 수렴하는지 확인하는 교차검증일 뿐이다.

[2026-10-19 T02 실측] 이 환경의 pandas-ta 는 0.4.71b0 이고, RSI 구현이 0.3.x 와 다르다.
  0.3.x/TA-Lib: 최초 period개 diff 의 **SMA 평균을 시드**로 Wilder 평활 (고전 Wilder 정의)
  0.4.x       : `ewm(alpha=1/period, adjust=False)` — **첫 diff 값에서 바로 재귀 시작**
두 방식은 같은 정의가 아니므로 초기 구간 값이 크게 다르고, 시간이 지나며 수렴한다.
실측(n=600): index14 차이 14.35 → index100 0.024 → index300 9e-9 → index400 5e-12.
따라서 pandas-ta 0.4.x 를 "정답"으로 놓고 자체 구현을 맞추면 **오히려 틀린다.**
자체 구현은 TA-Lib/고전 Wilder 호환(SMA 시드)을 유지하고, 이 파일은
  (1) 라이브러리 값이 어떤 정의인지 수치로 재현해 확인하고
  (2) 자체 구현이 고전 Wilder 정의와 정확히 일치함을 확인하고
  (3) 두 정의가 장기적으로 수렴함을 확인한다.
"""
from datetime import date, timedelta

import pytest

import indicators

pd = pytest.importorskip("pandas", reason="dev 의존성 pandas 없음")
pandas_ta = pytest.importorskip("pandas_ta", reason="dev 의존성 pandas-ta 없음")

# 허용 오차
# - 자체 구현 vs 고전 Wilder 정의(직접 재현): 같은 정의이므로 부동소수 누적 오차 수준.
RSI_TOL = 1e-9
# - 볼린저: 라이브러리는 표본표준편차(ddof=1), 자체 구현은 모표준편차(ddof=0)를 쓴다.
#   이 정의 차이는 **오차가 아니라 다른 정의**이므로, 검증 시 ddof 를 명시적으로 맞춘다.
#   (기본 pandas rolling().std() 는 ddof=1 이다.)
BB_TOL = 1e-9
# - ATR: 같은 Wilder 평활 정의. 누적 오차만 허용.
ATR_TOL = 1e-6
# - 두 RSI 정의가 "수렴했다"고 볼 기준. 초기 구간에는 적용하지 않는다.
RSI_CONVERGED_TOL = 1e-6



def _zigzag(n: int = 120, start: float = 100.0) -> list[float]:
    """기존 characterization 테스트와 같은 결정론적 계단 시계열."""
    steps = [0.5, -0.3, 0.8, -0.6, -0.2, 0.4]
    out = [start]
    for i in range(n - 1):
        out.append(round(out[-1] + steps[i % len(steps)], 4))
    return out


def _ohlc(n: int = 120):
    closes = _zigzag(n)
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return highs, lows, closes


def _classic_wilder_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """
    고전 Wilder/TA-Lib 정의를 pandas 로 독립 재현한다(검증 기준).

    최초 period개 diff 의 단순평균을 시드로, 이후 Wilder 평활(alpha=1/period)을 적용한다.
    자체 구현 indicators.rsi_series 와 구현 경로가 완전히 달라(벡터 vs 순차 누적)
    독립적인 대조군이 된다.
    """
    s = pd.Series(closes, dtype="float64")
    diff = s.diff()
    gain = diff.clip(lower=0.0)
    loss = (-diff).clip(lower=0.0)

    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out

    avg_gain = float(gain.iloc[1: period + 1].mean())
    avg_loss = float(loss.iloc[1: period + 1].mean())
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + float(gain.iloc[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss.iloc[i])) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def test_rsi_matches_classic_wilder_definition():
    """
    자체 RSI 가 고전 Wilder 정의와 시점별로 정확히 일치하는지 확인한다.
    라이브러리(pandas-ta 0.4.x)가 아니라 **정의를 기준**으로 검증한다.
    """
    closes = _zigzag()
    ours = indicators.rsi_series(closes, 14)
    ref = _classic_wilder_rsi(closes, 14)

    our_valid = [i for i, v in enumerate(ours) if v is not None]
    assert our_valid[0] == 14, f"RSI 유효 시작점: {our_valid[0]} (기대 14)"
    assert ref[13] is None and ref[14] is not None

    for i in our_valid:
        assert ours[i] == pytest.approx(ref[i], abs=RSI_TOL), (
            f"index {i} 에서 고전 Wilder 정의와 불일치: ours={ours[i]}, ref={ref[i]}"
        )


def _library_rsi_first_valid_index(closes: list[float], period: int = 14) -> int:
    """라이브러리 RSI 가 첫 유효값을 내는 index. 시드 방식을 판별하는 지표."""
    lib = pandas_ta.rsi(pd.Series(closes), length=period)
    if lib is None:
        return -1
    for i in range(len(closes)):
        if pd.notna(lib.iloc[i]):
            return i
    return -1


def test_library_rsi_seeding_scheme_is_identified():
    """
    설치된 pandas-ta 가 어떤 RSI 시드를 쓰는지 **판별하고, 그 정의를 그대로 재현**한다.
    버전에 따라 정의가 다르므로(0.3.x=SMA 시드, 0.4.x=첫 값 시드) 단정하지 않고 감지한다.
    무엇을 비교하고 있는지 모른 채 "라이브러리와 같다/다르다"고 말하지 않기 위한 테스트.
    """
    closes = _zigzag(n=600)
    period = 14
    first_valid = _library_rsi_first_valid_index(closes, period)
    ours = indicators.rsi_series(closes, period)
    lib = pandas_ta.rsi(pd.Series(closes), length=period)

    assert first_valid in (1, period), f"예상 밖 시드: 첫 유효 index={first_valid}"

    if first_valid == period:
        # 0.3.x / TA-Lib 계열: 고전 Wilder 와 같은 정의 → 전 구간 일치해야 한다
        ref = _classic_wilder_rsi(closes, period)
        assert ours[period] is not None
        for i in range(period, len(closes)):
            assert ours[i] == pytest.approx(float(lib.iloc[i]), abs=RSI_TOL)
            assert ref[i] == pytest.approx(float(lib.iloc[i]), abs=RSI_TOL)
    else:
        # 0.4.x: ewm(alpha=1/period, adjust=False) 를 첫 diff 부터 재귀 → 정의가 다르다.
        # 그 정의를 재현해 정확히 일치하면 "무엇이 다른지" 를 확정한 것이다.
        s = pd.Series(closes)
        diff = s.diff()
        pos = diff.clip(lower=0.0)
        neg = (-diff).clip(lower=0.0)
        pos_avg = pos.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
        neg_avg = neg.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()
        reproduced = 100.0 * pos_avg / (pos_avg + neg_avg.abs())

        assert float((reproduced - lib).abs().max()) == pytest.approx(0.0, abs=1e-12)
        # 고전 정의와는 초기 구간에서 크게 다르다 → 정답으로 쓸 수 없다
        assert ours[1] is None, "자체 구현은 고전 정의라 앞쪽이 None"
        assert abs(ours[period] - float(lib.iloc[period])) > 1.0


def test_two_rsi_definitions_converge_over_long_series():
    """
    시드 방식 차이가 있더라도 **과거 데이터가 쌓이면 사라진다.** 회귀 기간이 길어지면
    실전 판단(최신 RSI)에는 영향이 없다는 근거를 수치로 남긴다.

    라이브러리가 고전 정의(0.3.x)면 처음부터 일치하므로 그대로 통과한다.
    """
    closes = _zigzag(n=600)
    lib = pandas_ta.rsi(pd.Series(closes), length=14)
    ours = indicators.rsi_series(closes, 14)

    early = abs(ours[20] - float(lib.iloc[20]))
    late = abs(ours[400] - float(lib.iloc[400]))
    first_valid = _library_rsi_first_valid_index(closes, 14)

    assert late <= early, "시간이 지날수록 차이가 커지면 안 된다"
    assert late < RSI_CONVERGED_TOL, f"index400 차이 {late} — 수렴 기대"

    if first_valid == 1:
        # 정의가 다른 경우에만 초기 구간 차이를 요구한다
        assert early > 1.0, "0.4.x 라면 초기 구간 차이가 유의미해야 한다"

    # 최신 값 기준으로는 두 정의가 사실상 같은 결론을 준다
    assert ours[-1] == pytest.approx(float(lib.iloc[-1]), abs=RSI_CONVERGED_TOL)


def test_bollinger_matches_pandas_rolling_with_same_ddof():
    """
    자체 구현은 모표준편차(ddof=0)를 쓴다. 라이브러리 기본값(ddof=1)과 직접 비교하면
    항상 어긋나므로 ddof=0 을 명시해 **정의를 맞춘 뒤** 값을 검증한다.
    """
    closes = _zigzag()
    s = pd.Series(closes)
    period, num_std = 20, 2.0

    mid_lib = float(s.rolling(period).mean().iloc[-1])
    std_lib = float(s.rolling(period).std(ddof=0).iloc[-1])
    upper_lib = mid_lib + num_std * std_lib
    lower_lib = mid_lib - num_std * std_lib

    mid, upper, lower = indicators.bollinger(closes, period, num_std)
    assert mid == pytest.approx(mid_lib, abs=BB_TOL)
    assert upper == pytest.approx(upper_lib, abs=BB_TOL)
    assert lower == pytest.approx(lower_lib, abs=BB_TOL)


def test_bollinger_ddof_difference_is_documented_not_hidden():
    """
    ddof 정의 차이가 실제로 얼마나 되는지 수치로 남긴다(은폐 금지).
    기존 characterization 테스트가 ddof=0 값을 고정하고 있으므로 **정의를 바꾸지 않는다.**
    """
    closes = _zigzag()
    period = 20
    s = pd.Series(closes)
    std0 = float(s.rolling(period).std(ddof=0).iloc[-1])
    std1 = float(s.rolling(period).std(ddof=1).iloc[-1])

    _, upper0, lower0 = indicators.bollinger(closes, period, 2.0)
    mid = float(s.rolling(period).mean().iloc[-1])

    # 자체 구현 = ddof 0
    assert upper0 == pytest.approx(mid + 2 * std0, abs=BB_TOL)
    # ddof 1 기준으로는 밴드폭이 sqrt(20/19)배 넓다 — 무시할 수 있는 차이가 아니다
    scale = (period / (period - 1)) ** 0.5
    assert (upper0 - mid) == pytest.approx((mid + 2 * std1 - mid) / scale, abs=BB_TOL)
    assert abs(upper0 - (mid + 2 * std1)) > 1e-3, "ddof 차이가 유의미함을 명시"


def test_sma_matches_pandas_rolling():
    closes = _zigzag()
    lib = float(pd.Series(closes).rolling(20).mean().iloc[-1])
    assert indicators.sma(closes, 20) == pytest.approx(lib, abs=BB_TOL)


def test_atr_matches_pandas_ta():
    highs, lows, closes = _ohlc()
    lib = pandas_ta.atr(pd.Series(highs), pd.Series(lows), pd.Series(closes), length=14)
    lib_last = float(lib.iloc[-1])
    ours = indicators.atr(highs, lows, closes, 14)
    assert ours == pytest.approx(lib_last, abs=ATR_TOL), (
        f"ATR 불일치: ours={ours}, lib={lib_last}"
    )


def test_insufficient_data_returns_none_consistently():
    """데이터 부족 시 전 지표가 None — 라이브러리의 NaN 과 대응한다."""
    closes = [100.0 + i for i in range(10)]     # period(14/20) 미만
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]

    assert indicators.rsi(closes, 14) is None
    assert indicators.bollinger(closes, 20) is None
    assert indicators.sma(closes, 20) is None
    assert indicators.atr(highs, lows, closes, 14) is None
    # 라이브러리도 같은 구간에서 NaN 을 낸다(대응 관계 확인).
    # 0.4.x 는 데이터가 period 미만이면 계산을 포기하고 None 을 반환한다(예외 아님).
    lib_short = pandas_ta.rsi(pd.Series(closes), length=14)
    assert lib_short is None or pd.isna(lib_short.iloc[-1])


def test_history_confirmed_bars_excludes_same_day(tmp_path, monkeypatch):
    """당일 봉은 확정 전이므로 제외된다."""
    import history

    monkeypatch.setattr(history, "BARS_DIR", tmp_path)
    today = date(2026, 10, 20)
    rows = []
    for i in range(5):
        d = today - timedelta(days=4 - i)
        rows.append({"date": d.isoformat(), "open": 1.0, "high": 2.0,
                     "low": 0.5, "close": 1.5, "volume": 10})
    history.save_bars("TEST", rows)

    confirmed = history.get_confirmed_bars("TEST", as_of=today)
    dates = [b["date"] for b in confirmed]
    assert today.isoformat() not in dates
    assert len(confirmed) == 4
    assert dates == sorted(dates)
