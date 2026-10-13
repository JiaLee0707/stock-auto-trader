# 개발 가이드 - 나스닥 스노볼 자동 거래 봇

## 개요

이 가이드는 나스닥 스노볼 자동 거래 봇의 설정, 구현, 테스트 및 향후 향상 사항을 포함한 종합적인 개발 지침을 제공합니다.

## 프로젝트 구조

```
stock-auto-trader/
├── .claude/                    # Claude-specific configurations
│   ├── commands.py             # Command definitions
│   └── CHANGES-LOG.md          # Version control
├── .env                        # Environment configuration
├── .gitignore                  # Git ignore patterns
├── requirements.txt            # Python dependencies
├── main.py                     # Main trading bot
├── config.py                   # Configuration management
├── state.py                    # State tracking
├── kis_overseas.py              # KIS API integration
├── kis_domestic.py             # Domestic ETF API (Phase 2)
├── ai_analyzer.py              # AI market analysis
├── trader.py                   # Core trading logic
├── trading_strategy.py          # Advanced trading strategies
├── utils.py                     # Utility functions
├── token_cache.json            # API token cache
├── logs/                        # Execution logs
│   └── bot.log                 # Trading bot logs
├── README.md                   # Project documentation
└── README_FINAL.md             # Technical specifications
```

## 시작하기

### 필수 조건

1. **Python 3.8+**
   ```bash
   $ python3 --version
   Python 3.14.0
   ```

2. **Git 설치**
   ```bash
   $ git --version
   git version 2.43.0
   ```

3. **선택 사항: VS Code**
   - 개발에 권장
   - 확장 프로그램: Python, Pylance

### 초기 설정

#### 1. 프로젝트 디렉토리 생성
```bash
$ mkdir -p ~/Documents/project/stock-auto-trader
cd ~/Documents/project/stock-auto-trader
```

#### 2. 가상 환경 초기화
```bash
# 가상 환경 생성
$ python3 -m venv .venv

# 활성화 (Linux/Mac)
$ source .venv/bin/activate

# 활성화 (Windows)
$ .venv\Scripts\activate
```

#### 3. 의존성 설치
```bash
# pip 업그레이드
$ pip install --upgrade pip

# 요구 사항 설치
$ pip install -r requirements.txt

# 설치 확인
$ pip list | grep -E "(requests|schedule|google-generativeai)"
```

### 개발 워크플로우

#### 1. 프로젝트 초기화
```bash
# 기본 프로젝트 구조 생성
$ mkdir -p logs
$ touch .env requirements.txt

# git 저장소 초기화
$ git init
$ git add .
$ git commit -m "Initial commit - project structure"
```

#### 2. 환경 설정 구성
`.env` 파일 편집:
```env
# KIS API 인증
KIS_APP_KEY=your_app_key_here
KIS_APP_SECRET=your_app_secret_here
KIS_ACCOUNT_NO=XXXXXXXX-XX

# 거래 모드
IS_MOCK_TRADING=True  # 실제 거래 시 False로 설정

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key_here

# 투자 대상
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# 거래 매개변수
BUY_RATIO=0.30
DIP_THRESHOLD=-1.5
TAKE_PROFIT_THRESHOLD=5.0
AI_SCORE_FLOOR=-50
```

#### 3. 개발 명령어
다음 개발 명령어를 생성 및 사용하세요:

**설정 명령어:**
```bash
# 프로젝트 초기화 및 의존성 설치
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

**실행 명령어:**
```bash
# 거래 봇 시작
python main.py
```

**테스트 명령어:**
```bash
# Python 구문 확인
python3 -m py_compile *.py && echo 'Syntax check passed'
```

**로그 명령어:**
```bash
# 최근 봇 로그 표시
tail -20 logs/bot.log 2>/dev/null || echo 'No logs found'
```

## 모듈 개발 가이드

### 1. 핵심 모듈 개발

#### config.py
**목적**: 설정 로드 및 관리

**주요 기능**:
- 환경 변수 로드
- 모의/실제 거래 모드 전환
- API 엔드포인트 설정
- 거래 매개변수 검증

**개발 워크플로우**:
```python
# 1. 필수 모듈 가져오기
import os
from dotenv import load_dotenv

# 2. 환경 변수 로드
load_dotenv()

# 3. 설정 구성
IS_MOCK = os.getenv("IS_MOCK_TRADING", "True").lower() == "true"

# 4. 설정 테스트
if IS_MOCK:
    print("모의 모드 실행")
