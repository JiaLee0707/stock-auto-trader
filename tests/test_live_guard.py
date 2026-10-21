"""
실전 가드 테스트 (T01b).

모드 판정은 IS_MOCK_TRADING 하나로만 한다. TRADING_MODE 는 판정에 쓰지 않는다.
가장 중요한 안전 요구: 실전(IS_MOCK_TRADING=False)인데 CONFIRM_LIVE 확인값이 없으면
어떤 API 호출도 하기 전에 종료한다.
"""
import pytest

import config


# ── IS_MOCK_TRADING 엄격 파싱 ─────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("  true  ", True),
        ("false", False),
        ("FALSE", False),
        ("False", False),
        ("  false ", False),
    ],
)
def test_resolve_is_mock_valid_values(raw, expected):
    assert config.resolve_is_mock(raw) is expected


@pytest.mark.parametrize("raw", ["ture", "0", "yes", "true1", "fals", "", "  ", "1", "y"])
def test_resolve_is_mock_rejects_invalid_values(raw):
    """오타·비허용 값은 None(판정 불가) — 조용히 실전으로 넘어가지 않는다."""
    assert config.resolve_is_mock(raw) is None


def test_resolve_is_mock_none_is_unset():
    """미설정(None)은 호출부가 기본값 True(모의)를 채운다."""
    assert config.resolve_is_mock(None) is None


def test_load_is_mock_defaults_to_true_when_unset(monkeypatch):
    monkeypatch.delenv("IS_MOCK_TRADING", raising=False)
    assert config._load_is_mock() is True


@pytest.mark.parametrize("raw", ["ture", "yes", "0", ""])
def test_load_is_mock_exits_on_invalid_value(monkeypatch, raw):
    monkeypatch.setenv("IS_MOCK_TRADING", raw)
    with pytest.raises(SystemExit):
        config._load_is_mock()


# ── TRADING_MODE 는 판정에 쓰이지 않는다 ──────────────────

def test_trading_mode_is_derived_from_is_mock(monkeypatch):
    """TRADING_MODE 는 IS_MOCK 에서 유도된 표시용 값이다."""
    assert config.TRADING_MODE == ("paper" if config.IS_MOCK else "live")


@pytest.mark.parametrize(
    "derived,raw",
    [
        ("paper", None),
        ("paper", ""),
        ("paper", "  "),
        ("paper", "paper"),
        ("paper", "PAPER"),
        ("paper", "  paper  "),
        ("live", "live"),
        ("live", "LIVE"),
        ("live", None),
        ("live", ""),
    ],
)
def test_trading_mode_env_ok_cases(derived, raw):
    """미설정·빈 값·유도값과 일치하면 통과한다."""
    config.check_trading_mode_env(derived, raw)  # 예외가 나지 않아야 한다


@pytest.mark.parametrize(
    "derived,raw",
    [
        ("paper", "live"),
        ("live", "paper"),
        ("paper", "PAPERX"),
        ("live", "live_x"),
    ],
)
def test_trading_mode_env_mismatch_exits(derived, raw):
    """유도값과 다른 TRADING_MODE 는 경고 후 무시가 아니라 즉시 종료한다."""
    with pytest.raises(SystemExit):
        config.check_trading_mode_env(derived, raw)


def test_import_exits_when_trading_mode_conflicts(monkeypatch):
    """import 시점에도 충돌이면 종료되는지 실제 모듈 재로드로 확인한다."""
    import importlib

    monkeypatch.setenv("IS_MOCK_TRADING", "true")
    monkeypatch.setenv("TRADING_MODE", "live")
    with pytest.raises(SystemExit):
        importlib.reload(config)
    # 이후 테스트가 깨지지 않도록 정상 상태로 되돌린다
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.setenv("IS_MOCK_TRADING", "true")
    importlib.reload(config)


# ── live_guard_error (IS_MOCK, CONFIRM_LIVE) 조합 ─────────

@pytest.mark.parametrize(
    "is_mock,confirm,blocked",
    [
        (True, "", False),
        (True, "YES_I_UNDERSTAND", False),
        (False, "", True),
        (False, "YES_I_UNDERSTAND", False),
    ],
)
def test_guard_matrix(is_mock, confirm, blocked):
    err = config.live_guard_error(is_mock, confirm)
    if blocked:
        assert err is not None
        assert "CONFIRM_LIVE" in err
    else:
        assert err is None


@pytest.mark.parametrize("confirm", ["yes", "YES_I_UNDERSTAND_NOW", "true", "  YES_I_UNDERSTAND"])
def test_guard_rejects_wrong_confirm_values(confirm):
    assert config.live_guard_error(False, confirm) is not None


# ── enforce_trading_mode 동작 ────────────────────────────

def test_enforce_exits_when_live_without_confirm(monkeypatch):
    monkeypatch.setattr(config, "IS_MOCK", False)
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")
    with pytest.raises(SystemExit) as e:
        config.enforce_trading_mode()
    assert e.value.code == 1


