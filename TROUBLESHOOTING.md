# 나스닥 스노볼 자동 거래 봇 - 문제 해결 가이드

이 문서는 나스닥 스노볼 자동 거래 봇의 종합적인 문제 해결 정보를 제공합니다.

## 빠른 참조

| 문제 | 증상 | 해결 방법 |
|-------|----------|----------|
| 모듈을 찾을 수 없음 | `ModuleNotFoundError: No module named 'requests'` | 의존성 설치: `pip install -r requirements.txt` |
| 잘못된 API 키 | KIS 또는 Gemini 인증 실패 | `.env` 파일의 API 키 확인 |
| 가상 환경 | `python: not found` 또는 `pip: not found` | 가상 환경 생성: `python3 -m venv .venv` |
| 파일 권한 | `.env` 접근 시 Permission denied | 권한 설정: `chmod 600 .env` |
| 임포트 오류 | 여러 모듈의 임포트 실패 | Python 버전 확인 후 모든 의존성 설치 |

## 일반적인 문제와 해결책

### 문제 1: Python 환경이 설정되지 않음

**증상:**
```
python: not found
ModuleNotFoundError: No module named 'requests'
```

**해결책:**

#### 옵션 A: 가상 환경 생성
```bash
# Navigate to project directory
cd /Users/jia/Documents/project/stock-auto-trader

# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
# Linux/Mac
source .venv/bin/activate
# Windows
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

#### 옵션 B: 전역 설치 (권장하지 않음)
```bash
cd /Users/jia/Documents/project/stock-auto-trader
pip install requests python-dotenv schedule google-generativeai
```

**확인:**
```bash
python3 -c "import requests, schedule, google.generativeai, dotenv; print('All packages installed successfully')"
```

### 문제 2: .env 파일이 없거나 잘못됨

**증상:**
```
Error: Environment variable not found
ValueError: invalid
```

**해결책:**

#### 2.1 .env 파일 존재 여부 확인
```bash
# Check if .env file exists
cd /Users/jia/Documents/project/stock-auto-trader
ls -la .env

# If it doesn't exist, create it
cat > .env << 'EOF'
# KIS API Authentication
KIS_APP_KEY=your_kis_app_key_here
KIS_APP_SECRET=your_kis_app_secret_here
KIS_ACCOUNT_NO=12345678-01

# Trading Mode
IS_MOCK_TRADING=True

# Gemini AI
GEMINI_API_KEY=your_gemini_api_key_here

# Investment Target
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# Trading Parameters
BUY_RATIO=0.30
DIP_THRESHOLD=-1.5
TAKE_PROFIT_THRESHOLD=5.0
AI_SCORE_FLOOR=-50
EOF
```

#### 2.2 올바른 권한 설정
```bash
# Set secure permissions for .env file
chmod 600 .env
```

#### 2.3 .env 내용 확인
```bash
# Check the content of .env file
cat .env
```

### 문제 3: 가상 환경이 생성되지 않음

**증상:**
```
python: not found
pip: not found
```

**해결책:**

#### 3.1 가상 환경 생성
```bash
cd /Users/jia/Documents/project/stock-auto-trader

# Check if Python3 is available
python3 --version

# Create virtual environment
python3 -m venv .venv
```

#### 3.2 가상 환경 활성화
```bash
# Linux/Mac
source .env/bin/activate
# or
. .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 문제 4: 의존성 누락

**증상:**
```
ModuleNotFoundError: No module named 'schedule'
ModuleNotFoundError: No module named 'google-generativeai'
```

**해결책:**
```bash
# Activate virtual environment (if not already activated)
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

**전역 Python을 사용하는 경우:**
```bash
cd /Users/jia/Documents/project/stock-auto-trader
pip install requests python-dotenv schedule google-generativeai
```

### 문제 5: 임포트 오류

**증상:**
```
ImportError: cannot import name 'xyz' from 'module'
ModuleNotFoundError: No module named 'xyz'
```

**해결책:**

#### 5.1 Python 버전 확인
```bash
python3 --version
# Should be Python 3.8 or higher
```

#### 5.2 기본 임포트 테스트
```bash
python3 -c "
import sys
print(f'Python version: {sys.version}')

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

print('✓ All imports successful!')
"
```

### 문제 6: API 키 문제

**증상:**
```
Error: Invalid API key
401 Unauthorized
```

**해결책:**

#### 6.1 API 키 확인
```bash
# Check .env file content
cat .env | grep -E '(KIS_APP_KEY|KIS_APP_SECRET|GEMINI_API_KEY)'
```

#### 6.2 API 키 설정 테스트
```bash
python3 -c "
import os
from dotenv import load_dotenv
load_dotenv()

print('=== API Key Validation ===')
print(f'KIS App Key: {bool(os.getenv(\"KIS_APP_KEY\"))}')
print(f'KIS App Secret: {bool(os.getenv(\"KIS_APP_SECRET\"))}')
print(f'KIS Account No: {bool(os.getenv(\"KIS_ACCOUNT_NO\"))}')
print(f'Gemini API Key: {bool(os.getenv(\"GEMINI_API_KEY\"))}')

# Check if all required keys are present
required_keys = ['KIS_APP_KEY', 'KIS_APP_SECRET', 'KIS_ACCOUNT_NO', 'GEMINI_API_KEY']
missing_keys = [key for key in required_keys if not os.getenv(key)]

if missing_keys:
    print(f'❌ Missing API keys: {missing_keys}')
    print('   Please add these keys to your .env file')
else:
    print('✅ All API keys are configured')
