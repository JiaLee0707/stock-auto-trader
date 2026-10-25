"""
T08: `broker.Broker` 어댑터 테스트.

**실제 KIS API 를 호출하지 않는다.** 어댑터가 기존 `kis_*` 함수에 **무엇을 넘기는지**만
가짜 모듈로 확인한다. 어댑터의 목적이 "기존 함수를 호출만 하는 것"이므로, 여기서
검증할 것은 인자 전달과 **막아야 할 것을 막는지**(시장가 미구현, 모르는 시장)다.
"""
import pytest

import broker


class _FakeOverseas:
    """`kis_overseas` 대역. 호출 인자를 기록만 한다."""

    def __init__(self):
        self.calls = []

    def get_current_price(self, ticker):
        self.calls.append(("get_current_price", ticker))
        return 101.5

    def get_daily_closes(self, ticker, count=60):
        self.calls.append(("get_daily_closes", ticker))
        return [100.0, 101.5]

    def get_balance(self):
        self.calls.append(("get_balance",))
        return {"quantity": 1.0}

    def get_usd_krw_rate(self):
        self.calls.append(("get_usd_krw_rate",))
        return 1400.0

    def order_body(self, quantity, price_usd, *, order_type=None):
        self.calls.append(("order_body", quantity, price_usd, order_type))
        return {"PDNO": "QQQ", "ORD_QTY": str(int(quantity)), "ORD_DVSN": "00"}

    def cancel_order(self, order_no, *, quantity, symbol=None):
        self.calls.append(("cancel_order", order_no, quantity, symbol))
        return {"rt_cd": "0"}

    def get_order_status(self, order_no, *, symbol=None):
        self.calls.append(("get_order_status", order_no, symbol))

    def supports_order_inquiry(self):
        return False


class _FakeDomestic:
    """`kis_domestic` 대역."""

    def __init__(self):
        self.calls = []

    def get_domestic_price(self, ticker):
        self.calls.append(("get_domestic_price", ticker))
        return 23_220.0

    def get_daily_closes(self, ticker, count=60):
        self.calls.append(("get_daily_closes", ticker))
        return [23_000.0, 23_220.0]

    def get_domestic_balance(self, ticker=None):
        self.calls.append(("get_domestic_balance", ticker))
        return {"quantity": 2.0}

    def order_body(self, ticker, quantity, price, *, side, order_type=None):
        self.calls.append(("order_body", ticker, quantity, price, side, order_type))
        return {"PDNO": ticker, "ORD_DVSN": "00"}

    def cancel_order(self, order_no, *, quantity, ticker, org_no=None):
        self.calls.append(("cancel_order", order_no, quantity, ticker))
        return {"rt_cd": "0"}

    def get_order_status(self, order_no, *, ticker=None, org_no=None):
        self.calls.append(("get_order_status", order_no, ticker, org_no))

    def supports_order_inquiry(self):
        return True


# ── 시장 선택 ─────────────────────────────────────────────

def test_for_market_returns_matching_adapter():
    assert broker.for_market("overseas", "QQQ").market == "overseas"
    assert broker.for_market("domestic", "379800").market == "domestic"


@pytest.mark.parametrize("bad", [None, "", "krx", "nasdaq", "OVERSESA"])
def test_for_market_rejects_unknown_market(bad):
    """
    모르는 시장은 **국내로 기본 처리하지 않는다** — 잘못된 시장에 주문이 나갈 수 있다.
    """
    with pytest.raises(broker.BrokerError):
        broker.for_market(bad)


def test_broker_label():
    assert broker.broker_label("overseas") == "해외"
    assert broker.broker_label("domestic") == "국내"


def test_adapter_symbol_defaults_to_config():
    """종목을 주지 않으면 `config` 의 종목을 쓴다(기존 호출부와 동일)."""
    import config
    assert broker.KisOverseasBroker().symbol == config.TARGET_TICKER
    assert broker.KisDomesticBroker().symbol == config.DOMESTIC_TICKER


# ── 해외 어댑터 위임 ───────────────────────────────────────

def test_overseas_adapter_delegates_with_symbol():
    fake = _FakeOverseas()
    b = broker.KisOverseasBroker(module=fake, symbol="QQQ")

    assert b.get_quote() == 101.5
    assert b.get_daily_bars() == [100.0, 101.5]
    assert b.get_balance() == {"quantity": 1.0}
    assert b.get_usd_krw_rate() == 1400.0
    assert ("get_current_price", "QQQ") in fake.calls
    assert ("get_daily_closes", "QQQ") in fake.calls


def test_overseas_adapter_passes_symbol_override():
    fake = _FakeOverseas()
    b = broker.KisOverseasBroker(module=fake, symbol="QQQ")
    b.get_quote("SPY")
    assert ("get_current_price", "SPY") in fake.calls


