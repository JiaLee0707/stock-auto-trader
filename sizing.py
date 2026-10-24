"""
포지션 사이징 (T07). **순수 함수만 둔다 — I/O 를 하지 않는다.**

이전 "총자산 × 비중"(해외 15% / 국내 10%) 방식은 **1주 가격과 무관하게** 예산이
정해졌다. 자본 20만원 × 10% = 2만원 < 1주 23,220원 → **0주**. 매수 신호가 맞아도
주문이 나가지 않는다(CLAUDE.md "현재 알려진 문제"). 게다가 그 방식은 손절폭을
보지 않으므로, 손절 -3%든 -30%든 같은 수량이 나왔다 — 자금 배분이지 위험 관리가 아니다.

이제 **위험 한도와 현금 한도 중 작은 쪽**으로 정수 주 수를 계산한다.

    risk_budget   = equity × RISK_PER_TRADE_PCT          # 손절 시 감수할 최대 손실
    stop_distance = entry_price × |effective_stop_pct|   # 1주당 손절 시 손실
    qty_by_risk   = floor(risk_budget / stop_distance)
    qty_by_cash   = floor(min(cash, equity × MAX_POSITION_PCT) / entry_price)
    qty           = min(qty_by_risk, qty_by_cash)

**1주를 강제로 사지 않는다.** 위험 기준이 0주를 내면 위험 한도(기본 자산의 1%)를
넘는 주문이므로 사지 않고 `INSUFFICIENT_CAPITAL` 과 필요 자본을 남긴다. 규칙을
조용히 완화하지 않고 사용자가 "자본을 늘릴지, 위험 %를 올릴지" 판단하게 한다.

설계 원칙
  - **I/O 없음.** DB·API·설정을 읽지 않는다. 값은 호출부가 인자로 준다.
    그래야 경계값을 테스트로 고정할 수 있다.
  - **손절폭은 T06 의 손절선을 그대로 쓴다**(`exit_rules.effective_stop_loss_pct`).
    여기서 다시 계산하면 청산 규칙과 사이징이 서로 다른 손절선을 보게 된다.
  - 모든 수치는 `config` 에서 온다. 여기에 기본값을 하드코딩하지 않는다(절대 규칙 5).
  - **모두 검증 전 가설이다**(T11 백테스트로 확정). 성능을 위해 임계값을 조정하지 말 것.
"""
import math
from dataclasses import dataclass, field

import config
import exit_rules

# qty 가 0 인 이유. 로그에 그대로 남는다.
INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"   # 위험 기준·현금 기준 중 하나가 0주
INVALID_INPUT = "INVALID_INPUT"                 # 자본·가격·손절선이 계산 불가
INVALID_STOP = "INVALID_STOP"                   # 손절선이 없음(0 이나 양수)
MAX_POSITIONS_REACHED = "MAX_POSITIONS_REACHED" # 동시 보유 종목 상한
EXPOSURE_LIMIT = "EXPOSURE_LIMIT"               # 총 노출 상한 도달
LEGACY_OK = "OK"

_MESSAGES = {
    INSUFFICIENT_CAPITAL: "자본 부족 — 1주를 사려면 자본이 더 필요합니다",
    INVALID_INPUT: "입력값이 계산 불가(자본·가격 확인 필요)",
    INVALID_STOP: "손절선이 비활성(0 또는 양수)이라 위험을 계산할 수 없음",
    MAX_POSITIONS_REACHED: "동시 보유 종목 상한 도달",
    EXPOSURE_LIMIT: "총 노출 상한 도달 — 추가 매수 여력 없음",
    LEGACY_OK: "계산됨",
}


@dataclass
class SizingResult:
    """
    수량 계산 결과. 호출부(trader)는 `qty` 를 그대로 주문에 쓰고, `qty == 0` 이면
    **주문하지 않는다**(최소 1주 강제 매수 금지).

    계산 근거를 함께 담는다 — "왜 이 수량인가"를 로그·보고서에서 확인할 수 있어야
    사용자가 위험 %·현금 중 무엇이 병목인지 판단할 수 있다.
    """

    qty: int
    reason: str
    required_capital: float          # 최종 수량을 사는 데 필요한 금액. 0주면 1주 가격.
    qty_by_risk: int = 0
    qty_by_cash: int = 0
    risk_budget_krw: float = 0.0     # equity × RISK_PER_TRADE_PCT
    stop_distance_krw: float = 0.0   # 1주당 손절 시 손실
    stop_pct: float = 0.0            # 실제 적용한 손절선(%)
    detail: str = ""                 # 사람이 읽을 계산 과정

    @property
    def ok(self) -> bool:
        return self.qty > 0

    @property
    def message(self) -> str:
        return _MESSAGES.get(self.reason, self.reason)

    def __str__(self):
        if self.qty > 0:
            return (f"{self.qty}주 ({self.reason}: {self.detail}) "
                    f"투입 {self.required_capital:,.0f}원")
        return (f"0주 ({self.message}) — {self.detail} / "
                f"1주 필요 자본 {self.required_capital:,.0f}원")