@pytest.mark.parametrize(
    "is_mock,confirm",
    [(True, ""), (True, "YES_I_UNDERSTAND"), (False, "YES_I_UNDERSTAND")],
)
def test_enforce_passes_for_allowed_combos(monkeypatch, is_mock, confirm):
    monkeypatch.setattr(config, "IS_MOCK", is_mock)
    monkeypatch.setattr(config, "CONFIRM_LIVE", confirm)
    config.enforce_trading_mode()  # 예외가 나지 않아야 한다


# ── assert_order_allowed (주문 함수 방어선) ───────────────

def test_assert_order_allowed_raises_in_live_without_confirm(monkeypatch):
    monkeypatch.setattr(config, "IS_MOCK", False)
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")
    with pytest.raises(RuntimeError):
        config.assert_order_allowed()


@pytest.mark.parametrize(
    "is_mock,confirm",
    [(True, ""), (False, "YES_I_UNDERSTAND")],
)
def test_assert_order_allowed_passes_for_allowed_combos(monkeypatch, is_mock, confirm):
    monkeypatch.setattr(config, "IS_MOCK", is_mock)
    monkeypatch.setattr(config, "CONFIRM_LIVE", confirm)
    config.assert_order_allowed()


# ── 주문 함수 직접 호출: HTTP 이전에 차단되는지 ───────────

@pytest.fixture
def live_without_confirm(monkeypatch):
    """실전 + CONFIRM_LIVE 없음 상태로 만든다."""
    monkeypatch.setattr(config, "IS_MOCK", False)
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")


def test_overseas_buy_blocked_before_http(live_without_confirm, monkeypatch):
    import kis_overseas
    called = []
    # T04: 주문은 request_order() 로 나간다. _request 만 막아두면 검증이 무의미해진다.
    monkeypatch.setattr(kis_overseas, "request_order",
                        lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError):
        kis_overseas.buy_shares(1, 100.0)
    assert called == []


def test_overseas_sell_blocked_before_http(live_without_confirm, monkeypatch):
    import kis_overseas
    called = []
    monkeypatch.setattr(kis_overseas, "request_order",
                        lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError):
        kis_overseas.sell_all(1, 100.0)
    assert called == []


def test_overseas_cancel_blocked_before_http(live_without_confirm, monkeypatch):
    """T04 신규: 미체결 취소도 실전 가드를 지나야 한다."""
    import kis_overseas
    called = []
    monkeypatch.setattr(kis_overseas, "request_order",
                        lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError):
        kis_overseas.cancel_order("B1", quantity=1.0)
    assert called == []


def test_domestic_buy_blocked_before_http(live_without_confirm, monkeypatch):
    import kis_domestic
    called = []
    monkeypatch.setattr(kis_domestic, "request_order",
                        lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError):
        kis_domestic.buy_domestic("379800", 1, 10000.0)
    assert called == []


def test_domestic_sell_blocked_before_http(live_without_confirm, monkeypatch):
    import kis_domestic
    called = []
    monkeypatch.setattr(kis_domestic, "request_order",
                        lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError):
        kis_domestic.sell_domestic_all("379800", 1, 10000.0)
    assert called == []


def test_domestic_send_order_blocked_before_http(live_without_confirm, monkeypatch):
    """T04 신규: body 를 직접 넘겨도 전송 지점(send_order)에서 가드가 걸린다."""
    import kis_domestic
    called = []
    monkeypatch.setattr(kis_domestic, "request_order",
                        lambda *a, **k: called.append(1))
    body = {"PDNO": "379800", "ORD_QTY": "1", "ORD_UNPR": "10000"}
    with pytest.raises(RuntimeError):
        kis_domestic.send_order(body, side="BUY")
    assert called == []


# ── main() 이 매매 사이클 이전에 종료하는지 ───────────────

def test_main_exits_before_trading_when_live_unconfirmed(monkeypatch):
    import main

    monkeypatch.setattr(config, "IS_MOCK", False)
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")

    called = {"job": False}
    monkeypatch.setattr(main, "job", lambda: called.__setitem__("job", True))

    with pytest.raises(SystemExit):
        main.main()
    assert called["job"] is False, "가드 위반인데 매매 사이클(job)이 실행되었다"


def test_main_start_log_shows_mode_and_host(monkeypatch, caplog):
    """시작 로그 첫 줄에 모드와 도메인 호스트명이 있고 비밀값은 없다."""
    import logging

    import main

    monkeypatch.setattr(config, "IS_MOCK", False)
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")

    with caplog.at_level(logging.INFO, logger="bot"):
        with pytest.raises(SystemExit):
            main.main()

    first = caplog.records[0].getMessage()
    assert "현재 모드: 실전" in first
    # 도메인은 config.KIS_BASE_URL(모듈 로드 시점에 확정)의 호스트명과 일치해야 한다
    from urllib.parse import urlsplit
    assert urlsplit(config.KIS_BASE_URL).hostname in first
    # 비밀값이 로그에 없어야 한다
    assert "appsecret" not in first.lower()
    assert "account" not in first.lower()
