"""
브로커 인터페이스 (T08).

**목적은 "국내/해외를 같은 코드로 다루는 것"이 아니라, 판단·실행이 의존하는
브로커 기능의 목록을 한 곳에 못 박는 것이다.** `trader._run_cycle` 이 지금
`kis_overseas`·`kis_domestic` 을 섞어 쓰면서 "무엇을 브로커에 요구하는가"가
코드에 드러나지 않았다. 그 목록을 `Broker` Protocol 로 명시한다.

## 이 모듈이 하지 않는 것 (의도)

  - **`kis_*.py` 의 함수를 대체하지 않는다.** 어댑터는 기존 함수를 호출만 한다.
    여기서 HTTP 를 다시 짜면 엔드포인트·TR_ID 가 두 곳으로 갈라진다(절대 규칙 4).
  - **주문을 내지 않는다.** `place_order`/`cancel_order` 는 기존
    `kis_*.send_order`/`order_body` 를 그대로 쓴다 — `config.assert_order_allowed()`
    가드는 그 함수들 안에 있고, 그 방어선은 움직이지 않는다(절대 규칙 1).
  - **자동 재시도를 만들지 않는다.** 조회도 기존 `_request` 를 그대로 쓴다.

## 주문 함수에서 서명이 다른 부분 (의도적)

  `kis_overseas.send_order` 는 시장가가 미구현이라 `order_type` 을 받지 않고,
  `kis_domestic.send_order` 는 `ORD_DVSN`(지정가/시장가)을 받는다. Protocol 의
  `place_order` 서명에 `order_type` 을 두되 **해외 어댑터는 무시**한다 —
  해외에 시장가를 넘겨 구현된 것처럼 보이게 하면 갭 하락 시 체결되지 않는 주문이
  나갈 수 있다(T06 보고 참고). 해외 동작은 T07 까지와 같다(지정가 고정).
"""
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger("bot")


class BrokerError(RuntimeError):
    """브로커 어댑터가 요구한 기능을 제공할 수 없을 때."""


@runtime_checkable
class Broker(Protocol):
    """
    판단·실행이 요구하는 브로커 기능 (T08 요구사항 5).

    **`runtime_checkable` 이지만 `isinstance` 로 구현 여부를 확인하지 않는다.**
    Protocol 의 isinstance 는 메서드 *이름*만 보고 시그니처는 보지 않으므로,
    "구현됐다"는 잘못된 확신을 준다. 구현 여부는 `data.broker_label()` 과
    `_require_settlement_support`(체결 조회) 처럼 **명시적 플래그**로 판단한다.
    """

    market: str          # "overseas" / "domestic" (DB 의 market 값과 같다)
    symbol: str          # 이 어댑터가 다루는 종목. 한 사이클 = 한 종목이다.

    def get_quote(self, symbol: str | None = None) -> float:
        """현재가. 통화는 시장에 따른다(해외=달러, 국내=원). None 이면 자기 종목."""

    def get_daily_bars(self, symbol: str):
        """일봉. **확정 봉 판정은 호출부가 한다**(`history.get_confirmed_bars`)."""

    def get_balance(self, symbol: str | None = None) -> dict:
        """잔고·예수금. 정규화된 키(`quantity`/`avg_price`/`total_asset_krw`/`cash_krw`)."""

    def place_order(self, side: str, qty: float, price: float,
                    order_type: str = "limit") -> dict:
        """주문 본문을 만든다. **전송은 `send_order` 가 한다**(write-ahead 기록 때문)."""

    def cancel_order(self, order_no: str, qty: float, symbol: str | None = None) -> dict:
        """미체결 취소. `NotImplementedError` 면 호출부가 그 종목 신규 매수를 막는다."""

    def get_order_status(self, order_no: str, symbol: str | None = None) -> "object":
        """체결 조회. 미구현이면 `NotImplementedError`."""


