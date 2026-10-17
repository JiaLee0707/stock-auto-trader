"""
매매 판단 및 실행 모듈 (스노우볼 전략).

해외(QQQ)와 국내(379800) ETF를 매 사이클마다 각각 판단해 동시 운용한다.

매수 조건 (AND):
  1. 진입 타점 (USE_INDICATORS=True면 지표, 아니면 하락률)
     - 지표: RSI(14) <= RSI_OVERSOLD(30) 이고 종가 <= 볼린저 하단
     - 하락률: 현재가 < 평균 단가 × (1 + DIP_THRESHOLD/100), 보유 없으면 최초 매수 허용
  2. AI 점수 >= AI_SCORE_FLOOR                      →  심각한 악재 없음
  3. 당일 매수 횟수 < MAX_BUYS_PER_DAY              →  분할 매수 상한
  4. 직전 매수 후 MIN_BUY_INTERVAL_HOURS 경과        →  매수 소진 방지

매도 조건 (우선순위 순):
  1. 손절: 수익률 <= STOP_LOSS_THRESHOLD            →  전량 매도
  2. 부분 익절: 아래 둘 중 하나라도 해당하면 다음 단계 실행 (수익 중일 때만)
     - 수익률이 TAKE_PROFIT_STEPS 단계 도달
     - 지표 과매수: RSI >= RSI_OVERBOUGHT(70) 이고 종가 >= 볼린저 상단
     단계는 당일 1회씩만 실행하고, 수익률이 떨어져도 재실행하지 않는다.
"""
import logging

import config
import indicators
import kis_domestic as kd
import kis_overseas as kis
from state import bot_state

logger = logging.getLogger("bot")


def run_trading_cycle(ai_score: int, overseas_open: bool = True, domestic_open: bool = True):
    """
    1회 매매 사이클 실행 (해외 + 국내).

    Args:
        ai_score: AI 시장 감정 점수
        overseas_open: 해외(미국) 정규장 개장 여부
        domestic_open: 국내 정규장 개장 여부
    """
    if not config.IS_OVERSEAS_ENABLED:
        pass    # 해외 운용 비활성 (소액 자본 등)
    elif overseas_open:
        run_overseas_cycle(ai_score)
    else:
        logger.info("⏩ [해외] 정규장 아님 — 매매 스킵")

    if not config.IS_DOMESTIC_ENABLED:
        return
    if domestic_open:
        run_domestic_cycle(ai_score)
    else:
        logger.info("⏩ [국내] 정규장 아님 — 매매 스킵")


