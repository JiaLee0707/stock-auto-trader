"""
T03: 브로커 ↔ DB 대조(reconcile.py) 테스트.

브로커 응답은 **mock(직접 만든 리스트)** 으로만 넣는다. 실제 KIS API 를 호출하지 않는다.
임시 DB 만 쓴다.
"""
import pytest

import config
import db
import reconcile


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


def _holding(symbol, qty, market="overseas", avg_price=100.0):
    return {"symbol": symbol, "market": market, "qty": qty, "avg_price": avg_price}


def _open_position(conn, symbol, qty, market="overseas", avg_price=100.0):
    db.upsert_position(conn, symbol, market, qty=qty, avg_price=avg_price,
                       entry_time=db.now_iso(), entry_signal={"rsi": 25.0})


# ── compare_positions: 순수 비교 ──────────────────────────

def test_compare_all_match(conn):
    _open_position(conn, "QQQ", 3)
    result = reconcile.compare_positions(db.list_positions(conn), [_holding("QQQ", 3)])
    assert result["is_consistent"] is True
    assert len(result["matched"]) == 1
    assert not (result["qty_mismatch"] or result["db_only"] or result["broker_only"])


def test_compare_both_empty_is_consistent():
    """양쪽 다 비어 있으면 일치다(아무것도 안 산 정상 상태)."""
    result = reconcile.compare_positions([], [])
    assert result["is_consistent"] is True


def test_compare_qty_mismatch():
    db_pos = [{"symbol": "QQQ", "market": "overseas", "qty": 3}]
    broker = [_holding("QQQ", 5)]
    result = reconcile.compare_positions(db_pos, broker)
    assert result["is_consistent"] is False
    assert len(result["qty_mismatch"]) == 1
    key, d, b = result["qty_mismatch"][0]
    assert key == ("QQQ", "overseas")
    assert d["qty"] == 3 and b["qty"] == 5


def test_compare_db_only_position():
    """DB 에는 있는데 브로커에 없다 — 체결 누락 또는 매도 미반영."""
    db_pos = [{"symbol": "QQQ", "market": "overseas", "qty": 3}]
    result = reconcile.compare_positions(db_pos, [])
    assert result["is_consistent"] is False
    assert len(result["db_only"]) == 1
    assert result["db_only"][0][0] == ("QQQ", "overseas")


def test_compare_broker_only_is_external_holding():
    """브로커에만 있으면 외부 보유 — 봇이 만든 포지션이 아니다."""
    result = reconcile.compare_positions([], [_holding("SPY", 10)])
    assert result["is_consistent"] is False
    assert len(result["broker_only"]) == 1
    assert result["broker_only"][0][0] == ("SPY", "overseas")


def test_compare_market_is_part_of_identity():
    """같은 코드라도 시장이 다르면 다른 포지션이다(섞이면 안 됨)."""
    db_pos = [{"symbol": "X", "market": "overseas", "qty": 3}]
    broker = [_holding("X", 3, market="domestic")]
    result = reconcile.compare_positions(db_pos, broker)
    assert result["is_consistent"] is False
    assert len(result["db_only"]) == 1 and len(result["broker_only"]) == 1


def test_compare_handles_multiple_markets():
    db_pos = [
        {"symbol": "QQQ", "market": "overseas", "qty": 3},
        {"symbol": "379800", "market": "domestic", "qty": 7},
    ]
    broker = [_holding("QQQ", 3), _holding("379800", 7, market="domestic")]
    assert reconcile.compare_positions(db_pos, broker)["is_consistent"] is True


def test_compare_ignores_entry_without_symbol():
    result = reconcile.compare_positions([{"symbol": None, "market": "overseas",
                                           "qty": 1}], [])
    assert result["is_consistent"] is True, "symbol 없는 행은 비교 대상이 아니다"


def test_compare_qty_float_noise_is_tolerated():
    """부동소수 변환 오차는 불일치로 보지 않는다."""
    db_pos = [{"symbol": "QQQ", "market": "overseas", "qty": 3.0000000001}]
    result = reconcile.compare_positions(db_pos, [_holding("QQQ", 3)])
    assert result["is_consistent"] is True


