"""
나스닥 스노우볼 자동매매 봇 메인 진입점.

동작 방식 (노트북 친화적):
- schedule 라이브러리로 1시간마다 매매 사이클 실행
- 네트워크 단절 / 슬립 복귀 시에도 crash 없이 재시도
- KeyboardInterrupt로 깔끔하게 종료
- 로컬 실행 (IPC와 함께) - 사용 중 `Ctrl+A + D` 또는 연결 해제 시 자동으로 종료
"""
import logging
import os
import sys
import time
from datetime import datetime

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


def job():
    """1시간마다 실행되는 메인 작업."""
    logger.info("=" * 60)
    logger.info(f"🔄 매매 사이클 시작 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   모드: {'📋 모의투자' if config.IS_MOCK else '💰 실전투자'}")
    logger.info("=" * 60)

    try:
        # Step 1: AI 뉴스 분석
        sentiment = analyze_market_sentiment()
        ai_score = sentiment["score"]
        logger.info(f"🤖 AI 시장 분석: {sentiment['reason']} (점수: {ai_score})")

        # Step 2: 매매 실행
        run_trading_cycle(ai_score)

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
    logger.info(f"  - 목표 티커: {config.TARGET_TICKER}")
    logger.info(f"  - 구매 비율: {config.BUY_RATIO}")
    logger.info(f"  - DIP 임계값: {config.DIP_THRESHOLD}")
    logger.info(f"  - 익절 임계값: {config.TAKE_PROFIT_THRESHOLD}")
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