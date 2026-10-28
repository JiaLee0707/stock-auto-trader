"""
판단 입출력 자료구조 (T08).

**frozen dataclass 만 둔다 — 로직·I/O 를 넣지 않는다.**

목적: 백테스트와 실전이 **같은 판단 함수**(`strategy.decide`)를 쓰게 하려면, 판단에
필요한 입력을 한 덩어리로 명확히 정의해야 한다. 지금까지는 `trader._run_cycle` 이
인자 14개와 `config`·`bot_state` 전역을 섞어 판단했기 때문에, 백테스트에서 같은
판단을 재현하려면 같은 전역 상태를 흉내 내야 했다(CLAUDE.md "백테스트와 실전 판단
로직이 별개일 가능성").

설계 원칙
  - **frozen=True**: 판단 함수가 입력을 바꾸지 못하게 막는다. 사본을 만들 이유도
    없어진다(같은 입력 → 같은 출력이 구조적으로 보장된다).
  - **I/O 없음**: 이 모듈은 DB·API·시계를 만지지 않는다. `now` 는 호출부가 넣는다
    (절대 규칙 9 — naive datetime 금지).
  - 수량·비율 같은 값은 여기서 계산하지 않는다. 계산은 `sizing`·`exit_rules`·
    `strategy` 가 한다.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ── Decision.action 값 ───────────────────────────────────
BUY = "BUY"
SELL = "SELL"
HOLD = "HOLD"

# HOLD 사유(reason). 로그·테스트에서 문자열로 비교하므로 상수로 고정한다.
HOLD_NO_SIGNAL = "NO_SIGNAL"                 # 진입 조건 미충족
HOLD_DAILY_LIMIT = "DAILY_BUY_LIMIT"         # 당일 매수 한도 도달
HOLD_BUY_INTERVAL = "BUY_INTERVAL"           # 직전 매수 후 최소 간격 미경과
HOLD_MAX_POSITIONS = "MAX_OPEN_POSITIONS"    # 동시 보유 종목 상한
HOLD_AI_FILTER = "AI_SCORE_FILTER"           # AI 점수 하한 미달
HOLD_INDICATOR_UNAVAILABLE = "INDICATOR_UNAVAILABLE"  # 지표 계산 불가(데이터 부족)
HOLD_TREND_FILTER = "TREND_FILTER_UNAVAILABLE"        # SMA(200) 계산 불가
HOLD_NO_CAPITAL = "INSUFFICIENT_CAPITAL"     # 수량 0 (sizing 이 정한 사유)
HOLD_EXPOSURE_UNAVAILABLE = "EXPOSURE_UNAVAILABLE"  # 노출·보유·카운터 조회 실패
HOLD_TRADING_HALTED = "TRADING_HALTED"          # 킬스위치가 신규 매수를 막는 중 (T09a)

# 청산 판단을 하지 않은 이유.
HOLD_NO_POSITION = "NO_POSITION"             # 보유 없음 → 청산 대상 아님
HOLD_EXIT_NOT_TRIGGERED = "EXIT_NOT_TRIGGERED"  # evaluate_exit 이 None


@dataclass(frozen=True)
class MarketSnapshot:
    """
    한 종목의 시장 상황. **확정 일봉만** 담는다.

    Args:
        symbol: 종목 코드.
        bars: 확정 일봉 **DataFrame**(date, open, high, low, close, volume).
            `history.get_confirmed_bars()` 가 돌려주는 dict 목록을 DataFrame 으로
            바꿔 넣는다(호출부가 변환). 당일 미확정 봉은 **이미 제외돼 있어야 한다** —
            장중 신호로 판단하면 그 봉이 다시 그려지면서 판단이 뒤집힌다.
            비어 있을 수 있다(캐시 미구축). 그때는 지표를 계산하지 않는다.
        last_price: 현재가(주문·수량 계산에 쓴다). 통화는 시장에 따른다 —
            해외는 달러, 국내는 원.
        now: 판단 기준 시각 (tz-aware). naive 는 받지 않는다(절대 규칙 9).
    """

    symbol: str
    bars: Any                       # pandas.DataFrame (date, open, high, low, close, volume)
    last_price: float
    now: datetime

    def __post_init__(self):
        if self.now is not None and self.now.tzinfo is None:
            raise ValueError(
                f"MarketSnapshot.now 는 tz-aware 여야 합니다 (절대 규칙 9): {self.now!r}"
            )


@dataclass(frozen=True)
class PositionView:
    """
    보유 포지션의 판단용 요약.

    `qty <= 0` 이면 "보유 없음"이다. `entry_date` 는 진입 시각(보유 거래일 계산용),
    `peak_price_since_entry` 는 진입 후 고점(트레일링 스톱용)이다.
    """

    qty: float = 0.0
    avg_price: float = 0.0
    entry_date: datetime | None = None
    peak_price: float | None = None

    @property
    def is_open(self) -> bool:
        return float(self.qty or 0.0) > 0


@dataclass(frozen=True)
class AccountView:
    """
    자본 상황. **원화 기준**으로 통일한다.

    해외 종목의 가격이 달러여도 자본은 원화다. 호출부가 환율을 적용해
    `last_price` 를 원화로 환산한 뒤 사이징에 넘긴다(`sizing.compute_qty` 규약).
    `CAPITAL_CAP_KRW` 적용은 호출부가 `sizing.apply_capital_cap` 으로 하고,
    **제한된 값을 여기에 넣는다** — 여기서 다시 계산하면 두 곳이 어긋난다.
    """

    equity: float = 0.0
    cash: float = 0.0


@dataclass(frozen=True)
class DayCounters:
    """
    당일 카운터와 진입 제약 상태.

    Args:
        buy_count_today: 이 종목의 당일 누적 매수 횟수.
        last_buy_time: 직전 매수 시각 (tz-aware 또는 None).
        open_positions: **자기 자신을 제외한** 동시 보유 종목 수.
        exposure_krw: 자기 자신을 제외한 현재 총 노출(원, 원가 기준).
    """

    buy_count_today: int = 0
    last_buy_time: datetime | None = None
    open_positions: int = 0
    exposure_krw: float = 0.0

    def __post_init__(self):
        if self.last_buy_time is not None and self.last_buy_time.tzinfo is None:
            raise ValueError(
                f"DayCounters.last_buy_time 는 tz-aware 여야 합니다: {self.last_buy_time!r}"
            )


@dataclass(frozen=True)
class Decision:
    """
    판단 결과. **부작용은 담지 않는다** — 주문·DB 기록은 호출부가 한다.

    Args:
        action: `BUY` / `SELL` / `HOLD`.
        qty: 주문 수량. `HOLD` 면 0.
        reason: 왜 이 결정인지(위 상수 또는 청산 사유 `exit_rules.STOP_LOSS` 등).
        limit_price: 지정가 주문 가격(호출부가 호가 단위로 다듬는다). HOLD 면 None.
        meta: 재현용 부가 정보 — 입력 지표값, 사이징 계산 과정, 청산 상세 등.
            로그에 함께 남겨 "왜 이 주문이 나갔는지"를 사후에 확인할 수 있게 한다.
    """

    action: str
    qty: float = 0.0
    reason: str = ""
    limit_price: float | None = None
    meta: dict = field(default_factory=dict)

    # 편의 접근자 (판단 함수 밖에서 쓰는 조건문을 단순하게 만든다)
    @property
    def is_buy(self) -> bool:
        return self.action == BUY

    @property
    def is_sell(self) -> bool:
        return self.action == SELL

    @property
    def is_hold(self) -> bool:
        return self.action == HOLD

    def log_line(self) -> str:
        """
        로그 한 줄. **계좌번호·키는 넣지 않는다**(절대 규칙 2) — 여기 담기는 값은
        종목·수량·가격·지표뿐이다.
        """
        head = f"결정 {self.action} {self.qty:g}주 ({self.reason})"
        if self.limit_price is not None:
            head += f" @ {self.limit_price:,.2f}"
        if not self.meta:
            return head
        detail = ", ".join(f"{k}={v}" for k, v in self.meta.items())
        return f"{head} | {detail}"
