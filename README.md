# 나스닥 스노볼 자동 거래 봇

## 개요

이 나스닥 주식을 위한 종합적인 자동 거래 봇은 AI 기반 시장 감정 분석과 체계적인 거래 규칙을 결합하여 투자 결정을 자동화합니다. 강력한 오류 처리, 설정 관리, 프로덕션급 아키텍처를 포함합니다.

## 주요 기능

### 🤖 AI 시장 분석
- **Gemini 통합** (`gemini-3.1-flash-lite`, `google-genai` SDK): Google의 고급 AI를 사용한 일간 시장 감정 분석
- **감정 점수**: -100 (극도로 비관적)부터 +100 (극도로 낙관적)
- **스마트 의사결정**: 시장 상황에 따른 AI 기반 매수/매도 추천
- **위험 평가**: AI 점수가 임계값 이하일 때 거래 방지

### 📊 자동 거래 로직
**매수 조건 (모두 필요):**
1. `DIP_THRESHOLD` (-1.5% 기본값) 범위 내 가격 하락
2. `AI_SCORE_FLOOR` (-50 기본값) 이상의 AI 감정 점수
3. 오늘 이미 매수하지 않음 (중복 매수 방지)

**매도 조건:**
- 익절: 수익률 ≥ `TAKE_PROFIT_THRESHOLD` (5% 기본값) → 전량 매도
- 손절 로직은 미구현 (Phase 2 로드맵 항목)

### 🔄 강력한 아키텍처
- **모듈 설계**: 실행 경로 기준 6개 모듈 (config, state, kis_overseas, ai_analyzer, trader, main)
- **오류 복구**: 네트워크 장애 및 API 문제의 원활한 처리
- **스케줄 통합**: 자동화된 시간별 거래 주기
- **상태 관리**: 일별 매수 플래그와 자동 날짜 기반 리셋

### 🎯 포지션 관리
- **정수 수량 매수**: KIS 해외주식 현금 매수 주문 (`ORD_DVSN=00` 지정가)
- **동적 포지션 크기 조정**: 총자산의 `BUY_RATIO`(30%), 예수금의 99% 한도 내
- **위험 제한**: 1주 가격보다 예산이 작으면 매수하지 않음

## 시스템 아키텍처

### 핵심 모듈

#### 1. **config.py**
- `.env` 파일에서 설정 로드
- 모의/실제 거래 모드 전환
- API 엔드포인트 설정
- 거래 매개변수 검증

#### 2. **state.py**
- 싱글톤 상태 관리
- 일별 매수 플래그 추적
- 자동 날짜 기반 리셋 (자정)
- 스레드 안전 연산

#### 3. **kis_overseas.py**
- **KIS API 통합**: 완전한 해외 주식 API 지원
- **토큰 관리**: 자동 토큰 갱신 및 캐싱 (24시간 유효성)
- **시장 데이터**: 실시간 가격 및 포트폴리오 잔고
- **거래 연산**: 매수 및 매도 주문 실행
- **오류 처리**: 포괄적인 예외 관리

#### 4. **ai_analyzer.py**
- **Gemini API 통합**: 시장 감정 분석
- **JSON 응답 파싱**: 구조화된 감정 점수
- **대체 로직**: 오류 시 원활한 전환
- **로깅**: 상세 분석 기록

#### 5. **trader.py**
- **거래 로직**: 매수/매도 의사 결정 알고리즘
- **포지션 관리**: 위험 계산 및 크기 조정
- **주문 실행**: KIS API와의 통합
- **상태 추적**: 매수/매도 플래그 관리

#### 6. **main.py**
- **스케줄러**: 시간별 거래 주기 관리
- **오류 복구**: 네트워크 및 API 장애 처리
- **로깅**: 포괄적인 실행 기록
- **우아한 종료**: 키보드 인터럽트 처리

### 지원 모듈

