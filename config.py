"""
설정 로드 모듈.
모의/실전 도메인을 IS_MOCK_TRADING 플래그로 자동 전환.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

# 기본값 설정
IS_MOCK = os.getenv("IS_MOCK_TRADING", "True").lower() == "true"

# KIS 도메인 자동 전환
KIS_BASE_URL = (
    "https://openapivts.koreainvestment.com:29443"  # 모의투자
    if IS_MOCK
    else "https://openapi.koreainvestment.com:9443"  # 실전투자
)

# 필수 환경 변수 로드 및 검증
# KIS_APP_KEY    = os.getenv("KIS_APP_KEY")
# KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
# KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO")
# GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# IS_MOCK에 따라 모의/실전 키 자동 선택 (단일 키 KIS_APP_KEY도 fallback 지원)
if IS_MOCK:
    KIS_APP_KEY    = os.getenv("KIS_MOCK_APP_KEY") or os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_MOCK_APP_SECRET") or os.getenv("KIS_APP_SECRET")
    KIS_ACCOUNT_NO = os.getenv("KIS_MOCK_ACCOUNT_NO") or os.getenv("KIS_ACCOUNT_NO")
else:
    KIS_APP_KEY    = os.getenv("KIS_REAL_APP_KEY") or os.getenv("KIS_APP_KEY")
    KIS_APP_SECRET = os.getenv("KIS_REAL_APP_SECRET") or os.getenv("KIS_APP_SECRET")
    KIS_ACCOUNT_NO = os.getenv("KIS_REAL_ACCOUNT_NO") or os.getenv("KIS_ACCOUNT_NO")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# API 키 누락 시 경고
if not KIS_APP_KEY:
    logging.getLogger("bot").warning("KIS_APP_KEY가 설정되지 않았습니다")
if not KIS_APP_SECRET:
    logging.getLogger("bot").warning("KIS_APP_SECRET이 설정되지 않았습니다")
if not KIS_ACCOUNT_NO:
    logging.getLogger("bot").warning("KIS_ACCOUNT_NO가 설정되지 않았습니다")
if not GEMINI_API_KEY and not IS_MOCK:
    logging.getLogger("bot").warning("GEMINI_API_KEY가 설정되지 않았습니다 (모의투자 시 무시)")

TARGET_TICKER      = os.getenv("TARGET_TICKER", "QQQ")
TARGET_TICKER_NAME = os.getenv("TARGET_TICKER_NAME", "QQQ")

# 해외주식 운용 여부. QQQ는 1주에 약 98만원이라 소액 자본에서는 매수가 불가능하다.
# 1회 매수 예산(BUY_RATIO × 총자산)이 1주 가격보다 작으면 주문이 계속 스킵된다.
# 실전투자(소액)는 기본값 False — 국내 ETF만 운용한다. 모의투자는 그대로 True.
_overseas_default = "False" if not IS_MOCK else "True"
IS_OVERSEAS_ENABLED   = os.getenv("IS_OVERSEAS_ENABLED", _overseas_default).lower() == "true"

# 국내 ETF (해외와 동시 운용)
IS_DOMESTIC_ENABLED   = os.getenv("IS_DOMESTIC_ENABLED", "True").lower() == "true"
DOMESTIC_TICKER       = os.getenv("DOMESTIC_TICKER", "379800")   # TIGER 미국나스닥100
DOMESTIC_TICKER_NAME  = os.getenv("DOMESTIC_TICKER_NAME", "TIGER 미국나스닥100")

# AI 분석용 뉴스 수집 (RSS, 콤마 구분으로 교체 가능)
NEWS_FEEDS = [
    u.strip() for u in os.getenv(
        "NEWS_FEEDS",
        "https://news.google.com/rss/search?q=nasdaq+stock+market&hl=en-US&gl=US&ceid=US:en,"
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=QQQ&region=US&lang=en-US",
    ).split(",") if u.strip()
]
NEWS_MAX_ITEMS   = int(os.getenv("NEWS_MAX_ITEMS", "10"))
NEWS_TIMEOUT_SEC = float(os.getenv("NEWS_TIMEOUT_SEC", "8"))

# 1회 매수 비중 (다회 매수 전제). 하루 최대 노출 = 이 값 × MAX_BUYS_PER_DAY
# 균형 프로필: 해외 15% × 2회 = 30%, 국내 10% × 2회 = 20%
BUY_RATIO              = float(os.getenv("BUY_RATIO", "0.15"))
DOMESTIC_BUY_RATIO     = float(os.getenv("DOMESTIC_BUY_RATIO", "0.10"))
DIP_THRESHOLD          = float(os.getenv("DIP_THRESHOLD", "-1.5"))

# ── 보조지표 (RSI + 볼린저밴드, 눌림목 매매) ──────────────
USE_INDICATORS         = os.getenv("USE_INDICATORS", "True").lower() == "true"
RSI_PERIOD             = int(os.getenv("RSI_PERIOD", "14"))
RSI_OVERSOLD           = float(os.getenv("RSI_OVERSOLD", "30"))     # 이하면 매수 후보
RSI_OVERBOUGHT         = float(os.getenv("RSI_OVERBOUGHT", "70"))   # 이상이면 매도 후보
BB_PERIOD              = int(os.getenv("BB_PERIOD", "20"))
BB_NUM_STD             = float(os.getenv("BB_NUM_STD", "2.0"))
TAKE_PROFIT_THRESHOLD  = float(os.getenv("TAKE_PROFIT_THRESHOLD", "5.0"))
AI_SCORE_FLOOR         = int(os.getenv("AI_SCORE_FLOOR", "-50"))

# ── 스노우볼 전략 (균형 프로필) ──────────────────────────
# 매수: 종목당 하루 MAX_BUYS_PER_DAY회까지 분할 매수 (모든 회차 평단 대비 DIP_THRESHOLD 기준)
MAX_BUYS_PER_DAY       = int(os.getenv("MAX_BUYS_PER_DAY", "2"))
MIN_BUY_INTERVAL_HOURS = float(os.getenv("MIN_BUY_INTERVAL_HOURS", "2.0"))

# 부분 익절: (수익률 %, 매도 비중 %) 단계. 마지막 단계는 남은 수량 전량 매도.
# 예: (2.0, 33) → +2% 도달 시 보유의 약 1/3 매도
TAKE_PROFIT_STEPS = [
    tuple(float(v) for v in item.split(":"))
    for item in os.getenv("TAKE_PROFIT_STEPS", "2.0:33,4.0:33,6.0:100").split(",")
    if item.strip()
]

# 손절: 수익률이 이 값 이하로 떨어지면 전량 매도 (0 이하 = 비활성)
STOP_LOSS_THRESHOLD    = float(os.getenv("STOP_LOSS_THRESHOLD", "-5.0"))

# 모의/실전 토큰 캐시 충돌 방지
TOKEN_CACHE_FILE = "token_cache_mock.json" if IS_MOCK else "token_cache_real.json"
LOG_FILE         = "logs/bot.log"