def test_overseas_place_order_uses_limit():
    fake = _FakeOverseas()
    b = broker.KisOverseasBroker(module=fake, symbol="QQQ")
    body = b.place_order("BUY", 3, 101.5)
    assert body["ORD_QTY"] == "3"
    assert ("order_body", 3, 101.5, "limit") in fake.calls


@pytest.mark.parametrize("order_type", ["market", "MARKET", " best "])
def test_overseas_market_order_is_blocked(order_type):
    """
    해외 시장가는 **미구현**이다(ORD_DVSN 코드표 미확인, 절대 규칙 4).

    조용히 지정가로 바꾸지 않는다 — 호출부가 "시장가로 냈다"고 믿으면 갭 하락 시
    체결되지 않은 주문을 체결됐다고 착각한다.
    """
    b = broker.KisOverseasBroker(module=_FakeOverseas(), symbol="QQQ")
    with pytest.raises(NotImplementedError):
        b.place_order("SELL", 3, 100.0, order_type=order_type)


def test_domestic_market_order_is_supported():
    """국내는 `ORD_DVSN` 코드표가 확인돼 시장가를 낼 수 있다(대비 확인)."""
    fake = _FakeDomestic()
    b = broker.KisDomesticBroker(module=fake, symbol="379800")
    b.place_order("SELL", 3, 23_220.0, order_type="market")
    assert ("order_body", "379800", 3, 23_220.0, "SELL", "market") in fake.calls


# ── 국내 어댑터 위임 ───────────────────────────────────────

def test_domestic_adapter_delegates():
    fake = _FakeDomestic()
    b = broker.KisDomesticBroker(module=fake, symbol="379800")

    assert b.get_quote() == 23_220.0
    assert b.get_daily_bars() == [23_000.0, 23_220.0]
    assert b.get_balance() == {"quantity": 2.0}
    assert b.get_usd_krw_rate() == 1.0, "국내는 원화라 환산이 필요 없다"
    assert ("get_domestic_price", "379800") in fake.calls


def test_domestic_place_order_passes_ticker_and_int_qty():
    """국내 주문은 `ord_body(ticker, int(qty), ...)` 다 — 소수 수량을 넘기면 안 된다."""
    fake = _FakeDomestic()
    b = broker.KisDomesticBroker(module=fake, symbol="379800")
    b.place_order("BUY", 3.9, 23_220.0)
    assert ("order_body", "379800", 3, 23_220.0, "BUY", "limit") in fake.calls


def test_cancel_and_status_delegate_with_expected_kwargs():
    """취소·체결 조회는 시장마다 인자 이름이 다르다 — 어댑터가 맞춰 넘긴다."""
    fo, fd = _FakeOverseas(), _FakeDomestic()
    broker.KisOverseasBroker(module=fo, symbol="QQQ").cancel_order("O1", 3, "QQQ")
    broker.KisDomesticBroker(module=fd, symbol="379800").cancel_order("D1", 3, "379800")
    assert ("cancel_order", "O1", 3, "QQQ") in fo.calls
    assert ("cancel_order", "D1", 3, "379800") in fd.calls

    broker.KisDomesticBroker(module=fd, symbol="379800").get_order_status(
        "D1", symbol="379800", org_no="06010")
    assert ("get_order_status", "D1", "379800", "06010") in fd.calls


def test_supports_order_inquiry_is_forwarded():
    """
    체결 조회 지원 여부는 **어댑터가 판단하지 않고 위임**한다.

    `trader._require_settlement_support` 가 이 값을 보고 주문 여부를 정한다 —
    여기서 낙관적으로 True 를 돌려주면 체결 확인 없는 주문이 나간다.
    """
    assert broker.KisOverseasBroker(module=_FakeOverseas()).supports_order_inquiry() is False
    assert broker.KisDomesticBroker(module=_FakeDomestic()).supports_order_inquiry() is True


# ── Protocol 적합성 ────────────────────────────────────────

def test_adapters_have_all_protocol_methods():
    """
    어댑터가 Protocol 의 메서드를 모두 갖는지 **이름으로** 확인한다.

    `isinstance(obj, Broker)` 는 Protocol 이라 통과하지만, 그 검사는 메서드가
    *없어도* 통과할 수 있어 신뢰할 수 없다 — 그래서 직접 확인한다.
    """
    required = ("get_quote", "get_daily_bars", "get_balance", "place_order",
                "cancel_order", "get_order_status")
    for cls in (broker.KisOverseasBroker, broker.KisDomesticBroker):
        for name in required:
            assert callable(getattr(cls, name, None)), f"{cls.__name__} 에 {name} 가 없다"
