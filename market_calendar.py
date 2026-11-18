"""
시장 캘린더 (T05).

휴장일·정규장 시간·서머타임을 `exchange_calendars` 라이브러리의 **공식 캘린더**
(`XKRX` = 한국거래소, `XNYS` = 뉴욕증권거래소) 로 판정한다. 휴장일 표를 직접
만들지 않는다 — 직접 만든 표는 매년 갱신해야 하고, 갱신을 잊으면 휴장일에
주문을 내서 `장시작전`/`장종료` 거부를 받는다(2026-10-18 로그에 4건).

이 모듈이 `main.py` 의 평일+시간 체크를 대체한다.

시간은 **전부 tz-aware** 다(절대 규칙 9). naive datetime 을 받으면 예외를 던진다 —
naive 를 조용히 KST 로 해석하면 서버 시간대 설정에 따라 판정이 달라진다.

시장 이름은 이 모듈 안에서만 `"KRX"` / `"US"` 를 쓴다. `trader.py` 의 DB 저장용
`market` 값(`"domestic"` / `"overseas"`)과는 다른 층의 이름이라, 여기서 상호 변환하지
않는다(호출부가 각자 맞춘다). 이름을 억지로 통일하면 DB 마이그레이션이 필요해진다.
"""
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import config
from errfmt import exc_summary

try:
    import exchange_calendars as xc
except ImportError as e:      # pragma: no cover - 설치돼 있지 않은 환경에서만
    # 조용히 평일+시간 규칙으로 되돌아가지 않는다. 그 fallback 은 휴장일에 주문을 내서
    # 이 Task 가 없애려는 "장시작전/장종료" 거부를 그대로 재현한다.
    # 차라리 import 단계에서 멈춰 원인을 분명히 한다.
    raise ImportError(
        "exchange_calendars 가 필요합니다(휴장일 판정). "
        "`.venv/bin/pip install -r requirements.txt` 로 설치하세요. "
        "휴장일 판정 없이 평일 규칙으로 대체하지 않습니다 — 휴장일 주문 거부가 재발합니다."
    ) from e

logger = logging.getLogger("bot")

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")

# 시장 이름 → (exchange_calendars 이름, 표시 시간대)
MARKET_KRX = "KRX"
MARKET_US = "US"

_MARKETS = {
    MARKET_KRX: ("XKRX", KST, "한국거래소"),
    MARKET_US: ("XNYS", ET, "뉴욕증권거래소"),
}

# 캘린더를 만들 구간. 기본 구간(2006~2027) 밖의 날짜를 물으면 라이브러리가
# MinuteOutOfBounds / DateOutOfBounds 를 던지므로 넉넉히 잡는다.
_RANGE_START = "2015-01-01"
_RANGE_END = "2035-12-31"

# EXTRA_HOLIDAYS 는 config 에서 온다. {시장 이름: ["YYYY-MM-DD", ...]}
_CALENDARS: dict = {}


def market_label(market: str) -> str:
    """시장 이름이 올바른지 확인하고 그대로 돌려준다. 모르는 이름은 예외."""
    if market not in _MARKETS:
        raise ValueError(
            f"모르는 시장 이름입니다: {market!r} (사용 가능: {sorted(_MARKETS)})"
        )
    return market


def market_timezone(market: str):
    """시장의 표시 시간대를 돌려준다 (KRX=Asia/Seoul, US=America/New_York)."""
    return _MARKETS[market_label(market)][1]


