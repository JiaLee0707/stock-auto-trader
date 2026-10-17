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

### 🎯 포지션 관리 (스노우볼 전략)
- **정수 수량 매수**: KIS 해외주식 현금 매수 주문 (`ORD_DVSN=00` 지정가)
- **진입 타점 (보조지표)**: `RSI(14) ≤ 30` **이고** 종가가 볼린저 하단(20, 2σ) 이하일 때만 매수
  → 하락 추세에서 무작정 물타기하지 않고 과매도 확인 후 진입
- **청산 타점 (보조지표)**: `RSI(14) ≥ 70` **이고** 종가가 볼린저 상단 이상이면 익절 단계 1회 실행
  → 단, **수익 중일 때만** 동작하고 한 번에 한 단계만 매도
- **분할 매수**: 총자산의 해외 15% / 국내 10%씩, 종목당 하루 최대 `MAX_BUYS_PER_DAY`(2)회
  → 하루 최대 노출 해외 30% / 국내 20%, 평단 대비 `DIP_THRESHOLD` 하락 시마다 추가 매수
- **부분 익절**: `TAKE_PROFIT_STEPS`(기본 +2% 1/3, +4% 1/3, +6% 전량) 단계별 분할 매도
  → 급등으로 여러 단계를 건너뛰면 도달한 단계를 한 번에 모두 실행
- **손절**: `STOP_LOSS_THRESHOLD`(기본 -5%) 이하 시 전량 매도 (0 이하 설정 시 비활성)
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
- **실데이터 수집**: KIS 실시간 시세(QQQ·379800 현재가/등락률, USD/KRW) + RSS 뉴스 헤드라인
- **Gemini API 통합**: 수집한 데이터만을 근거로 시장 감정 분석 (날짜만 주던 방식 제거)
- **JSON 응답 파싱**: 구조화된 감정 점수
- **재시도**: 429/503 일시 오류 시 3회 재시도, 최종 실패 시 중립(0) 처리
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

#### 9. **indicators.py** — 보조지표 (RSI + 볼린저밴드)

#### 10. **kis_domestic.py** — 국내 ETF 매매 (구현 완료)
- **시세·잔고**: `get_domestic_price` (원화), `get_domestic_balance`, `get_orderable_cash`
- **주문**: `buy_domestic` / `sell_domestic_all` — 지정가(`ORD_DVSN=01`), `/trading/order-cash` 엔드포인트
- **모의/실전 TR_ID 분기**: 매수 `VTTC0802U`/`TTTC0802U`, 매도 `VTTC0801U`/`TTTC0801U`
- **실패 감지**: 응답 `rt_cd != 0`이면 예외 발생 (주문 실패를 성공으로 오인하지 않음)
- **기본 대상**: TIGER 미국나스닥100 (379800)

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

# AI 분석용 뉴스 수집 (RSS, 콤마로 여러 개 지정 가능)
NEWS_FEEDS=https://news.google.com/rss/search?q=nasdaq+stock+market&hl=en-US&gl=US&ceid=US:en,https://feeds.finance.yahoo.com/rss/2.0/headline?s=QQQ&region=US&lang=en-US
NEWS_MAX_ITEMS=10
NEWS_TIMEOUT_SEC=8

# 운용 시장 (미지정 시 모의=True, 실전=False — 실전 소액자본은 해외 비활성)
# 실전에서 해외를 켜려면 1주 가격(QQQ 약 98만원) 이상의 1회 매수 예산이 필요
IS_OVERSEAS_ENABLED=True

# 투자 대상 (해외)
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# 투자 대상 (국내 ETF, 해외와 동시 운용)
IS_DOMESTIC_ENABLED=True
DOMESTIC_TICKER=379800
DOMESTIC_TICKER_NAME=TIGER 미국나스닥100

# 거래 매개변수
BUY_RATIO=0.15           # 해외 1회 매수 비중 (15% × 2회 = 하루 최대 30%)
DOMESTIC_BUY_RATIO=0.10  # 국내 1회 매수 비중 (10% × 2회 = 하루 최대 20%)
DIP_THRESHOLD=-1.5
AI_SCORE_FLOOR=-50

# 보조지표 (RSI + 볼린저밴드) — 매수·매도 타점
USE_INDICATORS=True
RSI_PERIOD=14
RSI_OVERSOLD=30          # RSI 이하 + 볼린저 하단 이탈 → 매수
RSI_OVERBOUGHT=70        # RSI 이상 + 볼린저 상단 돌파 → 익절 트리거
BB_PERIOD=20
BB_NUM_STD=2.0

# 스노우볼 전략
MAX_BUYS_PER_DAY=2
TAKE_PROFIT_STEPS=2.0:33,4.0:33,6.0:100   # 부분 익절 (수익률%:매도비중%)
STOP_LOSS_THRESHOLD=-5.0                  # 손절선 (%), 0 이하면 비활성
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
- **환율**: KIS 해외시세 환율 (`/uapi/overseas-price/v1/quotations/inquire-daily-chartprice`, `FX@KRW`), 실패 시 1350원 기본값

