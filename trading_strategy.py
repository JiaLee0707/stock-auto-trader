"""
투자 대상 관리 및 포지션 크기 산정을 위한 거래 전략 모듈.

이 모듈은 다음을 정의합니다:
1. 대상 자산 설정 (해외 및 국내)
2. 총 자산 기준 포지션 크기 계산
3. 자산 유형별 진입/청산 신호 로직
4. 자산별 위험 관리 매개변수

포트폴리오 규모에 따라 해외 분수 거래와 국내 ETF 투자를
함께 운용하는 혼합 전략을 지원합니다.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from config import TARGET_TICKER, TARGET_TICKER_NAME, IS_MOCK


class AssetType(Enum):
    """투자 유형별 자산 분류."""
    OVERSEAS_FRACTIONAL = "overseas_fractional"  # 나스닥 주식 (QQQ 등)
    DOMESTIC_ETF = "domestic_etf"              # 국내 ETF (TIGER 등)
    CASH = "cash"                             # 현금 포지션


class SignalType(Enum):
    """거래 신호 유형."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class TradingTarget:
    """관련 매개변수와 함께 거래 대상을 나타냅니다."""
    symbol: str
    name: str
    asset_type: AssetType
    price: float = 0.0
    quantity: float = 0.0
    avg_cost: float = 0.0
    profit_rate: float = 0.0
    target_allocation: float = 0.0  # 포트폴리오 목표 비중 %

    @property
    def is_long(self) -> bool:
        """현재 롱 포지션을 보유 중인지 확인합니다."""
        return self.quantity > 0

    @property
    def market_value(self) -> float:
        """포지션의 시가 평가액을 계산합니다."""
        return self.price * self.quantity

    @property
    def unrealized_pnl(self) -> float:
        """평가 손익을 계산합니다."""
        if self.avg_cost == 0:
            return 0.0
        return (self.price - self.avg_cost) * self.quantity

    @property
    def unrealized_pnl_rate(self) -> float:
        """평가 손익률을 계산합니다."""
        if self.avg_cost == 0:
            return 0.0
        return (self.price - self.avg_cost) / self.avg_cost * 100


@dataclass
class TradingParameters:
    """위험 관리 및 거래 매개변수."""
    # 해외 거래 매개변수
    overseas_buy_ratio: float = 0.30  # 해외 매수에 사용할 총자산 비중 (%)
    overseas_dip_threshold: float = -1.5  # 가격 하락 트리거 (%)
    overseas_ai_score_floor: float = -50  # 매수를 위한 최소 AI 점수
    overseas_take_profit_threshold: float = 5.0  # 익절 임계값 (%)

    # 국내 거래 매개변수
    domestic_buy_ratio: float = 0.20  # 국내 매수에 사용할 총자산 비중 (%)
    domestic_stop_loss_threshold: float = -3.0  # 손절 임계값 (%)
    domestic_take_profit_threshold: float = 6.0  # 익절 임계값 (%)

    # 일반 매개변수
    max_position_size_ratio: float = 0.4  # 포트폴리오 대비 최대 포지션 크기 (%)
    min_buy_amount: float = 1000.0  # 최소 매수 금액 (KRW)
    cooldown_hours: float = 4.0  # 동일 자산 거래 간 대기 시간 (시간)

    # AI 감정 임계값
    ai_very_bullish: float = 50.0  # 매우 강세 임계값
    ai_bullish: float = 20.0  # 강세 임계값
    ai_neutral_low: float = -20.0  # 중립(하한) 임계값
    ai_neutral_high: float = 20.0  # 중립(상한) 임계값
    ai_bearish: float = -50.0  # 약세 임계값
    ai_very_bearish: float = -80.0  # 매우 약세 임계값


class AssetManager:
    """거래 대상과 그 생명주기를 관리합니다."""

    def __init__(self, parameters: Optional[TradingParameters] = None):
        self.parameters = parameters or TradingParameters()
        self.targets: Dict[str, TradingTarget] = {}
        self._init_targets()

    def _init_targets(self):
        """기본 거래 대상을 초기화합니다."""
        # 해외 대상 (Phase 1 - 분수 거래)
        self.targets[TARGET_TICKER] = TradingTarget(
            symbol=TARGET_TICKER,
            name=TARGET_TICKER_NAME,
            asset_type=AssetType.OVERSEAS_FRACTIONAL,
            target_allocation=self.parameters.overseas_buy_ratio
        )

    def update_target_price(self, symbol: str, price: float):
        """거래 대상의 가격을 갱신합니다."""
        if symbol in self.targets:
            self.targets[symbol].price = price

    def get_target(self, symbol: str) -> Optional[TradingTarget]:
        """심볼로 거래 대상을 조회합니다."""
        return self.targets.get(symbol)

    def get_all_targets(self) -> Dict[str, TradingTarget]:
        """모든 거래 대상을 조회합니다."""
        return self.targets.copy()


