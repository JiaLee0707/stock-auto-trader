"""
자동 거래 봇을 위한 유틸리티 모듈.

이 모듈은 다음을 포함합니다:
1. 로깅 구성 및 관리
2. 파일 처리 (JSON, YAML, CSV)
3. 데이터 검증 및 타입 확인
4. 거래 계산을 위한 수학 유틸리티
5. 시간 및 날짜 유틸리티
6. 설정 파일 유틸리티
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Dict, List, Any, Optional, Union

from config import TOKEN_CACHE_FILE, LOG_FILE


class TradingLogger:
    """거래 봇 로깅을 구성하고 관리합니다."""

    def __init__(self, name: str = "bot", log_file: Optional[str] = None):
        self.name = name
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)

        # 중복 핸들러 방지
        if self.logger.handlers:
            self.logger.handlers.clear()

        # 콘솔 핸들러
        console_handler = logging.StreamHandler(sys.stdout)
        console_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # 파일 핸들러
        if log_file:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setFormatter(console_formatter)
            self.logger.addHandler(file_handler)

    def get_logger(self) -> logging.Logger:
        return self.logger

    def set_level(self, level: int):
        self.logger.setLevel(level)

    def add_file_handler(self, log_file: str):
        """로거에 파일 핸들러를 추가합니다."""
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding='utf-8')

        # 콘솔 핸들러에서 기존 포매터 가져오기
        console_formatter = None
        for handler in self.logger.handlers:
            if handler.stream == sys.stdout:
                console_formatter = handler.formatter
                break

        if console_formatter:
            file_handler.setFormatter(console_formatter)
            self.logger.addHandler(file_handler)

    @staticmethod
    def setup_bot_logging(
        name: str = "bot",
        log_file: Optional[str] = None,
        level: int = logging.INFO
    ) -> logging.Logger:
        """표준 봇 로깅 구성을 설정합니다."""
        logger = TradingLogger(name, log_file).get_logger()
        logger.setLevel(level)
        return logger


class DataValidator:
    """거래 데이터를 검증하고 정제합니다."""

    @staticmethod
    def validate_number(value: Any, min_val: float = float('-inf'), max_val: float = float('inf')) -> float:
        """지정된 범위 내의 숫자인지 검증하고 반환합니다."""
        try:
            num = float(value)
            if min_val <= num <= max_val:
                return num
            else:
                raise ValueError(f"Value {num} out of range [{min_val}, {max_val}]")
        except (ValueError, TypeError):
            raise ValueError(f"Invalid number: {value}")

    @staticmethod
    def validate_positive(value: Any, min_positive: float = 0.0) -> float:
        """양수인지 검증하고 반환합니다."""
        num = float(value)
        if num >= min_positive:
            return num
        else:
            raise ValueError(f"Value {num} must be >= {min_positive}")

    @staticmethod
    def validate_range(value: float, min_val: float, max_val: float) -> float:
        """값이 지정된 범위 내에 있는지 검증합니다."""
        if min_val <= value <= max_val:
            return value
        else:
            raise ValueError(f"Value {value} out of range [{min_val}, {max_val}]")

    @staticmethod
    def sanitize_symbol(symbol: str) -> str:
        """거래 심볼을 정제합니다."""
        return symbol.strip().upper()

    @staticmethod
    def validate_asset_type(asset_type: str) -> str:
        """자산 유형을 검증합니다."""
        valid_types = ['overseas', 'domestic', 'cash', 'crypto']
        asset_type = asset_type.lower()
        if asset_type not in valid_types:
            raise ValueError(f"Invalid asset type: {asset_type}")
        return asset_type


class FileManager:
    """거래 봇의 파일 작업을 관리합니다."""

    def __init__(self, base_path: str = "."):
        self.base_path = Path(base_path).resolve()

    def ensure_directory(self, path: str) -> Path:
        """디렉토리가 존재하도록 보장하고 Path 객체를 반환합니다."""
        dir_path = self.base_path / path
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def read_json(self, file_path: str) -> Dict[str, Any]:
        """JSON 파일을 읽습니다."""
        full_path = self.base_path / file_path
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {file_path}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {file_path}: {e}")

    def write_json(self, file_path: str, data: Dict[str, Any]) -> None:
        """JSON 파일을 씁니다."""
        full_path = self.base_path / file_path
        self.ensure_directory(str(full_path.parent))
        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_cache(self, key: str) -> Any:
        """JSON 캐시 파일에서 값을 읽습니다."""
        try:
            data = self.read_json(TOKEN_CACHE_FILE)
            return data.get(key)
        except (FileNotFoundError, ValueError):
            return None

    def write_cache(self, key: str, value: Any) -> None:
        """JSON 캐시 파일에 값을 씁니다."""
        try:
            data = self.read_json(TOKEN_CACHE_FILE)
        except (FileNotFoundError, ValueError):
            data = {}

        data[key] = value
        self.write_json(TOKEN_CACHE_FILE, data)


class MathUtils:
    """거래 계산을 위한 수학 유틸리티."""

    @staticmethod
    def calculate_percentage_change(old_value: float, new_value: float) -> float:
        """두 값 사이의 변화율을 계산합니다."""
        if old_value == 0:
            return 0.0
        return ((new_value - old_value) / old_value) * 100

    @staticmethod
    def calculate_position_size(
        total_assets: float,
        percentage: float,
        min_amount: float = 0.0,
        max_amount: float = float('inf')
    ) -> float:
        """
        총 자산 대비 비율로 포지션 크기를 계산합니다.

        Args:
            total_assets: 총 포트폴리오 가치
            percentage: 목표 비율 (0-100)
            min_amount: 최소 포지션 크기
            max_amount: 최대 포지션 크기

        Returns:
            포지션 크기
        """
        position = total_assets * (percentage / 100)
        position = max(position, min_amount)
        position = min(position, max_amount)
        return position

    @staticmethod
    def calculate_profit_loss(
        entry_price: float,
        current_price: float,
        quantity: float,
        transaction_cost_rate: float = 0.001
    ) -> Dict[str, float]:
        """
        포지션의 손익을 계산합니다.

        Args:
            entry_price: 단위당 진입 가격
            current_price: 단위당 현재 가격
            quantity: 수량
            transaction_cost_rate: 거래 비용률 (기본 0.1%)

        Returns:
            손익 상세 정보 딕셔너리
        """
        gross_pnl = (current_price - entry_price) * quantity
        transaction_cost = gross_pnl * transaction_cost_rate
        net_pnl = gross_pnl - transaction_cost

        if entry_price > 0:
            pnl_percentage = (net_pnl / (entry_price * quantity)) * 100
        else:
            pnl_percentage = 0.0

        return {
            'gross_pnl': gross_pnl,
            'net_pnl': net_pnl,
            'pnl_percentage': pnl_percentage,
            'transaction_cost': transaction_cost
        }

    @staticmethod
    def round_to_precision(value: float, precision: int) -> float:
        """지정된 소수점 자릿수로 값을 반올림합니다."""
        if precision < 0:
            return value

        multiplier = 10 ** precision
        return round(value * multiplier) / multiplier

    @staticmethod
    def clamp_value(value: float, min_val: float, max_val: float) -> float:
        """값을 최소값과 최대값 사이로 제한합니다."""
        return max(min_val, min(value, max_val))


class TimeUtils:
    """거래를 위한 시간 관련 유틸리티."""

    @staticmethod
    def get_current_kst() -> datetime:
        """현재 시각을 KST (Korea Standard Time)로 반환합니다."""
        from datetime import timezone
        import pytz

        kst = timezone(timedelta(hours=9))
        return datetime.now(kst)

    @staticmethod
    def format_kst(dt: datetime) -> str:
        """datetime을 KST 문자열로 포맷합니다."""
        kst = timezone(timedelta(hours=9))
        kst_dt = dt.astimezone(kst)
        return kst_dt.strftime('%Y-%m-%d %H:%M:%S KST')

    @staticmethod
    def get_date_range(
        days: int,
        start_date: Optional[datetime] = None
    ) -> List[datetime]:
        """
        범위 내의 날짜 목록을 가져옵니다.

        Args:
            days: 일수
            start_date: 시작 날짜 (기본: 오늘)

        Returns:
            날짜 목록
        """
        if start_date is None:
            start_date = datetime.now()

        return [start_date - timedelta(days=i) for i in range(days)]

    @staticmethod
    def is_market_hours(dt: datetime, timezone_str: str = 'Asia/Seoul') -> bool:
        """
        해당 시각이 장중인지 확인합니다.

        Args:
            dt: 확인할 datetime
            timezone_str: 타임존 문자열

        Returns:
            장중이면 True (한국 시장 기준 9:00-15:30 가정)
        """
        import pytz

        tz = pytz.timezone(timezone_str)
        local_dt = dt.astimezone(tz)

        # 한국 시장 시간: 9:00-15:30 (주말 제외)
        market_open = local_dt.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = local_dt.replace(hour=15, minute=30, second=0, microsecond=0)

        return market_open <= local_dt <= market_close and local_dt.weekday() < 5


class ConfigManager:
    """설정 파일 작업을 관리합니다."""

    def __init__(self, config_file: str = '.env'):
        self.config_file = config_file
        self.config = {}
        self._load_config()

    def _load_config(self) -> None:
        """파일에서 설정을 로드합니다."""
        if os.path.exists(self.config_file):
            from dotenv import load_dotenv
            load_dotenv(self.config_file)

            # 공통 거래 설정 변수 로드
            self.config = {
                'KIS_APP_KEY': os.getenv('KIS_APP_KEY'),
                'KIS_APP_SECRET': os.getenv('KIS_APP_SECRET'),
                'KIS_ACCOUNT_NO': os.getenv('KIS_ACCOUNT_NO'),
                'GEMINI_API_KEY': os.getenv('GEMINI_API_KEY'),
                'TARGET_TICKER': os.getenv('TARGET_TICKER', 'QQQ'),
                'IS_MOCK_TRADING': os.getenv('IS_MOCK_TRADING', 'True').lower() == 'true',
                'BUY_RATIO': float(os.getenv('BUY_RATIO', '0.30')),
                'DIP_THRESHOLD': float(os.getenv('DIP_THRESHOLD', '-1.5')),
                'TAKE_PROFIT_THRESHOLD': float(os.getenv('TAKE_PROFIT_THRESHOLD', '5.0')),
                'AI_SCORE_FLOOR': int(os.getenv('AI_SCORE_FLOOR', '-50')),
                'TOKEN_CACHE_FILE': os.getenv('TOKEN_CACHE_FILE', TOKEN_CACHE_FILE),
                'LOG_FILE': os.getenv('LOG_FILE', LOG_FILE)
            }

    def get(self, key: str, default: Any = None) -> Any:
        """설정 값을 가져옵니다."""
        return self.config.get(key, default)

    def get_all(self) -> Dict[str, Any]:
        """모든 설정 값을 가져옵니다."""
        return self.config.copy()

    def update(self, key: str, value: Any) -> None:
        """설정 값을 갱신합니다."""
        self.config[key] = value

    def save(self) -> None:
        """설정을 파일에 저장합니다."""
        with open(self.config_file, 'w') as f:
            for key, value in self.config.items():
                f.write(f'{key}={value}\n')


# 손쉬운 사용을 위한 유틸리티 인스턴스 생성
logger = TradingLogger.setup_bot_logging()
config_manager = ConfigManager()
file_manager = FileManager()
math_utils = MathUtils()
time_utils = TimeUtils()
validator = DataValidator()
