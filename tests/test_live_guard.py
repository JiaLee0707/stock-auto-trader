"""
실전 가드 테스트 (T01).

가장 중요한 안전 요구: live 모드인데 CONFIRM_LIVE 확인값이 없으면 시작하지 않는다.
"""
import pytest

import config


def test_default_mode_is_paper(monkeypatch):
    """TRADING_MODE 미설정 시 기본값은 paper."""
    monkeypatch.delenv("TRADING_MODE", raising=False)
    assert config.os.getenv("TRADING_MODE", "paper").strip().lower() == "paper"


@pytest.mark.parametrize("mode", ["paper", "PAPER", " paper ", "", "live_x"])
def test_non_live_modes_have_no_guard_error(mode):
    assert config.live_guard_error(mode, "") is None


def test_live_without_confirm_is_blocked():
    err = config.live_guard_error("live", "")
    assert err is not None
    assert "CONFIRM_LIVE" in err


def test_live_with_wrong_confirm_is_blocked():
    assert config.live_guard_error("live", "yes") is not None
    assert config.live_guard_error("live", "YES_I_UNDERSTAND_NOW") is not None


def test_live_with_correct_confirm_passes():
    assert config.live_guard_error("live", "YES_I_UNDERSTAND") is None


def test_enforce_exits_when_live_without_confirm(monkeypatch):
    """가드 위반 시 SystemExit(1)로 즉시 종료한다."""
    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")
    with pytest.raises(SystemExit) as e:
        config.enforce_trading_mode()
    assert e.value.code == 1


def test_enforce_passes_in_paper(monkeypatch):
    monkeypatch.setattr(config, "TRADING_MODE", "paper")
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")
    config.enforce_trading_mode()  # 예외가 나지 않아야 한다


def test_main_exits_before_trading_when_live_unconfirmed(monkeypatch):
    """main()이 실전 주문 경로에 도달하기 전에 종료되는지 확인."""
    import main

    monkeypatch.setattr(config, "TRADING_MODE", "live")
    monkeypatch.setattr(config, "CONFIRM_LIVE", "")

    called = {"job": False}
    monkeypatch.setattr(main, "job", lambda: called.__setitem__("job", True))

    with pytest.raises(SystemExit):
        main.main()
    assert called["job"] is False, "가드 위반인데 매매 사이클(job)이 실행되었다"