#### Gemini API 통합
- **모델**: `gemini-3.1-flash-lite`
- **SDK**: `google-genai` (`genai.Client`)
- **입력**: 실시간 시세(KIS) + RSS 뉴스 헤드라인 — 날짜만 주면 학습 시점 일반론만 반복하므로 실제 데이터를 주입
- **응답 형식**: JSON 전용 (`response_mime_type="application/json"`)
- **온도 (Temperature)**: 0.2 (통제된 응답)
- **재시도**: 429(할당량)·503(과부하) 시 3초 간격 3회 재시도
- **대체 로직**: 최종 실패 시 기본 중립 점수(0) 사용 — 단, 실패와 중립이 로그에서 구분됨

#### 뉴스 수집 (RSS)
- **기본 피드**: Google News(나스닥 시장), Yahoo Finance(QQQ)
- **파싱**: 표준 라이브러리 `xml.etree.ElementTree` 사용 (추가 패키지 불필요)
- **정렬**: 발행시각 최신순, 중복 제거 후 `NEWS_MAX_ITEMS`건만 사용
- **실패 시**: 뉴스 없이 시세만으로 분석 (전체 분석은 계속 진행)

### 모의투자 운영규정 (실전투자와 다름)

아래는 한국투자증권 **모의투자** 운영규정 중 코드 동작에 영향을 주는 항목입니다. 실전투자에는 적용되지 않는 항목을 `실전` 열에 표시했습니다.

#### 해외주식

| 항목 | 내용 | 실전투자 | 코드 반영 |
|------|------|----------|-----------|
| 투자 원금 | 미국 USD 100,000 (참가신청 시 지급) | 실제 예수금 | - |
| 통합증거금 | 국내 원화 + 외화 예수금 합산해 주문가능금 사용 | 동일 | `get_balance()`가 외화 + 국내 원화 예수금(`dnca_tot_amt`)을 **합산**해 `cash_krw` 반환 |
| 호가 방식 | 지정가·시장가·최유리지정가·최우선지정가 (조건부지정가·IOC·FOK 미적용) | 동일 | `buy_shares()`/`sell_all()`은 지정가(`ORD_DVSN=00`)만 사용 |
| 주문 유형 | 현금매도/매수, 현금미수만 허용 (신용·공매도 불가) | 동일 | - |
| 예약주문 | 당일 15:40 ~ 익일 07:30 | 동일 | 미사용 |
| 거래시간 | **정규장 주문만 체결** (시간외매매 불가) | 시간외 체결 가능 | `main.py`가 **모의투자에서만** 정규장(09:30~16:00 ET) 확인 |
| 결제 | 3일 결제 | 동일 | - |
| 거래정지종목 | 주문접수는 가능하나 체결되지 않음 | - | - |
| 권리발생종목 | 액면분할·병합·감자 시 주식수 조정 전까지 매매 불가 | - | - |
| 신규상장종목 | 최초 상장일 동시호가 시간엔 주문접수 불가 | - | - |

#### 국내주식

| 항목 | 내용 | 실전투자 | 코드 반영 |
|------|------|----------|-----------|
| 투자 원금 | 개인설정 1,000만 / 3,000만 / 5,000만 / 1억 / 5억원 | 실제 예수금 | - |
| 투자 기간 | 개인설정 1·2·3개월 (변경 불가, 변경하려면 참가취소 후 재신청) | - | - |
| 거래일·시간 | 실제 주식시장과 동일 적용, **시간외매매 불가** | 시간외 체결 가능 | 모의투자는 KST 09:00~15:30만 주문 (`is_domestic_market_open()`) |
| 주문 유형 | 현금매도·현금매수·현금미수만 허용 (신용·공매도 불가) | 동일 | - |
| 호가 방식 | 지정가·시장가·최유리지정가·최우선지정가 (조건부지정가·IOC·FOK 미적용) | 동일 | 지정가(`ORD_DVSN=01`)만 사용 |
| 예약주문 | 당일 15:40 ~ 익일 07:30, 동시호가에 제출 | 동일 | 미사용 |
| 투자대상종목 | 거래소·코스닥 전 종목(매매제한종목 제외), ETF/ELW/ETN | 동일 | - |
| 매매제한종목 | 현재가 1,000원 미만, 총발행주식수 100,000주 미만, 거래정지, 실거래 없던 종목 | - | - |
| 신규상장종목 | 최초 상장일 동시호가 시간엔 주문접수 불가 (시초가 결정 후 가능) | - | - |
| 권리발생종목 | 대회기간 중 유·무상 및 배당 등 권리발생종목은 권리발생 금액을 현금 입금 처리. 권리락 시 보유수량 × (전일종가 − 권리락가격) 금액 보정 | - | - |
| 반대매매 | 결제일 미수금 발생 시 시행. 미수금 발생 종목 우선 선정, 부족 시 종목코드 빠른 순. 실시간 거래량이 1주라도 발생하면 반대매매 전량 동일가 일괄매도체결 | - | - |
| 결제 | 3일 결제 (결제순서: ELW → 코스닥종목 → 거래소종목, 각 시장 내 종목번호 빠른 순, 매도주문결제 후 매수주문결제) | 동일 | - |
| 체결 | 동시호가 및 장중거래. 주문 제출 이후 실제 시장에서 체결된 거래량 변동분만 체결. 상한가 매수·하한가 매도 시 실제시장 총잔량 대비 체결량 비율을 적용해 주문량에 해당 비율만 체결 | 동일 | - |
| 수수료·제세금 | 코스피: 수수료 0.0142%, 세금 0.20% (농특세 0.15% + 거래세 0.05%)<br>코스닥: 수수료 0.0142%, 세금 0.20%<br>ETF/ELW/ETN: 수수료 0.0148%, 세금 없음 | 동일 | - |