def _check_sell(prefix: str, ticker: str, qty: float, profit_rate: float, sell_fn,
                signal: dict | None = None) -> bool:
    """
    손절·부분 익절 판단 및 실행. 매도했으면 True.

    Args:
        sell_fn: (수량) -> 주문 실행 콜러블
        signal: 지표 신호 (indicators.evaluate 결과). None이면 수익률만으로 판단.
    """
    if qty <= 0:
        logger.info(f"⏩ {prefix} 매도 조건 미충족 (보유 수량 없음)")
        return False

    # ── 1. 손절 ──────────────────────────────────────────
    if config.STOP_LOSS_THRESHOLD < 0 and profit_rate <= config.STOP_LOSS_THRESHOLD:
        logger.info(
            f"🛑 {prefix} 손절 조건 충족 ({profit_rate:.2f}% ≤ {config.STOP_LOSS_THRESHOLD}%) "
            f"— {qty:.0f}주 전량 매도"
        )
        try:
            sell_fn(qty)
            bot_state.set_bought_today(ticker, False)
            bot_state.set_profit_stage(ticker, 0)
        except Exception as e:
            logger.error(f"{prefix} 손절 주문 실패: {e}")
        return True

    # ── 2. 부분 익절 ─────────────────────────────────────
    # 도달한 단계를 순서대로 모두 실행한다. (급등으로 여러 단계를 한 번에
    # 넘어선 경우에도 다음 사이클을 기다리지 않고 즉시 분할 매도)
    stage = bot_state.profit_stage(ticker)
    remaining = qty
    sold_any = False

    # 미충족 사유 로깅용 (첫 번째 미도달 단계)
    block_pct: float | None = None

    # 지표 과매수 신호: 수익 중일 때만 익절 트리거로 인정
    indicator_sell = bool(signal and signal.get("sell_signal") and profit_rate > 0)
    if signal and signal.get("sell_signal") and profit_rate <= 0:
        logger.info(f"ℹ️ {prefix} 지표 과매수 신호지만 수익 중이 아니어서 익절 보류 (수익률 {profit_rate:.2f}%)")

    while stage < len(config.TAKE_PROFIT_STEPS):
        target_pct, ratio = config.TAKE_PROFIT_STEPS[stage]
        # 수익률 도달이면 정상 진행 (여러 단계를 한 번에 넘어설 수 있음)
        by_profit = profit_rate >= target_pct
        # 수익률 미달이면 지표 과매수일 때만, 그리고 이번 단계 하나만 실행한다
        by_indicator = (not by_profit) and indicator_sell

        if not (by_profit or by_indicator):
            block_pct = target_pct
            break
        # 지표 신호로 진입한 경우에도 손실 중이면 매도하지 않음
        if profit_rate <= 0:
            block_pct = target_pct
            break

        # 마지막 단계(비중 100%)는 남은 수량 전량
        if ratio >= 100:
            sell_qty = remaining
        else:
            sell_qty = max(1, min(round(remaining * ratio / 100), int(remaining)))

        trigger = (
            f"수익률 {profit_rate:.2f}% ≥ {target_pct}%"
            if profit_rate >= target_pct
            else f"지표 과매수 (RSI {signal.get('rsi', 0):.1f} ≥ {config.RSI_OVERBOUGHT:g})"
        )
        logger.info(
            f"💰 {prefix} 부분 익절 {stage + 1}단계 ({trigger}) "
            f"— {sell_qty:.0f}주 매도 (보유 {remaining:.0f}주)"
        )
        try:
            sell_fn(sell_qty)
        except Exception as e:
            logger.error(f"{prefix} 부분 익절 주문 실패: {e}")
            break

        remaining -= sell_qty
        stage += 1
        sold_any = True
        bot_state.set_profit_stage(ticker, stage)

        if remaining <= 0:
            break
        # 지표 과매수만으로 진입한 경우엔 한 단계만 매도하고 종료
        # (급등으로 수익률이 여러 단계를 넘어선 경우에는 계속 진행)
        if by_indicator and not by_profit:
            break

    # 전량 매도한 경우에만 매수 플래그 초기화 (남은 수량이 있으면 유지)
    if sold_any and remaining <= 0:
        bot_state.set_bought_today(ticker, False)
        bot_state.set_profit_stage(ticker, 0)

    if not sold_any:
        if block_pct is not None:
            summary = "/".join(f"+{p:g}%" for p, _ in config.TAKE_PROFIT_STEPS)
            logger.info(
                f"⏩ {prefix} 매도 조건 미충족 (수익률: {profit_rate:.2f}%, "
                f"다음 목표: +{block_pct:g}%, 전체 단계: {summary})"
            )
        else:
            logger.info(
                f"⏩ {prefix} 매도 조건 미충족 (수익률: {profit_rate:.2f}%, "
                f"금일 익절 단계 모두 소진 {stage}/{len(config.TAKE_PROFIT_STEPS)})"
            )
    return sold_any