class PositionSizer:
    """거래 대상별 포지션 크기를 계산합니다."""

    def __init__(self, parameters: Optional[TradingParameters] = None):
        self.parameters = parameters or TradingParameters()

    def calculate_overseas_position_size(
        self, total_assets: float, cash_available: float
    ) -> float:
        """
        총 자산과 현금을 기준으로 해외 포지션 크기를 계산합니다.

        Args:
            total_assets: 총 포트폴리오 가치 (KRW)
            cash_available: 사용 가능한 현금 (KRW)

        Returns:
            KRW 기준 포지션 크기
        """
        # 총 자산 기준 목표 포지션
        target_position = total_assets * self.parameters.overseas_buy_ratio

        # 사용 가능한 현금 한도 적용
        position_size = min(target_position, cash_available * 0.95)  # 현금의 95% 사용

        # 최소 매수 금액 적용
        position_size = max(position_size, self.parameters.min_buy_amount)

        # 최대 포지션 크기 제약 적용
        max_allowed = total_assets * self.parameters.max_position_size_ratio
        position_size = min(position_size, max_allowed)

        return position_size

    def calculate_domestic_position_size(
        self, total_assets: float, cash_available: float, existing_domestic_value: float = 0.0
    ) -> float:
        """
        국내 ETF 포지션 크기를 계산합니다.

        Args:
            total_assets: 총 포트폴리오 가치 (KRW)
            cash_available: 사용 가능한 현금 (KRW)
            existing_domestic_value: 현재 국내 포지션 평가액

        Returns:
            KRW 기준 포지션 크기
        """
        # 목표 국내 배분 비중
        target_domestic_ratio = self.parameters.domestic_buy_ratio
        target_domestic_value = total_assets * target_domestic_ratio

        # 초과 배분 방지
        additional_needed = max(0, target_domestic_value - existing_domestic_value)

        # 사용 가능한 현금 한도 적용
        position_size = min(additional_needed, cash_available * 0.95)

        # 최소 매수 금액 적용
        position_size = max(position_size, self.parameters.min_buy_amount)

        return position_size


class SignalGenerator:
    """기술적·기본적 분석을 기반으로 거래 신호를 생성합니다."""

    def __init__(self, parameters: Optional[TradingParameters] = None):
        self.parameters = parameters or TradingParameters()

    def generate_signal(
        self, target: TradingTarget, ai_score: float, is_mock: bool = True
    ) -> SignalType:
        """
        대상에 대한 거래 신호를 생성합니다.

        Args:
            target: 거래 대상
            ai_score: AI 감정 점수 (-100 ~ +100)
            is_mock: 모의 거래 모드 여부

        Returns:
            거래 신호
        """
        # 보유 중이 아니면 매수 신호 확인
        if not target.is_long:
            return self._generate_buy_signal(target, ai_score, is_mock)

        # 보유 중이면 매도 신호 확인
        return self._generate_sell_signal(target)

    def _generate_buy_signal(
        self, target: TradingTarget, ai_score: float, is_mock: bool
    ) -> SignalType:
        """대상에 대한 매수 신호를 생성합니다."""
        # AI 감정 확인
        if ai_score <= self.parameters.ai_very_bearish:
            return SignalType.HOLD  # 너무 약세이므로 대기

        # 가격 확인 (DIP 임계값)
        dip_threshold = (
            self.parameters.overseas_dip_threshold
            if target.asset_type == AssetType.OVERSEAS_FRACTIONAL
            else self.parameters.domestic_stop_loss_threshold
        )

        if target.avg_cost > 0:
            current_drop = (target.price - target.avg_cost) / target.avg_cost * 100
        else:
            current_drop = 0  # 최초 매수이므로 어떤 가격도 허용

        price_ok = current_drop <= dip_threshold

        # AI 점수 확인
        ai_ok = ai_score >= self.parameters.overseas_ai_score_floor

        if price_ok and ai_ok:
            return SignalType.BUY
        else:
            return SignalType.HOLD

    def _generate_sell_signal(self, target: TradingTarget) -> SignalType:
        """대상에 대한 매도 신호를 생성합니다."""
        # 익절 확인
        take_profit_threshold = (
            self.parameters.overseas_take_profit_threshold
            if target.asset_type == AssetType.OVERSEAS_FRACTIONAL
            else self.parameters.domestic_take_profit_threshold
        )

        if target.unrealized_pnl_rate >= take_profit_threshold:
            return SignalType.SELL

        # 손절 확인
        stop_loss_threshold = (
            self.parameters.domestic_stop_loss_threshold
            if target.asset_type == AssetType.DOMESTIC_ETF
            else self.parameters.overseas_dip_threshold  # 해외 DIP와 동일하게 사용
        )

        if target.unrealized_pnl_rate <= stop_loss_threshold:
            return SignalType.SELL

        return SignalType.HOLD


