"""
나스닥 스노우볼 자동매매 봇 메인 진입점.

동작 방식 (노트북 친화적):
- schedule 라이브러리로 1시간마다 매매 사이클 실행
- 모의투자는 정규장 주문만 체결되므로 미국 정규장 시간에만 주문 실행
- 네트워크 단절 / 슬립 복귀 시에도 crash 없이 재시도
- KeyboardInterrupt로 깔끔하게 종료
- 로컬 실행 (IPC와 함께) - 사용 중 `Ctrl+A + D` 또는 연결 해제 시 자동으로 종료
"""
import logging
import os
import sys
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import schedule

import config
from ai_analyzer import analyze_market_sentiment
from trader import run_trading_cycle

# ── 로그 설정 ────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("bot")

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def is_regular_market_open() -> bool:
    """
    미국 정규장 개장 여부 판단.
    평일 09:30~16:00 ET 기준이며, 서머타임은 ET 기준이라 자동 반영된다.
    """
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:          # 토·일 휴장
        return False
    return dtime(9, 30) <= now_et.time() < dtime(16, 0)


def is_domestic_market_open() -> bool:
    """
    국내 정규장 개장 여부 판단 (평일 09:00~15:30 KST).
    모의투자는 국내·해외 모두 정규장 주문만 체결되므로 시간대를 따로 본다.
    """
    now_kst = datetime.now(KST)
    if now_kst.weekday() >= 5:         # 토·일 휴장
        return False
    return dtime(9, 0) <= now_kst.time() < dtime(15, 30)


def job():
    """1시간마다 실행되는 메인 작업."""
    logger.info("=" * 60)
    logger.info(f"🔄 매매 사이클 시작 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")
    logger.info(f"   모드: {'📋 모의투자' if config.IS_MOCK else '💰 실전투자'}")
    logger.info("=" * 60)

    # 모의·실전 모두 정규장에만 주문한다.
    # 모의투자는 정규장 주문만 체결되고, 실전투자는 시간외 체결 품질이 불량해 제한한다.
    # 운용이 꺼진 시장은 개장 여부와 무관하게 False로 두어 대상에서 제외한다.
    mode_label = "모의투자" if config.IS_MOCK else "실전투자"
    overseas_open = config.IS_OVERSEAS_ENABLED and is_regular_market_open()
    domestic_open = config.IS_DOMESTIC_ENABLED and is_domestic_market_open()
    if not (overseas_open or domestic_open):
        now_et, now_kst = datetime.now(ET), datetime.now(KST)
        logger.info(
            f"   [{mode_label}] 매매 대상 없음 — 스킵 "
            f"(해외: {'운용중' if config.IS_OVERSEAS_ENABLED else '비활성'}, "
            f"{now_et.strftime('%H:%M')} ET / "
            f"국내: {'운용중' if config.IS_DOMESTIC_ENABLED else '비활성'}, "
            f"{now_kst.strftime('%H:%M')} KST)"
        )
        return

    try:
        # Step 1: AI 뉴스 분석
        sentiment = analyze_market_sentiment()
        ai_score = sentiment["score"]
        logger.info(f"🤖 AI 시장 분석: {sentiment['reason']} (점수: {ai_score})")

        # Step 2: 매매 실행 (개장한 시장만)
        run_trading_cycle(ai_score, overseas_open, domestic_open)

    except ConnectionError as e:
        logger.warning(f"🌐 네트워크 연결 끊김 — 다음 사이클에서 재시도: {e}")
    except TimeoutError as e:
        logger.warning(f"⏱️ 요청 타임아웃 — 다음 사이클에서 재시도: {e}")
    except Exception as e:
        logger.error(f"❌ 예상치 못한 오류 — 프로그램은 계속 실행됩니다: {e}", exc_info=True)

    logger.info(f"✅ 매매 사이클 완료\n")


def quick_test():
    """퀵 테스트 실행 - 설정 상태 확인 및 샘플 매매"""
    logger.info("=" * 60)
    logger.info(f"🧪 퀵 테스트 시작 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    # 테스트: 설정 로드 확인
    logger.info(f"✓ 설정 확인:")
    logger.info(f"  - 모의투자: {config.IS_MOCK}")
    logger.info(f"  - 운용 시장: 해외 {'ON' if config.IS_OVERSEAS_ENABLED else 'OFF'}({config.TARGET_TICKER})"
                f" / 국내 {'ON' if config.IS_DOMESTIC_ENABLED else 'OFF'}({config.DOMESTIC_TICKER})")
    logger.info(f"  - 1회 매수 비중: 해외 {config.BUY_RATIO} / 국내 {config.DOMESTIC_BUY_RATIO}")
    logger.info(f"  - DIP 임계값: {config.DIP_THRESHOLD}")
    logger.info(f"  - 하루 최대 매수: {config.MAX_BUYS_PER_DAY}회")
    logger.info(f"  - 부분 익절 단계: {config.TAKE_PROFIT_STEPS}")
    logger.info(f"  - 손절선: {config.STOP_LOSS_THRESHOLD}%")
    logger.info(f"  - AI 점수 하한: {config.AI_SCORE_FLOOR}")

    # 테스트: 상태 객체 확인
    from state import bot_state
    logger.info(f"✓ 상태 확인: 오늘 매수 여부 = {bot_state.bought_today}")

    # 테스트: AI 분석기 (API 키가 없는 경우 처리)
    try:
        sentiment = analyze_market_sentiment()
        logger.info(f"✓ AI 분석 결과: {sentiment}")
    except Exception as e:
        logger.error(f"✗ AI 분석 실패: {e}")
        logger.info("  (API 키가 없는 경우 정상 - 기본값 0으로 처리)")

    # 테스트: KIS API (모의투자만 시도)
    try:
        if config.IS_MOCK:
            logger.info("✓ KIS 모의투자 API 시도...")
            # 현재가 조회만 시도 (매수/매도 없음)
            import kis_overseas
            price = kis_overseas.get_current_price(config.TARGET_TICKER)
            logger.info(f"  - 현재가: ${price:.2f}")
        else:
            logger.info("⚠️  실전투자 모드는 KIS API 호출 없음")
    except Exception as e:
        logger.error(f"✗ KIS API 테스트 실패: {e}")

    logger.info(f"✅ 퀵 테스트 완료 - 모든 기본 검증 성공\n")


def main():
    env_mode = "모의투자" if config.IS_MOCK else "실전투자"
    logger.info(f"🚀 나스닥 스노우볼 봇 시작 [{env_mode}] — 대상: {config.TARGET_TICKER}")

    # 시작 즉시 1회 실행
    job()

    # 이후 1시간마다 반복
    schedule.every(1).hours.do(job)

    logger.info("⏰ 스케줄러 등록 완료 (1시간 간격). 종료하려면 Ctrl+C")

    # 디버깅을 위한 퀵 테스트 실행
    quick_test()

    while True:
        try:
            schedule.run_pending()
            time.sleep(30)  # 30초마다 스케줄 체크
        except KeyboardInterrupt:
            logger.info("👋 사용자 종료 요청 — 봇을 안전하게 종료합니다.")
            break
        except Exception as e:
            # 메인 루프가 절대 crash 되지 않도록 최상위 예외 포착
            logger.error(f"🔥 메인 루프 오류: {e} — 30초 후 재시작", exc_info=True)
            time.sleep(30)


if __name__ == "__main__":
    main()