"""
청산 규칙 (T06). **순수 함수만 둔다 — I/O 를 하지 않는다.**

부분 익절(단계별 분할 매도)을 폐기하고 단순한 전량 청산 규칙으로 대체한다.
이유: 소액(1~4주)에서 `max(1, round(qty * 33%))` 이 1주가 되어 첫 단계에서
전량 청산되거나, 1주씩만 팔려 비중이 사실상 무의미했다(CLAUDE.md "현재 알려진 문제").
계산이 단순해야 검증할 수 있고, 검증되지 않은 전략은 실전에 쓸 수 없다(절대 규칙 10).

설계 원칙
  - **I/O 없음.** DB 조회·API 호출·주문은 호출부(trader)가 한다. 여기서는 값만 받아
    판단만 돌려준다. 그래야 경계값·우선순위·갭 케이스를 테스트로 고정할 수 있다.
  - **사유는 하나뿐이다.** 한 사이클에 한 종목은 청산 사유를 하나만 돌려준다.
    둘이 겹치면 우선순위(`PRIORITY`)로 정한다.
  - 모든 수치는 `config` 에서 온다. 여기에 기본값을 하드코딩하지 않는다(절대 규칙 5).

**갭 하락 주의**: 이 모듈은 "손절선을 넘었다"는 판단만 한다. 손절선 **가격에 체결된다는
보장은 없다.** 갭으로 시가가 손절선 아래에서 시작하면 그 가격에는 팔리지 않는다.
그래서 손절·시간청산 주문은 시장가로 내도록 `config.EXIT_ORDER_TYPE_*` 이 정해져 있다
(`trader._exit_order_type`) — 지정가로 내면 체결되지 않고, `cancel_order` 가 아직
미구현(T04b)이라 취소도 못 해 포지션이 그대로 남는다.
"""
import logging
from datetime import datetime

import config
import market_calendar
from errfmt import exc_summary

logger = logging.getLogger("bot")

# DB 의 market 값. `trader` 와 같은 문자열이지만 여기서 따로 정의한다 —
# `trader` 를 import 하면 순환 import 가 된다(trader 가 이 모듈을 쓴다).
# 값이 어긋나면 `tests/test_exit_rules.py` 의 대응 관계 테스트가 잡는다.
MARKET_OVERSEAS = "overseas"
MARKET_DOMESTIC = "domestic"

# 청산 사유. `orders.reason` 과 로그에 그대로 기록된다.
STOP_LOSS = "STOP_LOSS"
TIME_EXIT = "TIME_EXIT"
TAKE_PROFIT = "TAKE_PROFIT"
TRAILING = "TRAILING"
OVERBOUGHT = "OVERBOUGHT"

# 우선순위: 손절 > 시간청산 > 목표가 > 트레일링 > 과매수 (낮을수록 먼저).
# 손절이 가장 먼저인 이유는 손실 확대를 막는 것이 다른 어떤 목적보다 우선이기 때문이다.
# 시간청산이 목표가보다 앞인 이유는 "보유 기간이 끝났다"가 "목표를 못 채웠다"를
# 포함하는 상위 조건이기 때문이다 — 7거래일을 넘긴 종목은 목표가에 못 닿았어도 정리한다.
PRIORITY = {
    STOP_LOSS: 0,
    TIME_EXIT: 1,
    TAKE_PROFIT: 2,
    TRAILING: 3,
    OVERBOUGHT: 4,
}

# 임계값 비교의 부동소수점 오차 허용치(%p).
#
# 왜 필요한가: 가격은 소수 2자리 실수라 `(현재가 - 진입가) / 진입가 * 100` 이
# **정확히** 임계값이 되지 않는다. 예를 들어 고점 102.00 / 현재 100.98 은
# 이론상 정확히 -1.00% 지만 실제 계산값은 -0.999999999999996 이라
# `<= -1.0` 비교가 거짓이 된다 — "임계값에 닿았는데 발동하지 않는" 상태다.
# 허용치를 두면 경계 판정이 가격 반올림에 좌우되지 않고 결정적이 된다.
# 1e-9 는 2자리 가격의 표현 오차(1e-13 수준)보다 훨씬 크고, 실제 거래 판단에
# 영향을 줄 만한 크기(0.01%p = 1e-2)보다는 훨씬 작다.
_EPS_PCT = 1e-9


