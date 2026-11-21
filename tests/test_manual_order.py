"""모의 수동 주문 확인 도구 (T09c).

이 도구는 모의 서버로 1주짜리 시험 주문을 **사람이 직접** 내는 것이다. 여기서는 네트워크를 쓰지 않는다 —
시세·잔고·전송·체결 조회는 모두 대역이고, 대역을 안 붙인 시험이 실제 함수에 닿으면 예외가 난다(기본값이 예외를 던진다).
증명하는 것:
  - 순수 판정(거부 사유)과 주문 본문 검증
  - 기본은 미리보기이고 아무것도 보내지 않는다. 보내려면 터미널 + 확인 문장 + 시간 제한 + 전송 직전 재확인
  - 모의가 아니면 DB·API 에 닿기 전에 멈춘다
  - 전송은 `trader._submit_order` 한 곳뿐이고 그 가드 체인을 그대로 탄다(킬스위치가 막으면 orders 행이 없다)
  - 결과가 불확실하면 UNKNOWN 으로 남고 재시도하지 않으며 예외 문구가 출력에 새지 않는다
  - 체결 결과는 봇과 같은 `_settle_order` 로 DB 에 반영된다
"""
import ast
import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import db
import kis_domestic as kd
import manual_order
import market_calendar
import risk
import state
import trader

SYMBOL = "379800"
MARKET = trader.MARKET_DOMESTIC
FAKE_ACCOUNT = "88888888"
SECRET = "SECRET-TOKEN-123"
ROOT = Path(manual_order.__file__).resolve().parent


# ── 공통 대역 ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """대역을 붙이지 않은 시험이 실제 시세·잔고·전송·체결 조회에 닿으면 예외가 난다."""
    def boom(*args, **kwargs):
        raise AssertionError("실제 네트워크 함수에 닿으면 안 된다")

    for name in ("send_order", "get_domestic_price", "get_domestic_balance", "get_order_status"):
        monkeypatch.setattr(kd, name, boom)


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def bot(conn):
    st = state.reset_bot_state(conn)
    yield st
    state.reset_bot_state(None)


@pytest.fixture
def env(conn, bot, monkeypatch):
    monkeypatch.setattr(config, "IS_MOCK", True)
    monkeypatch.setattr(config, "DOMESTIC_TICKER", SYMBOL)
    monkeypatch.setattr(kd, "_account", lambda: (FAKE_ACCOUNT, "77"))
    sent = []
    prices = {"value": 23_220.0}
    holding = {"qty": 0.0}
    statuses = {"value": SimpleNamespace(status="SUBMITTED", filled_qty=0.0, avg_fill_price=None, raw={})}

    def fake_send(body, *, side, on_uncertain=None):
        sent.append((side, dict(body)))
        return {"rt_cd": "0", "msg1": "ok", "output": {"ODNO": "0001569139", "KRX_FWDG_ORD_ORGNO": "91252"}}

    monkeypatch.setattr(kd, "get_domestic_price", lambda ticker: prices["value"])
    monkeypatch.setattr(kd, "get_domestic_balance", lambda ticker=None: {
        "quantity": holding["qty"], "avg_price_krw": 0.0, "profit_rate": 0.0,
        "total_asset_krw": 10_000_000.0, "cash_krw": 10_000_000.0})
    monkeypatch.setattr(kd, "send_order", fake_send)
    monkeypatch.setattr(kd, "get_order_status", lambda order_no, *, ticker=None, org_no=None: statuses["value"])
    monkeypatch.setattr(trader, "_order_refs_from_response",
                        lambda result: {"order_no": "0001569139", "org_no": "91252"})
    monkeypatch.setattr(market_calendar, "is_order_window", lambda market, now: True)
    monkeypatch.setattr(manual_order, "_is_tty", lambda: True)
    return SimpleNamespace(conn=conn, sent=sent, prices=prices, holding=holding, statuses=statuses)


def _unpr(side, price=23_220.0):
    return kd.order_body(SYMBOL, 1, price, side=side, order_type="limit")["ORD_UNPR"]


def _phrase(side):
    return f"MOCK {side} {SYMBOL} 1 @ {_unpr(side)}"


def _type(monkeypatch, text):
    monkeypatch.setattr(manual_order, "_ask", lambda prompt="": text)


def _all_output(capsys):
    captured = capsys.readouterr()
    return captured.out + captured.err


def _open_orders(conn):
    return db.list_open_orders(conn, symbol=SYMBOL, market=MARKET)


def _filled(qty=1.0, price=23_200.0):
    return SimpleNamespace(status="FILLED", filled_qty=qty, avg_fill_price=price, raw={})


