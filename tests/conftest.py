"""
공통 pytest 설정 (T03).

안전장치: 어떤 테스트도 실제 `data/bot.db` 를 건드리지 못하게 막는다.
기본 DB 경로를 테스트마다 임시 디렉터리로 돌려놓으므로, 테스트가 실수로
`db.open_db()` 를 인자 없이 호출해도 저장소가 오염되지 않는다.

이 장치가 없으면 `BotState()` 를 인자 없이 만드는 테스트 하나가 실제 상태 DB 를
만들거나 덮어쓸 수 있다(실전 상태를 잃는 사고로 이어진다).
"""
from datetime import timedelta

import pytest

import config
import db
import history


@pytest.fixture(autouse=True)
def _isolate_default_db(tmp_path, monkeypatch):
    """기본 DB 경로를 테스트별 임시 경로로 바꾼다. 모든 테스트에 자동 적용."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "isolated_bot.db"))
    yield


@pytest.fixture(autouse=True)
def _isolate_bars_dir(tmp_path, monkeypatch):
    """
    일봉 캐시 경로를 테스트별 임시 경로로 바꾼다 (T08).

    **T08 부터 판단 지표는 확정 일봉(`history.get_confirmed_bars`)만 쓴다.** 격리하지
    않으면 테스트가 실수로 저장소의 실제 `data/bars` 를 읽어, "지표가 없다"를 기대한
    테스트가 개발자 PC에 캐시가 있다는 이유로 다르게 동작한다(그리고 파일을 쓴다).
    """
    monkeypatch.setattr(history, "BARS_DIR", tmp_path / "bars")
    yield


@pytest.fixture
def confirmed_bars(tmp_path, monkeypatch):
    """
    확정 일봉을 캐시에 심는 헬퍼를 준다 (T08).

    `_run_cycle` 이 지표를 확정 봉으로만 계산하므로, 매수 경로를 실제로 돌리려면
    캐시가 있어야 한다. 날짜는 **오늘 기준 과거**로 만든다 — `get_confirmed_bars` 가
    당일 봉을 제외하므로 미래 날짜를 쓰면 결과가 달라진다.
    """
    monkeypatch.setattr(history, "BARS_DIR", tmp_path / "bars")
    today = db.now_kst().date()

    def _write(symbol: str, closes, volume: float = 1000.0):
        # T09b-6: 마지막 봉을 **직전 KRX 거래일**로 맞춘다. 일봉 최신성 점검(`BARS_MAX_LAG_SESSIONS`)이
        # 요일(월요일 아침의 일요일 날짜 등)에 따라 테스트를 흔들지 않게 한다.
        import market_calendar
        last_day = today - timedelta(days=1)
        for _ in range(15):
            if market_calendar.is_session_day(market_calendar.MARKET_KRX, last_day):
                break
            last_day -= timedelta(days=1)
        else:
            last_day = today - timedelta(days=1)
        start = last_day - timedelta(days=len(list(closes)) - 1)
        rows = []
        for i, close in enumerate(closes):
            rows.append({
                "date": (start + timedelta(days=i)).isoformat(),
                "open": close, "high": close, "low": close,
                "close": close, "volume": volume,
            })
        history.save_bars(symbol, rows)
        return rows

    return _write


# ── T09a: 테스트가 실제 알림·실제 HALT 파일에 닿지 않게 한다 ─────────────
import pytest as _pytest_t09a  # noqa: E402


@_pytest_t09a.fixture(autouse=True)
def _isolate_alerts_and_halt_file(monkeypatch, tmp_path):
    """
    - 텔레그램 설정을 비운다: `.env` 에 토큰을 넣어도 테스트가 실제 메시지를 보내지 않는다.
    - HALT 파일 경로를 임시 폴더로 돌린다: 프로젝트 루트에 HALT 파일이 있어도 테스트가 영향받지 않는다.
    """
    import config
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(config, "HALT_FILE", str(tmp_path / "HALT_not_present"))