@dataclass
class EntryPlan:
    """
    한 종목의 진입 가능 여부와 필요 자본 (요구사항 4의 시작 로그용).

    유니버스 전체를 이 형태로 만들어 "살 수 있는 종목 / 없는 종목과 필요 자본"을
    한 번에 보여 준다. **규칙을 완화하지 않는다** — 사용자가 판단할 재료만 준다.
    """

    ticker: str
    name: str = ""
    price: float = 0.0
    result: SizingResult = field(default_factory=lambda: SizingResult(0, INVALID_INPUT, 0.0))

    @property
    def ok(self) -> bool:
        return self.result.ok

    def __str__(self):
        label = f"{self.ticker}" + (f"({self.name})" if self.name else "")
        if self.ok:
            return f"{label} {self.price:,.0f}원 → {self.result.qty}주"
        return (f"{label} {self.price:,.0f}원 → 0주({self.result.message}) "
                f"필요 {self.result.required_capital:,.0f}원")


def apply_capital_cap(equity_krw: float, cash_krw: float,
                      cap_krw: float | None = None) -> tuple[float, float]:
    """
    사용 자본 상한을 적용한다 (요구사항: 모의투자 규모 맞추기).

    모의투자 계좌는 가상 1,000만원이라 실전 20만원과 규모가 다르다. 상한을 두면
    모의투자에서도 실전과 같은 수량이 나와 사이징·손절을 실전 규모로 검증할 수 있다.

    **equity 와 cash 를 둘 다 제한한다.** equity 만 제한하면 현금은 1,000만원인 채로
    남아, 위험 한도는 20만원 기준인데 현금 한도는 1,000만원 기준이 되어 상한을
    훨씬 넘겨 살 수 있다. `cap <= 0` 은 "상한 없음"이며 실제 자본을 그대로 쓴다.

    반환: (제한된 equity, 제한된 cash)
    """
    cap = config.CAPITAL_CAP_KRW if cap_krw is None else float(cap_krw)
    eq = float(equity_krw or 0.0)
    cash = float(cash_krw or 0.0)
    if cap <= 0:
        return eq, cash
    # 두 값이 서로 모순되지 않도록 **같은 규칙**을 적용한다:
    # 상한을 넘는 만큼을 빼고, 상한 안이면 그대로 둔다.
    return min(eq, cap), min(cash, cap)


def effective_stop_pct(entry_price: float,
                       atr_value: float | None = None) -> float:
    """
    사이징에 쓸 손절선(%, 음수). T06 의 손절선을 **그대로** 가져온다.

    `STOP_LOSS_ATR_MULT` 가 0(기본)이면 고정 `STOP_LOSS_PCT` 다. 켜져 있으면
    고정 %와 ATR 기반 중 **더 넓은 쪽**을 쓴다 — 청산(`exit_rules`)과 같은 규칙이다.
    여기서 다시 구현하지 않고 `exit_rules.effective_stop_loss_pct` 를 부르는 이유는,
    두 곳이 다른 손절선을 보면 "위험 1%로 계산했는데 손절은 그보다 크게 나는"
    상태가 되기 때문이다.
    """
    return float(exit_rules.effective_stop_loss_pct(
        entry_price, atr_value,
        float(config.STOP_LOSS_PCT), float(config.STOP_LOSS_ATR_MULT),
    ))


