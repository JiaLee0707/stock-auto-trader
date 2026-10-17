"""
보조지표 계산 및 매매 신호 판단 모듈.

전략: RSI + 볼린저밴드 (눌림목 매매)
  - 매수: RSI(14) ≤ RSI_OVERSOLD 이고 종가가 볼린저 하단 이하
  - 매도: RSI(14) ≥ RSI_OVERBOUGHT 이고 종가가 볼린저 상단 이상

입력 종가 시계열은 반드시 과거 → 최신 순(오래된 것이 앞)이어야 한다.
"""
import logging

import config

logger = logging.getLogger("bot")


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
    종가 조회 함수를 받아 신호를 계산한다. 조회·계산 실패 시 None.
    매매 판단이 지표 없이도 계속되도록 예외를 삼킨다.
    """
    try:
        closes = fetch_closes()
        if not closes:
            logger.warning("일봉 종가 없음 — 지표 신호 없이 진행")
            return None
        return evaluate(closes)
    except Exception as e:
        logger.warning(f"지표 계산 실패 — 신호 없이 진행: {e}")
        return None