class KisOverseasBroker:
    """
    `kis_overseas` 어댑터. **기존 함수를 호출만 한다** — 새 로직을 넣지 않는다.

    해외 어댑터는 `order_type` 을 받지 않는다(위 모듈 docstring 참고). 지정가만 낸다.
    """

    market = "overseas"

    def __init__(self, module=None, symbol: str | None = None):
        if module is None:
            import kis_overseas as module
        self._m = module
        if symbol is None:
            import config
            symbol = config.TARGET_TICKER
        self.symbol = symbol

    def get_quote(self, symbol: str | None = None) -> float:
        return float(self._m.get_current_price(symbol or self.symbol))

    def get_daily_bars(self, symbol: str | None = None):
        return self._m.get_daily_closes(symbol or self.symbol)

    def get_balance(self, symbol: str | None = None) -> dict:
        # 해외 잔고 API 는 종목 인자를 받지 않는다(계좌 전체를 돌려준다).
        return self._m.get_balance()

    def get_usd_krw_rate(self) -> float:
        """해외 전용. 사이징이 원화로 계산하려면 필요하다(T07)."""
        return float(self._m.get_usd_krw_rate())

    def place_order(self, side: str, qty: float, price: float,
                    order_type: str = "limit") -> dict:
        # 시장가는 `kis_overseas.order_body` 가 NotImplementedError 로 막는다
        # (절대 규칙 4 — ORD_DVSN 코드표 미확인). 여기서 조용히 지정가로 바꾸지
        # 않는다: 호출부가 "시장가로 냈다"고 믿으면 갭 하락 시 체결되지 않은 주문을
        # 체결됐다고 착각한다.
        if str(order_type).strip().lower() != "limit":
            raise NotImplementedError(
                f"[해외] 시장가 주문은 미구현입니다(side={side}, order_type={order_type!r}). "
                "docs/kis_api_fields.md 에 ORD_DVSN 코드표가 없어 추측하지 않습니다(절대 규칙 4)."
            )
        return self._m.order_body(qty, price, order_type="limit")

    def send_order(self, body: dict, *, side: str, on_uncertain=None) -> dict:
        return self._m.send_order(body, side=side, on_uncertain=on_uncertain)

    def cancel_order(self, order_no: str, qty: float, symbol: str | None = None) -> dict:
        return self._m.cancel_order(order_no, quantity=qty, symbol=symbol)

    def get_order_status(self, order_no: str, symbol: str | None = None):
        return self._m.get_order_status(order_no, symbol=symbol)

    def supports_order_inquiry(self) -> bool:
        return bool(self._m.supports_order_inquiry())


class KisDomesticBroker:
    """`kis_domestic` 어댑터. 시장가/지정가를 모두 지원한다(`ORD_DVSN`)."""

    market = "domestic"

    def __init__(self, module=None, symbol: str | None = None):
        if module is None:
            import kis_domestic as module
        self._m = module
        if symbol is None:
            import config
            symbol = config.DOMESTIC_TICKER
        self.symbol = symbol

    def get_quote(self, symbol: str | None = None) -> float:
        return float(self._m.get_domestic_price(symbol or self.symbol))

    def get_daily_bars(self, symbol: str | None = None):
        return self._m.get_daily_closes(symbol or self.symbol)

    def get_balance(self, symbol: str | None = None) -> dict:
        return self._m.get_domestic_balance(symbol or self.symbol)

    def get_usd_krw_rate(self) -> float:
        """국내는 이미 원화다. 환산이 필요 없다."""
        return 1.0

    def place_order(self, side: str, qty: float, price: float,
                    order_type: str = "limit") -> dict:
        return self._m.order_body(self.symbol, int(qty), price, side=side,
                                  order_type=order_type)

    def send_order(self, body: dict, *, side: str, on_uncertain=None) -> dict:
        return self._m.send_order(body, side=side, on_uncertain=on_uncertain)

    def cancel_order(self, order_no: str, qty: float, symbol: str | None = None) -> dict:
        return self._m.cancel_order(order_no, quantity=int(qty), ticker=symbol)

    def get_order_status(self, order_no: str, symbol: str | None = None, org_no=None):
        return self._m.get_order_status(order_no, ticker=symbol, org_no=org_no)

    def supports_order_inquiry(self) -> bool:
        return bool(self._m.supports_order_inquiry())


# 시장 → 어댑터 클래스. `trader` 가 이 표를 써서 어댑터를 고른다.
_ADAPTERS = {
    "overseas": KisOverseasBroker,
    "domestic": KisDomesticBroker,
}


def for_market(market: str, symbol: str | None = None) -> Broker:
    """
    시장 이름으로 어댑터를 만든다. 모르는 시장은 **추측하지 않고 예외**로 막는다.

    기본값으로 국내를 돌려주는 식의 처리는 위험하다 — 잘못된 시장에 주문이 나갈 수 있다.
    """
    key = str(market or "").strip().lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        raise BrokerError(f"모르는 시장입니다: {market!r} (사용 가능: {sorted(_ADAPTERS)})")
    return cls(symbol=symbol)


def broker_label(market: str) -> str:
    """로그용 이름. 어댑터를 만들지 않고 표시만 한다."""
    return {"overseas": "해외", "domestic": "국내"}.get(str(market or "").lower(), str(market))
