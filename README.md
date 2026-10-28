# KIS ETF 자동매매 봇 (나스닥 스노우볼)

한국투자증권(KIS) OpenAPI로 국내 상장 ETF와 해외 ETF(QQQ)를 자동매매하는 Python 3.12 봇.
macOS 로컬에서 `schedule`로 1시간 주기 실행.

## 현재 상태

**모의투자 검증 단계이며, 실전 투입 불가 상태입니다.**

- 매도 체결이 **한 번도 발생한 적이 없음** — 익절/손절 로직이 실제로 발동한 실적 없음
- 이 사실은 `logs/bot.log`로 확인 가능. 2026-10-18 기준 "매도"가 들어간 줄 39건 중 13건이 `매도 조건 미충족`이고, 매도 주문 실행 기록은 0건이다 (`grep -cE "매도 주문 완료" logs/bot.log` → 0, 매수 주문 완료는 9건)
- 자동화 테스트는 **T01에서 최소 도입** (pytest). 지표 특성화·실전 가드에서 시작해 T08에서 판단 로직 전체(`tests/test_decide.py`)로 넓어짐
- 실전 가드가 **T01/T01b에서 추가됨**. `IS_MOCK_TRADING=false`(실전)이고 `CONFIRM_LIVE=YES_I_UNDERSTAND`가 없으면 시작 즉시 종료하며, 주문 함수 4개도 직접 호출 시 차단 (`tests/test_live_guard.py`)

## 판단과 실행의 분리 (T08)

**매매 판단은 `strategy.decide()` 하나이며, 순수 함수입니다** — I/O·시간 조회·DB 접근이 없습니다.
`trader.py` 는 오케스트레이션만 합니다: 데이터 수집 → `decide()` → 주문 → DB 갱신.
목적은 **백테스트와 실전이 같은 판단 함수를 쓰게** 하는 것입니다.

```
config/설정 ──┐
확정 일봉 ────┼→ MarketSnapshot ─┐
브로커 잔고 ──┼→ AccountView    ─┼→ strategy.decide() ─→ Decision ─→ trader (주문·DB)
상태 DB ──────┴→ PositionView/DayCounters ─┘   (순수)                      (부작용)
```

부작용이 있는 안전장치들은 **일부러 `decide()` 밖에** 둡니다 — 주문 함수의
`config.assert_order_allowed()` 실전 가드, `EXTERNAL` 매도 차단, 주문 직전 중복 확인,
주문 직전 브로커 잔고 재확인입니다. 이들을 판단 함수로 끌어오면 조회 실패 시
"모르는 값을 판단 입력으로" 넣게 됩니다.

### 동작이 바뀐 부분: 지표는 **확정 봉**만 씁니다

판단에 쓰는 종가는 **전일까지의 확정 종가**입니다(`history.get_confirmed_bars`, T02).
**장중에 형성 중인 당일 봉은 신호에 들어가지 않습니다.** T07까지는 KIS 일봉(당일 봉 포함)을
그대로 썼기 때문에, 오전에 매수 조건이 충족됐다가 그 봉이 다시 그려지면서 오후에
미충족으로 뒤집힐 수 있었습니다. 이제 판단은 하루 동안 고정되고 **주문만** 장중에 나갑니다.

