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
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import schedule

import config
import db
import market_calendar
import reconcile
import sizing
from ai_analyzer import analyze_market_sentiment
from state import get_bot_state
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
    미국 정규장 개장 여부 판단 (T05: `market_calendar` 로 대체).

    이전에는 "평일이고 09:30<=t<16:00 ET" 를 직접 계산했다. 그 방식은 **휴장일과
    반일장을 보지 못해** 휴장일에 주문을 내고 `장시작전`/`장종료` 거부를 받았다.
    지금은 XNYS 캘린더가 휴장일·반일장·서머타임을 모두 반영한다.

    하위 호환용 이름이다. 새 코드는 `market_calendar.is_market_open("US", now)` 를
    직접 쓰는 것이 낫다 — 주문 가능 여부는 `is_order_window` 로 판단해야 한다.
    """
    return market_calendar.is_market_open(market_calendar.MARKET_US, now_et())


def is_domestic_market_open() -> bool:
    """
    국내 정규장 개장 여부 판단 (T05: `market_calendar` 로 대체).

    이전에는 "평일이고 09:00<=t<15:30 KST" 를 직접 계산했다. 그 방식은 설·추석
    연휴, 크리스마스 같은 휴장일을 보지 못했다. 지금은 XKRX 캘린더가 판정한다.
    """
    return market_calendar.is_market_open(market_calendar.MARKET_KRX, now_kst())


def now_kst() -> datetime:
    """현재 시각 (KST, tz-aware)."""
    return datetime.now(KST)


def now_et() -> datetime:
    """현재 시각 (ET, tz-aware). 서머타임은 ZoneInfo 가 반영한다."""
    return datetime.now(ET)


# 시장 이름 → (운용 여부, 사람이 읽을 이름, 시간대). 주문 구간 판정에 쓴다.
_MARKET_TABLE = (
    (market_calendar.MARKET_US, lambda: config.IS_OVERSEAS_ENABLED, "해외(미국)"),
    (market_calendar.MARKET_KRX, lambda: config.IS_DOMESTIC_ENABLED, "국내"),
)


def market_windows(now=None) -> dict:
    """
    운용 중인 시장별 **주문 가능 여부**와 사유를 계산한다 (T05).

    반환: {시장 이름: {"enabled": bool, "open": bool, "orderable": bool, "reason": str}}

    `orderable` 이 곧 "이 시장에 주문을 내도 되는가"다. `open` 은 거래소가 열려
    있는지만 보고, 주문은 개장 후 `SKIP_OPEN_MINUTES` / 마감 전
    `SKIP_CLOSE_MINUTES` 마진까지 본 `is_order_window` 로 판단한다.
    """
    now = now or now_kst()
    result = {}
    for market, enabled_fn, label in _MARKET_TABLE:
        enabled = bool(enabled_fn())
        local = now.astimezone(market_calendar.market_timezone(market))
        if not enabled:
            reason = "운용 비활성"
            is_open = orderable = False
        else:
            is_open = market_calendar.is_market_open(market, local)
            orderable = market_calendar.is_order_window(market, local)
            if orderable:
                reason = "주문 가능"
            elif is_open:
                # 장은 열렸지만 안전 마진 안이다 — 여기서 주문하면 미체결·거부가 난다.
                reason = (
                    f"안전 마진 구간 (개장 후 {config.SKIP_OPEN_MINUTES}분 / "
                    f"마감 전 {config.SKIP_CLOSE_MINUTES}분 제외)"
                )
            else:
                reason = "정규장 아님 (휴장일 포함)"
        result[market] = {
            "enabled": enabled,
            "label": label,
            "open": is_open,
            "orderable": orderable,
            "reason": reason,
            "local_time": local,
        }
    return result


def job():
    """1시간마다 실행되는 메인 작업."""
    logger.info("=" * 60)
    logger.info(f"🔄 매매 사이클 시작 — {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} KST")
    logger.info(f"   모드: {'📋 모의투자' if config.IS_MOCK else '💰 실전투자'}")
    logger.info("=" * 60)

    # 매 사이클 시작 시 브로커 ↔ DB 대조 (T03).
    #
    # **장 외 시간에도 계속 실행한다** (T05 판단 근거):
    #   - 대조의 목적은 "브로커 실제 상태 ↔ DB" 를 맞추는 것이다. 상태가 어긋난 것을
    #     발견하는 일은 장중에만 의미가 있지 않다 — 오히려 장 외에 발견해야 개장 전에
    #     고칠 수 있다.
    #   - DB 쓰기(포지션·주문 상태)도 같은 이유로 계속한다. 미체결 주문이 장 마감으로
    #     끝났는지 정리하는 것도 이때 한다.
    #   - 반대로 **주문은 장 외에 절대 나가면 안 되므로** 아래에서 `orderable` 로 막는다.
    #     "DB 쓰기를 스킵"하면 장 외에 쌓인 미확정 주문이 개장까지 방치된다.
    # 대조 실패는 매매를 시작하지 않는 사유다 — 상태를 모르는 채 주문하면 위험하다.
    try:
        conn = get_bot_state().conn
        reconcile.reconcile(conn)
    except db.DatabaseUnavailable as e:
        logger.critical(f"🚨 상태 DB 사용 불가 — 매매 사이클을 건너뜁니다: {e}")
        return
    except Exception as e:
        logger.error(f"❌ 브로커 대조 실패 — 매매 사이클을 건너뜁니다: {e}", exc_info=True)
        return

    # 모의·실전 모두 정규장에만 주문한다.
    # 모의투자는 정규장 주문만 체결되고, 실전투자는 시간외 체결 품질이 불량해 제한한다.
    # `market_windows` 는 휴장일·반일장·서머타임을 캘린더로 판정하고, 개장 직후·
    # 마감 직전의 안전 마진까지 본다 (T05).
    mode_label = "모의투자" if config.IS_MOCK else "실전투자"
    windows = market_windows()
    us_win = windows[market_calendar.MARKET_US]
    kr_win = windows[market_calendar.MARKET_KRX]
    overseas_open = us_win["orderable"]
    domestic_open = kr_win["orderable"]

    summary = " / ".join(
        f"{w['label']}: {w['reason']} ({w['local_time'].strftime('%H:%M')} 현지)"
        for w in windows.values()
    )
    if not (overseas_open or domestic_open):
        # 주문 가능 구간이 아니면 **AI 분석·시세 조회 등 어떤 API 도 호출하지 않는다.**
        # (로그만 남기고 돌아간다 — 위에서 reconcile 은 이미 돌았다.)
        logger.info(f"   [{mode_label}] 주문 가능 구간 아님 — API 호출 없이 스킵 ({summary})")
        return
    logger.info(f"   [{mode_label}] 주문 구간 확인 — {summary}")

    try:
        # Step 1: AI 뉴스 분석 — 실패해도 매매 사이클(청산 포함)은 실행한다 (T09b).
        # `analyze_market_sentiment` 는 Gemini 호출만 try 로 감싸고, 시세·뉴스 수집과 프롬프트 구성은 밖에서 한다.
        # 거기서 예외가 새면 아래 매매 사이클이 통째로 건너뛰어져 손절도 나가지 않는다.
        # 점수를 모르면 None 으로 넘긴다 — `strategy.decide()` 는 AI 필터가 켜져 있을 때 None 이면
        # 신규 매수만 보류하고 청산은 그대로 판단한다. (AI 가 내부에서 실패해 0점을 돌려주는 기존 동작은 T10 에서 다룬다.)
        ai_score = None
        try:
            sentiment = analyze_market_sentiment()
            ai_score = sentiment["score"]
            logger.info(f"🤖 AI 시장 분석: {sentiment['reason']} (점수: {ai_score})")
        except Exception as e:
            ai_score = None   # 일부만 채워졌어도 점수는 신뢰하지 않는다
            logger.error(
                f"🤖 AI 분석 단계 실패({type(e).__name__}) — 점수 없이 매매 사이클을 계속합니다"
                f"(신규 매수만 보류, 청산은 계속): {e}"
            )

        # Step 2: 매매 실행 (개장한 시장만)
        run_trading_cycle(ai_score, overseas_open, domestic_open)

    except ConnectionError as e:
        logger.warning(f"🌐 네트워크 연결 끊김 — 다음 사이클에서 재시도: {e}")
    except TimeoutError as e:
        logger.warning(f"⏱️ 요청 타임아웃 — 다음 사이클에서 재시도: {e}")
    except Exception as e:
        logger.error(f"❌ 예상치 못한 오류 — 프로그램은 계속 실행됩니다: {e}", exc_info=True)

    logger.info(f"✅ 매매 사이클 완료\n")


def log_capital_summary():
    """
    시작 시 "현재 자본 / 살 수 있는 종목 / 못 사는 종목과 필요 자본"을 남긴다 (T07).

    왜 필요한가: 사이징이 "위험 1% / 현금 한도"로 바뀌면서, 자본이 작으면 0주가
    나와 **주문이 아예 나가지 않는다**. 로그가 없으면 사용자는 봇이 고장 난 줄 알고
    임계값을 완화하려 든다. 필요한 자본을 함께 보여 주면 "얼마를 넣어야 하는가"를
    바로 알 수 있다. 규칙을 조용히 완화하지 않는 것이 목적이다.

    자본·시세 조회는 **읽기 전용**이다(주문 없음). 조회 실패는 시작을 막지 않는다 —
    이 로그가 없어도 매매 안전성에는 영향이 없다.
    """
    try:
        equity = cash = 0.0
        entries = []      # (ticker, 이름, 1주 가격(원))

        if config.IS_OVERSEAS_ENABLED:
            try:
                import kis_overseas
                bal = kis_overseas.get_balance()
                equity = max(equity, float(bal.get("total_asset_krw") or 0.0))
                cash = max(cash, float(bal.get("cash_krw") or 0.0))
                rate = float(kis_overseas.get_usd_krw_rate() or 0.0)
                price_usd = float(kis_overseas.get_current_price(config.TARGET_TICKER))
                entries.append((config.TARGET_TICKER, config.TARGET_TICKER_NAME,
                                price_usd * rate))
            except Exception as e:
                logger.warning(f"[자본 점검] 해외 조회 실패: {e}")

        if config.IS_DOMESTIC_ENABLED:
            try:
                import kis_domestic
                bal = kis_domestic.get_domestic_balance(config.DOMESTIC_TICKER)
                equity = max(equity, float(bal.get("total_asset_krw") or 0.0))
                cash = max(cash, float(bal.get("cash_krw") or 0.0))
                entries.append((config.DOMESTIC_TICKER, config.DOMESTIC_TICKER_NAME,
                                float(kis_domestic.get_domestic_price(config.DOMESTIC_TICKER))))
            except Exception as e:
                logger.warning(f"[자본 점검] 국내 조회 실패: {e}")

        if equity <= 0:
            logger.warning("[자본 점검] 자본을 조회하지 못해 요약을 건너뜁니다")
            return

        capped_eq, capped_cash = sizing.apply_capital_cap(equity, cash)
        if config.CAPITAL_CAP_KRW > 0:
            logger.info(
                f"💰 현재 자본 {equity:,.0f}원 → 상한 적용 {capped_eq:,.0f}원 "
                f"(예수금 {cash:,.0f} → {capped_cash:,.0f}원, CAPITAL_CAP_KRW)"
            )
        else:
            logger.info(f"💰 현재 자본 {equity:,.0f}원 (예수금 {cash:,.0f}원), 자본 상한 없음")

        plans = [sizing.size_entry(t, capped_eq, capped_cash, price, name=name)
                 for t, name, price in entries]
        for line in sizing.describe_universe(plans).splitlines():
            logger.info(f"   {line}")
        logger.info(
            f"   규칙: 위험 {config.RISK_PER_TRADE_PCT}% / 포지션 {config.MAX_POSITION_PCT}% "
            f"/ 동시 {config.MAX_OPEN_POSITIONS}종목 / 총노출 {config.MAX_TOTAL_EXPOSURE_PCT}% "
            f"— 0주면 주문하지 않습니다(최소 1주 강제 매수 없음)"
        )
    except Exception as e:
        # 이 로그가 실패해도 봇은 계속 돌아야 한다. 다만 조용히 넘기지 않는다.
        logger.error(f"[자본 점검] 요약 생성 실패: {e}", exc_info=True)


def quick_test():
    """퀵 테스트 실행 - 설정 상태 확인 및 샘플 매매"""
    logger.info("=" * 60)
    # 시각은 tz-aware 로만 다룬다 (절대 규칙 9). 표시용이라도 naive 는 쓰지 않는다.
    logger.info(f"🧪 퀵 테스트 시작 — {now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST")
    logger.info("=" * 60)

    # 테스트: 설정 로드 확인
    logger.info(f"✓ 설정 확인:")
    logger.info(f"  - 모의투자: {config.IS_MOCK}")
    logger.info(f"  - 운용 시장: 해외 {'ON' if config.IS_OVERSEAS_ENABLED else 'OFF'}({config.TARGET_TICKER})"
                f" / 국내 {'ON' if config.IS_DOMESTIC_ENABLED else 'OFF'}({config.DOMESTIC_TICKER})")
    # T07: "총자산 × 비중" 대신 위험·현금 한도로 수량을 정한다. 상한(CAPITAL_CAP_KRW)
    # 이 걸려 있으면 실제 계좌(모의 1,000만원)보다 작은 자본으로 계산된다.
    cap = config.CAPITAL_CAP_KRW
    logger.info(
        f"  - 수량 계산: 위험 {config.RISK_PER_TRADE_PCT}% / 포지션 상한 "
        f"{config.MAX_POSITION_PCT}% / 동시 {config.MAX_OPEN_POSITIONS}종목 / "
        f"총노출 {config.MAX_TOTAL_EXPOSURE_PCT}%"
    )
    logger.info(f"  - 자본 상한: {cap:,.0f}원" if cap > 0 else "  - 자본 상한: 없음(실제 계좌)")
    logger.info(f"  - DIP 임계값: {config.DIP_THRESHOLD}")
    logger.info(f"  - 하루 최대 매수: {config.MAX_BUYS_PER_DAY}회")
    logger.info(f"  - 목표가 익절: +{config.TAKE_PROFIT_PCT}% (전량)")
    logger.info(f"  - 손절선: {config.STOP_LOSS_PCT}% (ATR 배수 {config.STOP_LOSS_ATR_MULT})")
    logger.info(f"  - 최대 보유: {config.MAX_HOLD_TRADING_DAYS}거래일")
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
    # 시작 로그 첫 줄: 현재 모드와 도메인 호스트명. 키·계좌번호는 출력하지 않는다 (T01b)
    mode_label = "모의" if config.IS_MOCK else "실전"
    host = urlsplit(config.KIS_BASE_URL).hostname or "(unknown)"
    logger.info(f"🔒 현재 모드: {mode_label} | 도메인: {host}")

    # 실전 가드: 위반 시 어떤 API 호출보다 먼저 종료 (T01b)
    config.enforce_trading_mode()

    # 더 이상 읽지 않는 `.env` 이름을 알린다 (T07). **값은 출력하지 않고 이름만**
    # 남긴다 — 사용자가 "설정했는데 왜 반영이 안 되나"를 헤매지 않게 하려는 것이다.
    # 경고는 `warn_obsolete_env()` 안에서 한 번만 찍는다(여기서 또 찍으면 중복된다).
    config.warn_obsolete_env()

    # 상태 DB 검증 (T03). 잠김·손상·경로 문제면 **시작을 거부**한다.
    # 상태를 모르는 채 스케줄러를 띄우면 이후 사이클마다 조용히 건너뛰기만 하고,
    # 사용자는 봇이 돌고 있다고 오해한다. 그래서 여기서 즉시 종료한다.
    try:
        probe = db.open_db()
    except db.DatabaseUnavailable as e:
        logger.critical(f"🚨 상태 DB 사용 불가 — 시작을 거부합니다: {e}")
        raise SystemExit(1)
    probe.close()   # 검증용 연결은 바로 닫는다(실제 사용은 get_bot_state() 싱글턴이 한다)

    env_mode = "모의투자" if config.IS_MOCK else "실전투자"
    logger.info(f"🚀 나스닥 스노우볼 봇 시작 [{env_mode}] — 대상: {config.TARGET_TICKER}")

    # 시작 시 자본·매수 가능 종목 요약 (T07). 읽기 전용 조회이며 실패해도 계속한다.
    log_capital_summary()

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