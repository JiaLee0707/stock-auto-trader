# 작업 이력 (CHANGES)

append-only. 새 작업은 파일 **끝에 추가**한다.

> 이 파일은 `.claude/CHANGES-LOG.md`를 `git mv`로 이관한 뒤 T00 보고를 덧붙인 것이다.
> 아래 "이관된 이력"의 완료/프로덕션 준비 서술은 **당시 작성자의 주장**이며 사실 검증되지 않았다. 검증 결과는 파일 끝의 T00 절 참고.

---

# 이관된 이력 (.claude/CHANGES-LOG.md)

# Change Log - Nasdaq Snowball Auto-Trading Bot

## Version 1.1.0 - 문서 정정 및 의존성 정리 (2026-10-13)

### Purpose
문서가 실제 코드와 어긋난 부분을 정정하고, `requirements.txt`를 실제 사용 패키지에 맞게 갱신.

### Documentation Corrections
- ✅ **용도 문구 삭제**: "교육 목적" 등 시스템 용도를 규정하는 문장 제거 (README, TRADING_BOT_README, project_summary, DEVELOPMENT-GUIDE)
- ✅ **Gemini 모델명 정정**: `Gemini 1.5 Flash` → `gemini-3.1-flash-lite`, SDK `google-generativeai` → `google-genai`
- ✅ **주문 방식 정정**: "분수/소수점 주식 거래" → 정수 수량 지정가 매수 (`buy_shares`)
- ✅ **손절 표기 정정**: 손절 조건은 미구현이며 Phase 2 로드맵 항목임을 명시
- ✅ **모듈 수 정정**: "9+ 전문 모듈" → 실행 경로 7개 모듈 + 확장용 3개
- ✅ **미사용 모듈 표기**: `trading_strategy.py`, `utils.py`는 현재 실행 경로에서 미사용
- ✅ **환경 변수 정정**: `KIS_MOCK_*` / `KIS_REAL_*` 분리 키와 `KIS_APP_KEY` fallback 동작 반영
- ✅ **토큰 캐시 파일명 정정**: `token_cache.json` → `token_cache_mock.json` / `token_cache_real.json`
- ✅ **Python 버전 정정**: `3.8+` / `3.14.0` → `3.12.15` (`.python-version`)
- ✅ **삭제된 파일 참조 제거**: `README_FINAL.md`, `final/README_FINAL.md`
- ✅ **KIS 엔드포인트 정정**: 매수도 `/trading/order` 사용 (`order-resv` 아님), 환율은 두나무 API

### Dependency Changes
- ✅ **`google-genai==2.30.0` 추가**: `ai_analyzer.py`가 사용하는 신규 SDK
- ✅ **`google-generativeai==0.8.6` 제거**: 구 SDK, 코드에서 미사용
- ✅ **전이 의존성 정리**: 구 SDK 전용 패키지(`google-api-python-client`, `grpcio-status`, `proto-plus`, `tqdm`, `httplib2`, `uritemplate`, `pyparsing`, `googleapis-common-protos`, `google-ai-generativelanguage`, `google-api-core`, `google-auth-httplib2`) 제거 — 40개 → 27개

### Added Documentation
- ✅ **`TROUBLESHOOTING.md`**, **`run_instructions.md`**, **`TRADING_BOT_README.md`** 항목을 구조도 및 문서 목록에 반영
- ✅ **`requirements.txt` 갱신 절차 안내**: `.venv/bin/python -m pip freeze > requirements.txt`

## Version 1.0.0 - Initial Implementation (2025-01-02)

### Major Features Implemented

#### Core Trading System
- ✅ **Main Trading Loop**: Automated hourly trading cycles
- ✅ **AI-Powered Market Analysis**: Gemini 1.5 Flash integration for sentiment analysis
- ✅ **KIS API Integration**: Complete overseas stock API support
- ✅ **Fractional Share Trading**: Real-world trading with partial shares
- ✅ **Risk Management**: Price-based entry/exit rules and AI sentiment filtering

#### Architecture Components
- ✅ **Modular Design**: 9+ specialized modules for maintainability
- ✅ **State Management**: Daily buy flag tracking with automatic reset
- ✅ **Error Handling**: Comprehensive exception management and recovery
- ✅ **Logging System**: Structured logging with file and console output

#### Development Features
- ✅ **Environment Configuration**: `.env` file with API credentials
- ✅ **Virtual Environment Support**: `.venv` initialization
- ✅ **Requirements Management**: `requirements.txt` with dependency pinning
- ✅ **Documentation**: Comprehensive README with technical specifications

### Technical Specifications

#### Trading Logic
- **Buy Conditions**: Price dip (-1.5%) + Positive AI sentiment + Daily buy limit
- **Sell Conditions**: Take profit (5%) + Stop loss (-1.5%)
- **Position Sizing**: 30% of total portfolio value per trade
- **AI Sentiment**: -100 to +100 scale, threshold at -50

#### API Integration
- **KIS API**: Mock and real trading endpoints
- **Gemini API**: Market sentiment analysis
- **Rate Limiting**: Built-in delays between API calls
- **Error Recovery**: Automatic token refresh and retry

#### System Architecture
```
main.py                    → Scheduler and main entry point
├── ai_analyzer.py          → AI market sentiment analysis
├── config.py               → Configuration management
├── kis_overseas.py          → KIS API integration
├── kis_domestic.py          → Domestic ETF support (Phase 2)
├── trader.py               → Core trading logic
├── trading_strategy.py      → Advanced trading strategies
├── utils.py                 → Utility functions
└── state.py                 → State tracking
```

### Key Achievements

#### Trading System Features
- [x] Automated hourly trading cycles
- [x] Real-time market data integration
- [x] AI-powered sentiment analysis
- [x] Fractional share trading capabilities
- [x] Risk management and position sizing
- [x] Comprehensive error handling

#### Development Quality
- [x] 100% documented with docstrings
- [x] Type-annotated code
- [x] Comprehensive logging
- [x] Modular architecture
- [x] Error recovery mechanisms
- [x] Production-ready structure

#### Testing and Validation
- [x] Syntax validation passed
- [x] Basic import testing successful
- [x] Project structure verified
- [x] Configuration file validated

### Phase 2 Roadmap

#### Immediate Next Steps
1. **Domestic ETF Integration**: Implement `kis_domestic.py` with Korean ETF support
2. **Enhanced Risk Management**: Advanced position sizing and stop-loss mechanisms
3. **Performance Analytics**: Detailed trade logging and reporting
4. **Paper Trading**: Risk-free testing environment

#### Future Enhancements
- **Multi-Asset Support**: Beyond QQQ (crypto, commodities, international)
- **Options Trading**: Advanced derivatives support
- **Web Dashboard**: Real-time monitoring interface
- **Mobile Notifications**: Alert system for trading events
- **Backtesting**: Historical strategy validation

### Project Structure

```
stock-auto-trader/
├── .claude/                    # Claude-specific configurations
│   ├── commands.py             # Command definitions
│   └── CHANGES-LOG.md          # This change log
├── .env                        # Environment configuration
├── .gitignore                  # Git ignore patterns
├── requirements.txt            # Python dependencies
├── main.py                     # Main trading bot
├── config.py                   # Configuration management
├── state.py                    # State tracking
├── kis_overseas.py              # KIS API integration (Phase 1)
├── kis_domestic.py              # Domestic ETF API (Phase 2)
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

### Installation and Usage

#### Prerequisites
1. Python 3.8+
2. Virtual environment
3. Internet connection
4. API credentials (KIS, Gemini)

#### Quick Start
```bash
# 1. Initialize project
python setup.py  # or manually create .venv and install requirements

# 2. Configure environment
# Edit .env file with your API keys
# Set IS_MOCK_TRADING=True for testing

# 3. Run the bot
python main.py