else:
    print("실제 거래 모드 실행")
```

#### state.py
**목적**: 일별 거래 상태 관리 및 리셋

**주요 기능**:
- 싱글톤 패턴 구현
- 일별 매수 플래그 추적
- 자동 날짜 기반 리셋
- 스레드 안전 연산

**개발 워크플로우**:
```python
from datetime import date

class BotState:
    def __init__(self):
        self._bought_today = False
        self._flag_date = date.today()
    
    def _check_date_reset(self):
        # 일별 리셋 로직 구현
        pass
    
    @property
    def bought_today(self):
        # 프로퍼티 getter 구현
        pass
    
    @bought_today.setter
    def bought_today(self, value):
        # 프로퍼티 setter 구현
        pass
```

#### kis_overseas.py
**목적**: KIS 해외 주식 API 통합

**주요 기능**:
- 토큰 관리 및 캐싱
- 시장 데이터 검색
- 주문 실행
- 오류 처리

**개발 워크플로우**:
```python
import requests
import json
from datetime import datetime, timedelta
from pathlib import Path

class KISOverseasAPI:
    def __init__(self):
        self.base_url = "https://openapi.koreainvestment.com:9443"
        self.token_cache = Path("token_cache.json")
    
    def get_access_token(self):
        # 토큰 검색 구현
        pass
    
    def get_current_price(self, ticker):
        # 가격 검색 구현
        pass
    
    def buy_fractional(self, krw_amount):
        # 분수 거래 구현
        pass
```

#### ai_analyzer.py
**목적**: Gemini 기반 시장 감정 분석

**주요 기능**:
- Gemini API 통합
- JSON 응답 파싱
- 감정 점수 (-100에서 +100)
- 오류 처리 및 대체 로직

**개발 워크플로우**:
```python
import google.generativeai as genai
import json
from datetime import date

class AIAnalyzer:
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")
    
    def analyze_market_sentiment(self):
        # 감정 분석 구현
        pass
```

### 2. 테스트 및 검증

#### 구문 확인
```bash
# 포괄적인 구문 확인
python3 -m py_compile *.py *.pyc

# 개별 파일 확인
python3 -m py_compile config.py
python3 -m py_compile state.py
python3 -m py_compile main.py
```

#### 임포트 테스트
```python
# 모든 모듈 임포트
import config
import state
import kis_overseas
import ai_analyzer
import trader
import main

# 설정 확인
print(f"모의 거래 모드: {config.IS_MOCK}")
print(f"대상 주식: {config.TARGET_TICKER}")
```

#### 환경 설정 테스트
```bash
# 환경 설정 로드 테스트
python3 -c "
from dotenv import load_dotenv
load_dotenv()
print('API 키:', os.getenv('GEMINI_API_KEY')[:10] + '...')
print('거래 모드:', os.getenv('IS_MOCK_TRADING'))
"
```

### 3. 고급 개발

#### 모듈 통합
모듈 상호작용 테스트:
```python
from trader import run_trading_cycle
from ai_analyzer import analyze_market_sentiment

# 통합 테스트
def test_integration():
    sentiment = analyze_market_sentiment()
    ai_score = sentiment['score']
    print(f"AI 분석 결과: {ai_score}")
    
    # 트레이더와의 통합 테스트
    # 참고: 전체 테스트를 위해서는 모의 데이터가 필요합니다
    pass
```

#### 오류 처리 테스트
```python
import pytest
from unittest.mock import Mock, patch

class TestTradingBot:
    def test_buy_conditions(self):
        # 매수 신호 생성 테스트
        pass
    
    def test_sell_conditions(self):
        # 매도 신호 생성 테스트
        pass
    
    def test_error_handling(self):
        # 오류 복구 테스트
        pass
```

## 고급 개발 주제

### 1. 성능 최적화

#### 스케줄러 최적화
```python
import schedule
import time

class OptimizedScheduler:
    def __init__(self):
        self.job_count = 0
    
    def schedule_hourly_job(self, job_func):
        # 효율적인 스케줄러 사용
        schedule.every(1).hours.do(job_func)
        self.job_count += 1
    
    def run_pending_jobs(self):
        # 효율적인 작업 실행 구현
        schedule.run_pending()
        time.sleep(30)  # 30초마다 확인
```

#### 메모리 관리
```python
import psutil
import gc

