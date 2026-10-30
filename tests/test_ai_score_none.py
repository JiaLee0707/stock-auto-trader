"""
T09b: AI 점수를 모르는 상태(None)에서의 매매 — 신규 매수만 보류하고 청산은 그대로 나간다.

main.job() 은 AI 분석 단계가 예외를 내면 점수를 None 으로 바꿔 매매 사이클에 넘긴다.
그 값으로 _run_cycle 이 실제로 어떻게 동작하는지 증명한다. 판단 로직은 패치하지 않는다.
"""
import pytest

import config
import db
import state
import trader

SYMBOL, MARKET = "379800", "domestic"
# 매수 신호가 나오는 확정 종가 시계열 (RSI ≤ 30 AND 종가 ≤ BB 하단) — test_exposure_failure.py 와 같다.
_BUY_CLOSES = [100 + i * 0.5 for i in range(29)] + [95.0]


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


def _balance(qty=0.0, avg=0.0, profit=0.0, total=1_000_000.0, cash=1_000_000.0):
    return {"quantity": qty, "avg_price": avg, "profit_rate": profit,
            "total_asset_krw": total, "cash_krw": cash}


def _run(conn, monkeypatch, *, balance, ai_score):
    """국내 1사이클(주문은 실제로 나가지 않는다). 나간 주문 [(방향, 수량), ...] 을 돌려준다."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    submitted = []
    trader._run_cycle(
        "국내", SYMBOL, balance, 23_220.0, ai_score, lambda p: f"{p:,.0f}원",
        sell_fn=None, buy_fn=None, market=MARKET,
        submit_buy=lambda qty, p: submitted.append(("BUY", qty)),
        submit_sell=lambda reason, qty: submitted.append(("SELL", qty)),
        status_fn=None, cancel_fn=None,
        params={"capital_cap_krw": 0},
    )
    return submitted


def test_stop_loss_still_sells_when_ai_score_is_unknown(conn, bot, monkeypatch):
    db.upsert_position(conn, SYMBOL, MARKET, qty=3.0, avg_price=100.0, status="OPEN")
    submitted = _run(conn, monkeypatch, balance=_balance(qty=3.0, avg=100.0, profit=-10.0), ai_score=None)
    assert ("SELL", 3.0) in submitted, "AI 점수를 모른다는 이유로 손절이 막혔다"


def test_entry_is_held_when_ai_score_is_unknown(conn, bot, monkeypatch, confirmed_bars):
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    monkeypatch.setattr(config, "AI_FILTER_ENABLED", True)
    confirmed_bars(SYMBOL, _BUY_CLOSES)

    assert _run(conn, monkeypatch, balance=_balance(), ai_score=None) == [], "점수를 모르는데 매수했다"

    # 대조군: 점수가 있으면(0점, 중립) 같은 입력에서 매수한다 — 보류의 원인이 None 임을 증명한다.
    submitted = _run(conn, monkeypatch, balance=_balance(), ai_score=0)
    assert [s[0] for s in submitted] == ["BUY"], "점수 때문이 아니라 다른 이유로 막혔다"