# 4. Monitor logs
tail -f logs/bot.log
```

### Technical Validation

#### Syntax Check Results
```bash
$ python3 -m py_compile *.py
Syntax check passed
```

#### Module Imports
```python
$ python3 -c "import config, state, ai_analyzer, trader, main; print('All imports successful')"
All imports successful
```

#### Environment Validation
```python
$ python3 -c "from dotenv import load_dotenv; load_dotenv(); print('Env loaded successfully')"
Env loaded successfully
```

### Error Handling and Recovery

#### Network Failures
- **Automatic Retry**: Next trading cycle
- **Graceful Degradation**: Continue running
- **Error Logging**: Detailed error tracking

#### API Failures
- **Token Refresh**: Automatic on expiration
- **Rate Limiting**: Built-in delays
- **Fallback Responses**: Neutral market values

#### System Failures
- **Main Loop Protection**: Core scheduler never crashes
- **Schedule Recovery**: Automatic job rescheduling
- **Resource Monitoring**: Continuous operation

### Security Considerations

#### Sensitive Data Protection
- API keys stored in `.env` (excluded from git)
- Token caching with file-based storage
- No hardcoded credentials
- Comprehensive logging without sensitive data

#### Access Control
- Virtual environment isolation
- File permission management
- Configuration-based access control

### Performance Characteristics

#### Resource Usage
- **Memory**: ~50-100 MB (steady state)
- **CPU**: Minimal (I/O bound)
- **Network**: ~10-20 API calls per hour

#### Execution Timing
- **Cycle Duration**: 1-3 minutes
- **Frequency**: Hourly
- **Startup Time**: 2-5 seconds
- **Cooldown**: 30 seconds between schedule checks

### Conclusion

The Nasdaq Snowball Auto-Trading Bot represents a complete, production-ready automated trading system with the following key achievements:

1. **Comprehensive Architecture**: 9+ modular components
2. **AI Integration**: Gemini-powered market sentiment analysis
3. **Real Trading Capability**: Fractional share support via KIS API
4. **Risk Management**: Multi-layered protection and position sizing
5. **Error Resilience**: Graceful handling of all failure modes
6. **Documentation**: Complete technical and user documentation

The system is ready for Phase 2 deployment with domestic ETF support and advanced trading strategies. All core functionality has been validated and is production-ready.

---

**Version**: 1.0.0 (Initial)  
**Date**: 2025-01-02  
**Status**: Production Ready  
**Next Version**: 2.0.0 (Phase 2 - Domestic ETF Support)

---
---

# T00 — 문서 정리와 기준점 확보 (2026-10-18)

브랜치: `improve/t00-docs` / 베이스: `baseline/before-improvements` (cba1a10)

## 1. 완료 여부

**완료.** 코드(`*.py`) 변경 0줄. 문서 구조 재편 + 감사 표 작성 + README/DEVELOPMENT/TROUBLESHOOTING 재작성.

## 2. 변경 파일과 핵심 요약

### 이동 (`git mv`, 이력 보존)

| 이전 | 이후 |
|---|---|
| `TRADING_BOT_README.md` | `docs/archive/TRADING_BOT_README.md` |
| `run_instructions.md` | `docs/archive/run_instructions.md` |
| `project_summary.md` | `docs/archive/project_summary.md` |
| `REVIEW_PROMPT.md` | `docs/archive/REVIEW_PROMPT.md` |
| `DEVELOPMENT-GUIDE.md` | `docs/DEVELOPMENT.md` |
| `TROUBLESHOOTING.md` | `docs/TROUBLESHOOTING.md` |
| `.claude/CHANGES-LOG.md` | `docs/CHANGES.md` (이 파일) |

`docs/archive/CLAUDE.old.md`는 사용자가 이미 이동해 둔 상태(확인만 함).

### 재작성

- `README.md` — 사용자용 단일 진입 문서. 현재 상태(모의투자 검증 중, 실전 투입 불가, 근거 = 로그 grep 결과) / 설치 / `.env` 변수 표(코드 검증) / 실행·로그 / 모의↔실전 전환 + 경고 / 문서 지도.
- `docs/DEVELOPMENT.md` — 개발 환경, **자동 테스트 없음 사실 명시**, 수동 검증 절차, 브랜치·커밋 규칙, 미사용 모듈 표.
- `docs/TROUBLESHOOTING.md` — 유효 항목만 남기고 재작성. 휴장일 미판정 한계, macOS에서 안 되는 명령 제거.

### 수정

- `CLAUDE.md` — "모듈 구조"·"현재 알려진 문제"를 코드 조사 결과로 갱신. "전략 기본값" 표에 현재 코드에 없는 **계획값**임을 명시.
- `.gitignore` — `*.db`, `data/`, `reports/` 추가(보완).

### 삭제

없음. 낡은 문서는 모두 `docs/archive/`로 보관.

## 3. 테스트 결과

**자동 테스트 없음** — `tests/` 없음, `pytest.ini`/`pyproject.toml` 없음, `pytest` 미설치. T00은 코드를 바꾸지 않았으므로 검증은 문서 사실 확인(코드 grep)으로 대체했다. CLAUDE.md의 "변경마다 pytest" 규칙은 T00 시점에 이행 불가이며 사용자 결정 필요(아래 5번).

## 4. 확인하지 못한 항목 (VERIFY)

| # | 항목 | 사유 |
|---|---|---|
| V1 | 실전 계좌에서의 TR_ID 분기 (`TTTS0308U`/`TTTT1002U`/`TTTC0802U`/`TTTC0801U`) | 모의투자만 실행됨. 실전 계좌로 검증 불가 |
| V2 | 실제 체결가·체결수량 | 주문 후 `rt_cd=="0"`(접수)만 확인. 체결 조회 코드 없음 |
| V3 | KIS 호출 제한 수치 (초당 건수) | `_MIN_CALL_INTERVAL_SEC=1.0`은 경험적 값. 근거 미확인 |
| V4 | 수수료율·세율 | 코드에 없음. 손익 계산에 반영되지 않음 |
| V5 | `TAKE_PROFIT_THRESHOLD` 구변수 처리 | `config.py`에 있으나 매매 로직에서 미사용. 삭제/유지 결정 필요 |
| V6 | 미국·한국 휴장일 | `main.py`가 평일 여부만 확인. 휴장일 판정 없음 |
| V7 | `google-generativeai` 잔존 설치 | `requirements.txt`에는 없으나 `.venv`에 남아 있음 (1.1.0 로그 주장과 불일치) |

## 5. 사용자가 결정해야 할 질문

1. **미사용 파일 삭제 여부** — `trading_strategy.py`(383줄), `utils.py`(385줄)는 어디에서도 import되지 않는다. 삭제할지, 확장용으로 보관할지. `utils.py`는 낡은 설정 기본값(`BUY_RATIO=0.30`, `TAKE_PROFIT_THRESHOLD`)을 자체 보유해 위험.
2. **`settings.local.json` deny 규칙 적용 여부** — 아래 A-4 제안안 참고. 파일은 T00에서 수정하지 않았다.
3. **git 히스토리 비밀 노출** — `.env`, `token_cache*.json`은 **한 번도 커밋된 적 없음**(`git log --all -- <path>` 결과 없음). 노출 없음으로 판단. 재확인 필요 시 사용자 확인 권장.
4. **실전 모드 킬스위치 구현** — CLAUDE.md 규칙 1의 `TRADING_MODE=paper` / `CONFIRM_LIVE`는 **현재 코드에 존재하지 않는다** (변수명이 `IS_MOCK_TRADING`). T01에서 구현 예정.
5. **테스트 인프라 도입** — pytest + `tests/`를 T01에서 만들지. 그 전까지 CLAUDE.md의 pytest 규칙은 공허하다.
6. **`IS_OVERSEAS_ENABLED` 실전 기본값** — 코드상 실전은 `False`. README에 반영했으나 사용자 의도 확인 필요.

## 6. 다음 Task에 영향을 주는 사항

- 모듈 구조·알려진 문제는 `CLAUDE.md`, 설치·설정은 `README.md`, 개발 규칙은 `docs/DEVELOPMENT.md`로 **단일화**했다. 이후 Task에서 중복 서술 금지.
- `CLAUDE.md`의 "모듈 구조"는 코드와 일치 확인 완료. T01에서 실전 킬스위치 도입 시 이 절과 README의 "모의 ↔ 실전 전환" 절을 함께 갱신해야 한다.
- "전략 기본값" 표는 **현재 코드에 없는 목표값**이다. T11 이전까지 코드에 반영하지 말 것.
- `docs/CHANGES.md`는 append-only. 이후 Task는 이 파일 끝에 추가.

## 7. 재검증 (T00 후속 확인)

위 1~6항과 부록 A·B의 주장을 **다시 실행·대조해 확인**했다. 결론: 핵심 주장은 사실이며, 아래 2건을 정정했다.

| 확인 항목 | 방법 | 결과 |
|---|---|---|
| `*.py` diff 0줄 | `git diff --stat -- '*.py'` / `--cached` | **0줄** (맞음) |
| 루트 md는 README·CLAUDE만 | `ls *.md` | `CLAUDE.md`, `README.md` (맞음) |
| 매도 주문 완료 0건 / 매수 9건 | `grep -c` on `logs/bot.log` | 0 / 9 (맞음) |
| 매도 조건 미충족 13건, 매도 언급 39건 | `grep -c` | 13 / 39 (맞음) |
| 장시작전·장종료 거부 4건 | `grep -cE` | 4 (맞음) |
| 계좌번호 로그 노출 | `grep -cE "[0-9]{8}-[0-9]{2}"` | 0 (노출 없음) |
| `.env`/`token_cache*` 커밋 이력 | `git log --all -- <path>` | 이력 없음 (노출 없음) |
| `.envrc` 비밀값 | 변수 할당 패턴 grep | 할당 0건, 비밀 없음 |
| 미사용 모듈 | `grep -rn "trading_strategy"\|"utils"` on `*.py` | `trading_strategy` 참조 0, `utils`는 표준 `email.utils`뿐 (미사용 맞음) |
| `TAKE_PROFIT_THRESHOLD` 미사용 | grep | `config.py`·`utils.py` 정의만, 매매 로직 참조 0 (맞음) |
| 전역 스레드 안전 서술 | `grep "Lock\|threading"` | 0 (lock 없음 → 옛 문서 "스레드 안전"은 틀림) |
| `pip freeze` 45 vs `requirements.txt` 27 | `comm` 비교 | 45 / 27, 차이 18개 = 구 SDK 전이 의존성 (맞음) |
| `pytest`·`psutil` 미설치, `tests/`·`config/` 없음 | import / `ls` | 모두 없음 (맞음) |
| `commands.py` 문법 오류 | `py_compile .claude/commands.py` | 21행 `unterminated string literal` (맞음) |
| 루트 `*.py` 구문 | `py_compile` + 8개 모듈 import | 통과, `imports OK` |
| 옛 README 인용 (B-1) | `git show HEAD:README.md` grep | "프로덕션급" / "실행 경로 6개·7개" 자기모순 / ":175 총 포트폴리오의 30%" / "스레드 안전" / pytest·flake8·mypy — **모두 실제 인용과 일치** |
| 옛 DEVELOPMENT-GUIDE / TROUBLESHOOTING 인용 (B-4·B-5) | `git show HEAD:` grep | `base62-sha`, `free -h`, `source .env/bin/activate`, `cat .env`, 전역 설치 옵션 — **모두 실제와 일치** |

### 정정한 것

1. **`.gitignore`** — `.DS_Store.venv/` 한 줄(복붙 오류)을 `.DS_Store` / `.venv/` 두 줄로 분리. 옛 문서 A-2는 "고치지 않고 보고만"이라 적었으나, 완료 기준이 `.gitignore` 보완을 허용하므로 수정했다.
2. **부록 B-1** — "환율: 두나무 API" 행의 출처가 README로 적혀 있었으나, README에는 "두나무"가 0건이다. 출처는 1.1.0 이력(B-8)이므로 행에 출처를 명시했다. 판정(낡음)은 동일.

### 정정하지 않은 것 (판단 보류)

- `CLAUDE.md` 규칙 1의 `TRADING_MODE=paper` / `CONFIRM_LIVE`: 현재 코드에 없는 **계획상 이름**이다. 규칙 자체가 "T01에서 구현"을 명시하므로 그대로 두었다.
- `docs/DEVELOPMENT.md`·`docs/TROUBLESHOOTING.md`의 `0.30`, `free -h`, `base62` 언급: 모두 **"옛 문서의 잘못된 값"을 설명하는 문맥**이라 낡은 설정명이 아니다. 그대로 둔다.

---

# 부록 A. 현황 조사 (T00-A)

## A-1. git status / log

- T00 시작 시점 working tree: **clean** (미커밋 M 없음, untracked U 없음)
- **`indicators.py`는 untracked가 아니었다.** `git ls-files`에 포함되어 있으며(커밋 `18e7ee3 snapshot before improvement work`), `git status`상 clean 상태였다. 즉 "indicators.py가 untracked"라는 전제는 **사실이 아니다**.
- 커밋 이력 (최근 7개):
  - `cba1a10 docs: replace CLAUDE.md`
  - `18e7ee3 snapshot before improvement work`
  - `ced271e docs: 실제 코드 기준으로 문서 정정 및 requirements 정리`
  - `68446c0 feat: KIS 매수 주문 체계 개선 및 전체 프로젝트 한글화`
  - `16a1da8 feat: add .envrc file for environment management`
  - `88206f5 feat: add basic .gitignore and .python-version files`
  - `e75342c docs: translate TRADING_BOT_README.md to Korean`

추적 파일 25개 중 비밀 관련 파일은 `.envrc` 하나이며, 그 내용은 venv 생성/활성화 4줄이다(A-3).

## A-2. .gitignore 점검

원본 `.gitignore`:

```
.env
token_cache.json
logs/
__pycache__/
*.pyc
.DS_Store.venv/
.direnv/
.venv/
token_cache*.json
```

| 요구 항목 | 상태 |
|---|---|
| `.env` | 무시됨 |
| `.envrc` | **무시 안 됨 — git에 추적 중** (내용은 venv 부트스트랩만, 비밀 없음) |
| `.venv/` | 무시됨 |
| `__pycache__/` | 무시됨 |
| `logs/` | 무시됨 |
| `token_cache_*.json` | 무시됨 (`token_cache*.json`) |
| `*.db` | T00에서 추가 |
| `data/` | T00에서 추가 |
| `reports/` | T00에서 추가 |

**버그 발견 및 수정**: `.DS_Store.venv/` 한 줄에 `.DS_Store`와 `.venv/`가 붙어 있었다. 원래는 두 줄이어야 했다. 결과적으로 `.DS_Store`는 이 파일로는 무시되지 않았으나, `.venv/`는 다음 줄에 별도로 있어 실질 피해는 없었다. T00에서 `.DS_Store` / `.venv/` 두 줄로 분리했다(보완 허용 범위 내). 참고: 이 머신에서는 `~/.gitignore_global:2`에 `.DS_Store`가 이미 있어 중복 방어가 된다.

추적 중인 비밀 관련 파일: **없음**. `git ls-files | grep -iE 'env|secret|token|key'` → `.envrc`만.

## A-3. .envrc

내용(4줄, 비밀값 없음 — 그대로 인용):

```bash
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
```

- 변수 **이름** 목록: 없음 (환경변수 할당 0건, `VAR=` 패턴 grep 결과 0)
- 비밀값 직접 기재: **없음**
- 판정: 안전. git 추적을 유지해도 문제없다. (`.gitignore`에 넣을 필요 없음)

## A-4. .claude/settings.local.json

현재 내용 (`.claude/.gitignore`에 의해 git 추적 제외됨):

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
    "ANTHROPIC_AUTH_TOKEN": "...(값 생략)",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "nvidia,deepseek-ai/deepseek-v4.1-flash",
    "ANTHROPIC_SMALL_FAST_MODEL": "nvidia,deepseek-ai/deepseek-v4.1-flash",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "nvidia,deepseek-ai/deepseek-v4.1-flash",
    "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_AUTOUPDATER": "1"
  },
  "permissions": {
    "allow": [
      "Read", "Edit", "WebFetch", "WebSearch",
      "Bash(.venv/bin/python:*)", "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)"
    ],
    "deny": ["Read(./.env)", "Read(./.env.*)"]
  }
}
```