class MemoryManager:
    def __init__(self):
        self.process = psutil.Process()
    
    def check_memory_usage(self):
        memory_info = self.process.memory_info()
        return memory_info.rss / 1024 / 1024  # MB
    
    def cleanup_if_needed(self):
        # 메모리 사용량이 높으면 가비지 컬렉션 강제 실행
        if self.check_memory_usage() > 100:  # 100MB 임계값
            gc.collect()
```

### 2. 보안 고려 사항

#### 환경 보안
```python
import os
from pathlib import Path

class EnvironmentManager:
    def __init__(self, env_file='.env'):
        self.env_file = Path(env_file)
    
    def load_secure_env(self):
        # 보안성을 고려해 환경 설정 로드
        if self.env_file.exists():
            # 올바른 권한 설정
            os.chmod(self.env_file, 0o600)
            # 환경 설정 로드
            from dotenv import load_dotenv
            load_dotenv(self.env_file)
    
    def validate_api_keys(self):
        # API 키 형식 검증
        required_keys = ['KIS_APP_KEY', 'KIS_APP_SECRET', 'GEMINI_API_KEY']
        for key in required_keys:
            value = os.getenv(key)
            if not value or len(value) < 10:
                raise ValueError(f"Invalid {key}")
```

### 3. 디버깅 및 모니터링

#### 로깅 구성
```python
import logging
import os
from datetime import datetime

def setup_comprehensive_logging(log_file='logs/bot.log'):
    # 로그 디렉토리 생성
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # 로깅 구성
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    return logging.getLogger('bot')
```

#### 오류 모니터링
```python
import logging
from datetime import datetime
import traceback

class ErrorMonitor:
    def __init__(self):
        self.error_count = 0
        self.last_error_time = None
        self.logger = logging.getLogger('error_monitor')
    
    def log_error(self, error, context=None):
        self.error_count += 1
        self.last_error_time = datetime.now()
        
        error_msg = f"Error #{self.error_count}: {str(error)}"
        if context:
            error_msg += f" | Context: {context}"
        
        self.logger.error(error_msg)
        self.logger.debug(traceback.format_exc())
        
        # 오류 발생률 높으면 경고
        if self.error_count > 10:
            self.logger.critical("High error rate detected")
```

## Phase 2 개발 가이드

### 1. 국내 ETF 통합 (kis_domestic.py)

#### 구현 단계
```python
# 1. 국내 ETF 모듈 구조 정의
class KISDomesticAPI:
    def __init__(self, config):
        self.config = config
        self.base_url = "https://openapi.koreainvestment.com:9443"
    
    def get_domestic_price(self, ticker):
        # 국내 가격 검색 구현
        pass
    
    def buy_domestic(self, ticker, quantity):
        # 국내 거래 구현
        pass
```

#### 설정 업데이트
```python
# 국내 거래 설정 추가
IS_DOMESTIC_ENABLED = os.getenv("IS_DOMESTIC_ENABLED", "False").lower() == "true"
DOMESTIC_TICKERS = os.getenv("DOMESTIC_TICKERS", "379800").split(',')
DOMESTIC_MAX_ALLOCATION = float(os.getenv("DOMESTIC_MAX_ALLOCATION", "0.20"))
```

### 2. 고급 거래 전략

#### 전략 패턴 구현
```python
from abc import ABC, abstractmethod
from typing import Dict, List

class TradingStrategy(ABC):
    @abstractmethod
    def should_buy(self, market_data, portfolio) -> bool:
        pass
    
    @abstractmethod
    def should_sell(self, market_data, portfolio) -> bool:
        pass
    
    @abstractmethod
    def get_position_size(self, portfolio_value) -> float:
        pass

class TrendFollowingStrategy(TradingStrategy):
    def should_buy(self, market_data, portfolio) -> bool:
        # 추세 추종 로직 구현
        pass
    
    # 다른 추상 메서드 구현
```

## 버전 관리 및 Git 워크플로우

### Git 구성
```bash
# git 사용자 설정
$ git config --global user.name "개발자 이름"
$ git config --global user.email "developer@example.com"

# main 브랜치 생성 및 전환
$ git checkout -b main
```

### 커밋 메시지 가이드라인
```
<유형>(<범위>): <설명>

<base62-sha> <메시지>

Co-authored-by: Claude Code <noreply@anthropic.com>

유형:
- feat: 새 기능
- fix: 버그 수정
- docs: 문서화
- style: 포맷팅
- refactor: 코드 리팩토링
- test: 테스트
- chore: 유지보수

