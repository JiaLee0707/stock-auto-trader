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

BUY_RATIO              = float(os.getenv("BUY_RATIO", "0.30"))
DIP_THRESHOLD          = float(os.getenv("DIP_THRESHOLD", "-1.5"))
TAKE_PROFIT_THRESHOLD  = float(os.getenv("TAKE_PROFIT_THRESHOLD", "5.0"))
AI_SCORE_FLOOR         = int(os.getenv("AI_SCORE_FLOOR", "-50"))

# 모의/실전 토큰 캐시 충돌 방지
TOKEN_CACHE_FILE = "token_cache_mock.json" if IS_MOCK else "token_cache_real.json"
LOG_FILE         = "logs/bot.log"