요약 및 위험도:

| 질문 | 답 |
|---|---|
| `.env` 읽기 허용? | **거부** (`Read(./.env)`, `Read(./.env.*)`) — 이미 차단됨 |
| `python main.py` 실행 허용? | **직접 허용 규칙 없음**. 다만 `Bash(.venv/bin/python:*)`가 있으므로 `.venv/bin/python main.py`는 **허용**된다 → **실전 주문이 나갈 수 있는 경로** |
| 임의 네트워크 호출? | `Bash(.venv/bin/python:*)`로 파이썬이 임의 네트워크 호출 가능. `WebFetch`/`WebSearch`도 허용 |
| `.envrc` 읽기 | 차단 안 됨 (내용은 무해하지만 추가 권장) |
| `token_cache_*.json` 읽기 | 차단 안 됨 (토큰 포함 파일) |

**deny 규칙 추가 제안안** (T00에서는 파일을 수정하지 않음. 문법은 Claude Code 공식 문서 `/docs/en/permissions`, `/docs/en/settings` 확인 결과):

```json
"deny": [
  "Read(./.env)",
  "Read(./.env.*)",
  "Read(./.envrc)",
  "Read(./token_cache*.json)",
  "Read(./**/token_cache*.json)",
  "Edit(./.env)",
  "Edit(./.env.*)",
  "Bash(cat .env)",
  "Bash(cat .envrc)"
]
```

근거 및 한계 (문서 확인 결과):
- `Read(path)` deny는 gitignore 패턴 문법을 쓴다. `Read(./.env.*)`가 이미 있으므로 `Read(./.envrc)`를 별도로 추가해야 한다(`.envrc`는 `.env.*`에 매칭되지 않음).
- **Read deny는 Edit/Write도 함께 막는다** (문서: "A `Read` deny rule also blocks the Edit and Write tools on the same path"). 따라서 `Edit(...)` 중복은 방어적 목적.
- **Bash deny는 보안 경계가 아니다.** 문서 명시: `Bash(cat .env)`는 `cat .env`를 막지만 `/bin/cat .env`, `sh -c 'cat .env'`는 막지 못한다. 또한 Python 스크립트가 파일을 직접 여는 것은 어떤 Read/Edit deny로도 막히지 않는다("arbitrary subprocesses that read or write files indirectly, like a Python or Node script that opens files itself").
- 따라서 **`python main.py` 실행 자체를 deny로 막는 것은 신뢰할 수 없다.** `Bash(.venv/bin/python:*)` allow를 제거하고 매번 승인받는 편이 실질적이다. 진짜 방어선은 T01의 코드 레벨 킬스위치(`CONFIRM_LIVE`)여야 한다.
- OS 수준 차단이 필요하면 샌드박스(`/docs/en/sandboxing`)를 써야 한다.

## A-5. .claude/commands.py

JSON 형식의 명령 정의 파일(파이썬 아님, `commands.py`라는 이름과 달리 실행 코드가 아니라 Claude Code 명령 정의):

| 명령 | 실행 내용 |
|---|---|
| `setup` | `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt` |
| `run` | `python main.py` |
| `test` | `python3 -m py_compile *.py && echo 'Syntax check passed'` |
| `logs` | `tail -20 logs/bot.log 2>/dev/null \|\| echo 'No logs found'` |

**문제점**:
1. `run` 명령이 **모의/실전을 구분하지 않고 `main.py`를 실행**한다. 현재 `main.py`에는 킬스위치가 없으므로 `.env`가 실전으로 설정돼 있으면 그대로 실전 주문이 나간다. → **제거 또는 모의투자 강제로 바꿔야 함** (사용자 결정).
2. 이 파일은 **JSON인데 확장자가 `.py`**이고 내용에도 문법 오류가 있다 (21행 `"command": "tail -20 logs/bot.log ...` 문자열 미종결). 셸의 `*.py` 글롭은 dot 디렉터리를 건너뛰므로 `python3 -m py_compile *.py`는 통과하지만, `.claude/commands.py`를 직접 지정하면 `SyntaxError: unterminated string literal`가 난다 (T00에서 실행 확인).
3. `setup`의 `python3 -m venv`는 CLAUDE.md 규칙(.venv/bin/python만 사용)과 배치되진 않으나, `pip install`을 활성화된 venv에서 하는 것은 전역 pip 사고 위험이 있다(`PIP_REQUIRE_VIRTUALENV`는 unset 금지).
4. `commands.py`는 Claude Code의 **현재** 커스텀 명령 방식(`.claude/commands/*.md`)이 아니다. 현재 방식으로는 동작하지 않을 가능성이 높다.

## A-6. trading_strategy.py / utils.py 사용 여부

`grep`/import 분석 결과 (2026-10-18):

| 파일 | import하는 곳 | 판정 |
|---|---|---|
| `trading_strategy.py` | **없음**. 언급은 문서(`*.md`)에만 존재 | **미사용** |
| `utils.py` | **없음**. `ai_analyzer.py:17`의 `from email.utils import ...`는 **표준 라이브러리**이며 이 파일과 무관 | **미사용** |

`utils.py:346-357`은 자체 `ConfigManager`에서 `os.getenv`를 다시 읽어 **별도 기본값**을 정의한다:
- `BUY_RATIO` 기본값 `0.30` (실제 `config.py`는 `0.15`) — **불일치**
- `TAKE_PROFIT_THRESHOLD`, `DIP_THRESHOLD`, `AI_SCORE_FLOOR` 등 중복 정의

삭제하지 않고 보고만 한다(A-5의 질문 1과 연결).

## A-7. Python 버전 / 의존성

- `.python-version` = `3.12.15`, `.venv/bin/python --version` = `Python 3.12.15` — **일치**
- `requirements.txt`: 27개 고정 (직접 사용 4개: `requests`, `python-dotenv`, `schedule`, `google-genai`)
- `pip freeze` 실제: **45개**, `requirements.txt`에 **없는 패키지 18개**:

```
google-ai-generativelanguage==0.6.15   google-api-core==2.33.0
google-api-python-client==2.201.0      google-auth-httplib2==0.4.4
google-generativeai==0.8.6             googleapis-common-protos==1.75.0
grpcio==1.84.0                         grpcio-status==1.71.2
httplib2==0.32.0                       packaging==26.3
proto-plus==1.28.2                     protobuf==5.29.6
pyparsing==3.3.3                       pytz==2026.5
setuptools==84.0.0                     tqdm==4.70.1
uritemplate==4.2.0                     wheel==0.48.0
```

대부분 **구 SDK `google-generativeai`의 전이 의존성**이다. 1.1.0 이력은 "구 SDK 제거"라고 주장했으나 **실제 `.venv`에는 남아 있다.** 동작에는 영향 없으나, `pip freeze > requirements.txt`를 그대로 실행하면 이 18개가 다시 들어온다.

## A-8. 환경변수 전체 목록 (코드 기준)

`config.py`가 읽는 변수와 기본값 — **README.md의 표와 동일**. 값은 `.env`를 읽지 않고 코드 기본값만 기재했다.