범위:
- config: 설정 변경
- api: API 통합
- trading: 거래 로직
- analysis: 시장 분석
- logging: 로깅 개선
- security: 보안 강화
```

### 브랜치 전략
```bash
# 기능 브랜치 워크플로우
$ git checkout -b feature/advanced-analytics
# ... 개발 ...
$ git add .
$ git commit -m "feat(analytics): implement advanced market analysis"
$ git push origin feature/advanced-analytics

# 풀 리퀘스트 생성
# 변경 사항 설명 및 검토 요청
```

## 배포 및 운영

### 배포 명령어
```bash
# 봇 시작
source .venv/bin/activate
python main.py

# 실행 중인 프로세스 확인
ps aux | grep python

# 로그 모니터링
tail -f logs/bot.log

# 우아한 종료
terminal에서 Ctrl+C
```

### 모니터링 및 알림
```bash
# 시스템 리소스 확인
$ free -h  # 메모리
$ df -h    # 디스크 공간
$ uptime    # 시스템 상태

# 봇 로그 모니터링
cron.daily/bot-monitor.sh
```

## 프로젝트 템플릿

### 1. 개발 템플릿
```python
"""
새 개발 파일을 위한 템플릿.

목적: [간단한 설명]
작성자: [개발자 이름]
날짜: [오늘 날짜]
버전: 1.0.0
"""

import logging
from typing import Dict, Any, Optional

from config import CONFIG
from utils import validator, logger

