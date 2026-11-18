"""
보조지표 계산 및 매매 신호 판단 모듈.

전략: RSI + 볼린저밴드 (눌림목 매매)
  - 매수: RSI(14) ≤ RSI_OVERSOLD 이고 종가가 볼린저 하단 이하
  - 매도: RSI(14) ≥ RSI_OVERBOUGHT 이고 종가가 볼린저 상단 이상

입력 종가 시계열은 반드시 과거 → 최신 순(오래된 것이 앞)이어야 한다.

모든 지표는 **리스트 기반**이며 종가/고가/저가 시계열을 과거 → 최신 순으로 받는다.
  rsi(period), rsi_series(period), bollinger(period, num_std), sma(period), atr(period)

rsi(2) 처럼 짧은 기간이 필요하면 rsi(closes, 2)를 그대로 쓴다(별도 함수 없음).
데이터가 부족하면 전 함수가 예외 없이 None을 반환한다(판단 불가를 명시적으로 표현).
"""
import logging

import config
from errfmt import exc_summary

logger = logging.getLogger("bot")

# 데이터 부족 시 지표는 None(=NaN 아님)을 반환한다. 이 규칙은 전 함수 공통이다.
INSUFFICIENT_DATA = None


class IndicatorDataUnavailable(RuntimeError):
    """
    지표를 계산할 **입력 데이터 자체**를 얻지 못했다 (T04).

    "신호 없음"(데이터는 있고 조건 미충족)과 구분하기 위한 예외다. 조회 실패를
    신호 없음으로 취급하면 매매 로직이 하락률 폴백으로 흘러 잘못된 매수를 낸다.
    """


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def rsi(closes: list[float], period: int = 14) -> float | None:
    """
    Wilder 방식 RSI. 종가가 부족하면 None.
    """
    if len(closes) < period + 1:
        return None

    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff

    avg_gain = gains / period
    avg_loss = losses / period

    # 이후 구간은 Wilder 평활법으로 누적
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    """
    각 시점의 RSI를 순서대로 반환한다 (앞쪽 period개는 None).
    라이브러리 비교 검증에서 시점별 값을 맞춰보기 위한 용도.
    """
    out: list[float | None] = []
    for i in range(len(closes)):
        out.append(rsi(closes[: i + 1], period))
    return out


def bollinger(closes: list[float], period: int = 20, num_std: float = 2.0):
    """
    볼린저밴드 (중심선, 상단, 하단). 종가가 부족하면 None.
    """
    if len(closes) < period:
        return None

    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((c - mid) ** 2 for c in window) / period
    std = variance ** 0.5
    return mid, mid + num_std * std, mid - num_std * std


def sma(closes: list[float], period: int) -> float | None:
    """
    단순이동평균 (마지막 period개의 평균). 데이터가 부족하면 None.
    """
    if period <= 0 or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window) / period


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> float | None:
    """
    ATR(평균 진폭, Wilder 평활). 데이터가 부족하면 None.

    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    최초 period개의 TR 평균을 시드로, 이후 Wilder 평활을 적용한다.
    입력 세 리스트는 길이가 같아야 하며 과거 → 최신 순이어야 한다.
    """
    if len(highs) != len(lows) or len(closes) != len(highs):
        raise ValueError("highs/lows/closes 길이가 다릅니다")
    if len(closes) < period + 1:
        return None

    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    # 최초 period개 TR의 단순평균을 시드로 사용 (Wilder 방식)
    avg = sum(trs[:period]) / period
    for tr in trs[period:]:
        avg = (avg * (period - 1) + tr) / period
    return avg


def evaluate(closes: list[float]) -> dict:
    """
    종가 시계열로 매수·매도 신호를 판단한다.

    반환: {
        "close": float, "rsi": float|None,
        "bb_mid": float|None, "bb_upper": float|None, "bb_lower": float|None,
        "buy_signal": bool, "sell_signal": bool, "reason": str,
    }
    신호를 계산할 수 없으면 buy_signal/sell_signal 모두 False.
    """
    result = {
        "close": closes[-1] if closes else 0.0,
        "rsi": None, "bb_mid": None, "bb_upper": None, "bb_lower": None,
        "buy_signal": False, "sell_signal": False,
        "reason": "데이터 없음",
    }
    if not closes:
        return result

    r = rsi(closes, config.RSI_PERIOD)
    bb = bollinger(closes, config.BB_PERIOD, config.BB_NUM_STD)
    result["rsi"] = r
    if bb:
        result["bb_mid"], result["bb_upper"], result["bb_lower"] = bb

    close = closes[-1]
    if r is None or bb is None:
        result["reason"] = "지표 계산 불가(데이터 부족)"
        return result

    _, upper, lower = bb
    buy = (r <= config.RSI_OVERSOLD) and (close <= lower)
    sell = (r >= config.RSI_OVERBOUGHT) and (close >= upper)

    result["buy_signal"] = buy
    result["sell_signal"] = sell
    result["reason"] = (
        f"RSI {r:.1f} (기준 ≤{config.RSI_OVERSOLD:g}/≥{config.RSI_OVERBOUGHT:g}) | "
        f"종가 {close:,.2f} | BB 하단 {lower:,.2f} / 상단 {upper:,.2f}"
    )
    return result


def evaluate_or_none(fetch_closes) -> dict | None:
    """
    종가 조회 함수를 받아 신호를 계산한다 (T04 개정).

    반환값
      - dict : 지표를 계산했다. `buy_signal`/`sell_signal` 이 판단 결과다.
               데이터가 부족해 계산 자체가 불가하면 두 값 모두 False 로 온다
               ("신호 없음"이며, 이때도 매수하지 않는다).
      - None : 이 함수는 이제 None 을 돌려주지 않는다(하위 호환으로 타입만 남겨 둔다).

    예외
      - `IndicatorDataUnavailable` : **조회 실패 또는 종가 데이터 없음.**
        T04 이전에는 이 경우도 None 을 돌려줬는데, 호출부(trader)가 None 을
        "지표 신호 없음"으로 해석해 **하락률 폴백 매수**로 이어질 수 있었다.
        데이터를 못 얻은 것과 신호가 없는 것은 다른 상태이므로 구분한다.
        호출부는 이 예외에서 **매수를 보류**해야 한다(모르는 채 사면 안 된다).

    절대 규칙 7(`try/except: pass` 금지)에 따라 예외를 삼키지 않는다.
    """
    try:
        closes = fetch_closes()
    except Exception as e:
        raise IndicatorDataUnavailable(f"일봉 종가 조회 실패: {exc_summary(e)}") from e

    if not closes:
        raise IndicatorDataUnavailable("일봉 종가 데이터 없음(빈 응답)")

    return evaluate(closes)