def compute_qty(equity_krw: float, cash_krw: float, entry_price: float,
                stop_pct: float, params: dict, *,
                open_positions: int = 0,
                current_exposure_krw: float = 0.0) -> SizingResult:
    """
    위험 한도·현금 한도·노출 한도로 살 수 있는 **정수 주 수**를 계산한다.

    Args:
        equity_krw: 총자산(현금 포함, 원). 위험 예산과 비중 한도의 기준.
        cash_krw: 예수금(원). 실제로 낼 수 있는 돈.
        entry_price: 1주 가격(원). 해외 종목은 환율을 곱한 원화 가격을 넘긴다.
        stop_pct: 유효 손절선(%, 음수). `effective_stop_pct()` 로 구해 넘긴다.
            **손절이 비활성이면(0 이상) 위험을 계산할 수 없으므로 0주로 막는다** —
            손절 없는 매매는 위험 한도 자체가 성립하지 않는다.
        params: {
            "risk_per_trade_pct", "max_position_pct", "max_open_positions",
            "max_total_exposure_pct",
        }. 테스트가 명시적으로 값을 주도록 dict 로 받는다(전역 설정과 분리).
        open_positions: 현재 보유 종목 수. 상한에 도달했으면 신규 진입을 막는다.
        current_exposure_krw: 현재 보유 평가액 합계(원). 총 노출 상한 계산용.

    반환: `SizingResult`. `qty == 0` 이면 **주문하면 안 된다**(강제 1주 매수 금지).

    우선순위: 입력 검증 → 손절선 검증 → 보유 종목 상한 → 노출 상한 →
              min(위험 기준, 현금 기준).
    """
    params = dict(params or {})

    # ── 입력 검증 ────────────────────────────────────────
    # 추측해서 계산하지 않는다. 자본을 모르면 위험 예산을 알 수 없고,
    # 가격을 모르면 수량을 알 수 없다.
    equity = float(equity_krw or 0.0)
    cash = float(cash_krw or 0.0)
    price = float(entry_price or 0.0)
    if equity <= 0 or cash < 0 or price <= 0:
        return SizingResult(0, INVALID_INPUT, price if price > 0 else 0.0,
                            detail=f"자본 {equity:,.0f} / 현금 {cash:,.0f} / "
                                   f"가격 {price:,.0f}")

    stop = float(stop_pct or 0.0)
    if stop >= 0:
        # 손절선이 없으면 1주당 위험이 0 이 되어 수량이 무한이 된다.
        # 그 상태를 "위험 한도 없음"으로 해석하면 위험 관리가 사라진다.
        return SizingResult(0, INVALID_STOP, price,
                            detail=f"손절선 {stop:.2f}% (0 이상은 비활성)")

    stop_distance = price * (-stop) / 100.0        # 1주당 손절 시 손실 (> 0)
    if stop_distance <= 0:
        return SizingResult(0, INVALID_STOP, price, stop_pct=stop,
                            detail="손절폭이 0")

    # ── 보유 종목 상한 ───────────────────────────────────
    # 수량과 무관하게 막는다. 2종목 상한인데 3종목째를 사면 분산이 깨진다.
    max_open = int(params.get("max_open_positions") or 0)
    if max_open > 0 and int(open_positions) >= max_open:
        return SizingResult(0, MAX_POSITIONS_REACHED, price, stop_pct=stop,
                            stop_distance_krw=stop_distance,
                            detail=f"보유 {int(open_positions)}종목 ≥ 상한 {max_open}종목")

    # ── 위험 기준 ────────────────────────────────────────
    risk_pct = float(params.get("risk_per_trade_pct") or 0.0)
    risk_budget = equity * risk_pct / 100.0
    qty_by_risk = int(math.floor(risk_budget / stop_distance)) if risk_budget > 0 else 0

    # ── 현금 기준 ────────────────────────────────────────
    # 현금과 비중 한도 중 **작은 쪽**까지만 쓴다. 비중 한도가 없으면 현금이 폭이 된다.
    max_position_pct = float(params.get("max_position_pct") or 0.0)
    position_cap = equity * max_position_pct / 100.0 if max_position_pct > 0 else cash
    spendable = min(cash, position_cap)

    # ── 총 노출 상한 ─────────────────────────────────────
    # 현금이 남아 있어도 전체 노출이 상한을 넘으면 그만큼만 쓴다.
    exposure_pct = float(params.get("max_total_exposure_pct") or 0.0)
    if exposure_pct > 0:
        room = equity * exposure_pct / 100.0 - float(current_exposure_krw or 0.0)
        spendable = min(spendable, max(0.0, room))

    qty_by_cash = int(math.floor(spendable / price))

    detail = (
        f"위험예산 {risk_budget:,.0f}원({risk_pct:g}%) / 손절폭 {stop_distance:,.2f}원"
        f"({stop:.2f}%) → {qty_by_risk}주 · "
        f"가용 {spendable:,.0f}원 / 가격 {price:,.0f}원 → {qty_by_cash}주"
    )

    if qty_by_cash <= 0 and spendable <= 0 and exposure_pct > 0 \
            and float(current_exposure_krw or 0.0) > 0:
        return SizingResult(0, EXPOSURE_LIMIT, price,
                            qty_by_risk=qty_by_risk, qty_by_cash=0,
                            risk_budget_krw=risk_budget,
                            stop_distance_krw=stop_distance, stop_pct=stop,
                            detail=detail)

    qty = min(qty_by_risk, qty_by_cash)
    if qty <= 0:
        # **최소 1주를 강제로 사지 않는다.** 그러면 위험 한도를 넘는다.
        # 대신 "1주를 사려면 얼마가 필요한가"를 남겨 사용자가 판단하게 한다.
        return SizingResult(0, INSUFFICIENT_CAPITAL, _capital_for_one_share(
                                price, stop_distance, risk_pct, max_position_pct,
                                exposure_pct, current_exposure_krw),
                            qty_by_risk=qty_by_risk, qty_by_cash=qty_by_cash,
                            risk_budget_krw=risk_budget,
                            stop_distance_krw=stop_distance, stop_pct=stop,
                            detail=detail)

    return SizingResult(qty, LEGACY_OK, qty * price,
                        qty_by_risk=qty_by_risk, qty_by_cash=qty_by_cash,
                        risk_budget_krw=risk_budget,
                        stop_distance_krw=stop_distance, stop_pct=stop,
                        detail=detail)


