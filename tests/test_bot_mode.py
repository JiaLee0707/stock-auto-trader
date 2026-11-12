"""
T09b-4: 상태 DB 에 모드(MOCK/LIVE)를 기록하고, 다른 모드로 시작하면 거부한다.

  - 모의 DB 의 포지션·주문·손실 기준선·고점이 실전 첫 사이클로 이어지면 안 된다 → 어느 방향이든 다르면 거부한다
  - 처음 쓰는 DB 는 지금 모드를 기록하고 계속한다. 거부할 때는 기록을 바꾸지 않는다
  - 기록이 없는데 이전 기록(주문·포지션 등)이 있는 DB 는 실전으로 시작하지 않는다(모의로는 기록하고 계속한다)
  - 확인하지 못해도(DB 오류) 시작하지 않고, 로그에 예외 본문을 남기지 않는다(requests 예외에 URL·토큰이 들어간다)
  - main() 이 DB 를 연 직후, 첫 사이클(job) 전에 이 확인을 부른다(AST 로 고정)
실제 API 는 쓰지 않는다. `main.main()` 은 실행하지 않는다.
"""
import ast
import logging
import pathlib

import pytest

import config
import db
import main


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


def _mode(conn):
    return db.get_kv(conn, db.KV_BOT_MODE)


# ── 순수 판정 ────────────────────────────────────────────

def _err(**over):
    kw = dict(recorded=None, current="MOCK", has_history=False)
    kw.update(over)
    return config.bot_mode_error(**kw)


@pytest.mark.parametrize("current", ["MOCK", "LIVE"])
def test_a_fresh_db_is_accepted_in_either_mode(current):
    assert _err(current=current) is None


@pytest.mark.parametrize("mode", ["MOCK", "LIVE"])
def test_the_same_mode_is_accepted(mode):
    assert _err(recorded=mode, current=mode, has_history=True) is None


@pytest.mark.parametrize("recorded,current", [("MOCK", "LIVE"), ("LIVE", "MOCK")])
def test_a_different_mode_is_refused_in_both_directions(recorded, current):
    msg = _err(recorded=recorded, current=current, has_history=True)
    assert msg and recorded in msg and current in msg and "DB_PATH" in msg


@pytest.mark.parametrize("recorded", ["", "mock", "모의", "PAPER"])
def test_an_unreadable_recorded_mode_is_refused(recorded):
    msg = _err(recorded=recorded, current="MOCK")
    assert msg and "알아볼 수 없습니다" in msg


def test_history_without_a_record_is_refused_for_live_but_not_for_mock():
    msg = _err(has_history=True, current="LIVE")
    assert msg and "DB_PATH" in msg
    assert _err(has_history=True, current="MOCK") is None


@pytest.mark.parametrize("current", ["", "mock", None, "PAPER"])
def test_an_unknown_current_mode_is_refused(current):
    assert _err(current=current)


# ── DB 기록 ──────────────────────────────────────────────

def test_the_label_follows_the_flag():
    assert db.bot_mode_label(True) == "MOCK"
    assert db.bot_mode_label(False) == "LIVE"


@pytest.mark.parametrize("is_mock,label", [(True, "MOCK"), (False, "LIVE")])
def test_a_fresh_db_records_the_mode_it_was_started_in(conn, is_mock, label):
    assert _mode(conn) is None
    assert db.verify_bot_mode(conn, is_mock) is None
    assert _mode(conn) == label


def test_a_second_start_in_the_same_mode_is_fine(conn):
    db.verify_bot_mode(conn, True)
    assert db.verify_bot_mode(conn, True) is None
    assert _mode(conn) == "MOCK"


@pytest.mark.parametrize("first,second", [(True, False), (False, True)])
def test_a_different_mode_is_refused_and_the_record_is_kept(conn, first, second):
    db.verify_bot_mode(conn, first)
    before = _mode(conn)
    assert db.verify_bot_mode(conn, second)
    assert _mode(conn) == before


def _seed_orders(c):
    ts = db.now_iso()
    c.execute(
        "INSERT INTO orders (client_ref, symbol, market, side, qty, status, created_at, updated_at) "
        "VALUES ('t-1', '379800', 'domestic', 'BUY', 1, 'FILLED', ?, ?)", (ts, ts))
    c.commit()


