"""
notifier.py — 텔레그램 알림 (T09a).

규칙
  1. 알림 실패가 매매 루프를 죽이지 않는다. 예외는 삼키지 않고 ERROR 로그로 남기되
     호출자에게 전파하지 않는다(반환값 False).
  2. 토큰이 어디에도 새지 않는다. requests 예외 메시지에는 요청 URL(= /bot<토큰>/...)이
     들어 있으므로 `str(e)` 를 로그에 쓰지 않는다. 예외 타입명과 HTTP 상태 코드만 남긴다.
  3. 재시도하지 않는다. timeout 5초, 평문(parse_mode 없음), 1000자 제한.
  4. 설정(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)이 하나라도 없으면 아무것도 보내지 않고,
     첫 호출 때 한 번만 안내한다.

호출하는 쪽도 메시지에 계좌번호·API 키를 넣지 않는다(필요하면 config.mask).
"""
import logging

import requests

import config

logger = logging.getLogger("bot")

# urllib3 는 DEBUG 로그에 요청 경로(= /bot<토큰>/sendMessage)를 그대로 찍는다.
# 어디선가 DEBUG 로 켜져도 토큰이 로그에 새지 않게 막는다.
logging.getLogger("urllib3").setLevel(logging.WARNING)

_TIMEOUT_SEC = 5
_MAX_LEN = 1000
_announced_disabled = False


def _build_text(level: str, title: str, message: str) -> str:
    """전송할 평문을 만든다. 모드 표시를 앞에 붙이고, 비밀값이 섞였으면 가린다."""
    mode = "MOCK" if config.IS_MOCK else "LIVE"
    text = f"[{mode}][{str(level).upper()}] {title}"
    if message:
        text += f"\n{message}"
    for secret in (config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID):
        if secret:
            text = text.replace(secret, "***")
    return text[:_MAX_LEN]


def notify(level: str, title: str, message: str = "") -> bool:
    """
    알림 1건을 보낸다. 성공하면 True, 설정이 없거나 실패하면 False.

    **어떤 경우에도 예외를 던지지 않는다** — 알림 실패 때문에 매매 루프가 멈추면 안 된다.
    실패는 로그(ERROR)로 남긴다.
    """
    global _announced_disabled
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        if not _announced_disabled:
            _announced_disabled = True
            logger.info(
                "[알림] 텔레그램 설정이 없어 알림은 로그로만 남깁니다 "
                "(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)"
            )
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": _build_text(level, title, message)},
            timeout=_TIMEOUT_SEC,
        )
    except Exception as e:
        # str(e) 금지 — requests 예외 메시지에 URL(토큰)이 들어 있다.
        detail = type(e).__name__
        logger.error(f"[알림] 텔레그램 전송 실패 — {detail}")
        return False

    if response.status_code != 200:
        logger.error(f"[알림] 텔레그램 전송 실패 — HTTP {response.status_code}")
        return False
    return True