def _run_cycle(prefix: str, ticker: str, balance: dict, current_price: float,
               ai_score: int, ratio: float, price_fmt: str, sell_fn, buy_fn,
               fetch_closes=None):
    """
    해외·국내 공통 매매 사이클 로직.

    Args:
        ratio: 총자산 대비 1회 매수 비중
        price_fmt: 로그용 가격 포맷 함수
        sell_fn / buy_fn: (수량) / (수량, 가격) 주문 실행 콜러블
    """
    qty         = balance["quantity"]
    avg_price   = balance["avg_price"]
    profit_rate = balance["profit_rate"]
    total_krw   = balance["total_asset_krw"]
    cash_krw    = balance["cash_krw"]

    logger.info(
        f"[{prefix} 현황] 현재가: {price_fmt(current_price)} | 평균단가: {price_fmt(avg_price)} | "
        f"수익률: {profit_rate:.2f}% | 총자산: {total_krw:,.0f}원 | "
        f"예수금: {cash_krw:,.0f}원 | AI점수: {ai_score}"
    )

    # ── 보조지표 신호 (RSI + 볼린저밴드) ──────────────────
    signal = None
    if config.USE_INDICATORS and fetch_closes is not None:
        signal = indicators.evaluate_or_none(fetch_closes)
        if signal:
            logger.info(f"[{prefix} 지표] {signal['reason']}")

    # ── 매도 (손절 > 부분 익절) ──────────────────────────
    if _check_sell(prefix, ticker, qty, profit_rate, sell_fn, signal):
        return

    # ── 매수 ────────────────────────────────────────────
    buy_count = bot_state.buy_count_today(ticker)
    if buy_count >= config.MAX_BUYS_PER_DAY:
        logger.info(f"⏩ [{prefix}] 당일 매수 상한 도달 ({buy_count}/{config.MAX_BUYS_PER_DAY}회) — 매수 스킵")
        return

    # 직전 매수 후 최소 간격 체크 (2회차 이상부터 적용)
    if buy_count > 0:
        from datetime import datetime
        last_t = bot_state.last_buy_time(ticker)
        if last_t:
            elapsed_h = (datetime.now() - last_t).total_seconds() / 3600
            if elapsed_h < config.MIN_BUY_INTERVAL_HOURS:
                logger.info(
                    f"⏩ [{prefix}] 직전 매수 후 {elapsed_h:.1f}h 경과 "
                    f"(최소 {config.MIN_BUY_INTERVAL_HOURS:.1f}h 필요) — 매수 스킵"
                )
                return

    # 조건 1: 진입 타점 (지표가 있으면 지표, 없으면 평단 대비 하락률)
    if avg_price > 0:
        dip_pct = (current_price - avg_price) / avg_price * 100
    else:
        dip_pct = 0

    if signal is not None:
        entry_ok = signal["buy_signal"]
        entry_desc = f"지표 매수신호 (RSI {signal['rsi']:.1f} ≤ {config.RSI_OVERSOLD:g} + BB 하단 이탈)"
    else:
        # 보유 없으면 최초 매수 허용 (기존 하락률 기준)
        entry_ok = (avg_price == 0) or (dip_pct <= config.DIP_THRESHOLD)
        entry_desc = f"딥 하락률 {dip_pct:.2f}% (기준: {config.DIP_THRESHOLD}%)"

    # 조건 2: AI 점수 체크
    ai_ok = ai_score >= config.AI_SCORE_FLOOR

    logger.info(
        f"[{prefix} 매수 조건] {entry_desc} → {'✅' if entry_ok else '❌'} | "
        f"AI점수: {ai_score} (기준: ≥{config.AI_SCORE_FLOOR}) "
        f"→ {'✅' if ai_ok else '❌'} | "
        f"매수 횟수: {buy_count}/{config.MAX_BUYS_PER_DAY}"
    )

    if not (entry_ok and ai_ok):
        logger.info(f"❌ [{prefix}] 매수 조건 미충족 — 대기")
        return

    # 매수 금액 = 총자산 × 1회 비중, 예수금 한도 내
    buy_krw = total_krw * ratio
    buy_krw = min(buy_krw, cash_krw * 0.99)

    if buy_krw <= 0:
        logger.warning(f"⚠️ [{prefix}] 주문가능금 부족({cash_krw:,.0f}원) — 매수 불가")
        return

    # 현금 조회에 지연이 있으므로 실제 주문가능금으로 재확인 (클램핑 역할)
    try:
        orderable = buy_fn.get_orderable(buy_krw)
        if orderable > 0:
            buy_krw = min(buy_krw, orderable)
    except Exception as e:
        logger.debug(f"[{prefix}] 주문가능금 재확인 실패, 계산값 사용: {e}")

    logger.info(
        f"🚀 [{prefix}] 매수 실행 ({buy_count + 1}/{config.MAX_BUYS_PER_DAY}회차): "
        f"예산 {buy_krw:,.0f}원 (총자산의 {ratio * 100:.0f}%)"
    )
    if buy_fn(buy_krw):
        bot_state.record_buy(ticker)
        logger.info(f"✅ [{prefix}] 매수 완료 — 당일 {bot_state.buy_count_today(ticker)}회차")
    else:
        logger.warning(f"⚠️ [{prefix}] 매수 미체결 — 횟수 미기록")


