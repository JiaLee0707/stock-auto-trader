"""
T03: SQLite 상태 저장소(db.py) 단위 테스트.

모든 테스트는 tmp_path 임시 DB 만 쓴다. 실제 `data/bot.db` 를 만들거나 지우지 않는다.
"""
import sqlite3

import pytest

import db


@pytest.fixture
def conn(tmp_path):
    """임시 DB 연결. 테스트마다 새 파일."""
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "bot.db"


# ── 열기 / 스키마 ─────────────────────────────────────────

def test_open_creates_all_tables(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    names = {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    )}
    for table in ("positions", "orders", "daily_counters", "equity_log", "kv",
                  "schema_version"):
        assert table in names, f"{table} 테이블 없음"
    c.close()


def test_schema_version_is_current(conn):
    assert db.schema_version(conn) == db.SCHEMA_VERSION


def test_open_is_idempotent(tmp_path):
    """같은 DB 를 다시 열어도 마이그레이션이 중복 적용되지 않는다."""
    p = tmp_path / "bot.db"
    c1 = db.open_db(p)
    assert db.schema_version(c1) == db.SCHEMA_VERSION
    c1.close()

    c2 = db.open_db(p)
    assert db.schema_version(c2) == db.SCHEMA_VERSION
    # 마이그레이션은 버전 행을 하나만 남긴다
    assert c2.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    c2.close()


def test_migration_applies_only_missing_versions(conn, tmp_path, monkeypatch):
    """
    마이그레이션은 **아직 적용되지 않은 버전만** 순서대로 적용한다.

    이미 v1 인 DB 에 v2 를 추가했을 때 v1 스크립트를 다시 돌리면
    (예: 기존 테이블을 지우는 문장이 있으면) 데이터가 날아간다.
    """
    p = tmp_path / "bot.db"
    # v1 만 적용된 상태를 만든다
    c1 = db.open_db(p)
    assert db.schema_version(c1) == 1
    db.upsert_position(c1, "QQQ", "overseas", qty=3, avg_price=100.0)
    c1.close()

    # v2 를 추가한 상태로 다시 연다
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(db, "_MIGRATIONS", db._MIGRATIONS + [
        (2, "ALTER TABLE positions ADD COLUMN test_note TEXT;"),
    ])

    c2 = db.open_db(p)
    try:
        assert db.schema_version(c2) == 2
        # v1 데이터가 살아 있어야 한다 (v1 을 재실행하지 않았다는 증거)
        assert db.get_position(c2, "QQQ", "overseas")["qty"] == 3.0
        cols = {r["name"] for r in c2.execute("PRAGMA table_info(positions)")}
        assert "test_note" in cols, "v2 마이그레이션이 적용되지 않았다"
    finally:
        c2.close()


def test_failed_migration_raises_database_unavailable(tmp_path, monkeypatch):
    """깨진 마이그레이션은 DatabaseUnavailable — 반쯤 적용된 스키마로 시작하지 않는다."""
    monkeypatch.setattr(db, "_MIGRATIONS", db._MIGRATIONS + [
        (2, "THIS IS NOT VALID SQL;"),
    ])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 2)
    with pytest.raises(db.DatabaseUnavailable):
        db.open_db(tmp_path / "bot.db")


def test_open_fails_on_corrupt_file(tmp_path):
    """
    손상된 DB 는 조용히 넘어가지 않고 DatabaseUnavailable 을 던진다.
    상태를 모르는 채 매매를 시작하면 안 되므로 시작 거부가 맞다.
    """
    p = tmp_path / "bot.db"
    p.write_bytes(b"this is not a sqlite database at all" * 10)

    with pytest.raises(db.DatabaseUnavailable):
        db.open_db(p)


def test_open_fails_when_path_is_directory(tmp_path):
    """열 수 없는 경로면 DatabaseUnavailable (sqlite3.Error 를 그대로 새지 않게)."""
    d = tmp_path / "adir"
    d.mkdir()
    with pytest.raises(db.DatabaseUnavailable):
        db.open_db(d)


def test_open_fails_when_path_unwritable(tmp_path):
    """쓰기 불가 경로도 조용히 넘어가지 않는다."""
    p = tmp_path / "ro" / "bot.db"
    (tmp_path / "ro").mkdir()
    (tmp_path / "ro").chmod(0o500)
    try:
        with pytest.raises(db.DatabaseUnavailable):
            db.open_db(p)
    finally:
        (tmp_path / "ro").chmod(0o700)


