# KIS ETF 자동매매 봇 (나스닥 스노우볼)

한국투자증권(KIS) OpenAPI로 국내 상장 ETF를 자동매매하는 Python 3.12 봇. 해외 ETF(QQQ) 코드는 있으나 체결조회·취소·시장가가 미구현이라 꺼 두며(`IS_OVERSEAS_ENABLED=false`), 켜면 시작 시 종료합니다.
macOS 로컬에서 `schedule`로 1시간 주기 실행.

## 현재 상태

**모의투자 검증 단계이며, 실전 투입 불가 상태입니다.**

- 매도 체결이 **한 번도 발생한 적이 없음** — 익절/손절 로직이 실제로 발동한 실적 없음
- 이 사실은 `logs/bot.log`로 확인 가능. 2026-10-18 기준 "매도"가 들어간 줄 39건 중 13건이 `매도 조건 미충족`이고, 매도 주문 실행 기록은 0건이다 (`grep -cE "매도 주문 완료" logs/bot.log` → 0, 매수 주문 완료는 9건)
- 자동화 테스트는 **T01에서 최소 도입** (pytest). 지표 특성화·실전 가드에서 시작해 T08에서 판단 로직 전체(`tests/test_decide.py`)로 넓어짐
- 실전 가드가 **T01/T01b에서 추가됨**. `IS_MOCK_TRADING=false`(실전)이고 `CONFIRM_LIVE=YES_I_UNDERSTAND`가 없으면 시작 즉시 종료하며, 주문 함수(`buy_shares`·`sell_all`·`buy_domestic`·`sell_domestic_all`·`send_order`·`cancel_order`)도 직접 호출 시 차단 (`tests/test_live_guard.py`)

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
| `MAX_DRAWDOWN_PCT` | `10.0` | `CAPITAL_CAP_KRW` 기준 고점 대비 하락 한도(%). 0 이하·100 초과면 시작 거부. **국내 사이클마다 평가하고, 한도에 닿으면 RISK 중단을 겁니다(T09b-2b)** |
| `DAILY_LOSS_LIMIT_PCT` | `3.0` | `CAPITAL_CAP_KRW` 기준 당일 손실 한도(%). 0 이하·100 초과면 시작 거부. **국내 사이클마다 평가하고, 한도에 닿으면 RISK 중단을 겁니다(T09b-2b)** |
| `HALT_ACTION` | `hold` | 중단 시 보유 포지션 처리. `hold`만 구현, `liquidate`는 시작 시 거부 (T09b) |
| `GEMINI_TIMEOUT_SEC` | `30` | Gemini 요청 제한시간(초). 0 초과 300 이하. 응답이 멈춰 매매 사이클이 밀리는 것을 막는다 (T09b) |
| `MAX_CONSECUTIVE_ERRORS` | `5` | 매매 사이클이 이 횟수만큼 **연속으로** 대조·API·네트워크 오류로 실패하면 RISK 중단을 겁니다(1시간 주기라 5회 = 5시간). 1 이상의 정수여야 하고 아니면 시작 거부. 정상 완료한 사이클이 한 번 있으면 0으로 돌아가며, 프로세스 메모리에만 있어 재시작하면 0에서 시작합니다 (T09b-2c) |
| `UNKNOWN_TIMEOUT_MINUTES` | `120` | 응답 불명(UNKNOWN) 주문이 생성 후 이 시간(분) 이상 확정되지 않으면 RISK 중단을 겁니다. 0 초과의 유한한 숫자여야 하고 아니면 시작 거부. `ORDER_TIMEOUT_MINUTES`(미체결 취소 시도)와는 별개입니다 (T09b-2c) |
| `BARS_MAX_LAG_SESSIONS` | `0` | 확정 일봉 최신성(T09b-6). 마지막 확정 봉이 직전 거래일보다 이 거래일 수를 넘겨 오래됐거나 최신성을 확인하지 못하면 **신규 매수만 보류**합니다(청산은 계속). 0 이상 5 이하의 정수여야 하고 아니면 시작 거부이며 끌 수 없습니다. 일봉을 사람이 CSV로 넣는 동안은 매일 갱신하지 않으면 그날 매수가 보류됩니다 (T09b-6) |
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
| `DB_PATH` | `data/bot.db` | 상태 SQLite 경로 (T03). 이 DB 를 처음 쓴 모드(모의/실전)를 기록하고 다른 모드로는 시작을 거부합니다(T09b-4). 실전에는 모의와 다른 새 경로를 쓰세요 |
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
어떤 API 호출보다 먼저 종료합니다. 또한 주문 함수(`buy_shares`·`sell_all`·`buy_domestic`·`sell_domestic_all`·`send_order`·`cancel_order`)가 각자 시작 시 같은 검사를 하고 `_submit_order`도 `orders` 기록 전에 같은 검사를 하므로,
`main()`을 거치지 않고 직접 호출돼도 주문이 나가지 않습니다.

## 모의 수동 주문 확인 (T09c)

봇은 매수 신호가 나야만 주문을 내기 때문에, 신 TR_ID·지정가를 모의 서버가 받는지 확인할 주문이 없을 수 있습니다. 이 도구는 신호와 상관없이 **사람이 직접** 1주짜리 시험 주문을 냅니다. 모의투자에서만 동작하고, 기본은 미리보기라서 아무것도 보내지 않습니다. 저장소 루트에서 실행하고, 절차와 확인 항목은 [docs/manual_checks.md](docs/manual_checks.md) 5절에 있습니다.

```bash
.venv/bin/python -m manual_order buy              # 미리보기(아무것도 보내지 않음)
.venv/bin/python -m manual_order buy --send       # 화면의 확인 문장을 그대로 입력하면 1주 지정가 매수를 낸다
.venv/bin/python -m manual_order sell [--send]    # 봇이 산 포지션이 있을 때만
.venv/bin/python -m manual_order settle           # 체결 결과를 DB 에 반영한다(주문은 내지 않는다)
```