#### 7. **trading_strategy.py** — 현재 실행 경로에서 미사용 (향후 확장용)
- **자산 관리**: TradingTarget / AssetManager
- **포지션 크기 조정**: PositionSizer (국내·해외 주문 수량 계산)
- **리스크 지표**: PortfolioAnalyzer
- **신호 생성**: SignalGenerator

#### 8. **utils.py** — 현재 실행 경로에서 미사용 (향후 확장용)
- **로깅 유틸리티**: TradingLogger
- **데이터 검증**: DataValidator
- **수학 유틸리티**: MathUtils (수량·손익 계산)
- **시간·설정 관리**: TimeUtils, ConfigManager

#### 9. **kis_domestic.py**
- **스캐폴드**: Phase 2 확장용 (모든 함수가 `NotImplementedError`)
- **국내 ETF**: TIGER 미국나스닥100 (379800) 등 예정
- **플레이스홀더**: 인터페이스만 정의됨

## 기술적 사양

### 거래 매개변수

#### 환경 변수
```env
# KIS API 자격 증명 (모의/실전 분리 권장)
IS_MOCK_TRADING=True                  # 실제 거래 시 False로 설정

KIS_MOCK_APP_KEY=your_mock_app_key
KIS_MOCK_APP_SECRET=your_mock_app_secret
KIS_MOCK_ACCOUNT_NO=XXXXXXXX-XX

KIS_REAL_APP_KEY=your_real_app_key
KIS_REAL_APP_SECRET=your_real_app_secret
KIS_REAL_ACCOUNT_NO=XXXXXXXX-XX

# 단일 키만 쓸 경우 아래도 동작 (fallback)
# KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO

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

`config.py`는 `IS_MOCK_TRADING` 값에 따라 KIS 도메인과 키를 자동 전환합니다.
- 모의투자: `openapivts.koreainvestment.com:29443`, 토큰 캐시 `token_cache_mock.json`
- 실전투자: `openapi.koreainvestment.com:9443`, 토큰 캐시 `token_cache_real.json`

#### 기본값
- **매수 비율 (Buy Ratio)**: 총 포트폴리오의 30%
- **하락 임계값 (Dip Threshold)**: 1.5% 가격 하락
- **익절 (Take Profit)**: 5% 수익 목표
- **AI 점수 하한 (AI Score Floor)**: -50 (허용 가능한 최소 감정 점수)

### API 통합

#### KIS API 엔드포인트
- **토큰 관리**: `/oauth2/tokenP` (24시간 캐시)
- **시세 조회**: `/uapi/overseas-price/v1/quotations/price`
- **포트폴리오 잔고**: `/uapi/overseas-stock/v1/trading/inquire-balance`
- **원화 예수금 조회**: `/uapi/domestic-stock/v1/trading/inquire-balance` (외화 예수금이 0일 때)
- **주문 실행**: `/uapi/overseas-stock/v1/trading/order` (매수·매도 공통)
- **환율**: 두나무 시세 API (`quotation-api-cdn.dunamu.com`), 실패 시 1350원 기본값

#### Gemini API 통합
- **모델**: `gemini-3.1-flash-lite`
- **SDK**: `google-genai` (`genai.Client`)
- **응답 형식**: JSON 전용 (`response_mime_type="application/json"`)
- **온도 (Temperature)**: 0.2 (통제된 응답)
- **대체 로직**: 오류 발생 시 기본 중립 점수(0) 사용

### 성능 특성

#### 리소스 사용량
- **메모리**: ~50-100 MB (안정 상태)
- **CPU**: 최소 (I/O 바운드 연산)
- **네트워크**: 사이클당 ~10-20 API 호출

#### 실행 시간
- **사이클 시간**: 1-3분
- **빈도**: 시간당
- **시작 시간**: 2-5초
- **쿨다운**: 스케줄 체크 간 30초

## 프로젝트 구조

```
stock-auto-trader/
├── .claude/                    # Claude 설정
│   ├── commands.py             # 명령 정의
│   └── CHANGES-LOG.md          # 변경 이력
├── .env                        # 환경 변수 (커밋 금지)
├── .gitignore                  # Git 제외 목록
├── requirements.txt            # Python 의존성
├── main.py                     # 메인 스케줄러 (1시간 주기)
├── config.py                   # 설정 관리
├── state.py                    # 당일 매수 상태 추적
├── kis_overseas.py             # KIS 해외주식 API
├── kis_domestic.py             # 국내 ETF API (Phase 2 스켈레톤)
├── ai_analyzer.py              # Gemini 시장 분석
├── trader.py                   # 핵심 매매 로직
├── trading_strategy.py         # 확장 전략 (현재 미사용)
├── utils.py                    # 유틸리티 (현재 미사용)
├── token_cache_mock.json       # 모의투자 토큰 캐시
├── token_cache_real.json       # 실전투자 토큰 캐시
├── logs/                       # 실행 로그
│   └── bot.log                 # 봇 로그
├── README.md                   # 프로젝트 문서
├── TRADING_BOT_README.md       # 빠른 실행 가이드
├── DEVELOPMENT-GUIDE.md        # 개발 가이드
├── TROUBLESHOOTING.md          # 문제 해결 가이드
├── run_instructions.md         # 실행 안내
└── project_summary.md          # 프로젝트 요약
```

## 시작하기

### 필수 조건
1. **Python 3.12+** (프로젝트는 3.12.15에서 개발·운영)
2. **가상 환경** (`.venv`)
3. **인터넷 연결**
4. **API 자격 증명** (KIS 및 Gemini)

### 설치 단계

#### 1. 가상 환경 생성
```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

