# KIS ETF 자동매매 봇 (나스닥 스노우볼)

한국투자증권(KIS) OpenAPI로 국내 상장 ETF와 해외 ETF(QQQ)를 자동매매하는 Python 3.12 봇.
macOS 로컬에서 `schedule`로 1시간 주기 실행.

## 현재 상태

**모의투자 검증 단계이며, 실전 투입 불가 상태입니다.**

- 매도 체결이 **한 번도 발생한 적이 없음** — 익절/손절 로직이 실제로 발동한 실적 없음
- 이 사실은 `logs/bot.log`로 확인 가능. 2026-10-18 기준 "매도"가 들어간 줄 39건 중 13건이 `매도 조건 미충족`이고, 매도 주문 실행 기록은 0건이다 (`grep -cE "매도 주문 완료" logs/bot.log` → 0, 매수 주문 완료는 9건)
- 자동화 테스트는 **T01에서 최소 도입** (pytest, `tests/`, 37개). 지표 특성화와 실전 가드만 덮으며 매매 판단 전체를 검증하지는 않음
- 실전 가드(`TRADING_MODE`/`CONFIRM_LIVE`)가 **T01에서 추가됨**. `TRADING_MODE=live`이고 `CONFIRM_LIVE=YES_I_UNDERSTAND`가 없으면 시작 즉시 종료 (`tests/test_live_guard.py`)

상세한 배경과 근거는 [docs/archive/REVIEW_PROMPT.md](docs/archive/REVIEW_PROMPT.md) 참고.

## 설치

```bash
cd /Users/jia/Documents/project/stock-auto-trader
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`.venv/bin/python` 을 직접 사용하거나, `.envrc`(direnv)가 있으면 `.venv/bin/activate`가 자동 실행됩니다.

Python 버전: `.python-version` = `3.12.15`

## 환경 변수

`.env` 파일에 설정합니다. **실제 키 값은 이 문서/저장소에 절대 기재하지 마세요.**

`config.py`에서 실제로 읽는 변수 전체 목록입니다 (기본값은 코드 기준).

| 변수 | 기본값 | 설명 |
|---|---|---|
| `TRADING_MODE` | `paper` | `paper`/`live`. 시작 로그 첫 줄에 출력됨. `live`는 아래 확인값 필수 (T01) |
| `CONFIRM_LIVE` | (없음) | `live`일 때 `YES_I_UNDERSTAND`여야 시작. 없으면 즉시 종료 (T01) |
| `IS_MOCK_TRADING` | `True` | `True`=모의투자(openapivts:29443), `False`=실전(openapi:9443) |
| `KIS_MOCK_APP_KEY` / `KIS_APP_KEY` | (없음) | 모의투자 앱키. `KIS_MOCK_*` 우선, 없으면 `KIS_APP_KEY` fallback |
| `KIS_MOCK_APP_SECRET` / `KIS_APP_SECRET` | (없음) | 모의투자 시크릿 |
| `KIS_MOCK_ACCOUNT_NO` / `KIS_ACCOUNT_NO` | (없음) | 모의투자 계좌번호 (`XXXXXXXX-XX`) |
| `KIS_REAL_APP_KEY` / `KIS_APP_KEY` | (없음) | 실전 앱키. `KIS_REAL_*` 우선, 없으면 `KIS_APP_KEY` fallback |
| `KIS_REAL_APP_SECRET` / `KIS_APP_SECRET` | (없음) | 실전 시크릿 |
| `KIS_REAL_ACCOUNT_NO` / `KIS_ACCOUNT_NO` | (없음) | 실전 계좌번호 |
| `GEMINI_API_KEY` | (없음) | Gemini 시장 분석용 키 |
| `TARGET_TICKER` | `QQQ` | 해외 매매 대상 |
| `TARGET_TICKER_NAME` | `QQQ` | 해외 종목 표시명 |
| `IS_OVERSEAS_ENABLED` | 모의=`True`, 실전=`False` | 해외 운용 여부. 실전 소액자본은 QQQ 1주(약 98만원)를 못 사서 기본 비활성 |
| `IS_DOMESTIC_ENABLED` | `True` | 국내 ETF 운용 여부 |
| `DOMESTIC_TICKER` | `379800` | 국내 매매 대상 (TIGER 미국나스닥100) |
| `DOMESTIC_TICKER_NAME` | `TIGER 미국나스닥100` | 국내 종목 표시명 |
| `NEWS_FEEDS` | Google News(나스닥), Yahoo Finance(QQQ) | AI 분석용 RSS, 콤마 구분 |
| `NEWS_MAX_ITEMS` | `10` | 뉴스 헤드라인 최대 건수 |
| `NEWS_TIMEOUT_SEC` | `8` | RSS 요청 타임아웃(초) |
| `BUY_RATIO` | `0.15` | 해외 1회 매수 비중 (총자산 대비) |
| `DOMESTIC_BUY_RATIO` | `0.10` | 국내 1회 매수 비중 |
| `DIP_THRESHOLD` | `-1.5` | 지표 폴백 시 평단 대비 매수 하락률(%) |
| `USE_INDICATORS` | `True` | RSI+볼린저 지표 진입 사용 |
| `RSI_PERIOD` | `14` | RSI 기간 |
| `RSI_OVERSOLD` | `30` | 이하면 매수 후보 |
| `RSI_OVERBOUGHT` | `70` | 이상이면 매도 후보 |
| `BB_PERIOD` | `20` | 볼린저밴드 기간 |
| `BB_NUM_STD` | `2.0` | 볼린저밴드 표준편차 배수 |
| `TAKE_PROFIT_THRESHOLD` | `5.0` | **구) 단일 익절 기준. 현재 매매 로직에서 사용되지 않음** |
| `AI_SCORE_FLOOR` | `-50` | 매수 허용 최소 AI 점수 |
| `MAX_BUYS_PER_DAY` | `2` | 종목당 하루 최대 매수 횟수 |
| `MIN_BUY_INTERVAL_HOURS` | `2.0` | 직전 매수 후 최소 대기(시간), 2회차부터 |
| `TAKE_PROFIT_STEPS` | `2.0:33,4.0:33,6.0:100` | 부분 익절 (`수익률%:매도비중%`) |
| `STOP_LOSS_THRESHOLD` | `-5.0` | 손절선(%), 0 이하면 비활성 |

`IS_MOCK_TRADING` 값에 따라 KIS 도메인, 키, 토큰 캐시 파일이 자동 전환됩니다.
- 모의투자: `https://openapivts.koreainvestment.com:29443`, 캐시 `token_cache_mock.json`
- 실전투자: `https://openapi.koreainvestment.com:9443`, 캐시 `token_cache_real.json`

