"""
SQLite 상태 저장소 (T03).

목적: 메모리 전용 상태를 없애 재시작해도 포지션·주문·당일 카운터가 유지되게 한다.
      표준 `sqlite3` 만 쓴다(새 의존성 없음).

DB 파일: `config.DB_PATH` (기본 `data/bot.db`). `data/` 는 .gitignore 대상이다.

설계 원칙
- **시간은 항상 tz-aware KST**(절대 규칙 9). 저장은 ISO 문자열(오프셋 포함), 비교는 문자열 정렬.
  KST는 오프셋이 고정(+09:00)이라 ISO 문자열 정렬 = 시간 정렬이 성립한다.
- **모든 쓰기는 트랜잭션**. `transaction()` 컨텍스트를 쓰면 예외 시 자동 롤백된다.
- **잠김/손상은 조용히 넘기지 않는다.** `open_db()` 가 `DatabaseUnavailable` 을 던지면
  호출부(봇 시작 지점)는 매매를 시작하지 않고 종료한다(상태를 모르는 채 주문하면 위험).
- 주문 상태 값은 `ORDER_STATUSES` 로 제한한다. 오타로 새 상태가 생기면 비교가 조용히 빗나간다.
"""
import hashlib
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger("bot")

KST = ZoneInfo("Asia/Seoul")

# 주문 상태. 임의 문자열을 쓰지 않도록 여기서만 정의한다.
ORDER_STATUSES = (
    "SUBMITTED",   # 접수만 확인 (현재 봇의 기본값)
    "PARTIAL",     # 부분 체결
    "FILLED",      # 전량 체결
    "CANCELED",
    "REJECTED",
    "UNKNOWN",     # 응답 불명 — 재시도 금지, 사람이 확인해야 함
)

SIDES = ("BUY", "SELL")


class OrderStatus(NamedTuple):
    """
    브로커가 알려준 주문 1건의 체결 상태 (T04).

    `status` 는 `ORDER_STATUSES` 중 하나다. 체결 수량·평균 체결가가 아직 없으면
    `filled_qty=0`, `avg_fill_price=None` 이다.
    """

    status: str
    filled_qty: float = 0.0
    avg_fill_price: float | None = None
    raw: dict | None = None

    @property
    def is_filled(self) -> bool:
        """전량 체결."""
        return self.status == "FILLED"

    @property
    def is_open(self) -> bool:
        """아직 살아 있는 주문(취소·체결 확정 전)."""
        return self.status in ("SUBMITTED", "PARTIAL", "UNKNOWN")
# EXTERNAL: 브로커에는 있는데 봇이 만든 기록이 없는 보유. 봇이 매도하지 않는다.
POSITION_STATUSES = ("OPEN", "CLOSED", "EXTERNAL")

# 킬스위치·자산고점 등 단일 값 저장 키 (오타 방지를 위해 상수로 고정)
KV_TRADING_HALTED = "trading_halted"
KV_HALT_REASON = "trading_halted_reason"
KV_PEAK_EQUITY = "peak_equity"

SCHEMA_VERSION = 1

# 잠금 대기 시간(ms). 잠긴 DB 를 오래 붙잡고 있지 않도록 짧게 잡는다.
# 이 값이 곧 "잠김을 얼마나 기다린 뒤 실패로 알릴지"이므로 테스트에서 낮춰 검증한다.
BUSY_TIMEOUT_MS = 5000


def now_kst() -> datetime:
    """현재 시각(KST, tz-aware)."""
    return datetime.now(KST)


def now_iso() -> str:
    """현재 시각을 오프셋 포함 ISO 문자열로. 저장용."""
    return now_kst().isoformat(timespec="seconds")


class DatabaseUnavailable(RuntimeError):
    """DB 를 열 수 없거나 손상됐다. 호출부는 매매를 시작하면 안 된다."""