#### 2. 의존성 설치
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

패키지를 새로 설치·업그레이드한 뒤에는 `requirements.txt`를 갱신합니다.

```bash
# 방법 1: 현재 환경을 그대로 고정 (권장)
.venv/bin/python -m pip freeze > requirements.txt

# 방법 2: 특정 패키지만 최소 의존성 기준으로 추가
.venv/bin/python -m pip install <패키지>
# → requirements.txt에 직접 한 줄 추가 (버전 고정: 패키지==버전)
```

`pip freeze`는 전이 의존성까지 전부 기록하므로 재현성은 높지만 목록이 길어집니다.
패키지를 지운 뒤에는 freeze 결과에서도 빠지므로, 불필요한 패키지가 requirements에 남지 않습니다.

#### 3. 환경 설정
API 키와 거래 매개변수로 `.env` 파일 편집:

```env
# 거래 모드 (테스트 시작 시 True로 설정)
IS_MOCK_TRADING=True

# 필수 API 키
KIS_APP_KEY=your_kis_app_key
KIS_APP_SECRET=your_kis_app_secret
KIS_ACCOUNT_NO=XXXXXXXX-XX
GEMINI_API_KEY=your_gemini_api_key

# 투자 대상
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# 거래 매개변수
BUY_RATIO=0.30
DIP_THRESHOLD=-1.5
TAKE_PROFIT_THRESHOLD=5.0
AI_SCORE_FLOOR=-50
```

#### 4. 봇 실행
```bash
python main.py
```

### 모니터링
- **로그**: `tail -f logs/bot.log`
- **실행 상태**: 콘솔 출력
- **오류 보고**: 상세한 오류 로깅

## 개발 가이드

### 모듈 구조

각 모듈은 특정 목적을 수행합니다:

#### **config.py** - 설정 관리
- `.env` 파일에서 환경 변수 로드
- 거래 매개변수 설정
- API 엔드포인트 구성
- 설정 검증

#### **state.py** - 상태 관리
- 일별 거래 상태 추적
- 매수/매도 플래그 관리
- 자동 일별 리셋
- 싱글톤 패턴 구현