# ── reconcile: 일치 ───────────────────────────────────────

def test_reconcile_consistent_does_not_halt(conn):
    _open_position(conn, "QQQ", 3)
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 3)], autofix=False)
    assert result["is_consistent"] is True
    assert result["halted"] is False
    assert reconcile.is_trading_halted(conn) is False


# ── reconcile: 불일치 → 중단 플래그 ───────────────────────

def test_reconcile_qty_mismatch_halts_without_autofix(conn):
    _open_position(conn, "QQQ", 3)
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 5)], autofix=False)
    assert result["halted"] is True
    assert result["autofixed"] is False
    assert reconcile.is_trading_halted(conn) is True
    assert "불일치" in reconcile.halt_reason(conn)
    # 자동 보정을 안 했으므로 DB 는 그대로
    assert db.get_position(conn, "QQQ", "overseas")["qty"] == 3.0


def test_reconcile_db_only_halts(conn):
    _open_position(conn, "QQQ", 3)
    result = reconcile.reconcile(conn, holdings=[], autofix=False)
    assert result["halted"] is True
    assert reconcile.is_trading_halted(conn) is True


def test_reconcile_broker_only_halts_without_autofix(conn):
    """외부 보유도 기본 설정에서는 사람이 확인해야 할 불일치다."""
    result = reconcile.reconcile(conn, holdings=[_holding("SPY", 10)], autofix=False)
    assert result["halted"] is True
    assert reconcile.is_trading_halted(conn) is True
    # 봇이 만들지 않은 보유는 DB 에 편입되지 않는다(자동 보정 꺼짐)
    assert db.list_positions(conn, status="EXTERNAL") == []


# ── reconcile: 자동 보정 ──────────────────────────────────

def test_reconcile_autofix_qty_overwrites_with_broker_value(conn):
    _open_position(conn, "QQQ", 3)
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 5, avg_price=101.0)],
                                 autofix=True)
    assert result["autofixed"] is True
    pos = db.get_position(conn, "QQQ", "overseas")
    assert pos["qty"] == 5.0
    assert pos["avg_price"] == 101.0


def test_reconcile_autofix_preserves_entry_signal(conn):
    """보정은 수량만 브로커 값을 따르고, 진입 근거는 봇의 기록을 유지한다."""
    _open_position(conn, "QQQ", 3)
    reconcile.reconcile(conn, holdings=[_holding("QQQ", 5)], autofix=True)
    assert db.get_position(conn, "QQQ", "overseas")["entry_signal"] == {"rsi": 25.0}


def test_reconcile_autofix_closes_db_only_position(conn):
    _open_position(conn, "QQQ", 3)
    reconcile.reconcile(conn, holdings=[], autofix=True)
    assert db.list_positions(conn, status="OPEN") == []
    assert len(db.list_positions(conn, status="CLOSED")) == 1


def test_reconcile_autofix_marks_external_holding(conn):
    """외부 보유는 EXTERNAL 로 기록해 봇이 매도 대상으로 잡지 않게 한다."""
    reconcile.reconcile(conn, holdings=[_holding("SPY", 10)], autofix=True)
    external = db.list_positions(conn, status="EXTERNAL")
    assert len(external) == 1
    assert external[0]["symbol"] == "SPY"
    assert external[0]["qty"] == 10.0


def test_reconcile_autofix_still_halts(conn):
    """
    보정했더라도 중단 플래그는 세운다. 보정 자체가 틀렸을 때
    조용히 계속하면 알아챌 기회가 없다.
    """
    _open_position(conn, "QQQ", 3)
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 5)], autofix=True)
    assert result["autofixed"] is True
    assert result["halted"] is True
    assert reconcile.is_trading_halted(conn) is True