# 사유별 사람이 읽을 설명 (로그·보고용).
LABELS = {
    STOP_LOSS: "손절",
    TIME_EXIT: "보유 기간 초과",
    TAKE_PROFIT: "목표가 도달",
    TRAILING: "트레일링 스톱",
    OVERBOUGHT: "과매수",
}


class ExitSignal:
    """
    청산 신호. `reason` 하나와 그 근거(`detail`)만 담는다.

    수량은 담지 않는다 — 청산은 항상 **전량**이고, 실제 수량은 브로커 잔고에서
    재확인해야 한다(요구사항 5). 여기에 수량을 넣어 두면 DB 값이 정답인 것처럼
    보여 미확인 수량으로 주문이 나갈 위험이 생긴다.
    """

    __slots__ = ("reason", "detail", "price")

    def __init__(self, reason: str, detail: str, price: float | None = None):
        if reason not in PRIORITY:
            raise ValueError(f"모르는 청산 사유입니다: {reason!r}")
        self.reason = reason
        self.detail = detail
        # 판단에 쓴 현재가. 호출부가 주문 가격을 정할 때 쓴다(주문 가격 자체는 아니다).
        self.price = price

    def __repr__(self):
        return f"ExitSignal({self.reason!r}, {self.detail!r}, price={self.price!r})"

    def __eq__(self, other):
        if not isinstance(other, ExitSignal):
            return NotImplemented
        return (self.reason, self.detail, self.price) == \
               (other.reason, other.detail, other.price)


def atr_stop_loss_pct(entry_price: float, atr_value: float | None,
                      mult: float) -> float | None:
    """
    ATR 기반 손절선(%). 계산할 수 없으면 None.

    ATR 은 **가격 단위**이므로 진입가로 나눠 % 로 바꾼다.
    `mult` 가 0 이하이거나 ATR 을 모르면 None 이다(고정 % 만 쓴다).

    반올림하지 않는다 — ATR 은 소수점이 길고, 반올림하면 경계에서 손절선이
    설정값과 어긋난다(예: 배수 2·ATR 2.0·진입가 100 → 정확히 -4.0 이어야 한다).
    """
    if mult <= 0 or not atr_value or atr_value <= 0 or entry_price <= 0:
        return None
    return -(float(atr_value) / float(entry_price)) * 100.0 * float(mult)


def effective_stop_loss_pct(entry_price: float, atr_value: float | None,
                            fixed_pct: float, mult: float) -> float:
    """
    이번 포지션에 실제로 적용할 손절선(%, 음수).

    `mult` 가 0 이하면 고정 `fixed_pct` 다.
    켜져 있으면 **고정 %와 ATR 기반 중 더 넓은(더 낮은) 쪽**을 쓴다 —
    변동성이 큰 종목에서 정상적인 흔들림에 손절당하는 것을 줄이기 위해서다.
    ATR 을 계산할 수 없으면 조용히 다른 규칙으로 바꾸지 않고 **고정 % 를 그대로 쓰되
    경고를 남긴다**(설정을 켰는데 동작이 다른 것을 모르면 안 된다).
    """
    fixed = float(fixed_pct)
    if mult <= 0:
        return fixed

    atr_pct = atr_stop_loss_pct(entry_price, atr_value, mult)
    if atr_pct is None:
        logger.warning(
            f"⚠️ STOP_LOSS_ATR_MULT={mult} 를 켰지만 ATR 을 계산할 수 없어 "
            f"고정 손절선({fixed}%)만 사용합니다."
        )
        return fixed
    return min(fixed, atr_pct)      # 더 낮은 쪽 = 더 넓은 손절


