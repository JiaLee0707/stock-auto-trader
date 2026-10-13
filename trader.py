"""
매매 판단 및 실행 모듈.

매수 조건 (AND):
  1. 현재가 < 평균 단가 × (1 + DIP_THRESHOLD/100)  →  -1.5% 이상 하락
  2. AI 점수 >= AI_SCORE_FLOOR                      →  심각한 악재 없음
  3. 금일 미매수                                     →  중복 방지

매도 조건:
  수익률 >= TAKE_PROFIT_THRESHOLD                   →  +5% 도달 시 전량 익절
"""
import logging

import config
import kis_overseas as kis
from state import bot_state

logger = logging.getLogger("bot")


def run_trading_cycle(ai_score: int):
    """1회 매매 사이클 실행."""

    # ── 1. 잔고 및 현재가 조회 ──────────────────────────
    balance = kis.get_balance()
    current_price = kis.get_current_price(config.TARGET_TICKER)
    rate = kis.get_usd_krw_rate()

    qty          = balance["quantity"]
    avg_price    = balance["avg_price_usd"]
    profit_rate  = balance["profit_rate"]
    total_krw    = balance["total_asset_krw"]
    cash_krw     = balance["cash_krw"]

    logger.info(
        f"[현황] 현재가: ${current_price:.2f} | 평균단가: ${avg_price:.2f} | "
        f"수익률: {profit_rate:.2f}% | 총자산: {total_krw:,.0f}원 | "
        f"예수금: {cash_krw:,.0f}원 | AI점수: {ai_score}"
    )

    # ── 2. 매도 조건 체크 ────────────────────────────────
    if qty > 0 and profit_rate >= config.TAKE_PROFIT_THRESHOLD:
        logger.info(f"✅ 익절 조건 충족 ({profit_rate:.2f}%) → 전량 매도 실행")
        try:
            kis.sell_all(qty, current_price)
            bot_state.bought_today = False   # 매도 후 플래그 초기화
        except Exception as e:
            logger.error(f"매도 주문 실패: {e}")
        return

    # ── 3. 매수 조건 체크 ────────────────────────────────
    if bot_state.bought_today:
        logger.info("⏩ 금일 이미 매수 완료 — 매수 스킵")
        return

    # 조건 1: 딥 체크
    if avg_price > 0:
        dip_pct = (current_price - avg_price) / avg_price * 100
    else:
        dip_pct = 0  # 보유 종목 없으면 최초 매수 허용

    dip_ok = (avg_price == 0) or (dip_pct <= config.DIP_THRESHOLD)

    # 조건 2: AI 점수 체크
    ai_ok = ai_score >= config.AI_SCORE_FLOOR

    logger.info(
        f"[매수 조건] 딥 하락률: {dip_pct:.2f}% (기준: {config.DIP_THRESHOLD}%) "
        f"→ {'✅' if dip_ok else '❌'} | "
        f"AI점수: {ai_score} (기준: ≥{config.AI_SCORE_FLOOR}) "
        f"→ {'✅' if ai_ok else '❌'}"
    )

    if dip_ok and ai_ok:
        # 매수 금액 계산 (총자산의 30%)
        buy_krw = total_krw * config.BUY_RATIO
        # 예수금보다 많으면 예수금 한도로 제한
        buy_krw = min(buy_krw, cash_krw * 0.99)

        # 1주당 원화 가격 계산
        stock_krw = current_price * rate
        target_qty = int(buy_krw // stock_krw)

        if target_qty < 1:
            logger.warning(
                f"⚠️ 예산({buy_krw:,.0f}원)이 1주 가격({stock_krw:,.0f}원)보다 작아 매수 불가"
            )
            return

        logger.info(f"🚀 매수 실행: {target_qty}주 (예산 {buy_krw:,.0f}원 중 약 {target_qty * stock_krw:,.0f}원)")
        try:
            kis.buy_shares(target_qty, current_price)
            bot_state.bought_today = True
        except Exception as e:
            logger.error(f"매수 주문 실패: {e}")
    else:
        logger.info("❌ 매수 조건 미충족 — 대기")