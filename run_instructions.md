# 나스닥 스노볼 자동 거래 봇 - 실행 안내

## 빠른 시작 가이드

이 가이드는 나스닥 스노볼 자동 거래 봇을 실행하기 위한 단계별 안내를 제공합니다.

## 사전 요구 사항

### 1. Python 환경
- Python 3.8+ (3.14.0에서 테스트됨)
- 선택 사항: 가상 환경 (`.venv`) 권장

### 2. 필요한 패키지
다음 패키지가 필요합니다 (`requirements.txt`에 명시됨):

```txt
requests==2.32.3
python-dotenv==1.0.1
schedule==1.2.2
google-generativeai==0.7.2
```

## 빠른 시작 단계

### 1단계: 프로젝트 설정

#### 옵션 A: 가상 환경 사용 (권장)
```bash
# Navigate to project directory
cd /Users/jia/Documents/project/stock-auto-trader

# Create virtual environment
python3 -m venv .venv

# Activate virtual environment (Linux/Mac)
source .venv/bin/activate

# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

#### 옵션 B: 전역 설치 (권장하지 않음)
```bash
cd /Users/jia/Documents/project/stock-auto-trader
pip install requests python-dotenv schedule google-generativeai
```

### 2단계: 환경 설정

#### 2.1 `.env` 파일 생성 또는 편집
```bash
cd /Users/jia/Documents/project/stock-auto-trader

# If .env doesn't exist, create it
nano .env
```

다음 설정을 추가하세요:

```env
# KIS API Authentication
KIS_APP_KEY=your_kis_app_key_here
KIS_APP_SECRET=your_kis_app_secret_here
KIS_ACCOUNT_NO=12345678-01       # 계좌번호 (XXXXXXXX-XX 형식)

# 모의투자=True / 실전투자=False
IS_MOCK_TRADING=True              # Set to False for real trading

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key_here

# 투자 대상 종목 (해외)
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# 매매 파라미터
BUY_RATIO=0.30                   # 총자산 대비 1회 매수 비율 (30%)
DIP_THRESHOLD=-1.5               # 매수 트리거 하락률 (%)
TAKE_PROFIT_THRESHOLD=5.0        # 익절 수익률 (%)
AI_SCORE_FLOOR=-50               # AI 점수 하한 (이 미만이면 매수 중지)
```

**중요:**
- `your_kis_app_key_here`를 실제 KIS API 키로 교체하세요
- `your_kis_app_secret_here`를 실제 KIS API 시크릿으로 교체하세요
- `your_gemini_api_key_here`를 실제 Gemini API 키로 교체하세요
- 테스트 시에는 `IS_MOCK_TRADING=True`로 시작하세요
- 충분히 테스트한 후 실제 거래를 위해 `IS_MOCK_TRADING=False`로 설정하세요

#### 2.2 파일 권한
```bash
# Set secure permissions for .env file
chmod 600 .env
```

### 3단계: 환경 검증

다음 테스트를 실행하여 환경 설정을 확인하세요:
```bash
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()
print('=== Environment Validation ===')
print(f'Mock Trading Mode: {os.getenv(\"IS_MOCK_TRADING\")}')
print(f'KIS API Key: {bool(os.getenv(\"KIS_APP_KEY\"))}')
print(f'KIS API Secret: {bool(os.getenv(\"KIS_APP_SECRET\"))}')
print(f'KIS Account No: {os.getenv(\"KIS_ACCOUNT_NO\")}')
print(f'Gemini API Key: {bool(os.getenv(\"GEMINI_API_KEY\"))}')
print(f'Target Ticker: {os.getenv(\"TARGET_TICKER\", \"QQQ\")}')
print('=== Validation Complete ===')
"
```

### 4단계: 구문 및 임포트 테스트

다음 종합 테스트를 실행하세요:
```bash
python3 -c "
import sys
print('=== Syntax and Import Test ===')
print(f'Python version: {sys.version}')

# Test imports
try:
    import config
    print('✓ config.py imports successfully')
except Exception as e:
    print(f'✗ config.py import failed: {e}')
    sys.exit(1)

try:
    import state
    print('✓ state.py imports successfully')
except Exception as e:
    print(f'✗ state.py import failed: {e}')
    sys.exit(1)

try:
    import kis_overseas
    print('✓ kis_overseas.py imports successfully')
