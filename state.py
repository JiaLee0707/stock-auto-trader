"""
봇 런타임 상태 관리.
- 금일 매수 완료 플래그: 중복 매수 방지 (종목별)
- 날짜가 바뀌면 플래그 자동 리셋

종목 키는 config.TARGET_TICKER(해외) / config.DOMESTIC_TICKER(국내)를 사용한다.
"""
from datetime import date


class BotState:
    def __init__(self):
        self._bought_today: dict = {}     # {종목코드: bool}
        self._buy_counts: dict = {}       # {종목코드: 당일 매수 횟수}
        self._profit_stages: dict = {}    # {종목코드: 당일 실행한 익절 단계}
        self._last_buy_time: dict = {}    # {종목코드: datetime}
        self._flag_date: date = date.today()

    def _check_date_reset(self):
        today = date.today()
        if today != self._flag_date:
            # 날짜가 바뀐 경우 모든 종목 플래그 초기화
            self._bought_today = {}
            self._buy_counts = {}
            self._profit_stages = {}
            self._last_buy_time = {}
            self._flag_date = today

    def bought_today_for(self, ticker: str) -> bool:
        """종목별 당일 매수 여부."""
        self._check_date_reset()
        return self._bought_today.get(ticker, False)

    def set_bought_today(self, ticker: str, value: bool):
        """종목별 당일 매수 여부 설정."""
        self._check_date_reset()
        self._bought_today[ticker] = value

    # ── 당일 매수 횟수 (다회 매수용) ──────────────────────
    def buy_count_today(self, ticker: str) -> int:
        """종목별 당일 누적 매수 횟수."""
        self._check_date_reset()
        return self._buy_counts.get(ticker, 0)

    def record_buy(self, ticker: str):
        """매수 1회 기록 (횟수 증가 + 당일 매수 플래그 설정)."""
        self._check_date_reset()
        self._buy_counts[ticker] = self._buy_counts.get(ticker, 0) + 1
        self._bought_today[ticker] = True
        from datetime import datetime
        self._last_buy_time[ticker] = datetime.now()

    # ── 부분 익절 단계 (당일 종목별 도달 단계) ────────────
    def profit_stage(self, ticker: str) -> int:
        """당일 이미 실행한 익절 단계 수 (0이면 미실행)."""
        self._check_date_reset()
        return self._profit_stages.get(ticker, 0)

    def set_profit_stage(self, ticker: str, stage: int):
        """당일 익절 단계 기록."""
        self._check_date_reset()
        self._profit_stages[ticker] = stage

    def last_buy_time(self, ticker: str):
        """종목별 마지막 매수 시각 (datetime 또는 None)."""
        self._check_date_reset()
        return self._last_buy_time.get(ticker)

    # ── 하위 호환: 단일 종목(config.TARGET_TICKER) 기준 접근 ──
    @property
    def bought_today(self) -> bool:
        import config
        return self.bought_today_for(config.TARGET_TICKER)

    @bought_today.setter
    def bought_today(self, value: bool):
        import config
        self.set_bought_today(config.TARGET_TICKER, value)


# 싱글턴
bot_state = BotState()