백테스트의 체결 가정은 `BACKTEST_FILL_MODEL`(기본 `next_open`)로 명시합니다 —
"전일 확정 종가로 신호를 내고 다음 거래일 시가에 체결된다"는 가정입니다.
실전은 신호가 나온 사이클의 장중 가격으로 주문하므로, 이 차이를 백테스트 결과에
적용할 때 반드시 고려해야 합니다.

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
| `IS_MOCK_TRADING` | `True` | **모드 판정의 유일한 기준.** `true`/`false`(대소문자 무시)만 허용. 그 외 값이면 즉시 종료. `true`=모의(openapivts:29443), `false`=실전(openapi:9443) |
| `CONFIRM_LIVE` | (없음) | `IS_MOCK_TRADING=false`(실전)일 때 `YES_I_UNDERSTAND`여야 시작·주문. 없으면 즉시 종료 (T01b) |
| `TRADING_MODE` | (유도값) | **모드 판정에 쓰지 않음.** 표시용 읽기 전용 값으로 `IS_MOCK_TRADING`에서 유도(`paper`/`live`). 환경변수로 다른 값을 넣으면 즉시 종료 (T01b) |
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
| `DOMESTIC_TICKER` | `379800` | 국내 매매 대상 (KODEX 미국S&P500TR) |
| `DOMESTIC_TICKER_NAME` | `KODEX 미국S&P500TR` | 국내 종목 표시명 |
| `NEWS_FEEDS` | Google News(나스닥), Yahoo Finance(QQQ) | AI 분석용 RSS, 콤마 구분 |
| `NEWS_MAX_ITEMS` | `10` | 뉴스 헤드라인 최대 건수 |
| `NEWS_TIMEOUT_SEC` | `8` | RSS 요청 타임아웃(초) |
| `RISK_PER_TRADE_PCT` | `1.0` | 1회 매매에서 감수할 최대 손실(자산 대비 %). 손절 시 손실이 이 값을 넘지 않게 수량 결정 (T07) |
| `MAX_POSITION_PCT` | `45.0` | 한 종목 최대 투입 금액 (자산 대비 %). 위험 기준이 허용해도 이 선은 넘지 않음 (T07) |
| `MAX_OPEN_POSITIONS` | `2` | 동시 보유 종목 수 상한. 도달 시 신규 진입만 차단(청산은 계속) (T07) |
| `MAX_TOTAL_EXPOSURE_PCT` | `90.0` | 총 노출(보유 평가액 합계) 상한 (자산 대비 %) (T07) |
| `CAPITAL_CAP_KRW` | `0` | 사용 자본 상한(원). **`0` = 상한 없음**. 설정하면 equity와 cash를 **둘 다** 이 값으로 제한해 모의투자에서도 실전 규모로 사이징 (T07) |
| `TELEGRAM_BOT_TOKEN` | (없음) | 텔레그램 봇 토큰. **`TELEGRAM_CHAT_ID`와 둘 다 있어야** 알림을 보낸다. 없으면 알림은 로그로만 남는다. 값은 `.env`에만 두고 채팅·로그에 붙이지 않는다 (T09a) |
| `TELEGRAM_CHAT_ID` | (없음) | 알림을 받을 텔레그램 채팅 ID (T09a) |
| `HALT_FILE` | (프로젝트 루트의 `HALT`) | 비상정지 파일 경로. 이 파일이 있으면 매수·매도 모두 중단. 상대경로는 프로젝트 루트 기준. 사람이 직접 지워야 풀린다 (T09a) |
| `DIP_THRESHOLD` | `-1.5` | 지표 폴백 시 평단 대비 매수 하락률(%) |
| `USE_INDICATORS` | `True` | RSI+볼린저 지표 진입 사용 |
| `RSI_PERIOD` | `14` | RSI 기간 |
| `RSI_OVERSOLD` | `30` | 이하면 매수 후보 |
| `RSI_OVERBOUGHT` | `70` | 이상이면 매도 후보 |
| `BB_PERIOD` | `20` | 볼린저밴드 기간 |
| `BB_NUM_STD` | `2.0` | 볼린저밴드 표준편차 배수 |
| `TAKE_PROFIT_PCT` | `3.0` | 목표가 익절(%). 도달 시 **전량** 매도. 0 이하면 비활성 (T06) |
| `STOP_LOSS_PCT` | `-3.0` | 손절선(%). 0 이하면 비활성 (T06) |
| `STOP_LOSS_ATR_MULT` | `0` | ATR 손절 배수. `0`이면 끔. 켜면 고정 %와 ATR 기반 중 **더 넓은 쪽** (T06) |
| `ATR_PERIOD` | `14` | ATR 기간 (T06) |
| `MAX_HOLD_TRADING_DAYS` | `7` | 보유 **거래일** 초과 시 전량 청산. 휴장일은 세지 않음 (T06) |
| `TRAILING_STOP_ENABLED` | `false` | 트레일링 스톱 사용 여부 (T06) |
| `TRAILING_ACTIVATE_PCT` | `2.0` | 이 수익률을 넘긴 뒤에 트레일링 활성 (T06) |
| `TRAILING_GAP_PCT` | `1.0` | 진입 후 고점 대비 이만큼 밀리면 청산 (T06) |
| `OVERBOUGHT_EXIT_ENABLED` | `false` | RSI≥70 AND 종가≥BB상단을 청산 사유로 쓸지 (T06) |
| `EXIT_ORDER_TYPE_STOP_LOSS` | `market` | 사유별 청산 주문 유형 (`market`/`limit`) (T06) |
| `EXIT_ORDER_TYPE_TIME_EXIT` | `market` | 〃 (T06) |
| `EXIT_ORDER_TYPE_TAKE_PROFIT` | `limit` | 〃 (T06) |
| `EXIT_ORDER_TYPE_TRAILING` | `market` | 〃 (T06) |
| `EXIT_ORDER_TYPE_OVERBOUGHT` | `market` | 〃 (T06) |
| `AI_SCORE_FLOOR` | `-50` | 매수 허용 최소 AI 점수 |
| `AI_FILTER_ENABLED` | `true` | AI 점수 필터 사용 여부 (T08). `true` = 현재 동작 유지. `false`면 점수를 못 받아도 매수. **T10에서 기본값 변경 예정** |
| `ENTRY_TREND_FILTER_ENABLED` | `false` | 진입 추세 필터(종가 > SMA) 사용 여부 (T08). 켜져 있는데 SMA를 계산할 수 없으면 **매수 보류** |
| `ENTRY_TREND_SMA_PERIOD` | `200` | 추세 필터의 SMA 기간 (T08) |
| `MAX_BUYS_PER_DAY` | `2` | 종목당 하루 최대 매수 횟수. **`0`이면 신규 매수 전면 차단** |
| `MIN_BUY_INTERVAL_HOURS` | `2.0` | 직전 매수 후 최소 대기(시간), 2회차부터 |
| `ORDER_TYPE` | `limit` | **매수** 주문 유형 기본값. 청산은 사유별 `EXIT_ORDER_TYPE_*` 가 우선 (T04/T06) |
| `DB_PATH` | `data/bot.db` | 상태 SQLite 경로 (T03) |
| `RECONCILE_AUTOFIX` | `false` | 브로커↔DB 불일치 시 자동 보정 여부. 기본은 보정 없이 **매매 중단**만 (T03) |
| `ORDER_PRICE_TICKS` | `1` | 지정가를 현재가에서 몇 호가 옮길지. 매수 +N틱, 매도 −N틱. `0`이면 현재가 그대로 (T04) |
| `ORDER_TIMEOUT_MINUTES` | `30` | 미체결 주문을 이 시간까지 기다렸다가 취소 (T04) |
| `BACKTEST_FILL_MODEL` | `next_open` | 백테스트 체결 가정 (T08). `next_open`(신호 다음 거래일 시가) / `close` / `next_close`. 실전 판단에는 쓰이지 않음 |