def test_open_fails_with_database_unavailable_when_mkdir_fails(tmp_path):
    """
    상위 디렉터리를 만들 수 없을 때도 `DatabaseUnavailable` 이어야 한다.

    OSError(PermissionError) 가 그대로 새면 호출부의 `except DatabaseUnavailable`
    이 못 잡고 `main()` 이 시작을 거부하지 못한 채 스케줄러가 뜬다.
    """
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(db.DatabaseUnavailable):
            db.open_db(ro / "sub" / "bot.db")
    finally:
        ro.chmod(0o700)


def test_open_fails_when_db_is_locked(tmp_path, monkeypatch):
    """
    다른 프로세스가 배타적 잠금을 잡고 있으면 `DatabaseUnavailable` 로 **시작을 거부**한다.
    잠긴 DB 를 열린 것처럼 넘기면 이후 쓰기가 조용히 유실돼 상태가 어긋난다.

    대기 시간을 짧게 낮춰 검증한다(기본 5초를 기다리면 테스트만 느려진다).
    """
    monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 100)
    p = tmp_path / "bot.db"
    holder = sqlite3.connect(str(p))
    try:
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("CREATE TABLE t (x)")             # 잠금 유지
        with pytest.raises(db.DatabaseUnavailable):
            db.open_db(p)
    finally:
        holder.rollback()
        holder.close()


def test_write_fails_loudly_when_locked_not_silently_lost(tmp_path, monkeypatch):
    """
    쓰기 중 잠금이 걸리면 **예외를 올린다**(조용히 유실되지 않는다).

    상태 쓰기가 조용히 실패하면 봇은 주문했다고 착각하고, 다음 대조에서
    "DB 에만 있음/브로커에만 있음" 불일치로 나타난다. 그 전에 실패를 알려야 한다.
    """
    monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 100)
    p = tmp_path / "bot.db"
    conn = db.open_db(p)

    holder = sqlite3.connect(str(p))
    try:
        holder.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.Error):
            db.set_kv(conn, "some_key", "value")
    finally:
        holder.rollback()
        holder.close()

    # 잠금이 풀린 뒤에는 정상 기록된다
    db.set_kv(conn, "some_key", "value")
    assert db.get_kv(conn, "some_key") == "value"
    conn.close()


# ── 재시작 후 읽기 (완료 기준 1) ──────────────────────────

def test_write_then_read_from_new_instance(db_path):
    """쓰기 → 새 인스턴스에서 읽기. 재시작해도 상태가 유지되는지."""
    c1 = db.open_db(db_path)
    db.upsert_position(c1, "QQQ", "overseas", qty=3, avg_price=100.0,
                       entry_signal={"rsi": 25.0})
    order_id = db.record_order(c1, symbol="QQQ", market="overseas", side="BUY",
                               qty=3, price=100.0, client_ref="ref-1")
    db.increment_buy_count(c1, "2026-10-20", "QQQ")
    db.set_kv(c1, db.KV_TRADING_HALTED, "1")
    c1.close()

    c2 = db.open_db(db_path)
    pos = db.get_position(c2, "QQQ", "overseas")
    assert pos["qty"] == 3.0
    assert pos["avg_price"] == 100.0
    assert pos["entry_signal"] == {"rsi": 25.0}

    assert db.get_order(c2, order_id)["client_ref"] == "ref-1"
    assert db.get_daily_counter(c2, "2026-10-20", "QQQ")["buy_count"] == 1
    assert db.get_kv(c2, db.KV_TRADING_HALTED) == "1"
    c2.close()


# ── positions ─────────────────────────────────────────────

def test_upsert_position_replaces_same_key(conn):
    db.upsert_position(conn, "QQQ", "overseas", qty=3, avg_price=100.0)
    db.upsert_position(conn, "QQQ", "overseas", qty=5, avg_price=101.0)
    assert len(db.list_positions(conn)) == 1
    assert db.get_position(conn, "QQQ", "overseas")["qty"] == 5.0


