"""
봇 런타임 상태 관리.
- 금일 매수 완료 플래그: 중복 매수 방지
- 날짜가 바뀌면 플래그 자동 리셋
"""
from datetime import date

class BotState:
    def __init__(self):
        self._bought_today: bool = False
        self._flag_date: date = date.today()

    def _check_date_reset(self):
        today = date.today()
        if today != self._flag_date:
            # 날짜가 바뀐 경우 상태 초기화
            self._bought_today = False
            self._flag_date = today

    @property
    def bought_today(self) -> bool:
        self._check_date_reset()
        return self._bought_today

    @bought_today.setter
    def bought_today(self, value: bool):
        self._check_date_reset()
        self._bought_today = value

# 싱글턴
bot_state = BotState()