class TemplateClass:
    """개발용 템플릿 클래스."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.logger = logging.getLogger(__name__)
    
    def method_example(self, parameter: str) -> str:
        """
        예시 메서드.
        
        인자:
            parameter: 입력 파라미터 설명
            
        반환:
            처리된 결과
        """
        try:
            # 입력 검증
            if not validator.validate_string(parameter):
                raise ValueError("Invalid parameter")
            
            # 데이터 처리
            result = self._process_data(parameter)
            
            self.logger.info(f"Successfully processed: {parameter}")
            return result
            
        except Exception as e:
            self.logger.error(f"Error processing {parameter}: {e}")
            raise
    
    def _process_data(self, data: str) -> str:
        """내부 데이터 처리 메서드."""
        # 구현 세부사항
        return data.upper()
```

### 2. 테스트 템플릿
```python
import pytest
from unittest.mock import Mock, patch
from datetime import datetime

class TestClassName:
    """테스트용 클래스 템플릿."""
    
    def setup_method(self):
        """각 테스트 전에 설정."""
        self.test_instance = ClassNameToTest()
    
    def teardown_method(self):
        """각 테스트 후 정리."""
        del self.test_instance
    
    def test_method_name(self):
        """테스트 메서드 예시."""
        # 설정
        mock_data = {"input": "test", "expected": "TEST"}
        
        # 실행
        result = self.test_instance.method_example(mock_data["input"])
        
        # 확인
        assert result == mock_data["expected"]
    
    def test_error_handling(self):
        """오류 처리 테스트."""
        # 설정
        invalid_input = ""
        
        # 실행 및 확인
        with pytest.raises(ValueError):
            self.test_instance.method_example(invalid_input)
    
    @patch('module.dependency')
    def test_with_mock(self, mock_dependency):
        """모의 의존성 테스트."""
        # 모의 설정
        mock_dependency.return_value = "mocked"
        
        # 테스트 실행
        result = self.test_instance.method_with_dependency()
        
        # 모의 호출 확인
        mock_dependency.assert_called_once()
        assert result == "mocked"
```

## 문제 해결 가이드

### 일반적인 문제와 해결책

#### 문제 1: 임포트 오류
**문제**: "ModuleNotFoundError: No module named 'requests'"

**해결책**:
```bash
# 의존성 재설치
pip install --upgrade -r requirements.txt

# 가상 환경 확인
which python
pip list
```

#### 문제 2: API 키 설정
**문제**: "환경 변수가 없습니다"

**해결책**:
```bash
# .env 파일 확인
ls -la .env
cat .env

# 올바른 권한 설정
chmod 600 .env
```

#### 문제 3: 거래 로직 오류
**문제**: "매수 조건이 충족되지 않습니다"

**해결책**:
```bash
# 설정 확인
python3 -c "import config; print('BUY_RATIO:', config.BUY_RATIO)"

# 상태 확인
python3 -c "from state import bot_state; print('오늘 매수 여부:', bot_state.bought_today)"
```

#### 문제 4: 메모리 문제
**문제**: "시스템이 메모리를 부족해합니다"

**해결책**:
```bash
# 메모리 사용량 확인
python3 -c "import psutil; p = psutil.Process(); print(f'Memory: {p.memory_info().rss / 1024 / 1024:.2f} MB')"

# 가비지 컬렉션 강제 실행
import gc
gc.collect()
```

### 디버깅 명령어
```bash
# 구문 확인
python3 -m py_compile *.py

# 임포트 테스트
python3 -c "import sys; modules = ['config', 'state', 'kis_overseas', 'ai_analyzer', 'trader', 'main']; [__import__(m) for m in modules]; print('모든 모듈 임포트 성공')"

# 환경 설정 테스트
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('환경 설정 로드 성공')
print(f'모의 거래 모드: {os.getenv(\"IS_MOCK_TRADING\")}')
print(f'Gemini API 키 설정: {bool(os.getenv(\"GEMINI_API_KEY\"))}')
"
```

## 모범 사례

### 1. 코드 품질
- **문서화**: 모든 공개 메서드에 대한 포괄적인 docstring
- **타입 힌트**: 코드 명확성을 높이기 위한 타입 어노테이션 사용
- **오류 처리**: 모든 I/O 연산을 위한 try-catch 블록 구현
- **로깅**: 디버깅과 모니터링을 위한 구조화된 로깅 사용

### 2. 성능 최적화
- **효율적인 스케줄링**: 적절한 sleep 간격 사용
- **메모리 관리**: 정기적인 가비지 컬렉션
- **API 요청 제한**: API 호출 간 지연 구현
- **연결 풀링**: 가능한 경우 HTTP 연결 재사용

### 3. 보안 관행
- **환경 변수**: `.env` 파일에 API 키 저장
- **파일 권한**: 민감한 파일에 적절한 권한 설정
- **입력 검증**: 모든 사용자 입력 검증
- **오류 메시지**: 오류 메시지에 민감한 정보 노출 금지

### 4. 테스트 전략
- **단위 테스트**: 개별 컴포넌트를 격리하여 테스트
- **통합 테스트**: 모듈 간 상호작용 테스트
- **오류 테스트**: 오류 조건과 경계 사례 테스트
- **성능 테스트**: 부하 조건에서의 테스트

## 참조

### 문서 소스
1. [KIS API 문서](https://apiportal.koreainvestment.com/apis)
2. [Gemini API 문서](https://ai.google.dev/api)
3. [Python Schedule 문서](https://pypi.org/project/schedule/)
4. [Python Dotenv 문서](https://pypi.org/project/python-dotenv/)

### 개발 도구
- **IDE**: VS Code with Python extension
- **린터**: flake8, black, isort
- **타입 검사기**: mypy
- **테스트**: pytest, unittest
- **버전 관리**: git

### 온라인 리소스
- **KIS 개발자 포털**: API 문서 및 예제
- **Claude Code 문서**: 구현 가이드라인
- **Python 모범 사례**: PEP 8, PEP 257

## 결론

이 개발 가이드는 나스닥 스노볼 자동 거래 봇을 개발하기 위한 종합적인 지침을 제공합니다. 다음 내용을 다룹니다:

1. **설정 및 설치**: 완전한 프로젝트 초기화
2. **모듈 개발**: 각 모듈에 대한 상세 가이드
3. **테스트 및 검증**: 포괄적인 테스트 전략
4. **고급 주제**: 성능, 보안, 디버깅
5. **Phase 2 로드맵**: 향후 개발 방향
6. **모범 사례**: 코드 품질 및 개발 표준

이 가이드는 프로젝트가 발전함에 따라 업데이트되는 살아있는 문서입니다. 항상 각 작업의 구체적인 요구사항과 지침을 따르고, 개발 전반에 걸쳐 일관된 코딩 표준을 유지하세요.

**다음 단계**:
1. 초기 설정 및 기본 테스트 완료
2. 핵심 거래 모듈 구현
3. 고급 거래 전략 추가
4. 모니터링 및 알림 설정
5. 프로덕션 배포 준비

나스닥 스노볼 자동 거래 봇은 향후 개선 및 기능 추가를 위한 명확한 경로와 함께 개발 준비가 완료되었습니다.

---

*문서 버전: 1.0.0*
*마지막 업데이트: 2025-01-02*
*작성자: Claude Code 구현*
*연락처: 교육 목적 전용*