def run_overseas_cycle(ai_score: int):
    """해외주식(QQQ) 매매 사이클."""
    ticker = config.TARGET_TICKER
    balance = kis.get_balance()
    current_price = kis.get_current_price(ticker)
    rate = kis.get_usd_krw_rate()

    def sell_fn(qty: float):
        kis.sell_all(qty, current_price)

    def buy_fn(buy_krw: float) -> bool:
        """원화 예산을 달러로 환산해 정수 수량 매수. 체결되면 True."""
        stock_krw = current_price * rate
        target_qty = int(buy_krw // stock_krw)
        if target_qty < 1:
            logger.warning(
                f"⚠️ [해외] 예산({buy_krw:,.0f}원)이 1주 가격({stock_krw:,.0f}원)보다 작아 매수 불가"
            )
            return False
        kis.buy_shares(target_qty, current_price)
        logger.info(f"   → {target_qty}주 × ${current_price:.2f} (약 {target_qty * stock_krw:,.0f}원)")
        return True

    # 주문가능금 재확인 함수 (buy_fn에 속성으로 부착)
    def _orderable(budget_krw: float) -> float:
        return 0.0   # 해외는 잔고 API의 통합증거금 값을 신뢰
    buy_fn.get_orderable = _orderable

    balance_norm = {
        "quantity": balance["quantity"],
        "avg_price": balance["avg_price_usd"],
        "profit_rate": balance["profit_rate"],
        "total_asset_krw": balance["total_asset_krw"],
        "cash_krw": balance["cash_krw"],
    }
    _run_cycle(
        "해외", ticker, balance_norm, current_price, ai_score,
        config.BUY_RATIO, lambda p: f"${p:,.2f}", sell_fn, buy_fn,
        lambda: kis.get_daily_closes(ticker),
    )


def run_domestic_cycle(ai_score: int):
    """국내주식 ETF 매매 사이클 (지정가 주문)."""
    ticker = config.DOMESTIC_TICKER
    balance = kd.get_domestic_balance(ticker)
    current_price = kd.get_domestic_price(ticker)

    def sell_fn(qty: float):
        kd.sell_domestic_all(ticker, int(qty), current_price)

    def buy_fn(buy_krw: float) -> bool:
        """원화 예산으로 정수 수량 매수. 체결되면 True."""
        target_qty = int(buy_krw // current_price)
        if target_qty < 1:
            logger.warning(
                f"⚠️ [국내] 예산({buy_krw:,.0f}원)이 1주 가격({current_price:,.0f}원)보다 작아 매수 불가"
            )
            return False
        kd.buy_domestic(ticker, target_qty, current_price)
        logger.info(f"   → {target_qty}주 × {current_price:,.0f}원 (약 {target_qty * current_price:,.0f}원)")
        return True

    def _orderable(budget_krw: float) -> float:
        return kd.get_orderable_cash(ticker, current_price)
    buy_fn.get_orderable = _orderable

    balance_norm = {
        "quantity": balance["quantity"],
        "avg_price": balance["avg_price_krw"],
        "profit_rate": balance["profit_rate"],
        "total_asset_krw": balance["total_asset_krw"],
        "cash_krw": balance["cash_krw"],
    }
    _run_cycle(
        "국내", ticker, balance_norm, current_price, ai_score,
        config.DOMESTIC_BUY_RATIO, lambda p: f"{p:,.0f}원", sell_fn, buy_fn,
        lambda: kd.get_daily_closes(ticker),
    )