def _capital_for_one_share(price: float, stop_distance: float, risk_pct: float,
                           max_position_pct: float, exposure_pct: float,
                           current_exposure_krw: float) -> float:
    """
    0주일 때 **1주를 사려면 자본이 얼마나 필요한지** 계산한다.

    두 제약이 각각 요구하는 자본 중 **큰 쪽**이다 — 둘 다 만족해야 1주가 나온다.
      - 위험 기준: 1주 손실(stop_distance)이 자본의 risk_pct 이하여야 한다
        → 자본 ≥ stop_distance / (risk_pct/100)
      - 비중 기준: 1주 가격이 자본의 max_position_pct 이하여야 한다
        → 자본 ≥ price / (max_position_pct/100)
    노출 여력이 부족해서 0주가 된 경우에는 그 몫도 더한다.

    근사값이지만 "얼마를 넣어야 하는가"를 바로 알 수 있게 하는 것이 목적이다
    (정확한 재계산은 자본이 늘어난 뒤 다시 `compute_qty` 를 부르면 된다).
    """
    needs = [0.0]
    if risk_pct > 0:
        needs.append(stop_distance / (risk_pct / 100.0))
    if max_position_pct > 0:
        needs.append(price / (max_position_pct / 100.0))
    base = max(needs)
    if exposure_pct > 0 and current_exposure_krw > 0:
        # 노출 상한 때문에 막힌 경우: (필요 자본 × exposure%) ≥ 현재 노출 + price
        used = float(current_exposure_krw) + price
        base = max(base, used / (exposure_pct / 100.0))
    return base


def params_from_config() -> dict:
    """
    `config` 에서 사이징 파라미터를 모은다.

    `compute_qty` 가 전역 설정에 직접 결합되지 않도록 호출부가 이 함수로 넘긴다.
    테스트는 dict 를 직접 만들어 설정과 무관하게 경계값을 검증할 수 있다.
    """
    return {
        "risk_per_trade_pct": config.RISK_PER_TRADE_PCT,
        "max_position_pct": config.MAX_POSITION_PCT,
        "max_open_positions": config.MAX_OPEN_POSITIONS,
        "max_total_exposure_pct": config.MAX_TOTAL_EXPOSURE_PCT,
    }


def size_entry(ticker: str, equity_krw: float, cash_krw: float, entry_price: float,
               *, name: str = "", atr_value: float | None = None,
               open_positions: int = 0, current_exposure_krw: float = 0.0,
               params: dict | None = None) -> EntryPlan:
    """
    한 종목의 진입 계획을 만든다 (요구사항 4의 시작 로그용 편의 함수).

    손절선을 `effective_stop_pct()` 로 구해 `compute_qty` 에 넘긴다 —
    호출부가 손절선을 따로 계산하지 않게 한다(두 곳이 달라지면 위험이 어긋난다).
    상한(`CAPITAL_CAP_KRW`)도 여기서 적용한다.
    """
    capped_eq, capped_cash = apply_capital_cap(equity_krw, cash_krw)
    stop = effective_stop_pct(entry_price, atr_value)
    result = compute_qty(capped_eq, capped_cash, entry_price, stop,
                         params if params is not None else params_from_config(),
                         open_positions=open_positions,
                         current_exposure_krw=current_exposure_krw)
    return EntryPlan(ticker=ticker, name=name, price=float(entry_price or 0.0),
                     result=result)


def describe_universe(plans) -> str:
    """
    시작 로그용 요약 문자열을 만든다 (요구사항 4).

    "매수 가능한 종목"과 "불가능한 종목과 필요 자본"을 나눠 보여 준다.
    **규칙을 완화하지 않는다** — 왜 못 사는지와 얼마가 더 필요한지만 알린다.
    """
    ok = [p for p in plans if p.ok]
    no = [p for p in plans if not p.ok]
    lines = [f"매수 가능 {len(ok)}종목: " + (", ".join(str(p) for p in ok) if ok else "없음")]
    lines.append(
        "매수 불가 {}종목: ".format(len(no))
        + (", ".join(str(p) for p in no) if no else "없음")
    )
    return "\n".join(lines)
