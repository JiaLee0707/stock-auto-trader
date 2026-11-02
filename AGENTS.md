# 작업 지침
- 모든 응답과 설명은 한국어로 작성하세요.
- 코드 주석도 한국어로 작성하세요.
- 변수명, 함수명 등 코드 식별자는 영어를 유지하세요.

# AGENTS.md — KIS 자동매매 봇 공통 규칙 (모든 작업에 적용)

## 프로젝트 성격
한국투자증권(KIS) OpenAPI로 ETF를 자동매매하는 Python 3.12 봇. macOS 로컬 실행, `schedule`로 1시간 주기.
**최종 목적은 실제 자금(20만원) 운용**입니다. 최우선 가치는 "수익"이 아니라 "**잘못된 주문이 나가지 않는 것**".
현재는 모의투자(가상 1,000만원) 단계입니다.

## 절대 규칙 (위반 금지)
1. **실전 주문이 나갈 수 있는 코드를 실행하지 마세요.** 모드 판정은 `IS_MOCK_TRADING` 하나로만 하세요(기본 `true`=모의, `true`/`false` 외 값이면 종료). `IS_MOCK_TRADING=false`(실전)는 `CONFIRM_LIVE=YES_I_UNDERSTAND` 환경변수가 없으면 시작 즉시 종료하고, 주문 함수도 직접 호출 시 차단합니다. `TRADING_MODE`는 표시용일 뿐 판정에 쓰지 마세요. (T01/T01b에서 구현)
2. **`.env`, API 키, 토큰, 계좌번호를 출력·로그·커밋하지 마세요.** 로그의 계좌번호는 마스킹하세요(`****1234`).
3. **주문(매수/매도/취소) API는 절대 자동 재시도하지 마세요.** 재시도는 읽기 전용 조회에만 허용합니다. (타임아웃 후 재시도 = 중복 주문)
4. **KIS API의 엔드포인트/TR_ID/파라미터를 추측하지 마세요.** 기존 코드의 것을 우선 따르고, 새로 필요한 것은 공식 문서·공식 GitHub 샘플(`koreainvestment/open-trading-api`)로 확인하세요. 확인 불가하면 구현하지 말고 `NotImplementedError` + TODO로 두고 "확인 필요"로 보고하세요. 모의/실전 TR_ID가 다름에 주의하세요.
5. **수치(수수료율, 세율, 임계값)는 하드코딩하지 않고 `config.py` 설정값으로** 두세요. 근거가 불확실하면 `# VERIFY:` 주석을 다세요. 전략 수치는 **모두 검증 전 가설**입니다.
6. 모르는 것·판단이 필요한 것은 **추측으로 구현하지 말고** 작업 보고서에 질문으로 남기세요.
7. `try/except: pass`를 쓰지 마세요. 모든 예외는 로그 + 상태에 반영하세요.
8. 요청 범위 밖의 대규모 리팩터링, 불필요한 의존성 추가는 하지 마세요. 새 의존성은 이유를 보고서에 적으세요.
9. 시간은 항상 tz-aware (`Asia/Seoul`, 미국은 `America/New_York`). naive datetime은 쓰지 마세요.
10. 백테스트 성능을 높이려고 임계값을 반복 조정하거나, 신호가 없다고 조건을 임의로 완화하지 마세요. 사실은 그대로 보고하세요.
11. **증거 없는 "완료"를 쓰지 마세요.** 완료·통과·해결은 명령 출력(`pytest -o addopts="" -q`의 마지막 줄, `git diff --stat` 등)으로 증명되는 것만 적고, 테스트 개수는 직접 센 값만 적으세요. 명령을 실행하지 못했으면 "미실행"이라고 적으세요.

## 작업 방식
- 한 번에 **지정된 Task 하나만** 수행하세요. 다음 Task의 기능을 미리 만들지 마세요.
- 브랜치 `improve/tNN-짧은이름`에서 작업하세요. `git status` 확인·브랜치 전환·커밋은 사용자가 합니다(`## TASK` 인수인계에서는 Bash·git을 실행하지 마세요 — `~/.claude/CLAUDE.md`).
- 변경마다 테스트를 작성하세요. 실행은 사용자가 하며, 커밋은 `pytest -o addopts="" -q`가 통과한 상태에서만 합니다.
- 기존 공개 함수 시그니처는 가능하면 유지하세요. 바꾸면 보고서에 명시하세요.
- `docs/CHANGES.md` 보고는 사용자가 요청할 때만, 아래 형식으로 **추가**(덮어쓰기 금지)하세요:
  1. Task 번호/이름, 완료 여부(완료/부분/미완료+이유)
  2. 변경 파일과 핵심 요약
  3. 테스트 결과 요약
  4. 확인하지 못한 항목 (`# VERIFY` 목록)
  5. 사용자가 결정해야 할 질문
  6. 다음 Task에 영향을 주는 사항