def extra_holidays(market: str) -> set:
    """
    라이브러리가 모르는 **임시 휴장일**을 `config.EXTRA_HOLIDAYS` 에서 읽는다.

    예: 임시공휴일, 국가 애도일, 거래소가 공지한 휴장. 라이브러리 갱신을 기다리지 않고
    설정 한 줄로 막을 수 있어야 한다. 설정에는 그 시장의 **날짜**("YYYY-MM-DD")를 넣는다.

    날짜를 해석할 수 없으면 조용히 넘기지 않고 예외를 던진다 — 오타 하나로
    휴장일 차단이 무력화되면 안 된다.

    반환: `datetime.date` 의 set.

    구현 메모: 라이브러리의 `adhoc_holidays` 를 건드리지 않고 **이 모듈에서 직접**
    판정한다. 그 property 는 읽기 전용이라 인스턴스에서 덮어쓸 수 없고, 상속으로
    우회하려면 라이브러리 내부 클래스(`exchange_calendars.calendar_utils`)를
    직접 import 해야 한다. 라이브러리 내부 구조에 의존하면 버전이 올라갈 때
    조용히 깨질 수 있어, 공개 API 만 쓰는 쪽을 택했다.
    """
    raw = getattr(config, "EXTRA_HOLIDAYS", {}) or {}
    values = raw.get(market_label(market)) or []
    parsed = set()
    for item in values:
        try:
            parsed.add(pd.Timestamp(item).date())
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"EXTRA_HOLIDAYS[{market!r}] 의 날짜를 해석할 수 없습니다: {item!r} ({exc_summary(e)})"
            ) from e
    return parsed


def _is_extra_holiday(market: str, day: date) -> bool:
    """그 날짜가 설정으로 추가한 임시 휴장일인지."""
    return day in extra_holidays(market)


def _build_calendar(market: str):
    """해당 시장의 캘린더 인스턴스를 만든다."""
    name, _tz, _label = _MARKETS[market_label(market)]
    return xc.get_calendar(name, start=_RANGE_START, end=_RANGE_END)


def get_calendar(market: str):
    """시장의 캘린더를 돌려준다. 첫 호출에 만들어 캐시한다(생성이 2초 정도 걸린다)."""
    market = market_label(market)
    if market not in _CALENDARS:
        _CALENDARS[market] = _build_calendar(market)
    return _CALENDARS[market]


def reset_cache() -> None:
    """캘린더 캐시를 비운다 (설정 변경 후 재생성·테스트 격리용)."""
    _CALENDARS.clear()


