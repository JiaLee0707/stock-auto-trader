"""
Gemini 1.5 Flash를 사용하여 오늘의 나스닥 시장 뉴스를 분석.
반환: {"score": int(-100~100), "reason": str}
"""
import json
import logging
import os
from datetime import date

# import google.generativeai as genai
from google import genai
from google.genai import types

import config

logger = logging.getLogger("bot")

# 신규 google.genai Client 초기화
if config.GEMINI_API_KEY:
    _client = genai.Client(api_key=config.GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY not configured - AI analysis will not work")
    _client = None


def analyze_market_sentiment() -> dict:
    """
    Gemini에게 오늘 날짜의 나스닥 시장 상황을 물어보고
    JSON 형식의 감정 점수를 반환받는다.

    반환 예시:
        {"score": 30, "reason": "FOMC 금리 동결 기대감으로 나스닥 상승 압력"}
        {"score": -70, "reason": "예상치 상회 CPI 발표로 금리 인상 우려 급증"}
    """
    today = date.today().strftime("%Y년 %m월 %d일")
    prompt = f"""
오늘은 {today}입니다.
현재 나스닥(QQQ) 시장에 영향을 미칠 수 있는 주요 뉴스와 거시경제 이슈를 종합하여
단기 매수 관점에서 시장 심리를 평가해 주세요.

**반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.**

{{"score": <-100에서 100 사이의 정수>, "reason": "<한 줄 핵심 요약>"}}

평가 기준:
- +50 이상: 강한 매수 신호 (호재 명확)
- 0 ~ +49: 중립 또는 약한 호재
- -1 ~ -49: 약한 악재 (매수 가능)
- -50 이하: 심각한 악재 (매수 중지)

분석 시 다음 사항도 함께 고려해 주세요:
1. 최근 FOMC/연준 관련 뉴스
2. 나스닥 구성 종목들의 실적 발표
3. 경기 침체 신호 (역 yield curve, PMI 등)
4. 인플레이션 지표 (CPI, PPI)
5. 기술주 실적 및 투심
6. 지정학적 위험 요인
7. 달러 강세/약세 영향
8. 주요 경제 지표 발표
"""
    try:
        if _client is None:
            logger.error("AI model not initialized - check GEMINI_API_KEY configuration")
            return {"score": 0, "reason": "Gemini API not configured"}

        response = _client.models.generate_content(
            # model="gemini-flash-latest",
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        raw = response.text.strip()
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


