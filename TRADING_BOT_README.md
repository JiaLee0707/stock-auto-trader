# 나스닥 스노우볼 자동매매 봇 - 빠른 설정 및 실행 가이드

## 🚀 빠른 시작

이 봇은 AI 기반 시장 심리 분석과 원칙 기반의 매매 규칙을 결합하여 나스닥 주식(QQQ 등) 매매를 자동화합니다.

### 사전 요구사항
- Python 3.12 이상 설치 (3.12.15에서 개발·운영)
- 인터넷 연결
- Git

### 1단계: 프로젝트 클론
```bash
git clone <your-repo-url>
cd stock-auto-trader
```

### 2단계: 가상환경 생성
```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 3단계: 의존성 패키지 설치
```bash
pip install --upgrade pip
pip install -r requirements.txt

# 새 패키지를 설치한 뒤에는 requirements.txt 갱신
.venv/bin/python -m pip freeze > requirements.txt
```

### 4단계: 환경 설정 (선택 사항이지만 권장)
```bash
# .env 파일에 API 키 입력 (테스트 시 모의투자 모드로 시작)
nano .env
```

### 5단계: 환경 변수 설정
```bash
# Windows PowerShell:
$env:IS_MOCK_TRADING="True"
$env:KIS_APP_KEY="your_kis_app_key"
$env:KIS_APP_SECRET="your_kis_app_secret"
$env:KIS_ACCOUNT_NO="XXXXXXXX-XX"
$env:GEMINI_API_KEY="your_gemini_api_key"

# 또는 .env 파일에 설정:
IS_MOCK_TRADING=True
KIS_APP_KEY=your_kis_app_key
KIS_APP_SECRET=your_kis_app_secret
KIS_ACCOUNT_NO=XXXXXXXX-XX
GEMINI_API_KEY=your_gemini_api_key
```

### 6단계: 봇 실행
```bash
python main.py
```

## 🛠️ 개발 및 실행 명령어

### 빠른 실행 (Claude Code 명령어 사용)
```bash
# 초기화: 가상환경 활성화 및 의존성 설치
source .venv/bin/activate && pip install -r requirements.txt

# 실행: 매매 봇 시작
python main.py

# 테스트: 문법 검사
python3 -m py_compile *.py && echo 'Syntax check passed'

# 로그: 최근 봇 로그 확인
tail -20 logs/bot.log 2>/dev/null || echo 'No logs found'
```

### 수동 테스트
```bash
# 문법 검사
python3 -m py_compile *.py

# 임포트 테스트
python3 -c "
import config, state, kis_overseas, ai_analyzer, trader, main
print('All imports successful!')
print(f'Mock trading: {config.IS_MOCK}')
print(f'Target ticker: {config.TARGET_TICKER}')
"

# 환경 변수 로드 테스트
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('Environment loaded successfully')
print(f'Mock trading: {os.getenv(\"IS_MOCK_TRADING\")}')
print(f'API Key configured: {bool(os.getenv(\"GEMINI_API_KEY\"))}')
"
```

## 📊 봇 설정

### 기본 매매 파라미터
```env
# 모드: 모의투자는 "True", 실전투자는 "False" (테스트 시 모의투자 권장)
IS_MOCK_TRADING=True

# 한국투자증권(KIS) API 인증 정보
KIS_APP_KEY=your_kis_app_key
KIS_APP_SECRET=your_kis_app_secret
KIS_ACCOUNT_NO=XXXXXXXX-XX

# Gemini AI API Key
GEMINI_API_KEY=your_gemini_api_key

# 운용 시장 (미지정 시 모의=True, 실전=False)
IS_OVERSEAS_ENABLED=True

# 투자 대상 종목
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# 매매 파라미터 (1회 매수 비중 × MAX_BUYS_PER_DAY = 하루 최대 노출)
BUY_RATIO=0.15                    # 해외 1회 매수 비중 (15% × 2회 = 하루 최대 30%)
DOMESTIC_BUY_RATIO=0.10           # 국내 1회 매수 비중 (10% × 2회 = 하루 최대 20%)
DIP_THRESHOLD=-1.5                # 평단가 대비 -1.5% 하락 시 매수
AI_SCORE_FLOOR=-50                # 매수를 위한 최소 AI 시장 심리 점수

# 스노우볼 전략
MAX_BUYS_PER_DAY=2                # 종목당 하루 최대 매수 횟수
TAKE_PROFIT_STEPS=2.0:33,4.0:33,6.0:100   # 부분 익절 (수익률%:매도비중%)
STOP_LOSS_THRESHOLD=-5.0          # 손절선 (%), 0 이하면 비활성