def _seed_positions(c):
    db.upsert_position(c, "379800", "domestic", qty=1.0, avg_price=100.0, status="OPEN")


def _seed_equity(c):
    db.log_equity(c, 200_000.0, None, "MOCK")


def _seed_halt(c):
    db.record_halt_event(c, "HALT", kind="RISK", reason="테스트", actor="bot", details=None)


def _seed_counters(c):
    c.execute("INSERT INTO daily_counters (date, symbol, buy_count) VALUES ('2026-11-03', '379800', 1)")
    c.commit()


SEEDS = {"orders": _seed_orders, "positions": _seed_positions, "equity_log": _seed_equity,
         "halt_events": _seed_halt, "daily_counters": _seed_counters}


def test_a_fresh_db_has_no_history(conn):
    assert db.has_state_history(conn) is False       # 모든 표 이름이 실제 스키마에 있는지도 함께 확인된다


def test_kv_entries_alone_are_not_history(conn):
    db.set_kv(conn, "anything", "1")
    assert db.has_state_history(conn) is False
    assert db.verify_bot_mode(conn, False) is None


@pytest.mark.parametrize("table", list(SEEDS))
def test_a_db_with_history_and_no_record_is_refused_for_live_and_adopted_for_mock(conn, table):
    SEEDS[table](conn)
    assert db.has_state_history(conn)
    assert db.verify_bot_mode(conn, False)           # 실전은 거부한다
    assert _mode(conn) is None                       # 거부할 때는 기록하지 않는다
    assert db.verify_bot_mode(conn, True) is None    # 모의는 기록하고 계속한다
    assert _mode(conn) == "MOCK"


def test_a_read_failure_is_raised_not_swallowed(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("SECRET-TOKEN-123")

    monkeypatch.setattr(db, "get_kv", boom)
    with pytest.raises(RuntimeError):
        db.verify_bot_mode(conn, True)


# ── main() 연결 ──────────────────────────────────────────

def test_the_startup_check_accepts_and_records_a_fresh_db(conn, monkeypatch):
    monkeypatch.setattr(config, "IS_MOCK", True)
    main._verify_db_mode(conn)
    assert _mode(conn) == "MOCK"


def test_the_startup_check_refuses_a_live_start_on_a_mock_db(conn, monkeypatch, caplog):
    monkeypatch.setattr(config, "IS_MOCK", True)
    main._verify_db_mode(conn)
    monkeypatch.setattr(config, "IS_MOCK", False)
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        with pytest.raises(SystemExit) as exc:
            main._verify_db_mode(conn)
    assert exc.value.code == 1
    assert "시작을 거부" in caplog.text
    assert _mode(conn) == "MOCK"


def test_the_startup_check_refuses_a_mock_start_on_a_live_db(conn, monkeypatch, caplog):
    monkeypatch.setattr(config, "IS_MOCK", False)
    main._verify_db_mode(conn)
    monkeypatch.setattr(config, "IS_MOCK", True)
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        with pytest.raises(SystemExit) as exc:
            main._verify_db_mode(conn)
    assert exc.value.code == 1
    assert "시작을 거부" in caplog.text
    assert _mode(conn) == "LIVE"


def test_the_startup_check_refuses_when_it_cannot_check_and_does_not_leak(conn, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("SECRET-TOKEN-123")

    monkeypatch.setattr(db, "verify_bot_mode", boom)
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        with pytest.raises(SystemExit) as exc:
            main._verify_db_mode(conn)
    assert exc.value.code == 1
    assert "RuntimeError" in caplog.text
    assert "SECRET-TOKEN-123" not in caplog.text


def test_main_checks_the_mode_right_after_opening_the_db_and_before_the_first_cycle():
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    lines = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "open_db":
            lines["open"] = node.lineno
        elif isinstance(f, ast.Name) and f.id == "_verify_db_mode":
            lines["verify"] = node.lineno
        elif isinstance(f, ast.Name) and f.id == "job":
            lines["job"] = node.lineno
    assert set(lines) == {"open", "verify", "job"}, lines
    assert lines["open"] < lines["verify"] < lines["job"]