| 변수 | 기본값 | 비고 |
|---|---|---|
| `IS_MOCK_TRADING` | `True` | |
| `KIS_MOCK_APP_KEY` → `KIS_APP_KEY` | 없음 | fallback |
| `KIS_MOCK_APP_SECRET` → `KIS_APP_SECRET` | 없음 | fallback |
| `KIS_MOCK_ACCOUNT_NO` → `KIS_ACCOUNT_NO` | 없음 | fallback |
| `KIS_REAL_APP_KEY` → `KIS_APP_KEY` | 없음 | fallback |
| `KIS_REAL_APP_SECRET` → `KIS_APP_SECRET` | 없음 | fallback |
| `KIS_REAL_ACCOUNT_NO` → `KIS_ACCOUNT_NO` | 없음 | fallback |
| `GEMINI_API_KEY` | 없음 | |
| `TARGET_TICKER` | `QQQ` | |
| `TARGET_TICKER_NAME` | `QQQ` | |
| `IS_OVERSEAS_ENABLED` | 모의 `True` / 실전 `False` | |
| `IS_DOMESTIC_ENABLED` | `True` | |
| `DOMESTIC_TICKER` | `379800` | |
| `DOMESTIC_TICKER_NAME` | `TIGER 미국나스닥100` | |
| `NEWS_FEEDS` | Google News + Yahoo Finance | |
| `NEWS_MAX_ITEMS` | `10` | |
| `NEWS_TIMEOUT_SEC` | `8` | |
| `BUY_RATIO` | `0.15` | |
| `DOMESTIC_BUY_RATIO` | `0.10` | |
| `DIP_THRESHOLD` | `-1.5` | |
| `USE_INDICATORS` | `True` | |
| `RSI_PERIOD` | `14` | |
| `RSI_OVERSOLD` | `30` | |
| `RSI_OVERBOUGHT` | `70` | |
| `BB_PERIOD` | `20` | |
| `BB_NUM_STD` | `2.0` | |
| `TAKE_PROFIT_THRESHOLD` | `5.0` | **매매 로직에서 미사용** |
| `AI_SCORE_FLOOR` | `-50` | |
| `MAX_BUYS_PER_DAY` | `2` | |
| `MIN_BUY_INTERVAL_HOURS` | `2.0` | |
| `TAKE_PROFIT_STEPS` | `2.0:33,4.0:33,6.0:100` | |
| `STOP_LOSS_THRESHOLD` | `-5.0` | |

### 낡은 설정명 / 존재하지 않는 설정명

| 문서에 나온 설정명 | 실제 | 판정 |
|---|---|---|
| `TRADING_MODE=paper` | 없음. 실제는 `IS_MOCK_TRADING` | **없는 설정** (CLAUDE.md 규칙 1에 T01 예정으로 기재) |
| `CONFIRM_LIVE` | 없음 (T01 예정) | **없는 설정** |
| `TOKEN_CACHE_FILE` | `config.py` 상수. **env로 읽지 않음** (`utils.py:356`만 env로 읽음) | 문서에 env처럼 적힌 곳 있음 → **틀림** |
| `LOG_FILE` | `config.py` 상수. env로 읽지 않음 (`utils.py:357`만) | **틀림** |
| `BUY_RATIO=0.30` (구 문서들) | `0.15` (해외) + `DOMESTIC_BUY_RATIO=0.10` | **낡음** |
| `DOMESTIC_BUY_RATIO=0.20` (DEVELOPMENT-GUIDE, project_summary) | `0.10` | **낡음** |
| `TAKE_PROFIT_THRESHOLD=5.0` (다수 문서) | 존재하나 **미사용**. 실제 청산은 `TAKE_PROFIT_STEPS` | **낡음** |
| `MIN_BUY_INTERVAL_HOURS` | `2.0` 존재 | 문서에 거의 없음 → 보완 |
| `config/universe.yaml` (CLAUDE.md 전략 기본값 표) | **파일 없음** (`config/` 디렉터리 자체가 없음) | **없는 파일** (계획) |
| `SMA(200)` 진입 필터 (CLAUDE.md 전략 기본값) | 코드에 없음 | **미구현** (계획) |

---

# 부록 B. 문서 감사 (T00-B)

각 md의 "사실 주장"을 코드/실행과 대조한 결과. 판정: **맞음 / 낡음 / 틀림 / 확인불가**

## B-1. README.md (구본, 26KB) — 재작성으로 대체

| 주장 | 실제 | 판정 |
|---|---|---|
| "프로덕션급 아키텍처", "포괄적인 예외 관리", "프로덕션 기능" | 자동 테스트 0개, 매도 체결 0건 | **틀림** (증거 없음) |
| "매수 비율: 총 포트폴리오의 30%" (README:175) | `BUY_RATIO=0.15`, `DOMESTIC_BUY_RATIO=0.10` | **낡음** |
| "익절: 5% → 전량 매도 / 손절 로직은 미구현" (:22-23) | `TAKE_PROFIT_STEPS`(2/4/6%), 손절 **구현됨** (`STOP_LOSS_THRESHOLD=-5`) | **틀림** |
| "state.py — 스레드 안전 연산" (:58) | `BotState`에 lock 없음. 싱글 스레드 전제 | **틀림** |
| "테스트: pytest" (:615) | pytest 미설치, `tests/` 없음 | **틀림** |
| "린터: flake8, black, isort / mypy" (:613-614) | 미설치, 설정 파일 없음 | **틀림** |
| 모듈 수 "실행 경로 6개" (:26) vs "7개" (:626) | 실제 실행 경로 8개(config, state, indicators, kis_overseas, kis_domestic, ai_analyzer, trader, main) | **틀림** (문서 내 자기모순) |
| "시세 조회 `/uapi/overseas-price/v1/quotations/price`" (:184) | `kis_overseas.py:136` 일치 | **맞음** |
| "일봉 API `/uapi/overseas-price/v1/quotations/dailyprice`" (:188 부근) | `kis_overseas.py:163` 일치 (`inquire-daily-chartprice`는 환율용) | **맞음** |
| "환율: 두나무 API" (출처: 1.1.0 이력, README 아님 — B-8 참고) | `kis_overseas.py:305` KIS `inquire-daily-chartprice` + `FX@KRW` | **낡음** |
| `_request()` "모든 호출 전 1초 이상 간격" (:245) | `_MIN_CALL_INTERVAL_SEC=1.0` (`kis_overseas.py:87`) | **맞음** |
| "미국 휴장일은 별도로 처리하지 않음" (:247) | `main.py:48` 평일+시간만 확인 | **맞음** |
| "실패 감지: `rt_cd != 0`이면 예외" (:106) | `kis_domestic.py:46` `_check_rt_cd` | **맞음** |
| "메모리 ~50-100MB, 사이클 1-3분" (:253-258) | 측정 근거 없음 (psutil 미설치) | **확인불가** |
| 설치/로그 명령 (`python3 -m venv`, `tail -f logs/bot.log`, `ps aux \| grep "python main.py"`) | macOS에서 동작 | **맞음** |

## B-2. TRADING_BOT_README.md — archive

| 주장 | 실제 | 판정 |
|---|---|---|
| "분할 매수: 하루 최대 2회" (:152) | `MAX_BUYS_PER_DAY=2` | **맞음** |
| "부분 익절 +2% 1/3, +4% 1/3, +6% 전량" (:153) | `TAKE_PROFIT_STEPS` 일치 | **맞음** |
| "손절 -5% 이하 시 전량 매도 (기본 활성)" (:154) | 코드 일치 | **맞음** |
| "모의투자 전용 정규장 제한, 실전은 시간외 가능" (:164) | `main.py:69-74`는 **모드 구분 없이** 정규장만 허용 | **틀림** |
| "`free -h` 메모리 사용량" (:285) | macOS에 `free` 없음 | **틀림** |
| "로그: `grep "buy\|sell" logs/bot.log`" (:303) | 로그 메시지가 한글("매수 주문 완료")이라 매칭 안 됨 | **틀림** |
| "/trading/order-cash 지정가 `ORD_DVSN=01`" (:104) | `kis_domestic.py:193` 일치 | **맞음** |
| "매수 `VTTC0802U` / 매도 `VTTC0801U`" (:105) | `kis_domestic.py:21` TR_ID 쌍과 일치 | **맞음** |
| "토큰 관리: 24시간 유효성" (:62) | `_save_token` 기본 `expires_in_sec=86400` | **맞음** |
| "안전한 종료: 종료 전 상태 저장" (:251) | 메모리 상태 저장 없음. `KeyboardInterrupt`는 로그만 남김 | **틀림** |

## B-3. run_instructions.md — archive

| 주장 | 실제 | 판정 |
|---|---|---|
| `TAKE_PROFIT_THRESHOLD=5.0  # (구) 단일 익절 기준` (:97) | 존재하나 미사용 — 문서 표기는 **정확** | **맞음** |
| "정규장 아님 로그: `[모의투자] 미국 정규장 아님 — 주문 스킵`" (:213) | 실제 로그는 `[해외] 정규장 아님 — 매매 스킵` | **틀림** |
| "실전투자는 시간 제한을 적용하지 않음" (:213) | `main.py`는 모드 무관하게 제한 | **틀림** |
| 환경변수 표 (:263-282) | 값 대부분 코드와 일치. 단 `MIN_BUY_INTERVAL_HOURS`, `USE_INDICATORS`, `RSI_*`, `BB_*`, `NEWS_*`, `IS_OVERSEAS_ENABLED` 누락 | **부분 맞음** |
| "KIS_ACCOUNT_NO 필수: 예" (:268) | 코드는 경고만 하고 진행 | **틀림** |
| "`python setup.py`" (이력) | `setup.py` 없음 | **틀림** |
| "포트가 이미 사용 중" 문제 항목 (:318) | 이 봇은 포트를 열지 않음(스케줄러) | **틀림** |
| "옵션 B: 전역 설치" (:47-51) | CLAUDE.md 위반 (전역 pip 금지) | **틀림** |
| 임포트 테스트 스크립트 (:136-185) | 유효 (`main` import 포함) | **맞음** |

## B-4. DEVELOPMENT-GUIDE.md — docs/DEVELOPMENT.md로 대체

