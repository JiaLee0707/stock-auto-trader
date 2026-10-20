"""
공통 pytest 설정 (T03).

안전장치: 어떤 테스트도 실제 `data/bot.db` 를 건드리지 못하게 막는다.
기본 DB 경로를 테스트마다 임시 디렉터리로 돌려놓으므로, 테스트가 실수로
`db.open_db()` 를 인자 없이 호출해도 저장소가 오염되지 않는다.

이 장치가 없으면 `BotState()` 를 인자 없이 만드는 테스트 하나가 실제 상태 DB 를
만들거나 덮어쓸 수 있다(실전 상태를 잃는 사고로 이어진다).
"""
import pytest

import config


@pytest.fixture(autouse=True)
def _isolate_default_db(tmp_path, monkeypatch):
    """기본 DB 경로를 테스트별 임시 경로로 바꾼다. 모든 테스트에 자동 적용."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "isolated_bot.db"))
    yield