종목은 `DOMESTIC_TICKER`, 수량은 1주(`MANUAL_ORDER_MAX_QTY`, 환경변수로 바꿀 수 없음), 주문은 지정가입니다. 전송은 봇과 같은 `_submit_order`(킬스위치 포함) 한 곳으로만 나가고, 주문 구간이 아니면 거부합니다. 국내 취소가 구현돼 있지 않아 체결되지 않은 주문은 KIS 모의 앱에서 직접 취소해야 합니다. 봇(`main.py`)이 실행 중일 때는 쓰지 마세요.

## 매매 중단과 재개 (T03·T09a)

봇은 상태를 믿을 수 없을 때 스스로 멈춥니다. 멈춘 이유에 따라 막히는 범위가 다릅니다.

| 종류 | 언제 | 막히는 것 |
|---|---|---|
| STATE | 브로커 보유와 DB 포지션이 어긋나는 등(`reconcile`) 상태를 믿을 수 없을 때 | 매수·매도 모두 (보유 종목도 정리되지 않음) |
| RISK | 손실 한도. **낙폭·일일 손실 한도, 손익 미확인 매도, 기준선 손상은 국내 사이클이 평가해 걸고(T09b-2b), 연속 사이클 오류(`MAX_CONSECUTIVE_ERRORS`)와 응답 불명(UNKNOWN) 주문 시한 초과(`UNKNOWN_TIMEOUT_MINUTES`)는 `main.job()` 이 겁니다(T09b-2c)** | 신규 매수만 (청산·손절은 계속) |
| HALT 파일 | 사람이 프로젝트 루트(또는 `HALT_FILE` 경로)에 파일을 만들었을 때 | 매수·매도 모두 |

중단 상태나 HALT 파일을 읽다가 실패해도 모든 주문을 막습니다(실패 시 닫힘).
중단 중에는 매 사이클 `🚨` CRITICAL 로그가 사유와 해제 방법과 함께 찍힙니다.

```bash
grep "중단 중" logs/bot.log | tail -1
```

**자동으로 해제되지 않습니다.** 로그로 원인을 확인한 뒤 사람이 직접 해제해야 합니다.

재개할 때 알아둘 점이 둘 있습니다.

- 응답 불명(UNKNOWN) 주문 때문에 걸린 중단은, 그 주문이 체결 조회로 확정되지 않은 채 남아 있으면 재개해도 다음 사이클에 다시 걸립니다. 재개하기 전에 브로커에서 그 주문을 직접 확인하세요.
- 연속 사이클 오류 횟수는 봇 프로세스의 메모리에만 있습니다. 봇이 켜진 채 재개하면 다음 실패 한 번에 바로 다시 걸릴 수 있고, 성공한 사이클이 한 번 있으면 0으로 돌아갑니다.

```bash
.venv/bin/python -m risk status                           # 게이트, 중단 종류·사유·시작 시각, HALT 파일, 최근 이력
.venv/bin/python -m risk resume --reason "확인한 내용"      # 사유 필수. 이력(halt_events)에 남는다
```

- 사유가 비어 있으면 거부하고 상태를 바꾸지 않습니다.
- HALT 파일이 있으면 거부합니다. 이 명령은 파일을 지우지 않으니 직접 삭제한 뒤 다시 실행하세요.
- 중단 상태가 아니면 아무것도 하지 않고 이력도 남기지 않습니다.
- STATE 중단은 해제하기 전에 DB 포지션을 확인하고 브로커 잔고와 맞추세요.

```bash
.venv/bin/python -c "import db; c=db.open_db(); print(db.list_positions(c), db.list_positions(c, status='EXTERNAL')); c.close()"
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
.venv/bin/python -c "import config, db, state, reconcile, risk, notifier, models, strategy, exit_rules, sizing, indicators, history, market_calendar, broker, kis_overseas, kis_domestic, ai_analyzer, trader, main; print('imports OK')"
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
| [AGENTS.md](AGENTS.md) | 코드 작업 시 지켜야 할 규칙, 모듈 구조, 알려진 제한 (에이전트 지침의 정본. `CLAUDE.md` 는 이를 불러오는 한 줄 파일) |
| [docs/CHANGES.md](docs/CHANGES.md) | 작업 이력 (append-only) |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 개발 환경, 브랜치/커밋 규칙, 수동 검증 절차 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | 문제 해결 |
| [docs/manual_checks.md](docs/manual_checks.md) | 모의 서버에서 사람이 직접 확인하는 절차(신 TR_ID·지정가 등) |
| [docs/kis_api_fields.md](docs/kis_api_fields.md) | KIS API 필드·TR_ID 사실 모음(포털 표 기준. 여기 없는 API는 추측 금지) |
| [docs/modules.md](docs/modules.md) | 모듈 구조 (AGENTS.md에서 분리) |
| [docs/strategy.md](docs/strategy.md) | 전략 기본값과 구현 상태 (AGENTS.md에서 분리) |
| [docs/archive/](docs/archive/) | 이전 문서 보관 (내용이 낡음. 참고용으로만) |

코드 구조는 [AGENTS.md](AGENTS.md)의 "모듈 구조" 절을 참고하세요.

## 참고 링크

- [KIS API 포털](https://apiportal.koreainvestment.com/apis)
- [KIS 공식 샘플 (open-trading-api)](https://github.com/koreainvestment/open-trading-api)
- [Gemini API 문서](https://ai.google.dev/api)

---

최초 작성 2026-10-13, 최종 갱신 2026-10-28 (T09a: 중단·재개 절차, 주문 함수 서술)