def _buy(**over):
    base = dict(side="BUY", qty=1, is_mock=True, db_mode_error=None, in_order_window=True,
                gate=risk.OPEN, gate_detail="", price=23_220.0, position=None, broker_qty=0.0,
                cash_krw=10_000_000.0, open_orders=[])
    base.update(over)
    return base


def _sell(**over):
    base = _buy(side="SELL", position={"status": "OPEN", "qty": 1.0, "avg_price": 23_000.0}, broker_qty=1.0)
    base.update(over)
    return base


def _joined(reasons):
    return " | ".join(reasons)


# ── 순수 판정: refusal_reasons ───────────────────────────

def test_buy_and_sell_pass_when_every_condition_holds():
    assert manual_order.refusal_reasons(**_buy()) == []
    assert manual_order.refusal_reasons(**_sell()) == []


@pytest.mark.parametrize("value", [False, None, "True", 1])
def test_refuses_when_not_mock(value):
    assert "모의투자 모드가 아닙니다" in _joined(manual_order.refusal_reasons(**_buy(is_mock=value)))


def test_refuses_when_the_db_mode_check_rejects():
    reasons = manual_order.refusal_reasons(**_buy(db_mode_error="LIVE 기록이 있는 DB"))
    assert "DB 모드 확인이 거부됐습니다" in _joined(reasons)


@pytest.mark.parametrize("value", [False, None, 0])
def test_refuses_outside_the_order_window(value):
    assert "주문 구간이 아닙니다" in _joined(manual_order.refusal_reasons(**_buy(in_order_window=value)))


def test_full_stop_refuses_both_sides():
    assert "전면 중단" in _joined(manual_order.refusal_reasons(**_buy(gate=risk.FULL_STOP, gate_detail="시험")))
    assert "전면 중단" in _joined(manual_order.refusal_reasons(**_sell(gate=risk.FULL_STOP, gate_detail="시험")))


def test_entries_blocked_refuses_buy_but_allows_sell():
    assert "신규 매수 중단" in _joined(manual_order.refusal_reasons(**_buy(gate=risk.ENTRIES_BLOCKED, gate_detail="시험")))
    assert manual_order.refusal_reasons(**_sell(gate=risk.ENTRIES_BLOCKED, gate_detail="시험")) == []


def test_unknown_gate_refuses():
    assert "알 수 없어" in _joined(manual_order.refusal_reasons(**_buy(gate="WEIRD")))
    assert "알 수 없어" in _joined(manual_order.refusal_reasons(**_sell(gate=None)))


@pytest.mark.parametrize("value", [0, 2, 3, -1, True, 1.0, "1", None])
def test_refuses_bad_quantity(value):
    assert "수량은 1 이상" in _joined(manual_order.refusal_reasons(**_buy(qty=value)))


@pytest.mark.parametrize("value", ["buy", "", None, "HOLD"])
def test_refuses_bad_side(value):
    assert "방향은" in _joined(manual_order.refusal_reasons(**_buy(side=value)))


@pytest.mark.parametrize("value", [None, 0, -5.0, float("nan"), float("inf"), True, "x"])
def test_refuses_bad_price(value):
    assert "현재가" in _joined(manual_order.refusal_reasons(**_buy(price=value)))


def test_refuses_when_unsettled_orders_exist():
    reasons = manual_order.refusal_reasons(**_buy(open_orders=[{"id": 1, "status": "SUBMITTED"}]))
    assert "미확정 주문" in _joined(reasons)


@pytest.mark.parametrize("value", [1.0, None, float("nan")])
def test_buy_refuses_when_the_account_already_holds(value):
    assert "계좌에 이미" in _joined(manual_order.refusal_reasons(**_buy(broker_qty=value)))


def test_buy_refuses_external_position():
    reasons = manual_order.refusal_reasons(**_buy(position={"status": "EXTERNAL", "qty": 1.0}))
    assert "외부 보유" in _joined(reasons)


def test_buy_refuses_when_the_bot_already_holds():
    reasons = manual_order.refusal_reasons(**_buy(position={"status": "OPEN", "qty": 1.0}))
    assert "이미 있습니다" in _joined(reasons)


@pytest.mark.parametrize("value", [None, 100.0, float("nan")])
def test_buy_refuses_without_enough_cash(value):
    assert "예수금" in _joined(manual_order.refusal_reasons(**_buy(cash_krw=value)))