def evaluate_exit(position, bar_info, params, now,
                  *, atr_value: float | None = None,
                  overbought: bool = False) -> ExitSignal | None:
    """
    청산 여부를 판단한다 (요구사항 2). **I/O 없음.**

    Args:
        position: {
            "qty": float,                 보유 수량 (0 이하면 청산 대상 아님)
            "avg_price": float,           평균 진입 단가
            "entry_time": str | datetime | None,  진입 시각 (보유 거래일 계산용)
            "peak_price_since_entry": float | None, 진입 후 고점 (트레일링용)
            "market": str,                "overseas" / "domestic" — 거래일 계산에 쓴다
            "current_price": float,       현재가
        }
        bar_info: {"atr": float|None, "overbought": bool} 또는 None.
            일봉 기반 보조값이다. 계산할 수 없으면 None/0 을 넣는다 —
            **없다고 청산 판단을 멈추지 않는다**(가격 기반 규칙은 계속 동작해야 한다).
        params: {
            "take_profit_pct", "stop_loss_pct", "stop_loss_atr_mult",
            "max_hold_trading_days", "trailing_stop_enabled",
            "trailing_activate_pct", "trailing_gap_pct", "overbought_exit_enabled",
        }. 테스트가 명시적으로 값을 주도록 dict 로 받는다(전역 설정에 결합되지 않게).
        now: tz-aware datetime. 보유 거래일 계산의 기준 시각.
        atr_value: ATR(가격 단위). `bar_info["atr"]` 보다 우선한다.
        overbought: 과매수 신호 여부. `bar_info["overbought"]` 보다 우선한다.

    반환: `ExitSignal` 또는 None(청산하지 않음). 사유는 **하나뿐**이다.

    우선순위: 손절 > 시간청산 > 목표가 > 트레일링 > 과매수.
    """
    if not position:
        return None

    qty = float(position.get("qty") or 0.0)
    if qty <= 0:
        return None

    entry_price = float(position.get("avg_price") or 0.0)
    price = float(position.get("current_price") or 0.0)
    if entry_price <= 0 or price <= 0:
        # 단가를 모르면 수익률을 계산할 수 없다. 추측해서 팔지 않는다.
        return None

    if bar_info:
        if atr_value is None:
            atr_value = bar_info.get("atr")
        overbought = overbought or bool(bar_info.get("overbought"))

    profit_pct = (price - entry_price) / entry_price * 100.0

    # ── 1. 손절 (최우선) ─────────────────────────────────
    stop_pct = effective_stop_loss_pct(
        entry_price, atr_value,
        float(params.get("stop_loss_pct") or 0.0),
        float(params.get("stop_loss_atr_mult") or 0.0),
    )
    if stop_pct < 0 and profit_pct <= stop_pct + _EPS_PCT:
        return ExitSignal(
            STOP_LOSS,
            f"수익률 {profit_pct:.2f}% ≤ 손절선 {stop_pct:.2f}%",
            price,
        )

    # ── 2. 시간청산 ──────────────────────────────────────
    hold_days = trading_days_held(position, now)
    max_days = int(params.get("max_hold_trading_days") or 0)
    if max_days > 0 and hold_days is not None and hold_days >= max_days:
        return ExitSignal(
            TIME_EXIT,
            f"보유 {hold_days}거래일 ≥ {max_days}거래일",
            price,
        )

    # ── 3. 목표가 ────────────────────────────────────────
    take_pct = float(params.get("take_profit_pct") or 0.0)
    if take_pct > 0 and profit_pct >= take_pct - _EPS_PCT:
        return ExitSignal(
            TAKE_PROFIT,
            f"수익률 {profit_pct:.2f}% ≥ 목표 {take_pct:.2f}%",
            price,
        )

    # ── 4. 트레일링 스톱 ─────────────────────────────────
    if params.get("trailing_stop_enabled"):
        peak = position.get("peak_price_since_entry")
        activate = float(params.get("trailing_activate_pct") or 0.0)
        gap = float(params.get("trailing_gap_pct") or 0.0)
        if peak and gap > 0:
            peak_pct = (float(peak) - entry_price) / entry_price * 100.0
            # 활성 조건: 고점이 활성선을 넘은 뒤에만 켠다. 안 그러면 진입 직후
            # 조금만 올랐다 내려도 팔려서 목표가 규칙과 다를 바가 없어진다.
            if peak_pct >= activate - _EPS_PCT:
                drop_from_peak = (price - float(peak)) / float(peak) * 100.0
                if drop_from_peak <= -gap + _EPS_PCT:
                    return ExitSignal(
                        TRAILING,
                        f"고점 {float(peak):,.2f} 대비 {drop_from_peak:.2f}% 하락 "
                        f"(고점 수익 {peak_pct:.2f}% ≥ 활성 {activate:.2f}%, "
                        f"이탈 기준 -{gap:.2f}%)",
                        price,
                    )

    # ── 5. 과매수 ────────────────────────────────────────
    # 수익 중일 때만 인정한다 — 손실 중 과매수로 파는 것은 손절과 구분되지 않는다.
    if params.get("overbought_exit_enabled") and overbought and profit_pct > 0:
        return ExitSignal(OVERBOUGHT, f"과매수 신호 (수익률 {profit_pct:.2f}%)", price)

    return None