`IS_MOCK_TRADING` 값에 따라 KIS 도메인, 키, 토큰 캐시 파일이 자동 전환됩니다.
- 모의투자: `https://openapivts.koreainvestment.com:29443`, 캐시 `token_cache_mock.json`
- 실전투자: `https://openapi.koreainvestment.com:9443`, 캐시 `token_cache_real.json`

## 실행

```bash
.venv/bin/python main.py
```

시작 즉시 1회 실행 후 1시간 주기로 반복합니다. `Ctrl+C`로 종료.
시작 로그 첫 줄에 현재 모드와 도메인 호스트명이 찍힙니다(키·계좌번호는 출력하지 않음).

```bash
grep "현재 모드" logs/bot.log | tail -1    # 예: 🔒 현재 모드: 모의 | 도메인: openapivts.koreainvestment.com
```

**실전 가드**: `IS_MOCK_TRADING=false`(실전)인데 `CONFIRM_LIVE=YES_I_UNDERSTAND`가 없으면
어떤 API 호출보다 먼저 종료합니다. 또한 주문 함수 4개가 각자 시작 시 같은 검사를 하므로,
`main()`을 거치지 않고 직접 호출돼도 주문이 나가지 않습니다.

## 매매 중단 플래그 해제 방법

브로커 보유와 DB 포지션이 어긋나면(`reconcile`) 봇이 **신규 매매를 멈춥니다.**
이때 매 사이클마다 `🚨 신규 매매 중단 중` CRITICAL 로그가 사유와 함께 찍힙니다.

```bash
grep "신규 매매 중단" logs/bot.log | tail -1
```

**자동으로 해제되지 않습니다.** 로그로 불일치 내역을 확인한 뒤 사람이 직접 해제해야 합니다.

```bash
# 1) 사유와 현재 상태 확인
.venv/bin/python -c "import db, reconcile; c=db.open_db(); print(reconcile.is_trading_halted(c), reconcile.halt_reason(c)); print(db.list_positions(c)); c.close()"

# 2) 불일치를 확인·정리한 뒤 해제
.venv/bin/python -c "import db, reconcile; c=db.open_db(); reconcile.resume_trading(c); c.close()"
```