## 실행

```bash
.venv/bin/python main.py
```

시작 즉시 1회 실행 후 1시간 주기로 반복합니다. `Ctrl+C`로 종료.

**주의**: 실전 주문을 막는 킬스위치가 아직 없습니다(T01 예정). 실행 전 `IS_MOCK_TRADING=True`인지 반드시 확인하세요.

### 로그 확인

```bash
tail -f logs/bot.log                        # 실시간
tail -20 logs/bot.log                       # 최근 20줄
grep "ERROR" logs/bot.log | tail -10        # 최근 오류
grep "미충족" logs/bot.log | tail -20        # 매수/매도 조건 미충족 사유
```

### 구문·임포트 확인

```bash
.venv/bin/python -m py_compile *.py
.venv/bin/python -c "import config, state, indicators, kis_overseas, kis_domestic, ai_analyzer, trader, main; print('imports OK')"
```

## 모의 ↔ 실전 전환

`.env`에서 `IS_MOCK_TRADING` 값을 바꿉니다.

| | 모의투자 | 실전투자 |
|---|---|---|
| `IS_MOCK_TRADING` | `True` | `False` |
| KIS 도메인 | openapivts:29443 | openapi:9443 |
| 토큰 캐시 | `token_cache_mock.json` | `token_cache_real.json` |
| `IS_OVERSEAS_ENABLED` 기본값 | `True` | `False` |
| 주문 가능 시간 | 국내 09:00~15:30 KST / 해외 09:30~16:00 ET 정규장만 | 정규장만 (코드가 동일하게 제한) |
| 매도 TR_ID (해외) | `VTTS0308U` | `TTTS0308U` |
| 매수 TR_ID (해외) | `VTTT1002U` | `TTTT1002U` |

**경고**
- 실전 전환은 **현재 금지**입니다. 매도 체결 실적 0건, 자동 테스트 없음, 킬스위치 없음 상태입니다.
- 실전 계좌로 전환하면 예외 없이 실제 주문이 나갈 수 있습니다.
- 전환 시 해외·국내 TR_ID 분기가 자동으로 바뀌지만, 이 분기가 실전 계좌에서 검증된 적은 없습니다 (`# VERIFY`).

## 문서 지도

| 문서 | 내용 |
|---|---|
| [README.md](README.md) | 이 문서. 설치·설정·실행의 단일 진입점 |
| [CLAUDE.md](CLAUDE.md) | 코드 작업 시 지켜야 할 규칙, 모듈 구조, 알려진 문제 |
| [docs/CHANGES.md](docs/CHANGES.md) | 작업 이력 (append-only) |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 개발 환경, 브랜치/커밋 규칙, 수동 검증 절차 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 문제 해결 |
| [docs/archive/](docs/archive/) | 이전 문서 보관 (내용이 낡음. 참고용으로만) |

코드 구조는 [CLAUDE.md](CLAUDE.md)의 "모듈 구조" 절을 참고하세요.

## 참고 링크

- [KIS API 포털](https://apiportal.koreainvestment.com/apis)
- [KIS 공식 샘플 (open-trading-api)](https://github.com/koreainvestment/open-trading-api)
- [Gemini API 문서](https://ai.google.dev/api)

---

최초 작성 2026-10-13, 최종 갱신 2026-10-18 (T00 문서 정리)