class PortfolioAnalyzer:
    """포트폴리오 구성을 분석하고 권고안을 제공합니다."""

    def __init__(self, asset_manager: AssetManager, parameters: Optional[TradingParameters] = None):
        self.asset_manager = asset_manager
        self.parameters = parameters or TradingParameters()

    def get_portfolio_allocation(self, total_assets: float) -> Dict[str, float]:
        """
        현재 포트폴리오 배분을 조회합니다.

        Args:
            total_assets: 총 포트폴리오 가치

        Returns:
            심볼별 배분 비중(%) 딕셔너리
        """
        allocation = {}
        for symbol, target in self.asset_manager.get_all_targets().items():
            if target.is_long:
                allocation[symbol] = target.market_value / total_assets * 100

        return allocation

    def get_rebalancing_recommendation(
        self, total_assets: float, cash_available: float
    ) -> Dict[str, SignalType]:
        """
        모든 대상에 대한 리밸런싱 권고안을 조회합니다.

        Args:
            total_assets: 총 포트폴리오 가치
            cash_available: 사용 가능한 현금

        Returns:
            심볼별 권고 신호 딕셔너리
        """
        recommendations = {}
        position_sizer = PositionSizer(self.parameters)

        for symbol, target in self.asset_manager.get_all_targets().items():
            # 현금은 건너뜀
            if target.asset_type == AssetType.CASH:
                continue

            # 현재 배분과 목표 배분 비교
            current_value = target.market_value
            target_value = total_assets * target.target_allocation

            # 괴리율과 시장 상황에 따라 신호 생성
            ai_score = 0  # AI 분석기에서 갱신될 예정

            if current_value < target_value * 0.8:  # 목표의 80% 미만
                recommendations[symbol] = SignalType.BUY
            elif current_value > target_value * 1.2:  # 목표의 120% 초과
                recommendations[symbol] = SignalType.SELL
            else:
                recommendations[symbol] = SignalType.HOLD

        return recommendations

    def calculate_risk_metrics(self) -> Dict[str, float]:
        """포트폴리오 위험 지표를 계산합니다."""
        targets = self.asset_manager.get_all_targets()

        if not targets:
            return {
                "total_value": 0.0,
                "largest_position": 0.0,
                "concentration_risk": 0.0,
                "diversification_score": 0.0
            }

        # 총 포트폴리오 가치 계산
        total_value = sum(
            t.market_value for t in targets.values() if t.is_long
        )

        if total_value == 0:
            return {
                "total_value": 0.0,
                "largest_position": 0.0,
                "concentration_risk": 0.0,
                "diversification_score": 0.0
            }

        # 최대 포지션 찾기
        largest_position = max(
            (t.market_value for t in targets.values() if t.is_long),
            default=0.0
        )

        # 집중 위험 계산 (총액 대비 최대 포지션 비중)
        concentration_risk = (largest_position / total_value) * 100

        # 단순 분산 점수 (보유 종목 수 / 목표 종목 수)
        num_positions = sum(1 for t in targets.values() if t.is_long)
        target_num_positions = 3  # 좋은 분산을 위해 3-5개 종목 목표
        diversification_score = min(num_positions / target_num_positions, 1.0) * 100

        return {
            "total_value": total_value,
            "largest_position": largest_position,
            "concentration_risk": concentration_risk,
            "diversification_score": diversification_score
        }
