"""
KIS 국내주식 ETF API 모듈 (Phase 2 확장용 (스켈레톤).
자산이 충분히 커졌을 때 TIGER 미국나스닥100(379800) 등 국내 ETF 매매에 사용.
현재는 인터페이스만 정의하고 Phase 2 시점에 구현합니다.
"""
import logging

logger = logging.getLogger("bot")


def get_domestic_price(ticker: str) -> float:
    """국내 ETF 현재가 조회 (Phase 2 구현 예정)."""
    raise NotImplementedError("Phase 2에서 구현 예정")


def buy_domestic(ticker: str, quantity: int) -> dict:
    """국내 ETF 매수 (Phase 2 구현 예정)."""
    raise NotImplementedError("Phase 2에서 구현 예정")


def sell_domestic_all(ticker: str, quantity: int) -> dict:
    """국내 ETF 전량 매도 (Phase 2 구현 예정)."""
    raise NotImplementedError("Phase 2에서 구현 예정")