# 보조지표 (RSI + 볼린저밴드) — 매수·매도 타점
USE_INDICATORS=True
RSI_PERIOD=14
RSI_OVERSOLD=30                   # RSI 이하 + 볼린저 하단 이탈 → 매수
RSI_OVERBOUGHT=70                 # RSI 이상 + 볼린저 상단 돌파 → 익절 트리거
BB_PERIOD=20
BB_NUM_STD=2.0
```

## 🔧 주요 기능

### 🤖 AI 시장 분석
- **Gemini** (`gemini-3.1-flash-lite`): 일일 시장 뉴스 및 심리 분석
- **감정 점수 산정**: -100(극단적 약세) ~ +100(극단적 강세)
- **스마트 매매 필터링**: AI 심리 점수가 양호할 때만 매수 진입

### 📈 자동 매매 (스노우볼)
- **분할 매수**: 가격 하락(Dip) + AI 심리 점수 충족 시, 1회 비중만큼 매수하고 하락이 이어지면 하루 최대 2회까지 추가 매수
- **부분 익절**: +2% 1/3, +4% 1/3, +6% 전량 — 단계별 분할 매도로 포지션을 유지하며 수익 실현
- **손절**: -5% 이하 시 전량 매도 (기본 활성)
- **정수 수량 매수**: 1회 예산 내에서 주문 수량 계산 (KIS 지정가 주문)
- **동시 운용**: 해외(QQQ)와 국내 ETF(TIGER 미국나스닥100, 379800)를 각각 독립적으로 판단
- **리스크 관리**: 1회 포지션 크기 제한 및 일일 매수 횟수 제한

### 🛡️ 안전 장치
- **모의투자 모드**: 실자본 투입 전 가상 매매 테스트
- **오류 복구**: 네트워크/API 장애 발생 시 자동 예외 처리 및 복구
- **일일 제한**: 1개 종목당 하루 최대 `MAX_BUYS_PER_DAY`(2)회 매수
- **수량 제한**: 예산이 1주 가격보다 작으면 매수하지 않음 (최소/최대 포지션 상한은 미구현)
- **정규장 제한(모의투자 전용)**: 모의투자는 정규장 주문만 체결되므로 모의투자에서만 해외(09:30~16:00 ET) / 국내(09:00~15:30 KST) 정규장에 주문 실행. 실전투자는 시간외 주문도 가능해 제한하지 않음
- **통합증거금**: 국내 원화 + 외화 예수금을 합산한 금액을 주문가능금으로 사용

## 📋 매매 로직

### 매수 조건 (모두 만족해야 함):
1. **진입 타점** — 둘 중 하나
   - **지표 (기본)**: RSI(14) ≤ RSI_OVERSOLD(30) **이고** 종가 ≤ 볼린저 하단(20, 2σ)
   - **하락률 (폴백, 일봉 조회 실패 시)**: 현재가 ≤ 평단가 × (1 + DIP_THRESHOLD/100)
2. **AI 심리 점수**: AI score ≥ AI_SCORE_FLOOR (-50 이상)
3. **당일 매수 횟수**: 오늘 매수 횟수 < MAX_BUYS_PER_DAY (기본 2회)
4. **매수 간격**: 직전 매수 후 MIN_BUY_INTERVAL_HOURS(2h) 경과 (2회차부터)

### 매도 조건 (우선순위 순):
1. **손절**: 수익률 ≤ STOP_LOSS_THRESHOLD (-5.0% 이하) → 전량 매도
2. **부분 익절**: 아래 둘 중 하나라도 해당하면 다음 단계 실행 — **수익 중일 때만**
   - 수익률이 TAKE_PROFIT_STEPS 단계 도달 (+2.0% → 1/3, +4.0% → 1/3, +6.0% → 전량)
   - 지표 과매수: RSI(14) ≥ RSI_OVERBOUGHT(70) **이고** 종가 ≥ 볼린저 상단
     · 이 경우엔 **한 번에 한 단계만** 매도 (수익률 미달 상태에서 전량 청산 방지)
   - 급등으로 수익률이 여러 단계를 건너뛰면 도달한 단계를 한 번에 모두 실행
   - 각 단계는 당일 1회씩만 실행 (수익률이 다시 올라도 중복 매도 없음)

## 📁 프로젝트 구조

```
stock-auto-trader/
├── .claude/                    # Claude 설정 파일
│   ├── commands.py             # 개발 명령어 모음
│   └── CHANGES-LOG.md          # 변경 이력 관리
├── .env                        # 환경 변수 파일 (커밋 금지)
├── .gitignore                  # Git 추적 제외 목록
├── requirements.txt            # Python 의존성 목록
├── main.py                     # 메인 실행 파일 (스케줄러)
├── config.py                   # 설정 관리
├── state.py                    # 당일 매수 상태 추적
├── kis_overseas.py              # KIS 해외주식 API 연동
├── kis_domestic.py              # KIS 국내주식 API 연동 (해외와 동시 운용)
├── ai_analyzer.py              # Gemini AI 시장 분석
├── trader.py                   # 핵심 매매 실행 로직
├── trading_strategy.py          # 확장 매매 전략 (현재 미사용)
├── utils.py                     # 유틸리티 함수 (현재 미사용)
├── token_cache_mock.json       # 모의투자 토큰 캐시
├── token_cache_real.json       # 실전투자 토큰 캐시
├── logs/                        # 실행 로그 디렉터리
│   └── bot.log                 # 봇 실행 로그 파일
├── README.md                   # 프로젝트 전체 문서
├── TRADING_BOT_README.md       # 빠른 설정 및 실행 가이드
├── DEVELOPMENT-GUIDE.md        # 개발 가이드
├── TROUBLESHOOTING.md          # 문제 해결 가이드
├── project_summary.md           # 프로젝트 요약 보고서
└── run_instructions.md         # 상세 실행 가이드
```

## 🎯 주요 파라미터

| 파라미터 | 기본값 | 설명 |
|-----------|-------|-------------|
| **Target Ticker** | QQQ | Invesco QQQ Trust (나스닥 100 ETF) |
| **Domestic Ticker** | 379800 | TIGER 미국나스닥100 (해외와 동시 운용) |
| **Buy Ratio** | 해외 15% / 국내 10% | 1회 매수 비중 (× 하루 최대 2회 = 하루 최대 30% / 20%) |
| **Max Buys Per Day** | 2 | 종목당 하루 최대 매수 횟수 |
| **Dip Threshold** | -1.5% | 평단가 대비 1.5% 이상 하락 시 추가 매수 |
| **Take Profit Steps** | +2%/+4%/+6% | 1/3 → 1/3 → 전량 단계별 부분 익절 |
| **Stop Loss** | -5.0% | 도달 시 전량 매도 (0 이하면 비활성) |
| **AI Score Floor** | -50 | 매수 허용을 위한 최소 AI 점수 하한선 |

## 🚀 운영 가이드

### 봇 실행
```bash
python main.py
```

### 로그 모니터링
```bash
tail -f logs/bot.log
```

### 빠른 테스트 (모의투자 모드)
첫 실행 시 다음 과정이 확인됩니다:
1. 모든 모듈 로드 및 설정 검증
2. AI 분석기 동작 테스트 (API 키 미설정 시 기본값 0 반환)
3. KIS API 연동 확인 (모의투자)
4. 매매 사이클 정상 동작 확인

### 안전한 종료
- 터미널에서 `Ctrl+C` 입력 시 즉시 안전 종료
- 종료 전 상태 저장 및 종료 로그 기록

## 🔍 문제 해결 (Troubleshooting)

### 주요 오류 및 조치

**1. "ModuleNotFoundError: No module named 'requests'"**
```bash
pip install -r requirements.txt
```

**2. API 키 설정 문제**
```bash
# .env 파일 존재 확인
cat .env