| 주장 | 실제 | 판정 |
|---|---|---|
| "상태: 프로덕션 준비 완료" | 증거 없음 | **틀림** |
| "`BUY_RATIO=0.30`" (:128) | `0.15` | **낡음** |
| "`DOMESTIC_BUY_RATIO=0.20`" (:500) | `0.10` | **낡음** |
| "state.py — 스레드 안전 연산" (:200) | lock 없음 | **틀림** |
| "`free -h`, `df -h`, `uptime`" (:602-605) | `free`는 macOS에 없음 | **틀림** |
| 커밋 템플릿 `<base62-sha> <메시지>` (:547) | 의미 없는 자리표시자 | **틀림** |
| "`Co-authored-by: Claude Code`" (:549) | 현재 규칙은 `Co-Authored-By: Claude Code <noreply@anthropic.com>` | **낡음** |
| "테스트 코드 없음 (`tests/`, pytest)" (:287) | 사실 | **맞음** |
| "kis_domestic 구현 완료 + TR_ID" (:491-493) | 코드 일치 | **맞음** |
| "`pip freeze > requirements.txt`" (:89) | 동작하나 구 SDK 전이 의존성이 부활 (A-7) | **부분 맞음** |
| "psutil 메모리 클래스" (:388-404) | psutil 미설치 | **틀림** |
| "import config / CONFIG 상수" (:627) | `config.CONFIG` 없음 | **틀림** |
| 브랜치/커밋 유형 표 (:551-566) | 유효 (내용은 타당) | **맞음** (재작성 시 채택) |

## B-5. TROUBLESHOOTING.md — docs/TROUBLESHOOTING.md로 대체

| 주장 | 실제 | 판정 |
|---|---|---|
| `source .env/bin/activate` (:133) | 오타 — `.venv` | **틀림** |
| "`cat .env`로 확인" (:105) | CLAUDE.md 규칙 2 위반 (비밀값 출력) | **틀림** |
| "`cat .env \| grep -E '(KIS_APP_KEY\|...)'`" (:241) | 값이 그대로 출력됨 | **틀림** |
| "`BUY_RATIO=0.30`" (:90) | `0.15` | **낡음** |
| "`TAKE_PROFIT_THRESHOLD=5.0`" (:92) | 존재하나 미사용 | **낡음** |
| "`.env` 생성용 heredoc 템플릿" (:73-94) | 유효하나 낡은 값 포함 | **부분 맞음** |
| "옵션 B: 전역 설치" (:46-49) | CLAUDE.md 위반 | **틀림** |
| 임포트 테스트, 권한 설정, 프로세스 종료 | 유효 | **맞음** |
| "`python3 -c "import psutil..."`" (없음) | 미설치 | — |

## B-6. project_summary.md — archive

| 주장 | 실제 | 판정 |
|---|---|---|
| "프로젝트 상태: ✅ 완료" (:7) | 매도 체결 0건, 테스트 0개 | **틀림** |
| "상태: 프로덕션 준비 완료" (:425) | 같은 문서에서 "모의투자 검증 단계"(:418) — 자기모순 | **틀림** |
| "매도 조건: 익절만 구현, 손절은 미구현" (:25, :128) | 손절 구현됨 | **틀림** |
| "동적 포지션 크기 조정 (거래당 30%)" (:27) | 15%/10% 고정, 동적 아님 | **틀림** |
| "`BUY_RATIO: 0.30` / `DOMESTIC_BUY_RATIO: 0.20`" (:225-226) | `0.15`/`0.10` | **낡음** |
| "`TAKE_PROFIT_THRESHOLD: 5.0`" (:228) | 미사용 | **낡음** |
| "요청 제한: 내장 지연 (호출 간 0.5초)" (:158) | `_MIN_CALL_INTERVAL_SEC=1.0` | **낡음** |
| 코드 라인 수 표 (:192-201) | 실제 다름 (config 63→106, state 46→82, trader 196→352, main 192→179, kis_overseas 364→393, kis_domestic 200→225, ai_analyzer 93→339) | **낡음** |
| "자동 테스트 없음" (:203) | 사실 | **맞음** |
| "`def run_trading_cycle(ai_score: int)`" (:87) | 실제 시그니처는 `(ai_score, overseas_open=True, domestic_open=True)` | **낡음** |
| KIS 국내 엔드포인트 4개 (:255-258) | `kis_domestic.py`와 일치 | **맞음** |

## B-7. REVIEW_PROMPT.md — archive (보존 가치 있음)

이 문서는 **다른 AI에게 리뷰를 요청하는 프롬프트**로, 자기 자신이 "완료"를 주장하지 않는다.
기재된 전략 수치·한계는 코드와 대체로 일치하며, **모의투자 4일 로그 분석과 3년 백테스트 결과**라는
다른 문서에 없는 사실 정보를 담고 있어 `docs/archive/`에 보존한다.

| 주장 | 실제 | 판정 |
|---|---|---|
| "매수 7회 시도 중 4건 장시작전/장종료 거부" (:58) | `logs/bot.log`에 관련 거부 4건 존재 — **일치** | **맞음** |
| "매도는 단 한 번도 체결된 적 없음" (:58) | 로그상 매도 주문 실행 0건 | **맞음** |
| "RSI+BB 신호 3년간 8회, 승률 87.5%" (:65) | 백테스트 스크립트가 저장소에 없음 → 재현 불가 | **확인불가** |
| 백테스트 스크립트 위치 | 문서에 없음 | **확인불가** |
| 나머지 전략 수치 (RSI 30, BB 2σ, 손절 -5%, 단계 2/4/6%) | `config.py` 일치 | **맞음** |

## B-8. .claude/CHANGES-LOG.md — docs/CHANGES.md로 이관

콘텐츠 자체가 이력이므로 위 "이관된 이력" 절에 원문을 보존했다. 감사 결과:
- "손절 미구현" 주장 → **틀림**
- "구 SDK 제거" 주장 → `.venv`에 잔존 → **부분 틀림** (A-7)
- "환율 두나무 API" → **낡음**
- "Version 1.0.0 Status: Production Ready" → **틀림** (증거 없음)
- 나머지(V1.1.0 문서 정정 항목, 의존성 정리)는 당시 작업 기록으로 유효

## B-9. CLAUDE.md (교체본) — 수정

| 주장 | 실제 | 판정 |
|---|---|---|
| 모듈 구조 8개 | 실제 import 관계와 일치 | **맞음** |
| "매도 체결 0건" | 로그 확인 | **맞음** |
| "`try/except: pass` 금지" 규칙 | `indicators.py:114` `evaluate_or_none`이 예외를 삼킴(`logger.warning` 후 None) | **부분 위반** — 로그는 남기므로 완전 위반은 아님. T01 확인 필요 |
| "주문 API 자동 재시도 금지" | `kis_overseas._request`가 주문에도 재시도 적용 (`max_attempts=3`) | **위반 상태** — T01 대상 |
| 전략 기본값 표 (SMA200, +3/-3%, 7거래일, 유니버스 yaml) | 코드에 전혀 없음 | **미구현** (계획으로 명시함) |

---

# T01 — 사전 조사 및 안전망 (2026-10-18)

브랜치: `improve/t01-baseline` / 베이스: `improve/t00-docs`

## 1. 완료 여부

**완료.** 조사·pytest 환경·실전 가드·마스킹·특성화 테스트를 모두 처리했다. 매매 판단 로직은
바꾸지 않았다(가드와 마스킹, 로그 1줄만 변경).

## 2. 변경 파일과 핵심 요약

| 파일 | 변경 |
|---|---|
| `config.py` | `TRADING_MODE`(기본 `paper`), `CONFIRM_LIVE`, `live_guard_error()`, `enforce_trading_mode()`, `mask()` 추가 |
| `main.py` | `main()` 진입 시 `TRADING_MODE` 로그 + `enforce_trading_mode()` 호출 (매매 사이클 이전) |
| `kis_overseas.py` | `sell_all` 로그가 응답 dict 전체를 찍던 것을 `rt_cd`/`msg1`만 찍도록 축소 |
| `pytest.ini` | 신규. `testpaths=tests`, `pythonpath=.` |
| `requirements-dev.txt` | 신규. `pytest==9.1.1` (런타임 의존성과 분리) |
| `tests/test_indicators.py` | 신규. RSI·볼린저·evaluate·evaluate_or_none 특성화 22개 |
| `tests/test_live_guard.py` | 신규. 실전 가드 8개 |
| `tests/test_mask.py` | 신규. 마스킹 7개 |
| `README.md` | 환경변수 표에 `TRADING_MODE`/`CONFIRM_LIVE` 추가, 현재 상태 절 갱신 |
| `docs/CHANGES.md` | 이 보고 |

코드 로직 변경은 가드·마스킹·로그 축소뿐이다. `trader.py`, `indicators.py`, `state.py`,
`kis_domestic.py`, `ai_analyzer.py`는 **한 줄도 바꾸지 않았다**.

## 3. 테스트 결과

```
.venv/bin/python -m pytest
37 passed in 0.37s
```

신규 설치: `pytest==9.1.1` (사용자에게 사전 고지함, `.venv`에만 설치). 그 외 패키지 설치 없음.

## 4. 조사 결과 — AGENTS.md와 실제 코드의 불일치

부록 A·B의 조사 결과를 재사용하고, T01에서 추가로 확인한 것만 적는다.

### 4-1. 주문 함수 호출 경로와 재시도

주문 4개 함수(`kis_overseas.buy_shares`, `kis_overseas.sell_all`,
`kis_domestic.buy_domestic`, `kis_domestic.sell_domestic_all`)는 **`trader.py`에서만** 호출된다
(`trader.py:282,293,323,333`). 네 함수 모두 내부에서 `_request(...)`를 쓴다.

- `_request`(`kis_overseas.py:92`)는 `max_attempts=3`이고, HTTP 500이면 `_RETRY_SLEEP_SEC`(1.5초)
  후 재시도한다. **조회와 주문이 같은 래퍼를 공유하므로 주문에도 재시도가 걸린다.**
- 재시도 조건은 `resp.status_code != 500`일 때 즉시 반환이다. 즉 **500만** 재시도하며,
  타임아웃(`requests.exceptions.Timeout`)은 `_request` 밖으로 전파돼 재시도되지 않는다
  (`requests.request(timeout=10)`가 예외를 던지면 `for` 루프를 벗어난다).
- 따라서 CLAUDE.md 규칙 3("주문 API 자동 재시도 금지") 위반은 **정확히는 "500 응답에 한해 위반"**이다.
  이 정정은 부록 B-9의 "위반 상태" 서술을 좁히는 것이다.
- **분류: T04 대상.** 주문 경로에서 `max_attempts=1`로 강제하는 것이 안전하다. T01에서는
  로직을 바꾸지 않았다(범위 밖).