def _require_aware(now: datetime, market: str) -> None:
    """naive datetime 을 막는다."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(
            f"{market} 판정에는 tz-aware datetime 이 필요합니다 (받은 값: {now!r}). "
            "naive 는 서버 시간대에 따라 결과가 달라지므로 허용하지 않습니다."
        )


def _minute(now: datetime, market: str) -> pd.Timestamp:
    """tz-aware datetime 을 라이브러리가 받는 분 단위 Timestamp 로 바꾼다."""
    _require_aware(now, market)
    return pd.Timestamp(now).floor("min")


def is_market_open(market: str, now: datetime) -> bool:
    """
    해당 시각에 **정규장이 열려 있는지** 판정한다 (개장 시각 포함, 마감 시각 제외).

    반일장(예: 미국 추수감사절 다음날 13:00 ET 마감)과 서머타임은 라이브러리 캘린더가
    반영한다. 휴장일이면 False 다.

    이 함수는 "거래소가 열려 있는가"만 본다. 주문은 `is_order_window()` 로 판단한다 —
    개장 직후·마감 직전은 열려 있지만 주문에 안전하지 않다.
    """
    cal = get_calendar(market)
    minute = _minute(now, market)
    if _is_extra_holiday(market, minute.date()):
        return False
    try:
        return bool(cal.is_open_on_minute(minute))
    except Exception as e:
        # 캘린더 구간 밖이거나 라이브러리 오류. **모르면 열려 있다고 하지 않는다.**
        # (열려 있다고 잘못 판정하면 주문이 나가고, 닫혀 있다고 잘못 판정하면 기회를 잃는다.
        #  주문이 나가는 쪽이 더 위험하므로 False 로 둔다.)
        logger.error(f"⚠️ [{market}] 정규장 판정 실패 — 닫힌 것으로 처리합니다: {exc_summary(e)}")
        return False


def is_order_window(market: str, now: datetime) -> bool:
    """
    해당 시각이 **주문을 내도 되는 구간**인지 판정한다 (T05 핵심).

    정규장 안이면서 개장 후 `config.SKIP_OPEN_MINUTES` 분이 지났고,
    마감 전 `config.SKIP_CLOSE_MINUTES` 분보다 앞일 때만 True 다.

    왜 마진이 필요한가: 스케줄러는 1시간 주기라 개장 직후나 마감 직전에 걸릴 수 있다.
    개장 직후에는 시가가 아직 안정되지 않고, 마감 직전에 낸 지정가는 체결되지
    못한 채 미체결로 남는다(그리고 모의투자는 정규장 밖 주문을 거부한다).
    2026-10-13 15:33 KST(국내 마감 3분 전), 2026-10-14 09:26 KST(개장 26분 후지만
    그날은 개장이 아니었음) 같은 거부가 이 마진으로 줄어든다.

    판정은 라이브러리의 세션 개장·마감 시각을 기준으로 한다. 즉 반일장에서는
    마감이 13:00 ET 로 당겨지므로 `SKIP_CLOSE_MINUTES` 도 그 마감 기준으로 적용된다.
    """
    cal = get_calendar(market)
    minute = _minute(now, market)
    if _is_extra_holiday(market, minute.date()):
        return False
    try:
        if not cal.is_open_on_minute(minute):
            return False
        session = cal.minute_to_session(minute, direction="previous")
        open_at = cal.session_open(session)
        close_at = cal.session_close(session)
    except Exception as e:
        logger.error(f"⚠️ [{market}] 주문 구간 판정 실패 — 주문하지 않습니다: {exc_summary(e)}")
        return False

    skip_open = timedelta(minutes=max(0, int(config.SKIP_OPEN_MINUTES)))
    skip_close = timedelta(minutes=max(0, int(config.SKIP_CLOSE_MINUTES)))
    return (minute >= open_at + skip_open) and (minute < close_at - skip_close)


def next_session(market: str, now: datetime) -> datetime:
    """
    해당 시각 **다음**의 개장 시각을 tz-aware 로 돌려준다.

    장중이면 다음 거래일 개장이다("다음 세션"). 장이 닫혀 있으면 가장 가까운
    개장 시각이다. 캘린더 구간 밖이면 `None`.

    **EXTRA_HOLIDAYS 를 반영한다.** 라이브러리는 임시 휴장일을 모르므로
    `next_open()` 이 그날을 가리킬 수 있다. 그 경우 다음 개장일을 다시 찾는다.
    """
    cal = get_calendar(market)
    minute = _minute(now, market)
    extra = extra_holidays(market)
    try:
        candidate = cal.next_open(minute).to_pydatetime()
        # 임시 휴장일이면 그날은 건너뛴다. 최대 30번까지만 넘어간다 —
        # 설정이 잘못돼 계속 넘어가면 무한 루프가 되므로 상한을 둔다.
        for _ in range(30):
            if candidate.date() not in extra:
                return candidate
            logger.warning(
                f"⚠️ [{market}] {candidate.date()} 는 EXTRA_HOLIDAYS 로 지정된 휴장일입니다 — "
                "다음 개장일을 찾습니다."
            )
            candidate = cal.next_open(candidate).to_pydatetime()
        logger.error(f"⚠️ [{market}] EXTRA_HOLIDAYS 가 연속으로 지정돼 다음 개장일을 찾지 못했습니다.")
        return None
    except Exception as e:
        logger.error(f"⚠️ [{market}] 다음 개장 시각을 알 수 없습니다: {exc_summary(e)}")
        return None


def is_session_day(market: str, day: date) -> bool:
    """해당 날짜가 거래일(개장일)인지."""
    if _is_extra_holiday(market, day):
        return False
    cal = get_calendar(market)
    try:
        return bool(cal.is_session(pd.Timestamp(day).date().isoformat()))
    except Exception as e:
        logger.error(f"⚠️ [{market}] 거래일 판정 실패 — 거래일 아님으로 처리: {exc_summary(e)}")
        return False


def trading_days_between(market: str, start: datetime, end: datetime) -> int:
    """
    두 시각 사이에 **개장일이 며칠** 있는지 (보유 거래일 계산용).

    `start` 는 포함, `end` 는 제외다. 반환은 정수 일수이며, `end <= start` 면 0 이다.

    시각이 아니라 **날짜** 단위로 센다 — 보유 거래일은 "며칠 동안 장이 열렸는가"이고,
    장중에 몇 시간을 들고 있었는지는 의미가 없기 때문이다.
    """
    _require_aware(start, market)
    _require_aware(end, market)
    if end <= start:
        return 0
    cal = get_calendar(market)
    try:
        sessions = cal.sessions_in_range(
            pd.Timestamp(start.date()), pd.Timestamp(end.date())
        )
    except Exception as e:
        logger.error(f"⚠️ [{market}] 거래일 수 계산 실패 — 0 으로 처리합니다: {exc_summary(e)}")
        return 0
    # end 와 같은 날짜는 제외한다 (`end` 는 미포함이므로).
    # EXTRA_HOLIDAYS 로 추가한 휴장일도 거래일이 아니므로 빼야 한다 —
    # 안 빼면 보유 거래일이 실제보다 길어져 청산 시점 판단이 어긋난다.
    extra = extra_holidays(market)
    return int(sum(1 for s in sessions if s.date() < end.date() and s.date() not in extra))


# ── 주문 거부 원인 분류 (요구사항 4) ──────────────────────
# KIS 가 장 상태 때문에 주문을 거부할 때 돌려주는 메시지 조각.
# VERIFY: msg_cd 값(40570000 등)은 로그에서 확인한 것이고, 아래 문구는 로그의
#         msg1 원문("모의투자 장시작전 입니다.")에서 확인한 것이다. 전체 코드표는
#         docs/kis_api_fields.md 에 없다. 문구 매칭이라 코드가 바뀌면 놓칠 수 있다 —
#         그래서 분류에 실패해도 "기타"로 남기고 원문(msg_cd/msg1)은 항상 로깅한다.
REJECT_MARKET_CLOSED = "market_closed"     # 장시작전·장종료·영업일 아님
REJECT_PRICE = "price"                     # 가격(호가 단위·상하한가) 문제
REJECT_QUANTITY = "quantity"               # 수량(주문가능수량 초과 등) 문제
REJECT_OTHER = "other"                     # 그 밖 (원문 확인 필요)

_CLOSED_HINTS = ("장시작전", "장종료", "장 개시", "영업일이 아닌", "휴장", "시간외")


def classify_rejection(msg_cd, msg1: str) -> str:
    """
    주문 거부 응답을 원인별로 분류한다 (요구사항 4).

    반환: 위 `REJECT_*` 상수 중 하나. **분류는 로그를 읽기 위한 것이며 판단을 바꾸지
    않는다** — 거부는 거부다. 장 상태 때문에 거부됐다면 그 사실을 로그에서 바로
    알아볼 수 있어야 다음 조치(캘린더·마진 조정)를 정할 수 있다.
    """
    text = msg1 or ""
    if any(hint in text for hint in _CLOSED_HINTS):
        return REJECT_MARKET_CLOSED
    if "호가" in text or "가격" in text or "단가" in text or "상한" in text or "하한" in text:
        return REJECT_PRICE
    if "수량" in text or "잔고" in text:
        return REJECT_QUANTITY
    return REJECT_OTHER


# 분류별 사람이 읽을 설명 (로그 한 줄에 함께 남긴다).
REJECT_LABELS = {
    REJECT_MARKET_CLOSED: "장 상태(장시작전·장종료·휴장)",
    REJECT_PRICE: "가격(호가 단위·상하한가)",
    REJECT_QUANTITY: "수량·잔고",
    REJECT_OTHER: "기타(원문 확인 필요)",
}