def test_same_symbol_different_market_are_separate(conn):
    """같은 코드라도 시장이 다르면 다른 포지션이다."""
    db.upsert_position(conn, "X", "overseas", qty=1, avg_price=1.0)
    db.upsert_position(conn, "X", "domestic", qty=2, avg_price=2.0)
    assert len(db.list_positions(conn)) == 2


def test_close_position_marks_closed_and_excluded(conn):
    db.upsert_position(conn, "QQQ", "overseas", qty=3, avg_price=100.0)
    db.close_position(conn, "QQQ", "overseas")
    assert db.list_positions(conn, status="OPEN") == []
    closed = db.list_positions(conn, status="CLOSED")
    assert len(closed) == 1 and closed[0]["qty"] == 0.0


def test_position_rejects_bad_status(conn):
    with pytest.raises(ValueError):
        db.upsert_position(conn, "QQQ", "overseas", qty=1, avg_price=1.0,
                           status="WHATEVER")


def test_external_status_is_allowed(conn):
    """외부 보유는 별도 상태로 기록된다(봇이 매도하지 않게 구분)."""
    db.upsert_position(conn, "SPY", "overseas", qty=7, avg_price=1.0,
                       status="EXTERNAL")
    assert db.list_positions(conn, status="EXTERNAL")[0]["symbol"] == "SPY"


def test_broken_signal_json_does_not_kill_position(conn):
    """신호 JSON 이 깨져도 포지션 자체는 읽히고, 경고를 남긴다."""
    db.upsert_position(conn, "QQQ", "overseas", qty=1, avg_price=1.0)
    with db.transaction(conn):
        conn.execute("UPDATE positions SET entry_signal_json = ? WHERE symbol = 'QQQ'",
                     ("{깨진 json",))
    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 1.0
    assert pos["entry_signal"] is None


# ── orders ────────────────────────────────────────────────

def test_record_order_and_update_status(conn):
    oid = db.record_order(conn, symbol="QQQ", market="overseas", side="BUY",
                          qty=3, price=100.0, order_type="LIMIT",
                          client_ref="r1", reason="지표 매수신호")
    db.update_order_status(conn, oid, status="FILLED", filled_qty=3,
                           avg_fill_price=100.5, broker_order_no="B123")
    o = db.get_order(conn, oid)
    assert o["status"] == "FILLED"
    assert o["filled_qty"] == 3.0
    assert o["avg_fill_price"] == 100.5
    assert o["broker_order_no"] == "B123"


def test_record_order_sets_timestamps_and_config_hash(conn):
    oid = db.record_order(conn, symbol="QQQ", market="overseas", side="BUY", qty=1)
    o = db.get_order(conn, oid)
    assert o["created_at"] and o["updated_at"]
    assert o["config_hash"], "설정 지문이 비어 있으면 어떤 설정으로 낸 주문인지 알 수 없다"


def test_client_ref_is_unique(conn):
    """같은 client_ref 로 두 번 기록되면 중복 주문이므로 막는다."""
    db.record_order(conn, symbol="QQQ", market="overseas", side="BUY", qty=1,
                    client_ref="dup")
    with pytest.raises(sqlite3.IntegrityError):
        db.record_order(conn, symbol="QQQ", market="overseas", side="BUY", qty=1,
                        client_ref="dup")


def test_order_stores_raw_json(conn):
    oid = db.record_order(conn, symbol="QQQ", market="overseas", side="BUY", qty=1,
                          raw_request={"ORD_QTY": "1"},
                          raw_response={"rt_cd": "0", "msg1": "정상처리"})
    o = db.get_order(conn, oid)
    assert '"rt_cd"' in o["raw_response"]
    assert '"ORD_QTY"' in o["raw_request"]


def test_order_status_values_are_restricted(conn):
    """오타로 새 상태가 생기면 비교가 조용히 빗나간다."""
    oid = db.record_order(conn, symbol="QQQ", market="overseas", side="BUY", qty=1)
    with pytest.raises(ValueError):
        db.update_order_status(conn, oid, status="FILED")   # 오타
    with pytest.raises(ValueError):
        db.record_order(conn, symbol="Q", market="o", side="BUY", qty=1, status="DONE")