## 주문·상태 코드 규약
- 주문 함수(`send_order`·`buy_shares`·`sell_all`·`buy_domestic`·`sell_domestic_all`·`cancel_order`)의 첫 문장은 `config.assert_order_allowed()`로 두세요.
- `trader._submit_order`의 첫 두 문장은 `config.assert_order_allowed()` → `risk.assert_order_permitted()` 순서로 두세요. 중복 확인·`orders` 기록보다 앞이어야 합니다(거부된 주문이 SUBMITTED 행을 남기면 이후 손절까지 막힙니다). `tests/test_halt_wiring.py`가 AST로 고정합니다.
- 주문을 감싸는 곳에서는 `except risk.TradingHalted`를 `except Exception`보다 **앞에** 두고 CRITICAL로 남기세요.
- 중단 해제(`reconcile.resume_trading`)는 `risk.py` CLI(`python -m risk resume --reason "..."`)에서만 호출하세요. 로그·문서 안내에 사유 없이 해제하는 방법을 쓰지 마세요(소스 스캔 테스트가 막습니다).
- 중단 종류: STATE(상태 불신, 모든 주문 차단) / RISK(손실 한도, 신규 매수만 차단·청산 계속) / `HALT` 파일(모든 주문 차단, 사람이 직접 삭제). 중단 상태를 읽다 실패하면 닫으세요(모든 주문 차단).
- 매도는 `positions`에 status=OPEN 행이 있을 때만(`_may_sell_holding`), 매수는 EXTERNAL 보유 위에 쌓지 마세요(`_may_buy_into`). 모르면 팔지 마세요.
- 판단은 `strategy.decide()`만 고치세요. `trader.py`에 판단 코드를 다시 넣으면 백테스트와 실전이 갈라집니다. 순수 함수(`decide`·`evaluate_exit`·`compute_qty`)는 I/O·시계·설정을 직접 읽지 말고 인자로 받으세요.
- 새 주문 코드는 `orders`·`positions` 기록을 함께 만드세요(안 하면 첫 사이클에 reconcile이 중단합니다).
- DB 스키마는 `db._MIGRATIONS` **끝에만** 추가하고 `SCHEMA_VERSION`을 올리세요. 기존 항목은 수정하지 마세요.
- 알림·로그에 `str(e)`를 쓰지 마세요. `requests` 예외 메시지에는 URL(토큰)이 들어 있습니다. 예외 타입명과 HTTP 상태 코드만 남기세요.
- 테스트는 `tests/conftest.py`가 DB·일봉 경로·텔레그램·HALT 파일을 격리합니다. 안전 규칙 테스트는 규칙을 깨는 변이를 넣었을 때 실제로 실패하는지 확인하세요.

## 우선순위 충돌 시
안전성(주문·상태·킬스위치) > 판단 로직 일관성 > 검증 > 편의.

## 모듈 구조 (T09a 시점으로 갱신)
```
config.py         설정(env 엄격 파싱), assert_order_allowed()·live_guard_error(), mask(). 옛 설정명 경고
db.py             sqlite3 상태 저장소(positions·orders·equity_log·kv·halt_events·realized_pnl). 마이그레이션 현재 v4
state.py          DB 기반 상태(당일 매수 횟수·시각), 지연 프록시 bot_state
reconcile.py      브로커↔DB 대조, 중단 플래그(halt_trading(kind=STATE|RISK)·resume_trading·halt_events 이력)
risk.py           킬스위치 게이트(cycle_gate·assert_order_permitted·TradingHalted), HALT 파일, 재개 CLI(python -m risk)
notifier.py       텔레그램 알림. 설정 없으면 no-op, 실패해도 예외를 던지지 않고 토큰은 로그에 남기지 않음
models.py         frozen dataclass(MarketSnapshot·PositionView·AccountView·DayCounters·Decision)와 HOLD 사유 상수
strategy.py       decide() — 매수·청산·수량 판단의 단일 진입점 (순수 함수)
exit_rules.py     evaluate_exit() — 청산 규칙 (순수 함수)
sizing.py         compute_qty() — 수량 계산·자본 상한 (순수 함수)
indicators.py     rsi/bollinger/sma/atr (순수) + fetch_closes/evaluate_or_none (I/O)
history.py        일봉 CSV 캐시(data/bars), get_confirmed_bars(당일 미확정 봉 제외)
market_calendar.py  exchange_calendars 래퍼 — 개장·휴장·주문 구간(안전 마진)
kis_overseas.py   KIS 해외 API. request_read(재시도 가능)/request_order(재시도 없음), _request는 조회용 하위 호환 별칭
kis_domestic.py   KIS 국내 API. order_body/send_order/get_order_status/get_all_holdings, cancel_order 미구현
ai_analyzer.py    KIS 시세 + FRED VIX + RSS 뉴스 → Gemini 점수(-100~100)
trader.py         오케스트레이션: 수집→reconcile→decide()→_submit_order→_settle_order. 판단 코드는 넣지 않음
main.py           1시간 스케줄러. 시작 시 DB 검증·가드, 사이클마다 reconcile→정규장 구간 판정→run_trading_cycle
```
미사용(T09a 시점): `broker.py`(어댑터 — `_run_cycle`이 받기만 하고 호출하지 않음), `trader._check_sell`(운영 호출 없음, 테스트만 부름). T12에서 통합·삭제를 결정합니다.