def test_external_position_is_not_reported_again_next_cycle(conn):
    """
    자동 보정으로 EXTERNAL 로 기록된 보유는 다음 대조에서 불일치로 잡히지 않는다
    (매 사이클마다 중단 플래그를 다시 세우면 영구 정지가 된다).
    """
    reconcile.reconcile(conn, holdings=[_holding("SPY", 10)], autofix=True)
    result = reconcile.reconcile(conn, holdings=[_holding("SPY", 10)], autofix=True)
    assert result["is_consistent"] is True
    assert result["halted"] is False


# ── 중단 플래그 동작 ──────────────────────────────────────

def test_halt_and_resume(conn):
    assert reconcile.is_trading_halted(conn) is False
    reconcile.halt_trading(conn, "테스트 사유")
    assert reconcile.is_trading_halted(conn) is True
    assert reconcile.halt_reason(conn) == "테스트 사유"

    reconcile.resume_trading(conn, "사람이 확인함")
    assert reconcile.is_trading_halted(conn) is False
    assert reconcile.halt_reason(conn) is None


def test_halt_survives_restart(tmp_path):
    """킬스위치는 재시작해도 유지돼야 한다(메모리였다면 사라진다)."""
    p = tmp_path / "bot.db"
    c1 = db.open_db(p)
    reconcile.halt_trading(c1, "불일치")
    c1.close()

    c2 = db.open_db(p)
    assert reconcile.is_trading_halted(c2) is True
    assert reconcile.halt_reason(c2) == "불일치"
    c2.close()


def test_consistent_does_not_auto_resume_previous_halt(conn):
    """
    불일치가 해소돼도 사람이 확인하기 전에는 중단을 풀지 않는다.
    (자동 해제하면 일시적 오류로 잘못 풀릴 수 있다.)
    """
    reconcile.halt_trading(conn, "이전 불일치")
    result = reconcile.reconcile(conn, holdings=[], autofix=False)
    assert result["is_consistent"] is True
    assert reconcile.is_trading_halted(conn) is True, "수동 해제 전에는 중단 유지"


# ── RECONCILE_AUTOFIX 기본값 ──────────────────────────────

def test_autofix_defaults_to_false():
    """기본은 자동 보정 안 함이어야 한다 — 소액 실전에서는 사람이 확인하는 편이 안전."""
    assert config.RECONCILE_AUTOFIX is False


def test_reconcile_uses_config_default_when_autofix_not_given(conn, monkeypatch):
    _open_position(conn, "QQQ", 3)
    monkeypatch.setattr(config, "RECONCILE_AUTOFIX", False)
    result = reconcile.reconcile(conn, holdings=[_holding("QQQ", 5)])
    assert result["autofixed"] is False
    assert db.get_position(conn, "QQQ", "overseas")["qty"] == 3.0


# ── 브로커 조회 실패는 삼키지 않는다 ──────────────────────

def test_fetch_failure_propagates(conn, monkeypatch):
    """
    잔고 조회가 실패하면 조용히 넘어가지 않고 예외를 올린다.
    대조를 못 했는데 '이상 없음'으로 처리하면 상태가 어긋난 채 매매가 계속된다.
    """
    import kis_overseas
    monkeypatch.setattr(config, "IS_OVERSEAS_ENABLED", True)
    monkeypatch.setattr(config, "IS_DOMESTIC_ENABLED", False)

    def boom():
        raise ConnectionError("네트워크 끊김")

    monkeypatch.setattr(kis_overseas, "get_all_holdings", boom)
    with pytest.raises(ConnectionError):
        reconcile.reconcile(conn, holdings=None)


def test_fetch_skips_disabled_market(conn, monkeypatch):
    """운용이 꺼진 시장은 조회하지 않는다."""
    import kis_domestic
    import kis_overseas
    monkeypatch.setattr(config, "IS_OVERSEAS_ENABLED", False)
    monkeypatch.setattr(config, "IS_DOMESTIC_ENABLED", True)

    called = {"overseas": False}
    monkeypatch.setattr(kis_overseas, "get_all_holdings",
                        lambda: called.__setitem__("overseas", True) or [])
    monkeypatch.setattr(kis_domestic, "get_all_holdings", lambda: [])
    reconcile.fetch_broker_holdings()
    assert called["overseas"] is False, "해외 비활성인데 조회했다"