def trading_days_held(position, now) -> int | None:
    """
    진입 후 지난 **거래일** 수. 계산할 수 없으면 None.

    `T05` 의 `market_calendar.trading_days_between` 을 쓴다 — 휴장일을 세지 않는다.
    주말을 거래일로 세면 금요일 매수가 월요일에 "3일 보유"가 되어 조기에 청산된다.

    진입 시각이 없거나 해석할 수 없으면 None 이다. **0 으로 대체하지 않는다** —
    0 은 "오늘 샀다"는 뜻이라, 진입 시각을 모르는 포지션의 보유 기간을 영원히
    짧게 보게 만든다(시간청산이 절대 발동하지 않는다).
    """
    raw = position.get("entry_time")
    if not raw:
        return None

    from datetime import datetime as _dt

    if isinstance(raw, _dt):
        entered = raw
    else:
        try:
            entered = _dt.fromisoformat(str(raw))
        except (TypeError, ValueError) as e:
            logger.warning(f"⚠️ entry_time 해석 실패 ({raw!r}): {exc_summary(e)}")
            return None

    if entered.tzinfo is None:
        # naive 를 조용히 KST 로 승격하지 않는다(절대 규칙 9).
        logger.warning(f"⚠️ entry_time 이 naive 입니다 — 보유 거래일을 계산하지 않습니다: {raw!r}")
        return None

    market = position.get("market")
    cal_market = _to_calendar_market(market)
    if cal_market is None:
        logger.warning(f"⚠️ 시장을 알 수 없어 보유 거래일을 계산하지 않습니다: {market!r}")
        return None

    try:
        return int(market_calendar.trading_days_between(cal_market, entered, now))
    except Exception as e:
        logger.warning(f"⚠️ 보유 거래일 계산 실패 — 시간청산을 건너뜁니다: {exc_summary(e)}")
        return None


def _to_calendar_market(market: str | None) -> str | None:
    """
    DB 의 market 값(`overseas`/`domestic`)을 캘린더 시장 이름(`US`/`KRX`)으로 바꾼다.

    두 이름 체계가 다른 것은 의도적이다 (T05 참고) — DB 값은 저장된 이력이 있어
    바꾸려면 마이그레이션이 필요하고, 캘린더 이름은 라이브러리 규약을 따른다.
    변환은 이 한 곳에서만 한다.
    """
    if market == MARKET_OVERSEAS:
        return market_calendar.MARKET_US
    if market == MARKET_DOMESTIC:
        return market_calendar.MARKET_KRX
    return None


def params_from_config() -> dict:
    """
    `config` 에서 청산 판단 파라미터를 모은다.

    `evaluate_exit` 가 전역 설정에 직접 결합되지 않도록 호출부가 이 함수로 넘긴다.
    테스트는 dict 를 직접 만들어 설정과 무관하게 경계값을 검증할 수 있다.
    """
    return {
        "take_profit_pct": config.TAKE_PROFIT_PCT,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "stop_loss_atr_mult": config.STOP_LOSS_ATR_MULT,
        "max_hold_trading_days": config.MAX_HOLD_TRADING_DAYS,
        "trailing_stop_enabled": config.TRAILING_STOP_ENABLED,
        "trailing_activate_pct": config.TRAILING_ACTIVATE_PCT,
        "trailing_gap_pct": config.TRAILING_GAP_PCT,
        "overbought_exit_enabled": config.OVERBOUGHT_EXIT_ENABLED,
    }