"
```

### 문제 7: 거래 로직 오류

**증상:**
```
Buy conditions not met
No positions to sell
```

**해결책:**

#### 7.1 설정 확인
```bash
python3 -c "
import config
print('=== Trading Configuration ===')
print(f'Mock Trading: {config.IS_MOCK}')
print(f'Target Ticker: {config.TARGET_TICKER}')
print(f'Buy Ratio: {config.BUY_RATIO}')
print(f'DIP Threshold: {config.DIP_THRESHOLD}')
print(f'Take Profit Threshold: {config.TAKE_PROFIT_THRESHOLD}')
print(f'AI Score Floor: {config.AI_SCORE_FLOOR}')
"
```

#### 7.2 상태 확인
```bash
python3 -c "
from state import bot_state
print('=== Trading State ===')
print(f'Bought Today: {bot_state.bought_today}')
print(f'Today Date: {bot_state._flag_date}')
"
```

### 문제 8: 파일 시스템 권한

**증상:**
```
Permission denied: logs/bot.log
Permission denied: .env
```

**해결책:**

#### 8.1 올바른 권한 설정
```bash
# Set permissions for .env file
chmod 600 .env

# Set permissions for logs directory
mkdir -p logs
chmod 755 logs
chmod 644 logs/bot.log
```

#### 8.2 파일 소유권 확인
```bash
# Check file ownership
ls -la .env
ls -la logs/
```

### 문제 9: 스케줄 및 타이밍 문제

**증상:**
```
Bot not running on schedule
Trading not happening at expected times
```

**해결책:**

#### 9.1 스케줄 설정 확인
```bash
# Run with debug output
python3 -c "
import schedule
import time
from main import job

print('Testing schedule configuration...')
schedule.every(1).minutes.do(job)
print('✓ Schedule configured successfully')
print('Note: In actual bot, schedule runs every hour')
"
```

### 문제 10: 네트워크 연결

**증상:**
```
ConnectionError: Failed to connect to API
TimeoutError: Request timed out
```

**해결책:**

#### 10.1 네트워크 연결 확인
```bash
# Test basic connectivity
ping -c 3 google.com

# Test API connectivity
python3 -c "
import requests
try:
    response = requests.get('https://httpbin.org/get', timeout=5)
    print('✓ Network connectivity test passed')
except Exception as e:
    print(f'❌ Network connectivity test failed: {e}')
"
```

## 비상 명령어

### 봇 중지
```bash
# Method 1: Keyboard interrupt (if running in terminal)
Ctrl+C

# Method 2: Find and stop the process
ps aux | grep python | grep main.py
kill <PID>

# Method 3: Force stop
pkill -f "python main.py"
```

### 봇 재시작
```bash
# Stop first
pkill -f "python main.py"

# Wait a moment
sleep 5

# Start again
python main.py
```

### 클린 재시작
```bash
# Stop, clean logs, and restart
pkill -f "python main.py"
sleep 3
rm -f logs/bot.log
python main.py
```

## 개발 모드

### 개발 명령어
```bash
# Setup project and install dependencies
source .venv/bin/activate && pip install -r requirements.txt

# Run the bot
python main.py

# Test syntax
python3 -m py_compile *.py && echo 'Syntax check passed'

# View recent logs
tail -20 logs/bot.log 2>/dev/null || echo 'No logs found'
```

### 디버그 모드
```bash
# Run with debug output
python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
import main
print('Debug mode enabled')
"
```

## 지원

### 도움 받기
1. 오류 메시지가 있으면 **로그를 확인**하세요
2. `.env` 파일의 **설정을 검증**하세요
3. 모든 의존성이 설치되었는지 **임포트를 테스트**하세요
4. `.env` 및 `logs/` 디렉토리의 **파일 권한을 확인**하세요
5. 위의 **문제 해결 가이드를 검토**하세요

### 일반적인 해결책
- **가상 환경이 설정되지 않음**: `python3 -m venv .venv`로 `.venv` 생성
- **의존성 누락**: `pip install -r requirements.txt`로 설치
- **잘못된 API 키**: `.env` 파일을 확인하고 갱신
- **임포트 오류**: 필요한 모든 패키지 설치
- **권한 오류**: 올바른 파일 권한 설정

### 비상 연락처
- **프로젝트 지원**: 문서 참조
- **API 지원**: KIS 또는 Gemini 지원팀에 직접 문의
- **개발 지원**: 개발 가이드 확인

## 빠른 참조 명령어

### 기본 명령어
```bash
# Setup and run (one-liner)
source .venv/bin/activate && pip install -r requirements.txt && python main.py

# Configuration test
python3 -c "import config; print(f'Mock Trading: {config.IS_MOCK}')"

# Basic test
python3 -c "import config, state, ai_analyzer, trader, main; print('All imports successful')"
```

### 비상 명령어
```bash
# Stop the bot
pkill -f "python main.py"

# Restart the bot
python main.py

# Clean restart
pkill -f "python main.py" && sleep 3 && python main.py
```

## 결론

이 문제 해결 가이드는 나스닥 스노볼 자동 거래 봇을 설정하고 실행할 때 사용자가 마주칠 수 있는 가장 일반적인 문제를 다룹니다. 이 가이드의 해결책을 따르면 대부분의 문제를 해결하고 봇을 성공적으로 실행할 수 있습니다.

**기억하세요**:
- 테스트 시에는 항상 `IS_MOCK_TRADING=True`로 시작하세요
- 모든 API 키가 올바르게 설정되었는지 확인하세요
- `.env` 및 `logs/` 디렉토리의 파일 권한을 확인하세요
- 봇을 실행하기 전에 임포트를 테스트하세요
- 문제가 없는지 로그를 정기적으로 모니터링하세요

나스닥 스노볼 자동 거래 봇은 이제 자동 거래에 바로 사용할 준비가 되었습니다!

---

*문서 버전: 1.0.0*
*마지막 업데이트: 2025-01-02*
*작성자: Claude Code 구현*