## 알려진 제한 (규칙에 가까운 것만 — 전체 미해결 목록은 `docs/CHANGES.md`의 VERIFY 항목)
- 해외(`IS_OVERSEAS_ENABLED`)는 체결조회·취소·시장가가 미구현이므로 `False`로 유지하세요.
- 국내 `cancel_order`는 미구현입니다(취소 요청의 `ORD_QTY`/`ORD_UNPR` 규칙이 `docs/kis_api_fields.md`에 없음). 미체결이 남으면 그 종목의 신규 매수를 막고 CRITICAL로 남깁니다.
- 신 TR_ID와 지정가(`00`)를 모의 서버가 받아들이는지는 확인되지 않았습니다. `docs/manual_checks.md` 절차로 사람이 확인합니다.
- 전략 수치는 모두 T11 백테스트 전의 가설입니다.
- T00 시점의 문제 목록은 `docs/archive/CLAUDE.known_issues_T00.md`에 보관했습니다(현재 사실 아님).

## 전략 기본값 (**T06·T07에서 코드에 반영됨, T11 결과로 확정**)
청산(T06)·사이징(T07) 항목은 코드에 반영됐습니다. 나머지는 아직 목표값입니다.
**모든 수치는 검증 전 가설이며 T11 백테스트 결과로 확정합니다** — 근거 없이 조정하지 마세요.
| 항목 | 목표값 | 현재 코드 |
|---|---|---|
| 진입 필터 | 종가 > SMA(200) | **구현됨, 기본 꺼짐**: ENTRY_TREND_FILTER_ENABLED (T08). 켜면 확정 봉 200개가 필요 |
| 진입 신호 | RSI(14) ≤ 35 AND 종가 ≤ BB 하단(20, 2σ) | RSI ≤ 30 (RSI_OVERSOLD) |
| 청산 | +3% / -3% / 최대 7거래일 | **반영됨**: TAKE_PROFIT_PCT / STOP_LOSS_PCT / MAX_HOLD_TRADING_DAYS (T06) |
| 동시 보유 | 최대 2종목 | **반영됨**: MAX_OPEN_POSITIONS=2 (T07) |
| 하루 신규 매수 | 최대 1건 | MAX_BUYS_PER_DAY=2 |
| 1회 위험 | 자산의 1% | **반영됨**: RISK_PER_TRADE_PCT=1.0 (T07) |
| 유니버스 | 국내 상장 ETF 5~10종, 1주 3만원 이하, 레버리지/인버스 제외 | `config/universe.yaml` **파일 없음**. 하드코딩 2종목(QQQ, 379800) |

T06 이후 청산 규칙 (순수 함수 `exit_rules.evaluate_exit`, 우선순위 순):
손절 > 시간청산 > 목표가 > 트레일링 > 과매수. **청산은 전량이고 사유는 하나**입니다.
목표 익절(+3%)·손절(-3%)·보유 7거래일은 반영됐고, ATR 손절·트레일링·과매수 청산은
설정으로만 켤 수 있게 두었습니다(기본 꺼짐 — 검증되지 않은 신호입니다).

T07 이후 수량 계산 (`sizing.compute_qty`, 순수 함수):
`qty = min(위험 기준, 현금/포지션 기준)`. 위험 기준은 `floor(자산×RISK_PER_TRADE_PCT / (진입가×손절폭))`,
현금 기준은 `floor(min(현금, 자산×MAX_POSITION_PCT) / 진입가)`입니다.
손절폭은 `exit_rules.effective_stop_loss_pct`(T06)에서 가져오므로 ATR 손절을 켜면
수량이 자동으로 줄어듭니다. 기존 비중 방식(`BUY_RATIO`/`DOMESTIC_BUY_RATIO`)은 삭제했습니다.
**qty가 0이면 주문하지 않습니다**(최소 1주 강제 매수 금지) — `INSUFFICIENT_CAPITAL`과
필요 자본을 로그에 남깁니다.

현재 코드의 실제 기본값은 `README.md`의 환경 변수 표를 참고하세요.
