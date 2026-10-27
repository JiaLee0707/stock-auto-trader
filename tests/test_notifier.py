"""
T09a-1: notifier — 알림 실패가 매매 루프를 죽이지 않고, 토큰이 어디에도 새지 않는다.
실제 네트워크를 쓰지 않는다 — requests.post 는 모두 가짜로 바꾼다.
"""
import logging

import pytest
import requests

import config
import notifier

TOKEN = "123456789:AAH-fake_token_for_tests"
CHAT = "987654321"


class _Resp:
    def __init__(self, status_code=200):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _reset_announce(monkeypatch):
    monkeypatch.setattr(notifier, "_announced_disabled", False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", CHAT)


def _capture_post(monkeypatch, *, response=None, exc=None):
    """requests.post 를 가짜로 바꾸고, 호출 기록 [(url, kwargs), ...] 을 돌려준다."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if exc is not None:
            raise exc
        return response or _Resp(200)

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    return calls


@pytest.mark.parametrize("token,chat", [("", ""), (TOKEN, ""), ("", CHAT)],
                         ids=["none", "no_chat", "no_token"])
def test_unconfigured_is_noop_and_announces_once(monkeypatch, caplog, token, chat):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", chat)
    calls = _capture_post(monkeypatch)
    with caplog.at_level(logging.INFO, logger="bot"):
        assert notifier.notify("INFO", "a") is False
        assert notifier.notify("INFO", "b") is False
    assert calls == [], "설정이 없는데 전송을 시도했다"
    assert sum("텔레그램 설정이 없어" in r.getMessage() for r in caplog.records) == 1


def test_success_sends_plain_text_with_timeout_and_mode_prefix(configured, monkeypatch):
    calls = _capture_post(monkeypatch)
    assert notifier.notify("WARNING", "제목", "본문") is True
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert kwargs["timeout"] == 5
    body = kwargs["json"]
    assert body["chat_id"] == CHAT
    assert "parse_mode" not in body
    prefix = "[MOCK]" if config.IS_MOCK else "[LIVE]"
    assert body["text"].startswith(prefix)
    assert "제목" in body["text"] and "본문" in body["text"]


def test_message_is_truncated_to_1000_chars(configured, monkeypatch):
    calls = _capture_post(monkeypatch)
    notifier.notify("INFO", "제목", "가" * 5000)
    assert len(calls[0][1]["json"]["text"]) <= 1000


def test_secrets_in_message_are_scrubbed(configured, monkeypatch):
    calls = _capture_post(monkeypatch)
    notifier.notify("INFO", f"제목 {TOKEN}", f"본문 chat={CHAT} token={TOKEN}")
    text = calls[0][1]["json"]["text"]
    assert TOKEN not in text and CHAT not in text


@pytest.mark.parametrize("exc", [
    requests.ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: /bot{TOKEN}/sendMessage"),
    requests.Timeout(f"Read timed out. url=/bot{TOKEN}/sendMessage"),
    ValueError(f"unexpected {TOKEN}"),
], ids=["connection", "timeout", "unexpected"])
def test_failure_is_logged_without_token_and_not_raised(configured, monkeypatch, caplog, exc):
    """예외 메시지에 토큰이 들어 있어도 로그에는 타입명만 남고, 호출자에게 전파되지 않는다."""
    calls = _capture_post(monkeypatch, exc=exc)
    with caplog.at_level(logging.ERROR, logger="bot"):
        result = notifier.notify("CRITICAL", "제목", "본문")
    assert result is False
    assert len(calls) == 1, "재시도하면 안 된다"
    assert type(exc).__name__ in caplog.text
    assert TOKEN not in caplog.text and CHAT not in caplog.text


def test_http_error_status_is_logged_without_token(configured, monkeypatch, caplog):
    _capture_post(monkeypatch, response=_Resp(401))
    with caplog.at_level(logging.ERROR, logger="bot"):
        assert notifier.notify("INFO", "제목") is False
    assert "401" in caplog.text
    assert TOKEN not in caplog.text


def test_urllib3_debug_logging_is_suppressed():
    """urllib3 DEBUG 로그에는 요청 경로(= /bot<토큰>/...)가 찍힌다 — 토큰이 로그에 새지 않게 막는다."""
    assert logging.getLogger("urllib3").getEffectiveLevel() >= logging.WARNING