except Exception as e:
    print(f'✗ kis_overseas.py import failed: {e}')
    sys.exit(1)

try:
    import ai_analyzer
    print('✓ ai_analyzer.py imports successfully')
except Exception as e:
    print(f'✗ ai_analyzer.py import failed: {e}')
    sys.exit(1)

try:
    import trader
    print('✓ trader.py imports successfully')
except Exception as e:
    print(f'✗ trader.py import failed: {e}')
    sys.exit(1)

try:
    import main
    print('✓ main.py imports successfully')
except Exception as e:
    print(f'✗ main.py import failed: {e}')
    sys.exit(1)

print('=== All Tests Passed ===')
"
```

### 5단계: 봇 실행

#### 5.1 빠른 테스트 모드
최초 테스트 시에는 빠른 테스트 모드로 봇을 실행할 수 있습니다:
```bash
python main.py
```

봇은 다음을 수행합니다:
1. 모든 설정을 검증하는 빠른 테스트 실행
2. 거래 사이클 1회 실행
3. 다음 시간별 사이클까지 대기

#### 5.2 프로덕션 모드
지속적인 자동 거래를 위해서는:
```bash
python main.py
```

봇은 다음을 수행합니다:
1. 시작 즉시 실행
2. 시간별 거래 사이클 실행
3. 네트워크 장애를 원활하게 처리
4. 중단될 때까지 계속 실행

### 6단계: 모니터링 및 로그

#### 6.1 현재 로그 보기
```bash
# Show recent bot activity
tail -20 logs/bot.log

# View error messages
grep "ERROR" logs/bot.log | tail -10

# Check trading activity
grep "buy\|sell" logs/bot.log | tail -5
```

#### 6.2 실시간 모니터링
```bash
# Monitor logs in real-time
tail -f logs/bot.log
```

### 7단계: 우아한 종료

봇은 다음과 같은 방법으로 중지할 수 있습니다:
- **키보드 인터럽트**: 터미널에서 `Ctrl+C` 누르기
- **SIGTERM**: `kill` 명령어 (Linux/macOS)

## 고급 사용법

### 7.1 설정 변경
거래 매개변수를 변경하려면 `.env` 파일을 편집하고 봇을 재시작하세요:

```bash
# Stop the bot (if running)
Ctrl+C

# Edit configuration
nano .env

# Restart bot
python main.py
```

### 7.2 모의 거래 대 실제 거래
- **모의 거래** (`IS_MOCK_TRADING=True`): 테스트에 안전하며 모의 API를 사용합니다
- **실제 거래** (`IS_MOCK_TRADING=False`): 실제 API를 사용한 실거래입니다

### 7.3 환경 변수

| 변수 | 설명 | 기본값 | 필수 |
|----------|-------------|---------|----------|
| `IS_MOCK_TRADING` | 모의 거래 활성화/비활성화 | `True` | 아니오 |
| `KIS_APP_KEY` | KIS API 키 | `None` | 예 |
| `KIS_APP_SECRET` | KIS API 시크릿 | `None` | 예 |
| `KIS_ACCOUNT_NO` | KIS 계좌번호 | `None` | 예 |
| `GEMINI_API_KEY` | Gemini API 키 | `None` | 예 |
| `TARGET_TICKER` | 거래 대상 티커 | `QQQ` | 아니오 |
| `BUY_RATIO` | 거래당 포트폴리오 배분 비율 | `0.30` | 아니오 |
| `DIP_THRESHOLD` | 가격 하락 트리거 (%) | `-1.5` | 아니오 |
| `TAKE_PROFIT_THRESHOLD` | 익절 임계값 (%) | `5.0` | 아니오 |
| `AI_SCORE_FLOOR` | 거래를 위한 최소 AI 점수 | `-50` | 아니오 |

## 문제 해결

### 오류: ModuleNotFoundError
**문제**: 필요한 Python 패키지가 설치되지 않음
**해결책**:
```bash
pip install -r requirements.txt
```

### 오류: 잘못된 KIS API 키
**문제**: KIS 인증 실패
**해결책**:
1. KIS API 키와 시크릿이 올바른지 확인하세요
2. KIS 계좌에 적절한 권한이 있는지 확인하세요
3. KIS 계좌가 분수 주식 거래를 지원하는지 확인하세요

### 오류: 잘못된 Gemini API 키
**문제**: Gemini API 인증 실패
**해결책**:
1. Gemini API 키가 올바른지 확인하세요
2. Gemini 계정에 결제가 활성화되어 있는지 확인하세요
3. Gemini API 키에 필요한 권한이 있는지 확인하세요

### 오류: 잘못된 .env 설정
**문제**: 설정 파일 형식이 올바르지 않음
**해결책**:
```bash
# Check .env file
cat .env