### 4-2. `evaluate_or_none`의 예외 삼킴 (부록 B-9 재분류)

`indicators.py:114-127`은 `fetch_closes()` 예외를 `logger.warning` 후 `None`으로 삼킨다.
`try/except: pass`는 아니고 로그를 남기므로 규칙 7의 **문구 위반은 아니지만**, 조회 실패가
"신호 없음"으로 둔갑해 매매 판단이 계속된다는 점에서 **위험은 실재한다**
(지표 신호를 못 받으면 `trader._run_cycle`이 하락률 폴백으로 매수할 수 있다).

- **분류: T04/T01 대상.** 신호 없음과 조회 실패를 구분해 실패 시 매수를 보류하는 편이 안전하다.
  T01에서는 동작을 고정하는 테스트(`test_evaluate_or_none_swallows_exception`)만 추가했다.

### 4-3. 상태 저장 위치

`state.py`의 `bot_state`는 **프로세스 메모리 전용**이다. 파일·DB 저장 없음(부록 A와 일치).
`trader.py`가 읽는 잔고는 매 사이클 KIS API에서 새로 조회하므로, 메모리 상태가
브로커 실제 잔고와 어긋나도 **잔고 기준 판단은 브로커 값을 쓴다**. 다만
`buy_count_today`, `profit_stage`, `last_buy_time`은 재시작 시 소실된다.

- 문서 불일치: **없음** (CLAUDE.md "메모리 전용" 서술과 일치).

### 4-4. 백테스트 코드

저장소에 백테스트/시뮬레이션 스크립트가 **없다**(`find` 결과 0건, `.git` 내 브랜치 이름만 존재).
따라서 "백테스트가 `trader.py`와 같은 로직을 쓰는가"는 **확인 불가**다.
`docs/archive/REVIEW_PROMPT.md`의 "3년 8회 신호, 승률 87.5%"는 **재현 불가** 주장이다(부록 B-7).

- 문서 불일치: CLAUDE.md의 "백테스트 스크립트가 저장소에 없음 → 확인 필요"는 **정확**하다.

### 4-5. 일봉 조회의 미확정 봉 포함 여부

- 해외(`kis_overseas.get_daily_closes`): `BYMD=""`(오늘 기준), `GUBN="0"`(일봉), `MODP="1"`.
  **종료일 파라미터를 오늘로 고정하지 않으므로 장중에 호출하면 당일 미확정 봉이 최신으로 들어올
  가능성이 있다.** 다만 응답의 `output2` 구조상 당일 봉 포함 여부는 API 문서로 확인하지 못했다.
  → **VERIFY.** (T01 이전부터 있던 사항, 이번 Task에서 로직을 바꾸지 않았다.)
- 국내(`kis_domestic.get_daily_closes`): `FID_INPUT_DATE_2`를 `date.today()`로 지정한다.
  즉 **당일 종가(장중이면 미확정)가 포함될 수 있다.**
- 영향: RSI·볼린저가 미확정 봉을 쓰면 장중 신호가 확정 종가와 달라진다. T04/T11에서 다룰 문제.

## 5. 확인하지 못한 항목 (VERIFY)

| # | 항목 | 사유 |
|---|---|---|
| V1 | 일봉 응답에 당일 미확정 봉이 포함되는지 | KIS 공식 문서로 미확인. 4-5 참고 |
| V2 | `_request` 재시도 시 실제 주문 중복 여부 | 모의투자에서 500 재시도를 실측하지 않음(main.py 실행 금지) |
| V3 | 실전 계좌 TR_ID 분기 | 부록 V1과 동일. 실전 검증 불가 |
| V4 | `mask()`를 로그에 실제 적용할 지점 | T01에서는 유틸만 추가. 현재 코드에 계좌번호를 찍는 로그가 없어(4-6) 적용 대상이 없음 |

### 4-6. 비밀 노출 점검 결과

`grep` 결과, 계좌번호·토큰·키를 로그·print로 출력하는 곳은 **없다**(`print(` 0건,
`logger.*` 중 `account_no`/`token`/`secret` 포함 0건). `kis_overseas.py:393`의 `sell_all`이
응답 dict 전체를 찍던 것만 유일한 위험 지점이라 `rt_cd`/`msg1`로 축소했다(응답에 계좌 관련
필드가 섞여 나올 수 있음). `mask()`는 향후 로그에 계좌번호를 남길 때를 위한 유틸로 추가했고,
자체 테스트로 고정했다.

## 6. 사용자가 결정해야 할 질문

1. **`TRADING_MODE`와 `IS_MOCK_TRADING`의 관계** — 현재 둘은 **독립**이다. `TRADING_MODE`는
   종료 여부만 판단하고, 실제 도메인/키 선택은 `IS_MOCK_TRADING`이 한다. 즉
   `TRADING_MODE=paper`인데 `IS_MOCK_TRADING=False`면 **가드는 통과하지만 실전 도메인으로 실행된다.**
   T01에서는 기존 동작을 바꾸지 않으려고 분리해 두었다. 둘을 묶을지(예: `TRADING_MODE`에서
   `IS_MOCK`을 유도) 결정이 필요하다.
2. **주문 경로 재시도 차단 시점** — T04에서 `_request`를 조회용/주문용으로 분리할지.
3. **`requirements.txt` 갱신 방식** — `pip freeze`를 쓰면 구 SDK 전이 의존성 18개가 부활한다(부록 A-7).
   직접 의존성만 손으로 관리할지 결정 필요. T01에서는 `requirements.txt`를 건드리지 않고
   dev 의존성을 `requirements-dev.txt`로 분리만 했다.

## 7. 다음 Task에 영향을 주는 사항

- `main.py`는 이제 시작 시 `config.enforce_trading_mode()`를 호출한다. 이후 Task에서 `main()`의
  초기화 순서를 바꿀 때 이 호출이 **매매 사이클보다 앞**에 있어야 한다(`test_main_exits_before_trading_when_live_unconfirmed`가 지킨다).
- 지표를 바꾸면 `tests/test_indicators.py`의 특성화 값이 깨진다. **의도한 변경일 때만** 값을 갱신하고
  왜 바뀌어야 하는지 커밋 메시지에 적을 것.
- `TRADING_MODE` 관련 문서는 `README.md` 환경변수 표가 단일 출처다. 중복 서술 금지.
- 부록 B-9의 두 항목은 이 보고 4-1·4-2로 **재분류(T04/T01 대상)** 되었다. B-9의 "T01 대상" 표기는
  이 절을 가리키는 것으로 본다.

---

# T01b — 실전 가드 보강 (모드 판정의 단일화) (2026-10-18)

브랜치: `improve/t01b-live-guard` / 베이스: `improve/t01-baseline`

## 1. 완료 여부

**완료.** 모드 판정을 `IS_MOCK_TRADING` 하나로 단일화하고, 엄격 파싱·주문 함수 방어선·테스트·문서를
모두 처리했다. 매매 판단 로직은 바꾸지 않았다.

## 2. 변경 파일과 핵심 요약

| 파일 | 변경 |
|---|---|
| `config.py` | `resolve_is_mock()`/`_load_is_mock()` 추가. `IS_MOCK`을 엄격 파싱(허용값 `true`/`false`만, 그 외 `SystemExit`). `TRADING_MODE`는 `IS_MOCK`에서 유도한 **읽기 전용** 값으로 변경, 다른 env 값이 있으면 `check_trading_mode_env()`가 즉시 종료(후속 수정). `live_guard_error()`가 `trading_mode` 대신 `is_mock`을 받도록 시그니처 변경. `assert_order_allowed()` 추가 |
| `main.py` | 시작 로그 첫 줄을 `현재 모드: 모의/실전 \| 도메인: <host>`로 변경(`urlsplit` 사용). `enforce_trading_mode()`는 그대로 매매 사이클 이전 |
| `kis_overseas.py` | `buy_shares`, `sell_all` 맨 앞에 `config.assert_order_allowed()` |
| `kis_domestic.py` | `buy_domestic`, `sell_domestic_all` 맨 앞에 `config.assert_order_allowed()` |
| `tests/test_live_guard.py` | T01 판을 새 규칙에 맞게 **전면 교체** (가드 행렬·엄격 파싱·주문 함수 직접 호출·main 종료) |
| `README.md` | 환경변수 표·실전 가드·모드 전환 절을 `IS_MOCK_TRADING` 기준으로 수정 |
| `CLAUDE.md` | 규칙 1, "현재 알려진 문제"의 킬스위치 항목 수정 |
| `docs/CHANGES.md` | 이 보고 |

**공개 시그니처 변경**: `config.live_guard_error(trading_mode, confirm_live)` →
`config.live_guard_error(is_mock, confirm_live)`. 인자 의미가 바뀌었으므로 위와 같이 명시한다.

## 3. 테스트 결과

```
.venv/bin/python -m pytest
70 passed in 0.39s
```

신규 설치 없음(pytest는 T01에서 설치). 테스트는 다음을 포함한다:

| 요구 | 테스트 |
|---|---|
| (IS_MOCK, CONFIRM_LIVE) 4조합 | `test_guard_matrix` (파라미터 4케이스) |
| IS_MOCK_TRADING 9가지 값 | `test_resolve_is_mock_valid_values`(true/TRUE/True/공백 true/false/FALSE/False/공백 false), `test_resolve_is_mock_rejects_invalid_values`(ture/0/yes/true1/fals/빈 문자열/공백/1/y), `test_load_is_mock_exits_on_invalid_value` |
| 미설정 → 모의 | `test_load_is_mock_defaults_to_true_when_unset` |
| 주문 함수 4개 직접 호출 차단 | `test_overseas_buy_blocked_before_http`, `test_overseas_sell_blocked_before_http`, `test_domestic_buy_blocked_before_http`, `test_domestic_sell_blocked_before_http` — `_request`를 스파이로 바꿔 **호출 0회**를 단언 |
| main()이 매매 사이클 이전 종료 | `test_main_exits_before_trading_when_live_unconfirmed` (유지) |
| 시작 로그 모드·호스트명 | `test_main_start_log_shows_mode_and_host` |

## 4. 확인하지 못한 항목 (VERIFY)