@pytest.mark.parametrize("position", [None, {"status": "EXTERNAL", "qty": 1.0}, {"status": "CLOSED", "qty": 0.0}])
def test_sell_refuses_without_an_open_position(position):
    assert "없습니다" in _joined(manual_order.refusal_reasons(**_sell(position=position)))


def test_sell_refuses_when_the_db_quantity_is_short():
    reasons = manual_order.refusal_reasons(**_sell(position={"status": "OPEN", "qty": 0.0}))
    assert "DB 포지션" in _joined(reasons)


@pytest.mark.parametrize("value", [0.0, None])
def test_sell_refuses_when_the_broker_quantity_is_short(value):
    assert "계좌 보유" in _joined(manual_order.refusal_reasons(**_sell(broker_qty=value)))


def test_confirm_phrase_format():
    assert manual_order.confirm_phrase("BUY", "379800", 1, "23200") == "MOCK BUY 379800 1 @ 23200"


# ── 주문 본문 검증: body_error ───────────────────────────

def test_body_error_accepts_the_real_limit_body(monkeypatch):
    monkeypatch.setattr(kd, "_account", lambda: (FAKE_ACCOUNT, "77"))
    body = kd.order_body(SYMBOL, 1, 23_220.0, side="BUY", order_type="limit")
    assert manual_order.body_error(body, SYMBOL, 1) is None


def test_body_error_rejects_a_market_order(monkeypatch):
    monkeypatch.setattr(kd, "_account", lambda: (FAKE_ACCOUNT, "77"))
    body = kd.order_body(SYMBOL, 1, 23_220.0, side="BUY", order_type="market")
    assert "지정가" in manual_order.body_error(body, SYMBOL, 1)


def test_body_error_rejects_wrong_symbol_quantity_and_price(monkeypatch):
    monkeypatch.setattr(kd, "_account", lambda: (FAKE_ACCOUNT, "77"))
    body = kd.order_body(SYMBOL, 1, 23_220.0, side="BUY", order_type="limit")
    assert "종목" in manual_order.body_error(dict(body, PDNO="005930"), SYMBOL, 1)
    assert "수량" in manual_order.body_error(dict(body, ORD_QTY="2"), SYMBOL, 1)
    assert "단가" in manual_order.body_error(dict(body, ORD_UNPR="0"), SYMBOL, 1)
    assert "단가" in manual_order.body_error(dict(body, ORD_UNPR="abc"), SYMBOL, 1)
    assert manual_order.body_error("not a dict", SYMBOL, 1) is not None


# ── 흐름: 미리보기와 거부 ────────────────────────────────

