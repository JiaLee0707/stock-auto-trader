"""
T09a-2: 중단 종류(STATE/RISK)·이력(halt_events)·알림 연결·마이그레이션 v3.
실제 알림은 보내지 않는다 — conftest 가 텔레그램 설정을 비우고, 여기서는 notify 를 가짜로 바꾼다.
"""
import logging
import sqlite3

import pytest

import db
import reconcile


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def sent(monkeypatch):
    """notifier.notify 를 가짜로 바꾸고 호출 기록 [(level, title, message), ...] 을 돌려준다."""
    calls = []

    def fake(level, title, message=""):
        calls.append((level, title, message))
        return True

    monkeypatch.setattr(reconcile.notifier, "notify", fake)
    return calls


def _version(conn):
    return conn.execute("SELECT version FROM schema_version").fetchone()["version"]


def _tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


# ── 마이그레이션 ──────────────────────────────────────────

def test_new_db_has_halt_events_at_latest_schema(conn):
    assert db.SCHEMA_VERSION == db._MIGRATIONS[-1][0]
    assert _version(conn) == db.SCHEMA_VERSION
    assert "halt_events" in _tables(conn)


def test_v2_db_upgrades_to_latest_and_keeps_data(tmp_path):
    path = tmp_path / "bot.db"
    c = db.open_db(path)
    db.upsert_position(c, "379800", "domestic", qty=3.0, avg_price=100.0, status="OPEN")
    c.execute("DROP TABLE halt_events")
    c.execute("DELETE FROM schema_version")
    c.execute("INSERT INTO schema_version (version) VALUES (2)")
    c.commit()
    c.close()

    c = db.open_db(path)
    try:
        assert _version(c) == db.SCHEMA_VERSION
        assert "halt_events" in _tables(c)
        assert db.get_position(c, "379800", "domestic")["qty"] == 3.0
    finally:
        c.close()


# ── 중단 ──────────────────────────────────────────────────

def test_halt_defaults_to_state_and_records_event(conn, sent):
    assert reconcile.halt_trading(conn, "불일치 테스트") is True
    assert reconcile.is_trading_halted(conn) is True
    assert reconcile.halt_kind(conn) == "STATE"
    assert reconcile.halt_reason(conn) == "불일치 테스트"
    assert reconcile.halt_since(conn)
    events = db.list_halt_events(conn)
    assert len(events) == 1
    assert (events[0]["event"], events[0]["kind"], events[0]["actor"]) == ("HALT", "STATE", "bot")
    assert len(sent) == 1 and sent[0][0] == "CRITICAL"
    assert "불일치 테스트" in sent[0][2] and "python -m risk resume" in sent[0][2]


def test_halt_survives_notifier_exception(conn, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("boom-secret")

    monkeypatch.setattr(reconcile.notifier, "notify", boom)
    with caplog.at_level(logging.ERROR, logger="bot"):
        assert reconcile.halt_trading(conn, "사유") is True
    assert reconcile.is_trading_halted(conn) is True
    assert len(db.list_halt_events(conn)) == 1
    assert "RuntimeError" in caplog.text
    assert "boom-secret" not in caplog.text


@pytest.mark.parametrize("kind", ["STATE", "RISK"])
def test_repeated_halt_of_same_kind_notifies_once(conn, sent, kind):
    assert reconcile.halt_trading(conn, "첫 사유", kind) is True
    assert reconcile.halt_trading(conn, "둘째 사유", kind) is False
    assert len(sent) == 1
    assert len(db.list_halt_events(conn)) == 1
    assert reconcile.halt_reason(conn) == "첫 사유"


def test_risk_halt_is_promoted_to_state(conn, sent):
    assert reconcile.halt_trading(conn, "낙폭", "RISK") is True
    assert reconcile.halt_kind(conn) == "RISK"
    assert reconcile.halt_trading(conn, "불일치", "STATE") is True
    assert reconcile.halt_kind(conn) == "STATE"
    assert reconcile.halt_reason(conn) == "불일치"
    events = db.list_halt_events(conn)
    assert [e["kind"] for e in events] == ["STATE", "RISK"]        # 최신순
    assert len(sent) == 2 and "RISK→STATE" in sent[1][1]


def test_state_halt_is_not_downgraded_by_risk(conn, sent):
    assert reconcile.halt_trading(conn, "불일치", "STATE") is True
    assert reconcile.halt_trading(conn, "낙폭", "RISK") is False
    assert reconcile.halt_kind(conn) == "STATE"
    assert reconcile.halt_reason(conn) == "불일치"
    assert len(db.list_halt_events(conn)) == 1
    assert len(sent) == 1


def test_invalid_kind_is_rejected_without_change(conn, sent):
    with pytest.raises(ValueError):
        reconcile.halt_trading(conn, "사유", "WHATEVER")
    assert reconcile.is_trading_halted(conn) is False
    assert db.list_halt_events(conn) == []
    assert sent == []


def test_halt_kind_is_strictest_when_missing(conn):
    assert reconcile.halt_kind(conn) is None
    db.set_kv(conn, db.KV_TRADING_HALTED, "1")          # T09a 이전 방식으로 세운 플래그
    assert reconcile.halt_kind(conn) == "STATE"
    db.set_kv(conn, db.KV_HALT_KIND, "엉뚱한값")
    assert reconcile.halt_kind(conn) == "STATE"


def test_db_write_failure_still_notifies_and_raises(conn, monkeypatch, sent):
    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "record_halt_event", boom)
    with pytest.raises(sqlite3.OperationalError):
        reconcile.halt_trading(conn, "사유")
    assert len(sent) == 1 and "기록 실패" in sent[0][1]
    assert reconcile.is_trading_halted(conn) is True     # 안전한 쪽으로 남는다


# ── 재개 ──────────────────────────────────────────────────

def test_resume_records_event_and_clears_state(conn, sent):
    reconcile.halt_trading(conn, "낙폭", "RISK")
    assert reconcile.resume_trading(conn, "확인함") is True
    assert reconcile.is_trading_halted(conn) is False
    assert reconcile.halt_reason(conn) is None
    assert reconcile.halt_kind(conn) is None
    last = db.list_halt_events(conn)[0]
    assert (last["event"], last["kind"], last["reason"], last["actor"]) == (
        "RESUME", "RISK", "확인함", "manual")


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_resume_without_reason_warns_and_records_placeholder(conn, sent, caplog, reason):
    reconcile.halt_trading(conn, "낙폭", "RISK")
    with caplog.at_level(logging.WARNING, logger="bot"):
        assert reconcile.resume_trading(conn, reason) is True
    assert "재개 사유가 없습니다" in caplog.text
    assert db.list_halt_events(conn)[0]["reason"] == "사유 미기록"


def test_resume_when_not_halted_records_nothing(conn):
    assert reconcile.resume_trading(conn, "아무것도 없음") is False
    assert db.list_halt_events(conn) == []