| # | 항목 | 사유 |
|---|---|---|
| V1 | 실전 계좌에서의 실제 차단 동작 | 실전 계좌 없음. `IS_MOCK=False`는 monkeypatch로만 검증 |
| V2 | `IS_MOCK_TRADING` 오타 값이 `.env`에 있을 때의 실제 기동 | import 시점 `SystemExit`이므로 `main.py` 실행 없이는 실측 불가(T01b에서 main 미실행) |
| V3 | T01의 V1(일봉 미확정 봉), V3(실전 TR_ID), V4(마스킹 적용 지점) | 이번 Task와 무관, 미해결 유지 |

## 5. 사용자가 결정해야 할 질문

1. **`SystemExit` vs 예외** — 잘못된 `IS_MOCK_TRADING` 값은 `config` import 시점에 `SystemExit`를
   던진다. 따라서 `config`를 import하는 **테스트·도구도** 그 환경에서는 함께 죽는다. 별도 예외
   클래스(`ConfigError`)로 바꿔 잡을 수 있게 할지, 지금처럼 즉시 종료할지 결정 필요.
2. ~~**`TRADING_MODE` 환경변수 처리**~~ — **해결됨(후속 수정, 2026-10-18).** 사용자 지시로
   "경고 후 무시"에서 "불일치 시 즉시 종료"로 변경했다(`config.check_trading_mode_env()`).
   관련 테스트 14개 추가.
3. **`assert_order_allowed()`의 예외 종류** — `RuntimeError`로 던진다. `trader.py`의 주문 호출부는
   `except Exception`으로 잡아 로그만 남기므로, 실전 가드 위반이 "주문 실패"로 삼켜질 수 있다.
   전용 예외로 올려 상위에서 구분할지 결정 필요.

## 6. 다음 Task에 영향을 주는 사항

- **모드 판정 출처는 `config.IS_MOCK` 하나다.** 이후 Task에서 `TRADING_MODE`를 조건문에 쓰지 말 것
  (T01b 후 `grep`으로 코드상 사용처 0건 확인 — `main.py`는 로그에만, `config.py`는 유도·경고에만).
- `config.live_guard_error`의 인자가 `is_mock`으로 바뀌었다. 이 함수를 쓰는 코드는 시그니처를 확인할 것.
- 주문 함수 4개는 이제 첫 줄에서 `assert_order_allowed()`를 호출한다. 주문 함수에 다른 사전 검증을
  추가할 때 이 호출보다 **뒤**에 두면 안 된다(가드가 먼저 와야 한다).
- `tests/test_live_guard.py`는 T01 판을 대체했다. T01 보고서의 "테스트 37개"는 이제 85개이며,
  T01 보고 3항의 개수는 T01 시점 값으로 읽어야 한다.
- `TRADING_MODE` 환경변수는 이제 **일치하지 않으면 종료 사유**다. `.env`에 남아 있다면
  `IS_MOCK_TRADING`과 일치하는지 확인하고, 아니라면 제거할 것.

---

# T02 — 지표 검증 및 일봉 히스토리 캐시 (2026-10-19)

## 1. 완료 여부

**완료.** 단, 두 가지는 "확인 필요"로 남겼다(4항). 지표 검증은 라이브러리 값을 정답으로
삼지 않고 **정의(고전 Wilder)를 기준으로** 검증하는 방식으로 확정했다.

## 2. 변경 파일과 핵심 요약

| 파일 | 변경 |
|---|---|
| `indicators.py` | `sma()`, `atr()`, `rsi_series()`, `INSUFFICIENT_DATA` 추가. 전 함수가 데이터 부족 시 예외 없이 `None` 반환으로 통일. `rsi(2)`는 별도 함수 없이 `rsi(closes, 2)`로 지원 |
| `history.py` | **신규**. 일봉 CSV 캐시(`data/bars/{symbol}.csv`) + 메타(`{symbol}.meta.json`). 증분 append, date 중복 제거(새 값 우선), 정렬. 원자적 쓰기(temp→replace). `get_confirmed_bars()`가 당일 미확정 봉 제외 |
| `history.py` (규칙 9) | naive `date.today()`/`datetime.now()` → **KST tz-aware**로 수정. 시스템 로컬 시간이 KST와 다른 환경에서 확정봉 경계가 하루 어긋나는 문제 |
| `tests/test_indicators.py` | `sma`/`atr`/`rsi(2)` 골든값 테스트 9개 추가(외부 의존성 없음) |
| `tests/test_history.py` | **신규**. 저장·병합·확정봉 규칙 16개 |
| `tests/test_indicator_verification.py` | **신규**. 라이브러리 대조 9개(dev 의존성 없으면 skip) |
| `requirements-dev.txt` | **핀 오류 수정**(아래) |

### 지표 검증 결과 — RSI 시드 차이는 "버그"가 아니라 "정의 차이"

설치된 `pandas-ta`의 RSI 구현을 소스와 수치로 특정했다.

| | 자체 구현 (유지) | pandas-ta 0.4.71b0 |
|---|---|---|
| 시드 | 최초 14개 diff의 **SMA 평균** | `ewm(alpha=1/14, adjust=False)`, **첫 diff에서 재귀** |
| 첫 유효 index | 14 | 1 |
| 정의 | 고전 Wilder / TA-Lib 호환 | 다른 정의 |

실측 차이(n=600, 톱니 계열): index14 **14.35** → index100 0.024 → index300 9e-9 → index400 5e-12.
즉 **초기 구간만 다르고 장기적으로 수렴**한다. 따라서 라이브러리 값을 정답으로 놓고 자체 구현을
맞추면 오히려 틀린다. **자체 구현(SMA 시드)을 유지**하고, 테스트는
(1) 고전 Wilder 정의를 pandas로 독립 재현해 자체 구현과 전 구간 일치 확인,
(2) 라이브러리 값이 어떤 정의인지 재현해 특정(무엇을 비교 중인지 명시),
(3) 두 정의가 장기적으로 수렴함을 수치로 고정 — 세 방향으로 검증한다.
`test_library_rsi_seeding_scheme_is_identified`는 라이브러리 버전에 따라 분기하므로
pandas-ta가 0.3.x(SMA 시드)로 바뀌어도 통과한다.

그 외: 볼린저는 ddof=0(모표준편차) vs pandas 기본 ddof=1 — `sqrt(20/19)`≈1.026배 차이로
무시할 수 없어 테스트에 수치로 남겼고, 기존 특성화 값이 ddof=0을 고정하므로 **정의를 바꾸지 않았다.**
ATR은 라이브러리와 정확히 일치(둘 다 Wilder 평활).

### `merge_bars` 버그 수정

`merge_bars`가 date를 정규화하지 않아 `"20261020"`과 `"2026-10-20"`이 **서로 다른 날짜로
취급**됐다. 중복 제거가 깨지고 메타의 `min_date`가 원본 형식으로 남았다. 병합 시점에
`_norm_date`를 반드시 적용하도록 수정.

## 3. 테스트 결과

```
126 passed in 1.12s          # 전체
117 passed, 1 skipped        # pandas/pandas-ta 미설치 환경 재현 시
```

`tests/test_indicator_verification.py`는 dev 의존성이 없으면 정상 skip됨을
`ModuleNotFoundError`를 던지는 import blocker로 실제 확인했다. (KST 수정 후 128개)

## 4. 확인하지 못한 항목 (VERIFY)

| # | 항목 | 상태 |
|---|---|---|
| V1 | **KIS 일봉 응답의 날짜/시가/고가/저가/거래량 필드명** | 공식 샘플(`koreainvestment/open-trading-api`)이 `output2`를 DataFrame으로 넘길 뿐 키를 열거하지 않아 **확인 불가**. 종가 키(`clos`, `stck_clpr`)만 저장소 코드로 확인됨. 절대 규칙 4에 따라 `kis_rows_to_bars()`는 `NotImplementedError`. **CSV 직접 입력 경로만 사용 가능** |
| V2 | KIS 일봉 1회 호출 건수 상한 | 해외는 문서화된 상한 없음. 국내는 문서상 1회 최대 100건(종료일 기준) |
| V3 | `data/bars/*.meta.json`의 `adjusted` 값 신뢰성 | `MODP=1`/`FID_ORG_ADJ_PRC=0`(수정주가 반영)으로 요청하지만, 실제로 수정주가가 반영되는지는 실호출로 미확인 |

**반복 호출 가능 여부는 부분 확인**: 해외 일봉은 공식 샘플이 응답 헤더 `tr_cont`가 `"M"/"F"`일 때
연속조회로 재귀 호출하므로 **공식 지원됨**을 확인했다. 국내는 샘플에 연속조회 로직이 없고
1회 100건 제한이므로 기간을 나눠 호출해야 한다. 다만 V1 때문에 실제 수집은 아직 불가하다.

## 5. 사용자가 결정해야 할 질문

1. **`requirements-dev.txt` 핀 오류를 수정했다.** 이전 핀 `pandas==2.3.4`는 **존재하지 않는
   버전**이었고(2.3.3 다음이 3.0.0), `pandas-ta==0.3.14b0`도 실제 배포본과 달랐다. 실제 설치본
   조합(`pandas==3.0.6`, `pandas-ta==0.4.71b0`)으로 맞추고 `--pre` 필요성을 주석에 적었다.
   이 조합으로 확정할지, pandas-ta를 아예 빼고 검증을 자체 재현만으로 할지 결정 필요.
2. **RSI 시드**: 자체 구현(고전 Wilder, TA-Lib 호환)을 유지하기로 했다. 이 결정이 맞는지 확인 필요.
   T11 전략 확정 시 진입 필터가 특정 구현에 의존한다면 다시 논의해야 한다.
3. **KIS 일봉 수집 경로**: V1이 풀리기 전까지 `data/bars/`에 CSV를 직접 넣어야 한다.
   사용자가 CSV를 준비할지, 아니면 KIS 공식 문서에서 필드명을 확인해 이어서 구현할지 결정 필요.
4. ~~`get_confirmed_bars()`의 기준일이 naive `date.today()`~~ — **수정함.** 절대 규칙 9에 따라
   `datetime.now(KST).date()`로 바꾸고, 메타의 `updated_at`도 tz-aware KST로 통일했다.
   KST 기준 경계 테스트 2개 추가.

## 6. 다음 Task에 영향을 주는 사항

- **`rsi()`는 고전 Wilder 정의다.** 이후 Task에서 RSI 값을 외부 라이브러리·차트와 비교할 때
  초기 구간 차이는 정의 차이이며 버그가 아니다(400봉 이후 1e-12 수준 수렴).
