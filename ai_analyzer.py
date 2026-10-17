"""
Gemini를 사용해 실제 시세·뉴스 데이터를 근거로 시장 심리를 점수화한다.

핵심: 모델에 날짜만 주면 학습 시점의 일반론을 반복하므로,
      (1) KIS 실시간 시세(QQQ·379800·환율)와
      (2) RSS 뉴스 헤드라인을 함께 넣어 매번 다른 근거로 판단하게 한다.

반환: {"score": int(-100~100), "reason": str}
"""
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import types

import config
import kis_domestic
import kis_overseas

logger = logging.getLogger("bot")

KST = ZoneInfo("Asia/Seoul")

# Gemini 일시 오류(429/503) 재시도 설정
_GEMINI_MODEL = "gemini-3.1-flash-lite"
_MAX_ATTEMPTS = 3
_RETRY_SLEEP_SEC = 3.0

# 신규 google.genai Client 초기화
if config.GEMINI_API_KEY:
    _client = genai.Client(api_key=config.GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY not configured - AI analysis will not work")
    _client = None


# ─────────────────────────────────────────────
# 시장 데이터 수집
# ─────────────────────────────────────────────

def _safe(fn, *args, **kwargs):
    """개별 데이터 수집 실패가 전체 분석을 막지 않도록 감싼다."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        logger.warning(f"시장 데이터 수집 실패 ({getattr(fn, '__name__', fn)}): {e}")
        return None


def collect_market_snapshot() -> dict:
    """
    프롬프트에 넣을 실제 시장 수치를 모은다.
    실패한 항목은 None으로 두고, 프롬프트에서 '데이터 없음'으로 표시한다.
    """
    snapshot = {}

    # 해외: QQQ 현재가 및 전일 대비 등락률
    price_out = _safe(_overseas_price_output, config.TARGET_TICKER)
    snapshot["qqq_price"] = _num(price_out, "last")
    snapshot["qqq_change_pct"] = _num(price_out, "rate")

    # 환율
    snapshot["usd_krw"] = _safe(kis_overseas.get_usd_krw_rate)

    # 국내 ETF
    if config.IS_DOMESTIC_ENABLED:
        dom_out = _safe(_domestic_price_output, config.DOMESTIC_TICKER)
        snapshot["domestic_price"] = _num(dom_out, "stck_prpr")
        snapshot["domestic_change_pct"] = _num(dom_out, "prdy_ctrt")

    snapshot["vix"] = _safe(_fetch_vix)
    return snapshot


def _fetch_vix() -> float | None:
    """FRED에서 최신 VIX(공포지수)를 가져온다. 결측치('.')는 건너뛴다."""
    resp = requests.get(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS",
        timeout=5,
        headers={"User-Agent": "Mozilla/5.0 (compatible; snowball-bot/1.0)"},
    )
    resp.raise_for_status()
    for line in reversed(resp.text.strip().split("\n")[1:]):
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in (".", ""):
            return float(parts[1].strip())
    return None


def _overseas_price_output(ticker: str) -> dict:
    """해외 현재가 원본 응답(output)을 반환. 등락률 필드까지 쓰기 위해 직접 호출."""
    url = f"{config.KIS_BASE_URL}/uapi/overseas-price/v1/quotations/price"
    resp = kis_overseas._request(
        "GET", url, "HHDFS00000300",
        params={"AUTH": "", "EXCD": "NAS", "SYMB": ticker},
    )
    resp.raise_for_status()
    return resp.json().get("output") or {}


def _domestic_price_output(ticker: str) -> dict:
    """국내 현재가 원본 응답(output)을 반환."""
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    resp = kis_domestic._request(
        "GET", url, "FHKST01010100",
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
    )
    resp.raise_for_status()
    return resp.json().get("output") or {}


def _num(output: dict | None, key: str) -> float | None:
    """KIS 응답 문자열 필드를 float로 변환. 없으면 None."""
    if not output:
        return None
    try:
        return float(output[key])
    except (KeyError, TypeError, ValueError):
        return None


# ─────────────────────────────────────────────
# 뉴스 수집 (RSS)
# ─────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """HTML 엔티티와 태그를 제거하고 한 줄로 정리."""
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _parse_pub_date(text: str | None) -> datetime | None:
    """RSS pubDate를 KST datetime으로 변환. 실패 시 None."""
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(KST)


def fetch_news_headlines() -> list[str]:
    """
    설정된 RSS 피드에서 최신 헤드라인을 모아 최신순으로 반환.
    모든 피드가 실패하면 빈 리스트 → 프롬프트는 시세만으로 판단한다.
    """
    items: list[tuple[datetime, str]] = []

    for feed_url in config.NEWS_FEEDS:
        try:
            resp = requests.get(
                feed_url, timeout=config.NEWS_TIMEOUT_SEC,
                headers={"User-Agent": "Mozilla/5.0 (compatible; snowball-bot/1.0)"},
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            logger.warning(f"뉴스 피드 수집 실패 ({feed_url[:60]}...): {e}")
            continue

        # RSS 2.0 <item> / Atom <entry> 모두 지원
        for node in root.iter():
            tag = node.tag.split("}")[-1]
            if tag not in ("item", "entry"):
                continue

            title = pub = None
            for child in node:
                ctag = child.tag.split("}")[-1]
                if ctag == "title":
                    title = _clean(child.text)
                elif ctag in ("pubDate", "published", "updated"):
                    pub = child.text

            if not title:
                continue
            # 제목이 비정상적으로 긴 경우 잘라 로그/토큰 낭비 방지
            if len(title) > 200:
                title = title[:200] + "…"
            items.append((_parse_pub_date(pub) or datetime.min.replace(tzinfo=KST), title))

    items.sort(key=lambda x: x[0], reverse=True)

    seen, headlines = set(), []
    for _, title in items:
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        headlines.append(title)
        if len(headlines) >= config.NEWS_MAX_ITEMS:
            break

    if headlines:
        logger.info(f"뉴스 헤드라인 {len(headlines)}건 수집")
    else:
        logger.warning("뉴스 헤드라인 수집 실패 — 시세 데이터만으로 분석")
    return headlines


# ─────────────────────────────────────────────
# 프롬프트 구성 및 분석
# ─────────────────────────────────────────────

def _fmt_pct(v: float | None) -> str:
    return "데이터 없음" if v is None else f"{v:+.2f}%"


def _fmt_num(v: float | None, unit: str = "", digits: int = 2) -> str:
    return "데이터 없음" if v is None else f"{v:,.{digits}f}{unit}"


def build_prompt(snapshot: dict, headlines: list[str]) -> str:
    """실제 수치와 뉴스만 근거로 삼도록 명시한 프롬프트."""
    today = date.today().strftime("%Y년 %m월 %d일")

    if headlines:
        news_block = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines, 1))
    else:
        news_block = "(뉴스 수집 실패 — 아래 시세 데이터만으로 판단하세요)"

    dom_line = ""
    if config.IS_DOMESTIC_ENABLED:
        dom_line = (
            f"- 국내 ETF {config.DOMESTIC_TICKER_NAME}({config.DOMESTIC_TICKER}): "
            f"{_fmt_num(snapshot.get('domestic_price'), '원', 0)} "
            f"({_fmt_pct(snapshot.get('domestic_change_pct'))})\n"
        )

    vix = snapshot.get("vix")
    vix_line = (
        f"- VIX 공포지수: {_fmt_num(vix, '', 1)}"
        f" (18 이하=안정, 25~30=경계, 35+=패닉)\n"
    ) if vix else ""

    return f"""오늘은 {today}입니다. 아래는 방금 수집한 실시간 데이터입니다.

[시세]
- QQQ 현재가: {_fmt_num(snapshot.get('qqq_price'), ' USD')} ({_fmt_pct(snapshot.get('qqq_change_pct'))})
{dom_line}- USD/KRW 환율: {_fmt_num(snapshot.get('usd_krw'), '원', 1)}
{vix_line}

[최신 뉴스 헤드라인]
{news_block}

위 데이터만을 근거로, 나스닥 단기(1~3거래일) 매수 관점의 시장 심리를 평가하세요.
규칙:
- 위에 제시되지 않은 사실을 지어내지 마세요. 확실하지 않으면 중립(0 근처)으로 평가하세요.
- 시세 등락률과 뉴스 내용이 서로 어긋나면 그 점을 reason에 반영하세요.
- 같은 날이라도 데이터가 다르면 점수도 달라져야 합니다.

**반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.**

{{"score": <-100에서 100 사이의 정수>, "reason": "<한 줄 핵심 요약, 한국어>"}}

평가 기준:
- +50 이상: 강한 매수 신호 (호재 명확)
- 0 ~ +49: 중립 또는 약한 호재
- -1 ~ -49: 약한 악재 (매수 가능)
- -50 이하: 심각한 악재 (매수 중지)
"""


def _generate_json(prompt: str) -> str:
    """
    Gemini 호출. 429/503(일시적 과부하·할당량)는 재시도하고,
    최종 실패 시 예외를 올려 호출부가 중립 처리하도록 한다.
    """
    last_error = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = _client.models.generate_content(
                model=_GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            return response.text.strip()
        except Exception as e:
            last_error = e
            status = getattr(e, "code", None) or getattr(e, "status_code", None)
            retryable = "429" in str(e) or "503" in str(e) or status in (429, 503)
            if not retryable or attempt == _MAX_ATTEMPTS - 1:
                raise
            logger.warning(f"Gemini 일시 오류 ({attempt + 1}/{_MAX_ATTEMPTS}), {_RETRY_SLEEP_SEC}초 후 재시도: {e}")
            time.sleep(_RETRY_SLEEP_SEC)
    raise last_error


def analyze_market_sentiment() -> dict:
    """
    실시간 시세 + 뉴스 헤드라인을 Gemini에 전달해 감정 점수를 받는다.

    반환 예시:
        {"score": 30, "reason": "QQQ -2.1% 급락에도 AI 실적 호조 뉴스가 우세"}
        {"score": -70, "reason": "CPI 예상치 상회 + 나스닥 3일 연속 하락"}
    """
    snapshot = collect_market_snapshot()
    headlines = fetch_news_headlines()
    prompt = build_prompt(snapshot, headlines)

    try:
        if _client is None:
            logger.error("AI model not initialized - check GEMINI_API_KEY configuration")
            return {"score": 0, "reason": "Gemini API not configured"}

        raw = _generate_json(prompt)
        # JSON 블록만 추출 (```json ... ``` 감싸진 경우 대비)
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        # 스키마 검증
        assert isinstance(result.get("score"), int), "score는 정수여야 합니다"
        assert isinstance(result.get("reason"), str), "reason은 문자열이어야 합니다"
        score = max(-100, min(100, result["score"]))  # 범위 클램핑
        logger.info(f"AI 분석 완료 → 점수: {score}, 사유: {result['reason']}")
        return {"score": score, "reason": result["reason"]}
    except ConnectionError as e:
        logger.warning(f"Gemini API 네트워크 오류: {e} — 기본값 0 반환")
        return {"score": 0, "reason": "네트워크 오류로 분석 실패"}
    except Exception as e:
        logger.error(f"AI 분석 실패: {e} — 기본값 0 반환")
        return {"score": 0, "reason": "AI 분석 실패, 중립 처리"}