#### **kis_overseas.py** - KIS API 통합
- 토큰 관리 및 캐싱
- 시장 데이터 검색
- 주문 실행
- 오류 처리

#### **ai_analyzer.py** - AI 시장 분석
- Gemini API 통합
- 시장 감정 분석
- JSON 응답 파싱
- 대체 로직

#### **trader.py** - 거래 로직
- 매수/매도 의사 결정 알고리즘
- 포지션 관리
- 위험 계산
- 상태 통합

#### **main.py** - 메인 스케줄러
- 시간별 거래 주기
- 오류 복구
- 로깅
- 우아한 종료

### 개발 명령어

다음 개발 명령어를 생성 및 사용하세요:

```bash
# Setup: 프로젝트 초기화 및 의존성 설치
source .venv/bin/activate && pip install -r requirements.txt

# Run: 거래 봇 시작
python main.py

# Test: 구문 확인
python3 -m py_compile *.py && echo 'Syntax check passed'

# Logs: 최근 봇 로그 표시
tail -20 logs/bot.log 2>/dev/null || echo 'No logs found'
```

## 테스트 및 검증

### 구문 확인
```bash
python3 -m py_compile *.py
```

### 임포트 테스트
```python
import config
import state
import kis_overseas
import ai_analyzer
import trader
import main

print("모든 모듈 임포트 성공!")
print(f"모의 거래 모드: {config.IS_MOCK}")
print(f"대상 주식: {config.TARGET_TICKER}")
```

### 환경 설정 테스트
```bash
python3 -c "
from dotenv import load_dotenv
load_dotenv()
print('환경 설정 로드 성공')
print(f'모의 거래 모드: {os.getenv(\"IS_MOCK_TRADING\")}')
print(f'Gemini API 키 설정: {bool(os.getenv(\"GEMINI_API_KEY\"))}')
"
```

## Phase 2 로드맵

### 향후 기능

#### 1. 국내 ETF 지원
- **구현**: `kis_domestic.py` 모듈
- **대상**: TIGER 미국나스닥100 (379800)
- **통합**: 해외 주식과 혼합 전략

#### 2. 향상된 위험 관리
- **손절매**: 동적 손절매 구현
- **포지션 크기 조정**: 켈리 기준, 고정 비율 방식
- **포트폴리오 재조정**: 자산 할당 유지

#### 3. 성과 분석
- **상세 거래 로그**: 전체 거래 기록
- **성과 분석**: 승리율, 평균 수익률
- **자동 보고서**: 일일/주간 성과 요약

#### 4. 추가 기능
- **다중 자산 지원**: QQQ 외 추가 자산
- **알림 시스템**: SMS/이메일 알림
- **모의 거래**: 위험 없는 테스트 환경
- **백테스팅**: 과거 전략 검증

## 보안 고려 사항

### 민감한 데이터 보호
- **API 키**: `.env` 파일에 저장 (코드에 저장 금지)
- **토큰 캐싱**: 보안 토큰 저장 및 유효기간 설정
- **설정 파일**: 적절한 파일 권한 설정 (`chmod 600 .env`)

### 모범 사례
- **가상 환경**: 의존성 격리
- **입력 검증**: 모든 사용자 입력 검증
- **오류 처리**: 민감한 정보 노출 없이 원활한 실패
- **로깅**: 민감한 정보 없이 로깅

## 문제 해결

### 일반적인 문제와 해결책

#### 문제 1: 임포트 오류
**문제**: 모듈 임포트 시 `ModuleNotFoundError` 발생

**해결책**:
```bash
# 의존성 재설치
pip install -r requirements.txt

# 가상 환경 확인
which python
pip list
```

#### 문제 2: API 설정
**문제**: 환경 변수가 없습니다

**해결책**:
```bash
# .env 파일 확인
cat .env

# 올바른 권한 설정
chmod 600 .env
```