주문 상태가 `UNKNOWN`(타임아웃 등)으로 남아 있으면 그 종목은 조회로 확정될 때까지
신규 주문이 막힙니다. `ORDER_TIMEOUT_MINUTES`를 넘긴 미확정 주문은 봇이 취소를 시도하고,
취소까지 실패하면 `[reconcile] 주문 #.. 이 ...상태로 ...분을 넘겼습니다` ERROR 로그가 남습니다.

```bash
.venv/bin/python -c "import db; c=db.open_db(); [print(o['id'], o['symbol'], o['side'], o['status'], o['created_at']) for o in db.list_open_orders(c)]; c.close()"
```

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

모드 판정은 **`IS_MOCK_TRADING` 하나로만** 합니다(대소문자 무시, 앞뒤 공백 무시).
허용값은 `true`/`false`뿐이며, 그 외 값(오타·`0`·`yes` 등)이면 조용히 실전으로 넘어가지 않고 즉시 종료합니다.
미설정이면 `true`(모의)입니다.

| | 모의투자 | 실전투자 |
|---|---|---|
| `IS_MOCK_TRADING` | `true` | `false` |
| `CONFIRM_LIVE` | 불필요 | **`YES_I_UNDERSTAND` 필수** (없으면 종료) |
| KIS 도메인 | openapivts:29443 | openapi:9443 |
| 토큰 캐시 | `token_cache_mock.json` | `token_cache_real.json` |
| `IS_OVERSEAS_ENABLED` 기본값 | `true` | `false` |
| 주문 가능 시간 | 국내·해외 모두 정규장 내 안전 마진 구간 (T05, 아래 참고) | 동일 |
| 매도 TR_ID (해외) | `VTTS0308U` | `TTTS0308U` |
| 매수 TR_ID (해외) | `VTTT1002U` | `TTTT1002U` |
| 매수 TR_ID (국내) | `VTTC0012U` | `TTTC0012U` |
| 매도 TR_ID (국내) | `VTTC0011U` | `TTTC0011U` |
| 체결조회 TR_ID (국내) | `VTTC0081R` | `TTTC0081R` |
| 정정취소 TR_ID (국내) | `VTTC0013U` | `TTTC0013U` |

국내 TR_ID는 T04b(`docs/kis_api_fields.md` §1~§3)에서 신TR로 교체했습니다.
구TR(`VTTC0802U`/`TTTC0802U`, `VTTC0801U`/`TTTC0801U`, `VTTC0803U`/`TTTC0803U`)은
공식 문서가 "사전 고지 없이 막힐 수 있음"이라고 명시해 사용하지 않습니다.
해외 TR_ID는 `docs/kis_api_fields.md`에 해외 주문 API가 없어 **확인 필요**(`# VERIFY`)입니다.

### 주문 가능 시간 (T05)

`market_calendar.py`가 `exchange_calendars`의 `XKRX`/`XNYS` 캘린더로 판정합니다.
**휴장일 표를 직접 만들지 않습니다** — 설·추석 연휴, 크리스마스, 미국 반일장,
서머타임이 라이브러리에서 나옵니다.

주문 구간은 정규장보다 좁습니다. 개장 직후 시가는 안정되지 않고, 마감 직전 지정가는
체결되지 못한 채 미체결로 남기 때문입니다.

| 설정 | 기본값 | 뜻 |
|---|---|---|
| `SKIP_OPEN_MINUTES` | 10 | 개장 후 이 시간 동안 주문하지 않음 |
| `SKIP_CLOSE_MINUTES` | 10 | 마감 전 이 시간부터 주문하지 않음 (반일장은 그날 실제 마감 기준) |
| `EXTRA_HOLIDAYS` | `{}` | 라이브러리가 모르는 임시 휴장일. `{"KRX": ["2026-06-03"]}` 형식 |

주문 구간이 아니면 **AI 분석·시세 조회를 포함해 어떤 API도 호출하지 않고** 건너뜁니다.
브로커 ↔ DB 대조(`reconcile`)는 장 외에도 계속 실행됩니다 — 불일치는 장 외에 발견해야
개장 전에 고칠 수 있고, DB 쓰기를 멈추면 장 외에 쌓인 미체결 주문이 개장까지 방치됩니다.

`TRADING_MODE`는 모드 판정에 쓰이지 않습니다. `IS_MOCK_TRADING`에서 유도한 표시용 값이며,
환경변수로 다른 값을 넣으면 조용히 무시하지 않고 **즉시 종료**합니다(모드 판정이 두 곳으로 갈라지는 것을 막기 위함).

**경고**
- 실전 전환은 **현재 금지**입니다. 매도 체결 실적 0건, 매매 판단 로직에 대한 자동 테스트 없음 상태입니다.
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