**주의**
- 위 표의 `실전` 열이 "동일"이 아닌 항목(투자원금, 거래시간)이 모의투자에서만 적용되는 규정입니다. 2026-10-13 작업에서 정규장 제한을 모드 구분 없이 넣었던 것을 모의투자 전용으로 수정했습니다.
- **초당 거래건수 제한**: KIS 모의투자는 호출 제한이 낮아 0.5초 간격 호출 시 HTTP 500(`초당 거래건수를 초과하였습니다`)이 간헐 발생합니다. `kis_overseas._request()`가 모든 호출 전 1초 이상 간격을 보장하고 500 응답은 1.5초 후 재시도합니다.
- 모의투자 통합증거금은 간소화 적용되어 실제 서버와 차이가 있을 수 있습니다.
- `main.py`의 정규장 판정은 평일 여부만 확인하므로 미국 휴장일은 별도로 처리하지 않습니다. 서머타임은 ET 기준 판정이라 자동 반영됩니다.
- 국내주식 매매는 구현되어 `run_domestic_cycle()`로 실행됩니다. 모의투자는 국내·해외 모두 정규장 주문만 체결되므로, 국내는 KST 09:00~15:30에만 주문합니다(`is_domestic_market_open()`).

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
├── indicators.py               # 보조지표 (RSI + 볼린저밴드)
├── kis_domestic.py             # 국내 ETF API (구현 완료)
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

# 운용 시장 (미지정 시 모의=True, 실전=False — 실전 소액자본은 해외 비활성)
# 실전에서 해외를 켜려면 1주 가격(QQQ 약 98만원) 이상의 1회 매수 예산이 필요
IS_OVERSEAS_ENABLED=True

# 투자 대상 (해외)
TARGET_TICKER=QQQ
TARGET_TICKER_NAME=Invesco QQQ Trust

# 투자 대상 (국내 ETF, 해외와 동시 운용)
IS_DOMESTIC_ENABLED=True
DOMESTIC_TICKER=379800
DOMESTIC_TICKER_NAME=TIGER 미국나스닥100

# 거래 매개변수
BUY_RATIO=0.15           # 해외 1회 매수 비중 (15% × 2회 = 하루 최대 30%)
DOMESTIC_BUY_RATIO=0.10  # 국내 1회 매수 비중 (10% × 2회 = 하루 최대 20%)
DIP_THRESHOLD=-1.5
AI_SCORE_FLOOR=-50

# 보조지표 (RSI + 볼린저밴드) — 매수·매도 타점
USE_INDICATORS=True
RSI_PERIOD=14
RSI_OVERSOLD=30          # RSI 이하 + 볼린저 하단 이탈 → 매수
RSI_OVERBOUGHT=70        # RSI 이상 + 볼린저 상단 돌파 → 익절 트리거
BB_PERIOD=20
BB_NUM_STD=2.0

# 스노우볼 전략
MAX_BUYS_PER_DAY=2
TAKE_PROFIT_STEPS=2.0:33,4.0:33,6.0:100   # 부분 익절 (수익률%:매도비중%)
STOP_LOSS_THRESHOLD=-5.0                  # 손절선 (%), 0 이하면 비활성
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
- `collect_market_snapshot()`: KIS 시세·환율 수집
- `fetch_news_headlines()`: RSS 헤드라인 수집
- `build_prompt()`: 수치 + 헤드라인으로 프롬프트 구성
- `analyze_market_sentiment()`: Gemini 호출, 점수 반환

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

#### 1. 향상된 위험 관리
- **손절매**: 동적 손절매 구현
- **포지션 크기 조정**: 켈리 기준, 고정 비율 방식
- **포트폴리오 재조정**: 자산 할당 유지

#### 2. 성과 분석
- **상세 거래 로그**: 전체 거래 기록
- **성과 분석**: 승리율, 평균 수익률
- **자동 보고서**: 일일/주간 성과 요약

#### 3. 추가 기능
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