# 파일 권한 설정
chmod 600 .env
```

**3. 매매 로직 동작 점검**
```bash
# 설정값 출력
python3 -c "import config; print('Configuration:', {k: v for k, v in config.__dict__.items() if not k.startswith('__')})"

# 당일 매수 상태 점검
python3 -c "from state import bot_state; print('Bought today:', bot_state.bought_today)"
```

## 📊 모니터링

### 시스템 자원 확인
```bash
# 메모리 사용량
free -h

# 디스크 여유 공간
df -h

# 실행 중인 파이썬 프로세스
ps aux | grep python
```

### 봇 로그 검색
```bash
# 실시간 로그 확인
tail -f logs/bot.log

# 최근 발생한 에러 확인
grep "ERROR" logs/bot.log | tail -10

# 매수/매도 내역 확인
grep "buy\|sell" logs/bot.log | tail -5
```

## 🌟 2단계(Phase 2) 로드맵

### 예정된 향상

1. **고급 리스크 관리**
   - 동적 트레일링 스탑(Trailing Stop) 구현
   - 켈리 공식 기반 포지션 사이징
   - 포트폴리오 리밸런싱

2. **성과 분석 및 알림**
   - 상세 거래 내역 데이터베이스화
   - 일일/주간 수익률 보고서 자동 생성
   - 텔레그램/슬랙 알림 연동

3. **추가 기능**
   - QQQ 외 다중 자산 지원
   - SMS/email 알림 시스템
   - 페이퍼 트레이딩 환경
   - 백테스트 기능

## 🏆 우수 개발 사례

### 보안
- `.env` 파일에 API 키 저장 (코드에 절대 저장하지 않음)
- `chmod 600 .env`로 파일 권한 설정
- 가상 환경을 통한 격리

### 개발
- 실전 매매 전 모의투자 환경에서 테스트
- 구조화된 커밋 메시지로 버전 관리
- 정기적인 로그 모니터링으로 문제 점검

### 매매
- 소액으로 시작
- 지속적인 성과 모니터링
- 시장 상황에 따라 파라미터 조정

## 📞 도움말 얻기

### 문서
- README.md (프로젝트 전체 문서)
- DEVELOPMENT-GUIDE.md (개발 프로세스)
- TROUBLESHOOTING.md (문제 해결)
- run_instructions.md (실행 안내)
- project_summary.md (프로젝트 개요)

### 지원
- 로그 확인으로 에러 메시지 점검
- 설정값 재확인
- 모의투자 환경으로 테스트
- 인터넷 연결 확인

---

**참고**: 처음 도입 시에는 모의투자 환경에서 충분히 검증한 뒤 실전으로 전환하세요.

**마지막 업데이트**: 2026-10-13
**버전**: 1.1.0