# Fix any syntax errors
nano .env
```

### 오류: 포트가 이미 사용 중
**문제**: 봇이 이미 실행 중임
**해결책**:
```bash
# Find running bot process
ps aux | grep python | grep main.py

# Stop the bot
kill <PID>
```

## 개발 모드

### 개발 명령어
개발을 위해 다음 명령어를 사용할 수 있습니다:

```bash
# Setup project and install dependencies
source .venv/bin/activate && pip install -r requirements.txt

# Run the bot
python main.py

# Test Python syntax
python3 -m py_compile *.py && echo 'Syntax check passed'

# View recent logs
tail -20 logs/bot.log 2>/dev/null || echo 'No logs found'
```

### 개발 워크플로우

1. **개발**: `IS_MOCK_TRADING=True` 상태에서 `python main.py` 사용
2. **테스트**: 변경 전에 종합 테스트 실행
3. **검증**: 배포 전에 모든 설정 확인
4. **배포**: 충분히 테스트한 후에만 `IS_MOCK_TRADING=False`로 테스트

## 모범 사례

### 1. 보안
- `.env` 파일을 버전 관리에 절대 커밋하지 마세요
- 적절한 파일 권한을 사용하세요 (`chmod 600 .env`)
- 공개 저장소에 API 키를 절대 공유하지 마세요

### 2. 설정
- 테스트 시에는 `IS_MOCK_TRADING=True`로 시작하세요
- 실제 거래를 활성화하기 전에 충분히 테스트하세요
- 실제 거래 중에는 봇을 정기적으로 모니터링하세요

### 3. 모니터링
- `logs/bot.log`를 정기적으로 확인하세요
- 시스템 리소스를 모니터링하세요
- 오류에 대한 알림을 설정하세요

### 4. 유지보수
- 설정 변경 후에는 봇을 재시작하세요
- 봇 성능을 정기적으로 모니터링하세요
- 의존성을 최신 상태로 유지하세요

## 지원

### 일반적인 문제
일반적인 문제와 해결책은 `DEVELOPMENT-GUIDE.md` 파일을 참조하세요.

### 도움 받기
1. 오류 메시지가 있으면 로그를 확인하세요
2. 모든 설정 값을 검증하세요
3. 구문 및 임포트 테스트를 실행하세요
4. 개발 가이드를 참조하세요

## 빠른 참조

### 요약 명령어

```bash
# Quick setup and run
source .venv/bin/activate && pip install -r requirements.txt && python main.py

# Configuration validation
python3 -c "import config; print(f'Mock Trading: {config.IS_MOCK}')"

# Basic test
python3 -c "import config, state, ai_analyzer, trader, main; print('All imports successful')"
```

### 비상 명령어

```bash
# Stop the bot (if running)
Ctrl+C

# Restart the bot after configuration changes
python main.py

# Clean restart
pkill -f "python main.py" && python main.py
```

## 결론

이 자동 거래 봇은 AI 기반 시장 분석과 함께 나스닥 자동 거래를 위한 종합적인 솔루션을 제공합니다. 이 안내를 따르면 테스트 및 프로덕션 환경 모두에서 봇을 성공적으로 설정, 구성, 실행할 수 있습니다.

**기억하세요**:
- 항상 `IS_MOCK_TRADING=True`로 먼저 테스트하세요
- 시스템에 확신이 들기 전까지 실제 자금으로 거래하지 마세요
- 특히 실제 거래 중에는 봇을 정기적으로 모니터링하세요
- API 키를 안전하게 보관하고 절대 공개적으로 공유하지 마세요

나스닥 스노볼 자동 거래 봇은 이제 자동 거래에 바로 사용할 준비가 되었습니다!

---

*문서 버전: 1.0.0*
*마지막 업데이트: 2025-01-02*
*작성자: Claude Code 구현*