def test_all_documented_order_statuses_are_accepted(conn):
    """문서에 적은 6개 상태가 실제로 저장 가능한지 고정한다."""
    assert db.ORDER_STATUSES == ("SUBMITTED", "PARTIAL", "FILLED", "CANCELED",
                                 "REJECTED", "UNKNOWN")
    for i, status in enumerate(db.ORDER_STATUSES):
        oid = db.record_order(conn, symbol="QQQ", market="overseas", side="BUY",
                              qty=1, client_ref=f"s{i}", status=status)
        assert db.get_order(conn, oid)["status"] == status


def test_order_side_is_restricted(conn):
    with pytest.raises(ValueError):
        db.record_order(conn, symbol="QQQ", market="overseas", side="HOLD", qty=1)


def test_list_orders_filters(conn):
    db.record_order(conn, symbol="QQQ", market="overseas", side="BUY", qty=1,
                    client_ref="a")
    db.record_order(conn, symbol="379800", market="domestic", side="SELL", qty=1,
                    client_ref="b")
    assert len(db.list_orders(conn, symbol="QQQ")) == 1
    assert len(db.list_orders(conn, market="domestic")) == 1
    assert len(db.list_orders(conn)) == 2


# ── daily_counters ────────────────────────────────────────

def test_daily_counter_defaults_to_zero(conn):
    c = db.get_daily_counter(conn, "2026-10-20", "QQQ")
    assert c["buy_count"] == 0 and c["last_buy_time"] is None


def test_increment_buy_count_accumulates(conn):
    assert db.increment_buy_count(conn, "2026-10-20", "QQQ") == 1
    assert db.increment_buy_count(conn, "2026-10-20", "QQQ") == 2
    assert db.get_daily_counter(conn, "2026-10-20", "QQQ")["buy_count"] == 2


def test_daily_counters_are_separate_per_day(conn):
    """날짜가 다르면 다른 행이다 → 날짜가 바뀌면 자동으로 0회."""
    db.increment_buy_count(conn, "2026-10-20", "QQQ")
    db.increment_buy_count(conn, "2026-10-20", "QQQ")
    assert db.get_daily_counter(conn, "2026-10-20", "QQQ")["buy_count"] == 2
    assert db.get_daily_counter(conn, "2026-10-21", "QQQ")["buy_count"] == 0


def test_increment_records_last_buy_time_as_kst_iso(conn):
    """last_buy_time 은 오프셋 포함 KST 문자열이어야 한다(절대 규칙 9)."""
    db.increment_buy_count(conn, "2026-10-20", "QQQ")
    raw = db.get_daily_counter(conn, "2026-10-20", "QQQ")["last_buy_time"]
    assert raw is not None
    parsed = __import__("datetime").datetime.fromisoformat(raw)
    assert parsed.tzinfo is not None, f"naive 시각 저장됨: {raw!r}"
    assert parsed.utcoffset() == __import__("datetime").timedelta(hours=9)


def test_purge_daily_counters_before(conn):
    db.increment_buy_count(conn, "2026-10-18", "QQQ")
    db.increment_buy_count(conn, "2026-10-20", "QQQ")
    removed = db.purge_daily_counters_before(conn, "2026-10-19")
    assert removed == 1
    assert db.get_daily_counter(conn, "2026-10-18", "QQQ")["buy_count"] == 0
    assert db.get_daily_counter(conn, "2026-10-20", "QQQ")["buy_count"] == 1


# ── kv ────────────────────────────────────────────────────

def test_kv_roundtrip_and_overwrite(conn):
    assert db.get_kv(conn, "missing") is None
    assert db.get_kv(conn, "missing", "기본값") == "기본값"
    db.set_kv(conn, "k", "v1")
    assert db.get_kv(conn, "k") == "v1"
    db.set_kv(conn, "k", "v2")
    assert db.get_kv(conn, "k") == "v2"


def test_kv_none_clears_value(conn):
    db.set_kv(conn, "k", "v")
    db.set_kv(conn, "k", None)
    assert db.get_kv(conn, "k") is None


def test_kv_key_constants_are_stable():
    """킬스위치 키 이름이 바뀌면 이전 DB 의 플래그를 못 읽게 된다."""
    assert db.KV_TRADING_HALTED == "trading_halted"
    assert db.KV_HALT_REASON == "trading_halted_reason"
    assert db.KV_PEAK_EQUITY == "peak_equity"


# ── equity_log ────────────────────────────────────────────