- **데이터 부족 시 전 지표가 `None`을 반환**한다. `None`을 0으로 취급하면 매수 신호가 될 수 있다
  (RSI 0 = 극단 과매도). 진입 필터를 붙일 때 `None`을 명시적으로 처리할 것.
- **`evaluate_or_none()`은 예외를 삼킨다**(T01 보고 B-9). 조회 실패가 "신호 없음"으로 위장돼
  눌림목 매수로 이어질 수 있다. T04 대상.
- `history.py`의 `kis_rows_to_bars()`는 V1이 풀릴 때까지 `NotImplementedError`다.
  호출부를 만들지 말 것.
- `data/`는 `.gitignore`에 있다 → 일봉 캐시는 커밋되지 않는다(의도된 동작).

---

# T03 — 상태 영속화(SQLite)와 브로커 대조 (2026-10-20)

## 1. 완료 여부

**완료.** 재시작 후 읽기, 대조 4가지 시나리오, 자정·타임존 경계, DB 잠김·손상 시 시작 거부를
모두 테스트로 고정했다. 다만 진행 중 **매도 경로의 실제 안전 갭 1건**을 발견해 함께 막았다(2항).

## 2. 변경 파일과 핵심 요약

| 파일 | 변경 |
|---|---|
| `db.py` | **신규**. 표준 `sqlite3` 만 사용. 테이블 6개(`positions`/`orders`/`daily_counters`/`equity_log`/`kv`/`schema_version`) + `_MIGRATIONS` 기반 단순 마이그레이션. 모든 쓰기는 `transaction()`(예외 시 롤백 후 재던짐) |
| `state.py` | 메모리 dict → **DB 기반**으로 교체. 공개 시그니처 전부 유지(`bought_today_for`/`set_bought_today`/`buy_count_today`/`record_buy`/`profit_stage`/`set_profit_stage`/`last_buy_time`/`bought_today` 프로퍼티). `date.today()` 리셋 로직 제거 — `daily_counters` 의 날짜 키가 자연 해결 |
| `reconcile.py` | **신규**. `compare_positions()`(순수 함수) + `reconcile()`. 불일치 시 중단 플래그. `RECONCILE_AUTOFIX` 기본 false |
| `trader.py` | 주문을 `orders` 에, 포지션을 `positions` 에 기록. 사이클 시작 시 중단 플래그 확인. **외부 보유 매도 가드 추가(아래)** |
| `kis_overseas.py` / `kis_domestic.py` | `get_all_holdings()` 추가 — 기존 잔고 조회와 **같은 엔드포인트·TR_ID·파라미터**, 이미 확인된 필드만 사용 |
| `main.py` | 시작 시 DB 검증(실패 → `SystemExit(1)`), 매 사이클 시작 시 `reconcile()` |
| `config.py` | `DB_PATH`, `RECONCILE_AUTOFIX` 추가 |

### 진행 중 발견한 안전 갭 — 외부 보유가 매도될 수 있었다

**요구사항은 "브로커에만 있는 보유는 봇이 매도하지 않음"인데, 실제 매도 경로가 이를 지키지 않았다.**
`_check_sell()` 은 매도 수량을 **브로커 잔고**에서 받으므로, DB 에 `EXTERNAL` 로 표시돼 있어도
`RECONCILE_AUTOFIX=true` + `resume_trading()` 이후에는 손절·익절 규칙으로 **팔 수 있었다.**
`_check_sell()` 앞에 상태 확인을 넣어 막았다.

- `EXTERNAL` 포지션 → 매도하지 않음(로그 경고). 봇의 관리 대상이 아니다.
- **상태 조회 자체가 실패하면 팔지 않는다**(모르면 주문하지 않는다). 실패를 "포지션 없음"으로
  처리하면 외부 보유를 팔게 된다.
- DB 에 포지션 행이 아예 없으면 막지 않는다. 여기서 막으면 기록 누락이 영구 매도 불가가 된다
  (불일치는 `reconcile()` 이 잡는 몫).

### 트랜잭션 / 실패 동작

- 모든 쓰기가 `transaction()` 을 쓴다. 마이그레이션만 예외적으로 BEGIN/COMMIT 을 직접 관리한다
  (`executescript` 가 실행 전 암묵적 COMMIT 을 해서 `with conn:` 과 함께 쓰면 원자성이 깨짐 — 코드에 근거 명시).
- **잠김·손상·경로 문제 → `DatabaseUnavailable` → 시작 거부.** `main()` 이 `SystemExit(1)`.
  매 사이클에서도 `DatabaseUnavailable` 이면 그 사이클을 건너뛴다.
- `OSError`(mkdir 실패 등)도 `DatabaseUnavailable` 로 변환한다. 그대로 새면 호출부의
  `except DatabaseUnavailable` 이 못 잡고 봇이 뜬다.

### 남용 방지: 테스트 격리

`tests/conftest.py` 가 `config.DB_PATH` 를 **모든 테스트에서 임시 경로로** 바꾼다(autouse).
이 장치가 없으면 `BotState()` 를 인자 없이 만드는 테스트 하나가 실제 상태 DB 를 덮어쓸 수 있다.
`bot_state` 는 import 시점에 DB 를 열지 않는 지연 프록시(`_LazyBotState`)다.

## 3. 테스트 결과

```
235 passed in 2.68s
```

- 진행 중 실패 1건을 고쳤다: `test_last_buy_time_advances_on_new_buy` 가 시각을
  `2026-10-20T23:30` 으로 하드코딩해, 실제 시각이 그 날짜를 지나자 실패했다. 기준 시각을
  첫 매수 시각 기준 상대값으로 바꿔 시간 비의존적으로 만들었다.
- DB 잠김 테스트가 기본 5초 대기를 그대로 써서 스위트가 2.8초 → 10.7초로 느려졌다.
  `db.BUSY_TIMEOUT_MS` 상수로 빼고 테스트에서 낮춰 0.7초로 되돌렸다.

## 4. 확인하지 못한 항목 (VERIFY)

| # | 항목 | 상태 |
|---|---|---|
| V1 | `orders.broker_order_no` | KIS 주문 응답의 주문번호 필드명(ODNO 등)이 저장소 코드·공식 샘플에서 확인되지 않아 **NULL 로 둔다**(절대 규칙 4). `# VERIFY` 주석. T05 대상 |
| V2 | 체결 반영의 정확성 | 접수(`rt_cd=="0"`)만 보고 포지션에 반영한다. **미체결이면 DB 와 브로커가 어긋나고 다음 사이클에 중단 플래그가 선다.** 이것은 의도된 안전장치지만, 체결 조회가 들어오기 전까지는 사람이 확인해야 한다 |
| V3 | `get_all_holdings()` 의 필드명 | 기존 잔고 조회 함수가 이미 쓰던 필드(`ovrs_pdno`/`ovrs_cblc_qty`/`pchs_avg_pric`, `pdno`/`hldg_qty`/`pchs_avg_pric`)만 사용. **실제 응답으로 미검증**(모의 서버 호출 없음) |
| V4 | 해외 잔고의 `get_balance()` vs `get_all_holdings()` 범위 | `get_all_holdings()` 는 `OVRS_EXCG_CD=""`(전체 거래소)로 조회한다. 기존 `get_balance()` 와 동일한 파라미터지만, 다거래소 보유 시 동작은 실호출 미확인 |

## 5. 사용자가 결정해야 할 질문

1. **자동 보정(`RECONCILE_AUTOFIX`) 기본값을 false 로 두었다.** 켜면 봇이 만들지 않은 보유까지
   DB 에 편입되고, 그 뒤로는 봇이 관리 대상으로 오인할 여지가 생긴다. 소액 실전에서는
   "중단 + 사람 확인"이 안전하다고 판단했다. 이 기본값이 맞는지 확인 필요.
2. **불일치가 해소돼도 중단 플래그를 자동 해제하지 않는다.** 일시적 오류로 잘못 풀리는 것을
   막기 위해서인데, 그러면 사람이 `reconcile.resume_trading()` 을 호출할 때까지 봇이 멈춘다.
   알림(T09)이 붙기 전까지는 **멈춘 줄 모르고 지나갈 위험**이 있다. 이 위험을 감수할지,
   아니면 별도로 "중단됨"을 눈에 띄게 남길지(예: 매 사이클 CRITICAL 로그) 결정 필요.
3. **`profit_stage`(익절 단계)는 재시작 시 0으로 돌아간다**(DB가 아니라 메모리). "당일 도달 단계"
   성격이라 의도한 동작으로 두었으나, 재시작이 잦으면 같은 단계를 다시 익절할 수 있다.
   DB 로 옮길지 결정 필요.
4. **V2 위험**: 체결 확인이 없어 미체결 주문이 다음 사이클에 불일치로 잡힌다. T04/T05 에서
   체결 조회를 넣기 전까지 모의운용에서 중단이 잦을 수 있다.

## 6. 다음 Task에 영향을 주는 사항

- **`state.py` 공개 시그니처는 그대로다.** 내부만 DB 로 바뀌었고 `bot_state.xxx` 사용처는
  수정 없이 동작한다. `bot_state` 는 이제 지연 프록시이므로 `isinstance` 검사에 쓰면 안 된다.
- **모든 매매 경로가 중단 플래그를 먼저 본다.** `run_trading_cycle()` 진입부와 `main.job()` 의
  `reconcile()`. 새 매매 경로를 추가하면 같은 확인을 넣어야 한다.
- **포지션을 기록하지 않는 주문은 불일치로 잡힌다.** 주문을 내는 코드를 새로 만들면
  `orders`/`positions` 기록을 반드시 함께 넣어야 한다(안 넣으면 첫 사이클에 중단된다).
- **매도 가드**: `_check_sell(..., market=...)` 에 `market` 을 넘기지 않으면 외부 보유 가드가
  동작하지 않는다(`None` 이면 건너뜀). 새 매도 경로는 `market` 을 반드시 넘길 것.
- `db.BUSY_TIMEOUT_MS`, `db.SCHEMA_VERSION`, `db._MIGRATIONS` 는 테스트가 monkeypatch 한다.
  이름을 바꾸면 테스트도 함께 고쳐야 한다.
- 스키마를 바꿀 때는 `_MIGRATIONS` **끝에 추가**하고 `SCHEMA_VERSION` 을 올린다.
  기존 항목을 수정하면 이미 적용된 DB 와 어긋난다.
- `data/bot.db` 는 아직 만들어지지 않았다(테스트는 임시 DB만 씀). 첫 실행 시 `main()` 이 만든다.