def test_dry_run_buy_shows_the_order_and_sends_nothing(env, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("미리보기가 주문 경로를 불렀다")

    monkeypatch.setattr(trader, "_submit_order", boom)
    rc = manual_order.main(["buy"], conn=env.conn)
    out = _all_output(capsys)
    assert rc == 0
    assert env.sent == []
    assert _open_orders(env.conn) == []
    assert "미리보기" in out and "아무것도 보내지 않았습니다" in out
    assert "VTTC0012U" in out
    assert _unpr("BUY") in out.replace(",", "")
    assert FAKE_ACCOUNT not in out


def test_dry_run_sell_shows_the_sell_tr_id(env, capsys):
    db.upsert_position(env.conn, SYMBOL, MARKET, qty=1.0, avg_price=23_000.0, status="OPEN")
    env.holding["qty"] = 1.0
    rc = manual_order.main(["sell"], conn=env.conn)
    out = _all_output(capsys)
    assert rc == 0
    assert "VTTC0011U" in out
    assert env.sent == []


def test_not_mock_stops_before_touching_the_db_or_the_api(monkeypatch, capsys):
    monkeypatch.setattr(config, "IS_MOCK", False)

    def boom(*args, **kwargs):
        raise AssertionError("모의가 아닌데 닿으면 안 된다")

    for obj, name in ((db, "open_db"), (kd, "get_domestic_price"), (kd, "get_domestic_balance"),
                      (trader, "_submit_order")):
        monkeypatch.setattr(obj, name, boom)
    rc = manual_order.main(["buy", "--send"])
    assert rc == 1
    assert "모의투자" in _all_output(capsys)


def test_send_requires_a_terminal(env, monkeypatch, capsys):
    monkeypatch.setattr(manual_order, "_is_tty", lambda: False)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert "터미널" in _all_output(capsys)


def test_wrong_confirmation_sends_nothing(env, monkeypatch, capsys):
    _type(monkeypatch, "yes")
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert _open_orders(env.conn) == []
    assert "일치하지 않습니다" in _all_output(capsys)


def test_cancelling_the_prompt_sends_nothing(env, monkeypatch, capsys):
    def interrupted(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(manual_order, "_ask", interrupted)
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert "취소" in _all_output(capsys)


def test_confirming_too_late_sends_nothing(env, monkeypatch, capsys):
    ticks = itertools.chain([0.0, 1000.0], itertools.repeat(1000.0))
    monkeypatch.setattr(manual_order, "_clock", lambda: next(ticks))
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert "시간이 너무 지났습니다" in _all_output(capsys)


def test_gate_closing_after_the_prompt_stops_the_order(env, monkeypatch, capsys):
    calls = []

    def gate(conn):
        calls.append(1)
        return (risk.OPEN, "") if len(calls) == 1 else (risk.FULL_STOP, "시험 중단")

    monkeypatch.setattr(risk, "gate_status", gate)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert len(calls) == 2
    assert env.sent == []
    assert _open_orders(env.conn) == []
    assert "전면 중단" in _all_output(capsys)


def test_window_closing_after_the_prompt_stops_the_order(env, monkeypatch, capsys):
    calls = []

    def window(market, now):
        calls.append(1)
        return len(calls) == 1

    monkeypatch.setattr(market_calendar, "is_order_window", window)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert len(calls) == 2
    assert env.sent == []
    assert "주문 구간" in _all_output(capsys)


def test_buy_is_refused_while_an_unsettled_order_exists(env, monkeypatch, capsys):
    db.record_order(env.conn, symbol=SYMBOL, market=MARKET, side="BUY", qty=1, price=23_000.0,
                    order_type="LIMIT", status="SUBMITTED", client_ref="t-open", broker_order_no=None,
                    reason=None, raw_request={})
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert "미확정 주문" in _all_output(capsys)


def test_sell_is_refused_without_a_bot_position(env, monkeypatch, capsys):
    env.holding["qty"] = 1.0
    _type(monkeypatch, _phrase("SELL"))
    rc = manual_order.main(["sell", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert _open_orders(env.conn) == []


# ── 흐름: 전송 ───────────────────────────────────────────

def test_confirmed_buy_goes_through_submit_order_once(env, monkeypatch, capsys):
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    out = _all_output(capsys)
    assert rc == 0
    assert len(env.sent) == 1
    side, body = env.sent[0]
    assert side == "BUY"
    assert body["PDNO"] == SYMBOL and body["ORD_QTY"] == "1"
    assert body["ORD_DVSN"] == "00" and body["ORD_UNPR"] == _unpr("BUY")
    rows = _open_orders(env.conn)
    assert len(rows) == 1 and rows[0]["side"] == "BUY" and rows[0]["status"] == "SUBMITTED"
    assert FAKE_ACCOUNT not in out
    assert "0001569139" not in out and "9139" in out


def test_confirmed_sell_sends_one_limit_order(env, monkeypatch, capsys):
    db.upsert_position(env.conn, SYMBOL, MARKET, qty=1.0, avg_price=23_000.0, status="OPEN")
    env.holding["qty"] = 1.0
    _type(monkeypatch, _phrase("SELL"))
    rc = manual_order.main(["sell", "--send"], conn=env.conn)
    assert rc == 0
    assert len(env.sent) == 1
    side, body = env.sent[0]
    assert side == "SELL" and body["ORD_QTY"] == "1" and body["ORD_DVSN"] == "00"
    assert FAKE_ACCOUNT not in _all_output(capsys)


def test_global_market_order_setting_is_ignored(env, monkeypatch):
    monkeypatch.setattr(config, "ORDER_TYPE", "market")
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 0
    _, body = env.sent[0]
    assert body["ORD_DVSN"] == "00" and body["ORD_UNPR"] != "0"


def test_the_kill_switch_stops_the_order_before_any_record(env, monkeypatch, capsys):
    def halted(conn, side, reason=None):
        raise risk.TradingHalted("시험")

    monkeypatch.setattr(risk, "assert_order_permitted", halted)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert _open_orders(env.conn) == []
    assert "킬스위치" in _all_output(capsys)


def test_uncertain_result_is_recorded_unknown_without_retry(env, monkeypatch, capsys):
    attempts = []

    def uncertain(body, *, side, on_uncertain=None):
        attempts.append(side)
        on_uncertain(TimeoutError(SECRET))
        raise TimeoutError(SECRET)

    monkeypatch.setattr(kd, "send_order", uncertain)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    out = _all_output(capsys)
    assert rc == 2
    assert attempts == ["BUY"]
    assert [r["status"] for r in _open_orders(env.conn)] == ["UNKNOWN"]
    assert SECRET not in out
    assert "재전송" in out


def test_filled_buy_is_applied_to_the_position_right_after_sending(env, monkeypatch):
    env.statuses["value"] = _filled()
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    position = db.get_position(env.conn, SYMBOL, MARKET)
    assert rc == 0
    assert position is not None and position["status"] == "OPEN" and float(position["qty"]) == 1.0
    assert _open_orders(env.conn) == []


def test_unfilled_buy_stays_open_until_settle_applies_the_fill(env, monkeypatch, capsys):
    _type(monkeypatch, _phrase("BUY"))
    assert manual_order.main(["buy", "--send"], conn=env.conn) == 0
    assert db.get_position(env.conn, SYMBOL, MARKET) is None
    assert len(_open_orders(env.conn)) == 1
    env.statuses["value"] = _filled()
    capsys.readouterr()
    assert manual_order.main(["settle"], conn=env.conn) == 0
    position = db.get_position(env.conn, SYMBOL, MARKET)
    assert position is not None and position["status"] == "OPEN" and float(position["qty"]) == 1.0
    assert _open_orders(env.conn) == []


def test_filled_sell_closes_the_position(env, monkeypatch):
    db.upsert_position(env.conn, SYMBOL, MARKET, qty=1.0, avg_price=23_000.0, status="OPEN")
    env.holding["qty"] = 1.0
    env.statuses["value"] = _filled()
    _type(monkeypatch, _phrase("SELL"))
    assert manual_order.main(["sell", "--send"], conn=env.conn) == 0
    position = db.get_position(env.conn, SYMBOL, MARKET)
    assert position is None or float(position.get("qty") or 0.0) == 0.0


def test_a_failed_status_lookup_keeps_the_order_and_leaks_nothing(env, monkeypatch, capsys):
    def failing(order_no, *, ticker=None, org_no=None):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(kd, "get_order_status", failing)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 0
    assert len(env.sent) == 1
    assert [r["status"] for r in _open_orders(env.conn)] == ["SUBMITTED"]
    assert SECRET not in _all_output(capsys)


def test_settle_is_refused_when_not_mock(monkeypatch, capsys):
    monkeypatch.setattr(config, "IS_MOCK", False)
    assert manual_order.main(["settle"]) == 1


# ── 구조 ────────────────────────────────────────────────

def _tree():
    return ast.parse(Path(manual_order.__file__).read_text(encoding="utf-8"))


def test_the_only_way_to_send_is_submit_order():
    tree = _tree()
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_submit_order"]
    assert len(calls) == 1
    send_kw = [k for k in calls[0].keywords if k.arg == "send"]
    assert len(send_kw) == 1 and isinstance(send_kw[0].value, ast.Lambda)
    inside = {id(n) for n in ast.walk(send_kw[0].value)}
    senders = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "send_order"]
    assert len(senders) == 1 and id(senders[0]) in inside


def test_no_direct_http_or_legacy_order_functions():
    tree = _tree()
    modules = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    assert not modules & {"requests", "httpx", "urllib3", "aiohttp", "socket", "urllib.request"}
    assert not names & {"buy_domestic", "sell_domestic_all", "request_order"}


def test_no_other_module_imports_the_tool():
    offenders = [p.name for p in ROOT.glob("*.py")
                 if p.name != "manual_order.py" and "manual_order" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_source_never_names_the_raw_columns():
    source = Path(manual_order.__file__).read_text(encoding="utf-8")
    assert "raw_request" not in source and "raw_response" not in source


def test_the_quantity_cap_is_a_constant_not_an_environment_setting():
    assert config.MANUAL_ORDER_MAX_QTY == 1
    assert config.MANUAL_ORDER_CONFIRM_TIMEOUT_SEC > 0
    lines = (ROOT / "config.py").read_text(encoding="utf-8").splitlines()
    cap_lines = [l for l in lines if l.startswith("MANUAL_ORDER_MAX_QTY")]
    assert len(cap_lines) == 1 and "getenv" not in cap_lines[0] and "environ" not in cap_lines[0]


def test_a_bad_body_is_refused_before_any_send(env, monkeypatch, capsys):
    real = kd.order_body

    def market_body(*args, **kwargs):
        return dict(real(*args, **kwargs), ORD_DVSN="01")

    monkeypatch.setattr(kd, "order_body", market_body)
    _type(monkeypatch, _phrase("BUY"))
    rc = manual_order.main(["buy", "--send"], conn=env.conn)
    assert rc == 1
    assert env.sent == []
    assert _open_orders(env.conn) == []
    assert "본문 검증 실패" in _all_output(capsys)