def test_equity_log_records_and_returns_latest(conn):
    db.log_equity(conn, 10_000_000, 5_000_000, "paper", at="2026-10-20T09:00:00+09:00")
    db.log_equity(conn, 11_000_000, 4_000_000, "paper", at="2026-10-20T10:00:00+09:00")
    latest = db.latest_equity(conn)
    assert latest["total_equity"] == 11_000_000
    assert latest["mode"] == "paper"


def test_max_equity_tracks_peak_not_latest(conn):
    """고점은 최신값이 아니라 최대값이어야 한다(낙폭 계산용)."""
    db.log_equity(conn, 10_000_000, at="2026-10-20T09:00:00+09:00")
    db.log_equity(conn, 12_000_000, at="2026-10-20T10:00:00+09:00")
    db.log_equity(conn, 9_000_000, at="2026-10-20T11:00:00+09:00")
    assert db.max_equity(conn) == 12_000_000
    assert db.latest_equity(conn)["total_equity"] == 9_000_000


def test_latest_equity_none_when_empty(conn):
    assert db.latest_equity(conn) is None
    assert db.max_equity(conn) is None


# ── 트랜잭션 ──────────────────────────────────────────────

def test_transaction_rolls_back_on_error(conn):
    db.upsert_position(conn, "QQQ", "overseas", qty=1, avg_price=1.0)
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction(conn):
            conn.execute("UPDATE positions SET qty = 99 WHERE symbol = 'QQQ'")
            # UNIQUE 위반으로 실패 → 앞의 UPDATE 도 롤백돼야 한다
            conn.execute("INSERT INTO orders (client_ref, symbol, market, side, qty, "
                         "created_at, updated_at) VALUES ('x','A','o','BUY',1,'t','t')")
            conn.execute("INSERT INTO orders (client_ref, symbol, market, side, qty, "
                         "created_at, updated_at) VALUES ('x','B','o','BUY',1,'t','t')")
    assert db.get_position(conn, "QQQ", "overseas")["qty"] == 1.0
    assert db.list_orders(conn) == []


def test_transaction_error_is_not_swallowed(conn):
    """절대 규칙 7: 실패를 조용히 넘기지 않는다."""
    with pytest.raises(sqlite3.OperationalError):
        with db.transaction(conn):
            conn.execute("SELECT * FROM table_that_does_not_exist")


# ── config_hash ───────────────────────────────────────────

def test_config_hash_is_stable_and_short():
    a = db.config_hash()
    b = db.config_hash()
    assert a == b
    assert len(a) == 16 and all(ch in "0123456789abcdef" for ch in a)


def test_config_hash_has_no_secret_material():
    """
    설정 지문에는 키·계좌번호가 들어가면 안 된다(절대 규칙 2).
    config 의 비밀값을 바꿔도 해시가 달라지지 않아야 한다.
    """
    import config
    before = db.config_hash()
    original = config.KIS_ACCOUNT_NO
    try:
        config.KIS_ACCOUNT_NO = "12345678-01"
        assert db.config_hash() == before, "계좌번호가 해시에 섞이면 안 된다"
    finally:
        config.KIS_ACCOUNT_NO = original


def test_config_hash_changes_with_strategy_value():
    """전략 수치가 바뀌면 지문도 바뀌어야 추적이 된다."""
    import config
    before = db.config_hash()
    original = config.RSI_OVERSOLD
    try:
        config.RSI_OVERSOLD = original + 1
        assert db.config_hash() != before
    finally:
        config.RSI_OVERSOLD = original


# ── 시간 ──────────────────────────────────────────────────

def test_now_iso_is_tz_aware_kst():
    from datetime import datetime, timedelta
    parsed = datetime.fromisoformat(db.now_iso())
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(hours=9)


def test_iso_strings_sort_chronologically():
    """
    저장을 ISO 문자열로 하므로 문자열 정렬이 시간 정렬과 같아야 한다.
    KST 는 오프셋 고정(+09:00)이라 성립한다 — 이 가정이 깨지면 정렬이 조용히 틀어진다.
    """
    from datetime import timedelta
    base = db.now_kst()
    stamps = [(base + timedelta(hours=h)).isoformat(timespec="seconds")
              for h in (0, 5, 12, 23, 47)]
    assert sorted(stamps) == stamps