# ── 스키마 ────────────────────────────────────────────────
# 마이그레이션은 인덱스 순서대로 적용한다. 새 버전은 리스트 끝에 추가하고
# SCHEMA_VERSION 을 올린다. 기존 항목은 절대 수정하지 않는다(이미 적용된 DB와 어긋남).
_MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE IF NOT EXISTS positions (
        symbol                 TEXT NOT NULL,
        market                 TEXT NOT NULL,
        qty                    REAL NOT NULL DEFAULT 0,
        avg_price              REAL NOT NULL DEFAULT 0,
        entry_time             TEXT,
        entry_signal_json      TEXT,
        peak_price_since_entry REAL,
        status                 TEXT NOT NULL DEFAULT 'OPEN',
        PRIMARY KEY (symbol, market)
    );

    CREATE TABLE IF NOT EXISTS orders (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        client_ref      TEXT UNIQUE,
        broker_order_no TEXT,
        symbol          TEXT NOT NULL,
        market          TEXT NOT NULL,
        side            TEXT NOT NULL,
        qty             REAL NOT NULL,
        price           REAL,
        order_type      TEXT,
        status          TEXT NOT NULL DEFAULT 'SUBMITTED',
        filled_qty      REAL NOT NULL DEFAULT 0,
        avg_fill_price  REAL,
        reason          TEXT,
        config_hash     TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        raw_request     TEXT,
        raw_response    TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol, market);
    CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

    CREATE TABLE IF NOT EXISTS daily_counters (
        date          TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        buy_count     INTEGER NOT NULL DEFAULT 0,
        last_buy_time TEXT,
        PRIMARY KEY (date, symbol)
    );

    CREATE TABLE IF NOT EXISTS equity_log (
        ts           TEXT NOT NULL,
        total_equity REAL NOT NULL,
        cash         REAL,
        mode         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_log(ts);

    CREATE TABLE IF NOT EXISTS kv (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    );
    """),
]


def _configure(conn: sqlite3.Connection) -> None:
    """연결 공통 설정. 잠금 대기는 짧게 잡아 오래 붙잡지 않는다."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    필요한 마이그레이션만 순서대로 적용한다.

    `executescript` 는 실행 전에 암묵적 COMMIT 을 하므로 `with conn:` 트랜잭션과
    함께 쓰면 원자성이 깨진다. 그래서 BEGIN/COMMIT 을 직접 관리한다.
    """
    # 신규 DB 에는 schema_version 테이블 자체가 없으므로 존재 여부를 먼저 본다.
    if _table_exists(conn, "schema_version"):
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = int(row["version"]) if row else 0
    else:
        current = 0

    for version, script in _MIGRATIONS:
        if version <= current:
            continue
        logger.info(f"[db] 스키마 마이그레이션 적용: v{current} → v{version}")
        try:
            conn.executescript(script)
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            raise DatabaseUnavailable(
                f"스키마 마이그레이션 실패 (v{current} → v{version}): {e}"
            ) from e
        current = version


def open_db(path: str | os.PathLike | None = None,
            *, check_integrity: bool = True) -> sqlite3.Connection:
    """
    DB 를 열고 마이그레이션을 적용한다.

    잠김·손상·경로 문제면 `DatabaseUnavailable` 을 던진다(조용히 넘어가지 않는다).
    상태를 모르는 채 매매를 시작하면 중복 주문·미관리 포지션이 생기므로,
    호출부는 이 예외를 잡아 **시작을 거부**해야 한다.
    """
    db_path = Path(path).expanduser() if path is not None else Path(config.DB_PATH)
    # 디렉터리 생성 실패도 DatabaseUnavailable 로 올린다. OSError 를 그대로 새면
    # 호출부의 `except DatabaseUnavailable` 이 못 잡고 스케줄러가 그대로 뜬다.
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise DatabaseUnavailable(
            f"DB 디렉터리를 만들 수 없습니다 ({db_path.parent}): {e}"
        ) from e

    try:
        conn = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000.0,
                               isolation_level="DEFERRED")
    except sqlite3.Error as e:
        raise DatabaseUnavailable(f"DB 열기 실패 ({db_path}): {e}") from e

    try:
        _configure(conn)

        if check_integrity:
            result = conn.execute("PRAGMA integrity_check").fetchone()
            verdict = result[0] if result else "unknown"
            if verdict != "ok":
                conn.close()
                raise DatabaseUnavailable(
                    f"DB 무결성 검사 실패 ({db_path}): {verdict}. "
                    "손상된 상태로 매매를 시작하지 않습니다. 백업 후 새 DB 로 복구하세요."
                )

        _apply_migrations(conn)
    except sqlite3.OperationalError as e:
        # 잠김(database is locked)·읽기 전용 등
        conn.close()
        raise DatabaseUnavailable(f"DB 잠김/접근 불가 ({db_path}): {e}") from e
    except sqlite3.DatabaseError as e:
        conn.close()
        raise DatabaseUnavailable(f"DB 손상 의심 ({db_path}): {e}") from e

    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """
    쓰기 트랜잭션. 예외가 나면 롤백하고 그대로 올린다(삼키지 않음).

    `try/except: pass` 금지(절대 규칙 7)에 따라 실패는 호출부가 반드시 알게 한다.
    """
    try:
        with conn:
            yield conn
    except sqlite3.Error as e:
        logger.error(f"[db] 트랜잭션 실패 — 롤백했습니다: {e}")
        raise


# ── 공통 조회/저장 ────────────────────────────────────────

def get_kv(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """kv 단일 값 조회."""
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_kv(conn: sqlite3.Connection, key: str, value) -> None:
    """kv 단일 값 저장(있으면 덮어씀)."""
    with transaction(conn):
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, None if value is None else str(value)),
        )


def config_hash() -> str:
    """
    전략 수치의 지문. 주문 시점의 설정을 남겨 "어떤 설정으로 낸 주문인지" 추적한다.
    비밀값(키·계좌번호)은 포함하지 않는다 — 해시만 저장하고 원문은 저장하지 않는다.
    """
    payload = {
        "rsi_period": config.RSI_PERIOD,
        "rsi_oversold": config.RSI_OVERSOLD,
        "rsi_overbought": config.RSI_OVERBOUGHT,
        "bb_period": config.BB_PERIOD,
        "bb_num_std": config.BB_NUM_STD,
        "max_buys_per_day": config.MAX_BUYS_PER_DAY,
        "min_buy_interval_hours": config.MIN_BUY_INTERVAL_HOURS,
        "take_profit_steps": config.TAKE_PROFIT_STEPS,
        "stop_loss_threshold": config.STOP_LOSS_THRESHOLD,
        "dip_threshold": config.DIP_THRESHOLD,
        "buy_ratio": config.BUY_RATIO,
        "domestic_buy_ratio": config.DOMESTIC_BUY_RATIO,
        "ai_score_floor": config.AI_SCORE_FLOOR,
        "is_mock": config.IS_MOCK,
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def schema_version(conn: sqlite3.Connection) -> int:
    """현재 스키마 버전."""
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row["version"]) if row else 0


# ── positions ─────────────────────────────────────────────

def position_key(symbol: str, market: str) -> tuple[str, str]:
    """포지션 식별 키. 시장이 다르면 같은 코드라도 다른 포지션이다."""
    return (str(symbol), str(market))


def upsert_position(conn: sqlite3.Connection, symbol: str, market: str, *,
                    qty: float, avg_price: float, entry_time: str | None = None,
                    entry_signal: dict | None = None,
                    peak_price_since_entry: float | None = None,
                    status: str = "OPEN") -> None:
    """포지션을 생성하거나 갱신한다(같은 symbol+market 이면 덮어씀)."""
    if status not in POSITION_STATUSES:
        raise ValueError(f"허용되지 않는 포지션 상태: {status!r} (허용: {POSITION_STATUSES})")
    signal_json = (
        json.dumps(entry_signal, ensure_ascii=False, sort_keys=True)
        if entry_signal is not None else None
    )
    with transaction(conn):
        conn.execute(
            "INSERT INTO positions (symbol, market, qty, avg_price, entry_time, "
            "        entry_signal_json, peak_price_since_entry, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(symbol, market) DO UPDATE SET "
            "  qty = excluded.qty, avg_price = excluded.avg_price, "
            "  entry_time = excluded.entry_time, "
            "  entry_signal_json = excluded.entry_signal_json, "
            "  peak_price_since_entry = excluded.peak_price_since_entry, "
            "  status = excluded.status",
            (str(symbol), str(market), float(qty), float(avg_price), entry_time,
             signal_json, peak_price_since_entry, status),
        )


def get_position(conn: sqlite3.Connection, symbol: str, market: str) -> dict | None:
    """포지션 1건 조회. 없으면 None."""
    row = conn.execute(
        "SELECT * FROM positions WHERE symbol = ? AND market = ?",
        (str(symbol), str(market)),
    ).fetchone()
    return _position_row(row) if row else None


def list_positions(conn: sqlite3.Connection, *, status: str = "OPEN") -> list[dict]:
    """포지션 목록. 기본은 OPEN 만."""
    rows = conn.execute(
        "SELECT * FROM positions WHERE status = ? ORDER BY symbol, market", (status,)
    ).fetchall()
    return [_position_row(r) for r in rows]


def _position_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    raw = d.get("entry_signal_json")
    if raw:
        try:
            d["entry_signal"] = json.loads(raw)
        except json.JSONDecodeError as e:
            # 신호 JSON 이 깨져도 포지션 자체는 살린다. 다만 조용히 넘기지 않는다.
            logger.warning(f"[db] entry_signal_json 해석 실패 ({d.get('symbol')}): {e}")
            d["entry_signal"] = None
    else:
        d["entry_signal"] = None
    return d


def close_position(conn: sqlite3.Connection, symbol: str, market: str) -> None:
    """포지션을 CLOSED 로 표시한다(삭제하지 않는다 — 이력 보존)."""
    with transaction(conn):
        conn.execute(
            "UPDATE positions SET status = 'CLOSED', qty = 0 "
            "WHERE symbol = ? AND market = ?",
            (str(symbol), str(market)),
        )


# ── orders ────────────────────────────────────────────────

def record_order(conn: sqlite3.Connection, *, symbol: str, market: str, side: str,
                 qty: float, price: float | None = None,
                 order_type: str | None = None, status: str = "SUBMITTED",
                 client_ref: str | None = None, broker_order_no: str | None = None,
                 filled_qty: float = 0, avg_fill_price: float | None = None,
                 reason: str | None = None, raw_request: dict | None = None,
                 raw_response: dict | None = None, at: str | None = None) -> int:
    """
    주문 1건을 기록하고 id 를 돌려준다.

    `client_ref` 는 우리가 만든 주문 식별자로 UNIQUE 다. 같은 값으로 다시 넣으면
    중복 주문 기록을 막기 위해 예외가 난다(중복 주문 방지에 쓴다).
    """
    if side not in SIDES:
        raise ValueError(f"허용되지 않는 주문 방향: {side!r} (허용: {SIDES})")
    if status not in ORDER_STATUSES:
        raise ValueError(f"허용되지 않는 주문 상태: {status!r} (허용: {ORDER_STATUSES})")
    ts = at or now_iso()
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO orders (client_ref, broker_order_no, symbol, market, side, "
            "  qty, price, order_type, status, filled_qty, avg_fill_price, reason, "
            "  config_hash, created_at, updated_at, raw_request, raw_response) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (client_ref, broker_order_no, str(symbol), str(market), side, float(qty),
             price, order_type, status, float(filled_qty), avg_fill_price, reason,
             config_hash(), ts, ts,
             json.dumps(raw_request, ensure_ascii=False, default=str) if raw_request else None,
             json.dumps(raw_response, ensure_ascii=False, default=str) if raw_response else None),
        )
        return int(cur.lastrowid)


def update_order_status(conn: sqlite3.Connection, order_id: int, *,
                        status: str, filled_qty: float | None = None,
                        avg_fill_price: float | None = None,
                        broker_order_no: str | None = None,
                        raw_response: dict | None = None,
                        at: str | None = None) -> None:
    """주문 상태를 갱신한다. 상태 값은 `ORDER_STATUSES` 만 허용한다."""
    if status not in ORDER_STATUSES:
        raise ValueError(f"허용되지 않는 주문 상태: {status!r} (허용: {ORDER_STATUSES})")

    sets = ["status = ?", "updated_at = ?"]
    params: list = [status, at or now_iso()]
    if filled_qty is not None:
        sets.append("filled_qty = ?")
        params.append(float(filled_qty))
    if avg_fill_price is not None:
        sets.append("avg_fill_price = ?")
        params.append(avg_fill_price)
    if broker_order_no is not None:
        sets.append("broker_order_no = ?")
        params.append(broker_order_no)
    if raw_response is not None:
        sets.append("raw_response = ?")
        params.append(json.dumps(raw_response, ensure_ascii=False, default=str))

    params.append(int(order_id))
    with transaction(conn):
        conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id = ?", params)


def get_order(conn: sqlite3.Connection, order_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (int(order_id),)).fetchone()
    return dict(row) if row else None


def list_orders(conn: sqlite3.Connection, *, symbol: str | None = None,
                market: str | None = None, status: str | None = None,
                limit: int = 100) -> list[dict]:
    """주문 목록(최신순)."""
    where, params = [], []
    if symbol is not None:
        where.append("symbol = ?"); params.append(str(symbol))
    if market is not None:
        where.append("market = ?"); params.append(str(market))
    if status is not None:
        where.append("status = ?"); params.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(int(limit))
    rows = conn.execute(
        f"SELECT * FROM orders {clause} ORDER BY id DESC LIMIT ?", params
    ).fetchall()
    return [dict(r) for r in rows]


# ── 주문 상태 조회 (T04) ──────────────────────────────────

# "아직 살아 있는" 주문 상태. 이 상태의 주문이 있으면 같은 종목·방향으로 새 주문을 내지 않는다.
OPEN_ORDER_STATUSES = ("SUBMITTED", "PARTIAL", "UNKNOWN")


def list_open_orders(conn: sqlite3.Connection, *, symbol: str | None = None,
                     market: str | None = None, side: str | None = None) -> list[dict]:
    """
    아직 확정되지 않은 주문(`SUBMITTED`/`PARTIAL`/`UNKNOWN`) 목록 (오래된 순).

    중복 주문 차단과 미체결 취소 대상 선별에 쓴다.
    """
    placeholders = ", ".join("?" for _ in OPEN_ORDER_STATUSES)
    where = [f"status IN ({placeholders})"]
    params: list = list(OPEN_ORDER_STATUSES)
    if symbol is not None:
        where.append("symbol = ?"); params.append(str(symbol))
    if market is not None:
        where.append("market = ?"); params.append(str(market))
    if side is not None:
        where.append("side = ?"); params.append(str(side))
    rows = conn.execute(
        f"SELECT * FROM orders WHERE {' AND '.join(where)} ORDER BY id", params
    ).fetchall()
    return [dict(r) for r in rows]


def has_open_order(conn: sqlite3.Connection, *, symbol: str, market: str,
                   side: str) -> bool:
    """
    같은 종목·시장·방향의 미확정 주문이 있는지 (중복 주문 방지).

    있으면 새 주문을 내면 안 된다. 특히 `UNKNOWN` 은 주문이 나갔을 수도 있어
    체결 조회로 확정되기 전까지 재주문이 곧 중복 주문이다.
    """
    return bool(list_open_orders(conn, symbol=symbol, market=market, side=side))


def find_order_by_broker_no(conn: sqlite3.Connection, broker_order_no: str) -> dict | None:
    """브로커 주문번호로 주문 1건을 찾는다."""
    if not broker_order_no:
        return None
    row = conn.execute(
        "SELECT * FROM orders WHERE broker_order_no = ? ORDER BY id DESC LIMIT 1",
        (str(broker_order_no),),
    ).fetchone()
    return dict(row) if row else None


# ── daily_counters ────────────────────────────────────────

def get_daily_counter(conn: sqlite3.Connection, day: str, symbol: str) -> dict:
    """당일 카운터 조회. 행이 없으면 0회로 본다."""
    row = conn.execute(
        "SELECT * FROM daily_counters WHERE date = ? AND symbol = ?",
        (str(day), str(symbol)),
    ).fetchone()
    if row:
        return dict(row)
    return {"date": str(day), "symbol": str(symbol), "buy_count": 0,
            "last_buy_time": None}


def increment_buy_count(conn: sqlite3.Connection, day: str, symbol: str,
                        at: str | None = None) -> int:
    """당일 매수 횟수를 1 늘리고 누적값을 돌려준다."""
    ts = at or now_iso()
    with transaction(conn):
        conn.execute(
            "INSERT INTO daily_counters (date, symbol, buy_count, last_buy_time) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(date, symbol) DO UPDATE SET "
            "  buy_count = daily_counters.buy_count + 1, "
            "  last_buy_time = excluded.last_buy_time",
            (str(day), str(symbol), ts),
        )
    return get_daily_counter(conn, day, symbol)["buy_count"]


def set_buy_count(conn: sqlite3.Connection, day: str, symbol: str, count: int,
                  at: str | None = None) -> None:
    """
    당일 매수 횟수를 지정값으로 설정한다(보정용).

    `at` 을 주면 `last_buy_time` 도 함께 갱신한다. 주지 않으면 기존 시각을 유지한다
    (횟수만 0으로 되돌리는 `set_bought_today(False)` 가 시각을 건드리지 않게 하기 위함).
    """
    with transaction(conn):
        if at is None:
            conn.execute(
                "INSERT INTO daily_counters (date, symbol, buy_count, last_buy_time) "
                "VALUES (?, ?, ?, NULL) "
                "ON CONFLICT(date, symbol) DO UPDATE SET buy_count = excluded.buy_count",
                (str(day), str(symbol), int(count)),
            )
        else:
            conn.execute(
                "INSERT INTO daily_counters (date, symbol, buy_count, last_buy_time) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(date, symbol) DO UPDATE SET "
                "  buy_count = excluded.buy_count, "
                "  last_buy_time = excluded.last_buy_time",
                (str(day), str(symbol), int(count), at),
            )


def purge_daily_counters_before(conn: sqlite3.Connection, day: str) -> int:
    """`day` 이전의 당일 카운터를 삭제한다. 반환: 삭제 건수."""
    with transaction(conn):
        cur = conn.execute("DELETE FROM daily_counters WHERE date < ?", (str(day),))
        return int(cur.rowcount)


# ── equity_log ────────────────────────────────────────────

def log_equity(conn: sqlite3.Connection, total_equity: float, cash: float | None = None,
               mode: str | None = None, at: str | None = None) -> None:
    """자산 스냅샷을 기록한다."""
    with transaction(conn):
        conn.execute(
            "INSERT INTO equity_log (ts, total_equity, cash, mode) VALUES (?, ?, ?, ?)",
            (at or now_iso(), float(total_equity), cash, mode),
        )


def latest_equity(conn: sqlite3.Connection) -> dict | None:
    """가장 최근 자산 스냅샷."""
    row = conn.execute(
        "SELECT * FROM equity_log ORDER BY ts DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def max_equity(conn: sqlite3.Connection) -> float | None:
    """기록된 자산 최고값(고점 대비 낙폭 계산용)."""
    row = conn.execute("SELECT MAX(total_equity) AS m FROM equity_log").fetchone()
    return float(row["m"]) if row and row["m"] is not None else None
