"""
봇 런타임 상태 관리 (T03: SQLite 기반).

기존 메모리 전용 구현을 DB 기반으로 교체했다. 재시작해도 상태가 유지된다.

- 금일 매수 횟수·시각: `daily_counters` 를 **날짜 키**로 조회하므로 날짜가 바뀌면
  자동으로 0회가 된다(별도 리셋 로직이 필요 없다).
- 익절 단계: 기존과 동일하게 당일 기준이다. 같은 `daily_counters` 의 `buy_count` 와
  별도로, 단계는 프로세스 내 상태가 아니라 DB `kv` 가 아닌 **당일 카운터의 확장**으로
  관리하지 않고 메모리에 둔다 — 아래 "익절 단계" 주석 참고.
- 시간은 tz-aware KST(절대 규칙 9).

공개 함수 시그니처는 기존과 동일하게 유지한다(교체 전 `BotState` 와 호환).
`date.today()` 리셋 로직은 없어졌지만 외부 동작(날짜가 바뀌면 0부터)은 같다.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import db

logger = logging.getLogger("bot")

KST = ZoneInfo("Asia/Seoul")


class BotState:
    """
    DB 기반 상태 접근자.

    Args:
        conn: 열린 sqlite3 연결. 주지 않으면 `db.open_db()` 로 새로 연다.
        close_on_exit: 스스로 연 연결을 닫을지 여부(외부에서 준 연결은 닫지 않는다).
    """

    def __init__(self, conn=None):
        if conn is None:
            self._conn = db.open_db()
            self._owns_conn = True
        else:
            self._conn = conn
            self._owns_conn = False
        # 익절 단계는 "당일 도달 단계"라 프로세스 재시작 시 0으로 돌아가는 것이 맞다.
        # (재시작 후에는 어차피 브로커 잔고 기준으로 다시 판단한다.)
        self._profit_stages: dict = {}

    # ── 내부 ────────────────────────────────────────────
    def _today(self) -> str:
        """KST 기준 오늘 날짜(YYYY-MM-DD). daily_counters 의 키."""
        return datetime.now(KST).date().isoformat()

    def close(self) -> None:
        """스스로 연 연결을 닫는다."""
        if self._owns_conn and self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self):
        """
        열린 DB 연결. `db.py`·`reconcile.py` 함수에 넘겨 쓴다.

        사설 `_conn` 을 밖에서 직접 만지지 않도록 공개 접근자를 둔다.
        """
        return self._conn

    # ── 당일 매수 여부 ──────────────────────────────────
    def bought_today_for(self, ticker: str) -> bool:
        """종목별 당일 매수 여부. 당일 카운터가 1회 이상이면 True."""
        return self.buy_count_today(ticker) > 0

    def set_bought_today(self, ticker: str, value: bool):
        """
        종목별 당일 매수 여부 설정.

        기존 메모리 구현과 호환용이다. `False` 는 당일 카운터를 0으로 되돌린다
        (전량 매도 후 재진입 허용). `True` 는 카운터를 최소 1로 만든다.
        """
        if value:
            if self.buy_count_today(ticker) < 1:
                db.set_buy_count(self._conn, self._today(), ticker, 1)
        else:
            db.set_buy_count(self._conn, self._today(), ticker, 0)

    # ── 당일 매수 횟수 (다회 매수용) ──────────────────────
    def buy_count_today(self, ticker: str) -> int:
        """종목별 당일 누적 매수 횟수. 날짜가 바뀌면 자동으로 0이다."""
        return int(db.get_daily_counter(self._conn, self._today(), ticker)["buy_count"])

    def record_buy(self, ticker: str):
        """매수 1회 기록 (횟수 증가 + 시각 갱신)."""
        count = db.increment_buy_count(self._conn, self._today(), ticker)
        logger.info(f"[state] {ticker} 당일 매수 {count}회 기록")

    # ── 부분 익절 단계 (당일 종목별 도달 단계) ────────────
    def profit_stage(self, ticker: str) -> int:
        """당일 이미 실행한 익절 단계 수 (0이면 미실행)."""
        return int(self._profit_stages.get(ticker, 0))

    def set_profit_stage(self, ticker: str, stage: int):
        """당일 익절 단계 기록."""
        self._profit_stages[ticker] = int(stage)

    def last_buy_time(self, ticker: str):
        """
        종목별 마지막 매수 시각 (tz-aware datetime 또는 None).

        DB 에는 ISO 문자열로 저장돼 있으므로 datetime 으로 되돌려 준다.
        naive 로 저장된 값이 있으면 KST 로 간주하지 않고 None 을 돌려준다
        (절대 규칙 9 — naive 를 조용히 KST 로 승격하면 시각이 어긋난다).
        """
        raw = db.get_daily_counter(self._conn, self._today(), ticker)["last_buy_time"]
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as e:
            logger.warning(f"[state] last_buy_time 해석 실패 ({ticker}): {raw!r} ({e})")
            return None
        if parsed.tzinfo is None:
            logger.warning(f"[state] last_buy_time 이 naive 입니다 ({ticker}): {raw!r}")
            return None
        return parsed

    # ── 하위 호환: 단일 종목(config.TARGET_TICKER) 기준 접근 ──
    @property
    def bought_today(self) -> bool:
        import config
        return self.bought_today_for(config.TARGET_TICKER)

    @bought_today.setter
    def bought_today(self, value: bool):
        import config
        self.set_bought_today(config.TARGET_TICKER, value)


# 싱글턴. import 시점에 DB 를 열지 않도록 지연 생성한다(테스트가 임시 DB 를 쓸 수 있게).
_bot_state: BotState | None = None


def get_bot_state() -> BotState:
    """프로세스 싱글턴을 돌려준다(최초 호출 시 DB 를 연다)."""
    global _bot_state
    if _bot_state is None:
        _bot_state = BotState()
    return _bot_state


def reset_bot_state(conn=None) -> BotState:
    """
    싱글턴을 새로 만든다. 테스트에서 임시 DB 로 갈아끼울 때 쓴다.

    기존 싱글턴이 스스로 연 연결이면 닫는다.
    """
    global _bot_state
    if _bot_state is not None:
        _bot_state.close()
    _bot_state = BotState(conn)
    return _bot_state


class _LazyBotState:
    """
    `bot_state` 이름을 그대로 쓰는 기존 코드를 위한 프록시.

    import 만으로 DB 가 열리면 테스트가 실제 `data/bot.db` 를 건드리게 되므로,
    실제 속성 접근 시점에 싱글턴을 만든다.
    """

    def __getattr__(self, name):
        return getattr(get_bot_state(), name)

    def __setattr__(self, name, value):
        setattr(get_bot_state(), name, value)


bot_state = _LazyBotState()
