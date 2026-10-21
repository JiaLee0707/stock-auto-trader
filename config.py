"""
설정 로드 모듈.
모의/실전 도메인을 IS_MOCK_TRADING 플래그로 자동 전환.
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ── 모드 판정 (T01b: IS_MOCK_TRADING 단일 기준) ──────────
# 모의/실전 판정은 IS_MOCK_TRADING 하나로만 한다.
# 허용값은 true/false(대소문자 무시)뿐이며, 그 외 값은 조용히 실전으로 넘어가지 않고 종료한다.
_LIVE_CONFIRM_VALUE = "YES_I_UNDERSTAND"


def resolve_is_mock(raw: str | None) -> bool | None:
    """
    IS_MOCK_TRADING 문자열을 모의 여부로 해석한다.

    반환: True(모의) / False(실전) / None(허용되지 않는 값).
    "true"/"false" 만 허용하며 대소문자·앞뒤 공백은 무시한다.
    미설정(None)은 모의(True)가 기본값이지만, 여기서는 호출부가 기본값을 채운다.
    """
    if raw is None:
        return None
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def _load_is_mock() -> bool:
    """환경변수를 읽어 모의 여부를 확정한다. 값이 잘못되면 로그 후 즉시 종료한다."""
    raw = os.getenv("IS_MOCK_TRADING", "True")
    resolved = resolve_is_mock(raw)
    if resolved is None:
        message = (
            f"IS_MOCK_TRADING 값이 올바르지 않습니다: {raw!r}. "
            "true 또는 false 만 허용합니다(대소문자 무시). "
            "잘못된 값으로 실전 주문이 나가지 않도록 종료합니다."
        )
        logging.getLogger("bot").critical(message)
        # 로깅이 아직 설정되지 않았어도 이유가 보이도록 SystemExit 메시지로도 남긴다.
        raise SystemExit(message)
    return resolved


IS_MOCK = _load_is_mock()

# TRADING_MODE 는 모드 판정에 쓰지 않는다. 표시용으로만 IS_MOCK 에서 유도한다.
TRADING_MODE = "paper" if IS_MOCK else "live"


def check_trading_mode_env(derived_mode: str, raw: str | None) -> None:
    """
    TRADING_MODE 가 유도값과 충돌하면 즉시 종료한다.

    모드 판정이 두 곳으로 갈라지면 오설정이 실전 오발주로 이어질 수 있으므로,
    어긋난 값을 무시하지 않고 시작 단계에서 멈춘다.
    """
    if raw is None:
        return
    value = raw.strip().lower()
    if not value or value == derived_mode:
        return
    message = (
        f"TRADING_MODE={value!r} 가 IS_MOCK_TRADING 에서 유도한 모드({derived_mode!r})와 다릅니다. "
        "모드 판정은 IS_MOCK_TRADING 하나로만 하므로, 혼동을 막기 위해 종료합니다. "
        "TRADING_MODE 환경변수를 제거하거나 IS_MOCK_TRADING 과 일치시키세요."
    )
    logging.getLogger("bot").critical(message)
    raise SystemExit(message)


check_trading_mode_env(TRADING_MODE, os.getenv("TRADING_MODE"))

CONFIRM_LIVE = os.getenv("CONFIRM_LIVE", "")


def live_guard_error(is_mock: bool, confirm_live: str) -> str | None:
    """실전 모드인데 확인값이 없으면 오류 메시지를, 아니면 None을 돌려준다."""
    if is_mock:
        return None
    if confirm_live == _LIVE_CONFIRM_VALUE:
        return None
    return (
        f"IS_MOCK_TRADING=False(실전) 인데 CONFIRM_LIVE={_LIVE_CONFIRM_VALUE} 가 없습니다. "
        "실전 주문을 막기 위해 종료합니다."
    )


def enforce_trading_mode() -> None:
    """가드 위반 시 로그를 남기고 SystemExit로 즉시 종료한다."""
    error = live_guard_error(IS_MOCK, CONFIRM_LIVE)
    if error:
        logging.getLogger("bot").critical(error)
        raise SystemExit(1)


def assert_order_allowed() -> None:
    """
    주문 직전 방어선. 실전 모드인데 확인값이 없으면 예외를 던진다.
    main() 을 거치지 않고 주문 함수가 직접 호출돼도 주문이 나가지 않게 한다.
    """
    error = live_guard_error(IS_MOCK, CONFIRM_LIVE)
    if error:
        raise RuntimeError(error)


def mask(value, keep: int = 4) -> str:
    """비밀값·계좌번호를 마스킹한다. 뒤 keep자리만 남기고 나머지는 *로 가린다."""
    if value is None:
        return ""
    text = str(value)
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]

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

# ── 상태 저장소 (T03) ────────────────────────────────────
# 표준 sqlite3 파일. `data/` 는 .gitignore 대상이라 커밋되지 않는다.
DB_PATH = os.getenv("DB_PATH", "data/bot.db")

# 브로커 잔고와 DB 포지션이 어긋날 때 자동 보정 여부.
# 기본 false(자동 보정 안 함 + 신규 매매 중단). true 일 때만 브로커 값으로 덮어쓴다.
# VERIFY: 자동 보정을 켜면 봇이 만든 포지션이 아닌 것까지 DB 에 편입된다.
#         소액 실전에서는 "중단하고 사람이 확인"이 더 안전하다고 보고 기본을 false 로 두었다.
RECONCILE_AUTOFIX = os.getenv("RECONCILE_AUTOFIX", "false").strip().lower() == "true"

# ── 주문 실행 (T04) ──────────────────────────────────────
# 주문 유형. limit(지정가) / market(시장가). 기본 지정가.
# 시장가는 체결은 잘 되지만 슬리피지를 통제할 수 없어 기본을 지정가로 둔다.
ORDER_TYPE = os.getenv("ORDER_TYPE", "limit").strip().lower()

# 지정가 주문 시 현재가에서 몇 호가(틱) 떨어진 가격으로 낼지.
# 매수는 현재가 + N틱(체결 가능성↑), 매도는 현재가 - N틱. 0이면 현재가 그대로.
# T04b: 기본값을 1 → 0 으로 내렸다. 국내 호가 단위표(kis_domestic._TICK_TABLE)가
#        일반 주식 호가표인지 ETF 호가표인지 근거를 찾지 못했다. 틱을 옮기지 않으면
#        그 표가 틀려도 주문에 영향이 없다. 표가 확인되면 1 이상으로 올린다.
# VERIFY: ETF 호가 단위표 근거 미확인 (docs/kis_api_fields.md "이 파일에 없는 것").
ORDER_PRICE_TICKS = int(os.getenv("ORDER_PRICE_TICKS", "0"))

# 미체결 주문을 이 시간(분)까지 기다렸다가 취소한다. 0 이하면 자동 취소 비활성.
ORDER_TIMEOUT_MINUTES = float(os.getenv("ORDER_TIMEOUT_MINUTES", "30"))