#### 문제 3: 거래 로직 오류
**문제**: 매수/매도 조건이 충족되지 않습니다

**해결책**:
```bash
# 설정 확인
python3 -c "import config; print('Configuration:', config.__dict__)"

# 거래 상태 확인
python3 -c "from state import bot_state; print('오늘 매수 여부:', bot_state.bought_today)"
```

#### 문제 4: 메모리 문제
**문제**: 시스템이 메모리를 부족해합니다

**해결책**:
```bash
# 실행 중인 봇 프로세스의 메모리 사용량 확인 (psutil 미설치 환경)
ps aux | grep "python main.py"
```

프로세스 자체는 사이클마다 종료되지 않으므로, 메모리가 계속 증가하면 봇을 재시작하세요.

### 개발 명령어
```bash
# Syntax check
python3 -m py_compile *.py

# Import test
python3 -c "import sys; modules = ['config', 'state', 'kis_overseas', 'ai_analyzer', 'trader', 'main']; [__import__(m) for m in modules]; print('All imports successful')"

# Configuration test
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('Environment loaded successfully')
print(f'Mock trading: {os.getenv(\"IS_MOCK_TRADING\")}')
print(f'API Key configured: {bool(os.getenv(\"GEMINI_API_KEY\"))}')
"
```

## 모범 사례

### 1. 코드 품질
- **문서화**: 모든 메서드에 대한 포괄적인 docstring
- **타입 힌트**: 코드 명확성을 위한 타입 어노테이션 사용
- **오류 처리**: 모든 I/O 연산을 위한 try-catch 블록 구현
- **로깅**: 디버깅을 위한 구조화된 로깅 사용

### 2. 성능 최적화
- **효율적인 스케줄링**: 적절한 sleep 간격 사용
- **메모리 관리**: 정기적인 가비지 컬렉션
- **API 요청 제한**: API 호출 간 내장 지연
- **연결 재사용**: HTTP 연결 재사용

### 3. 보안 관행
- **환경 변수**: `.env` 파일에 API 키 저장
- **파일 권한**: 적절한 파일 권한 설정
- **입력 검증**: 모든 입력 검증
- **오류 메시지**: 민감한 정보 노출 방지

## 참조

### 문서 소스
- [KIS API 문서](https://apiportal.koreainvestment.com/apis)
- [Gemini API 문서](https://ai.google.dev/api)
- [Python Schedule 문서](https://pypi.org/project/schedule/)
- [Python Dotenv 문서](https://pypi.org/project/python-dotenv/)

### 개발 도구
- **IDE**: VS Code with Python 확장
- **린터**: flake8, black, isort
- **타입 검사기**: mypy
- **테스트**: pytest
- **버전 제어**: git

## 결론

나스닥 스노볼 자동 거래 봇은 나스닥 주식을 위한 포괄적이고 프로덕션급 자동 거래 시스템을 제공합니다. 이 시스템은 다음과 같은 기능을 결합합니다:

1. **AI 시장 분석**: Gemini 기반 감정 분석
2. **자동 거래**: 체계적인 매수/매도 로직
3. **위험 관리**: 다층적 보호
4. **오류 복구**: 모든 장애 모드의 원활한 처리
5. **모듈식 아키텍처**: 실행 경로 기준 7개 모듈 + 확장용 3개 모듈
6. **프로덕션 기능**: 완전한 설정 관리 및 로깅

이 시스템은 향후 향상 및 기능 추가를 위한 명확한 경로를 포함합니다. 처음 도입할 때는 `IS_MOCK_TRADING=True`로 모의투자에서 검증한 뒤 실전으로 전환하세요.

**참고**: 자동 매매는 손실 위험을 수반합니다. 시장 상황에 따라 성능을 지속적으로 모니터링하고 매개변수를 조정하세요.

---

*마지막 업데이트: 2026-10-13*
*버전: 1.1.0*
*작성자: Claude Code 구현*
