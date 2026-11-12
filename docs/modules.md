# 모듈 구조 (AGENTS.md에서 이동)

## 모듈 구조 (T09b 시점으로 갱신)
```
config.py         설정(env 엄격 파싱), assert_order_allowed()·live_guard_error(), mask(), bot_mode_error()(DB 모드 판정, 순수). 옛 설정명 경고
db.py             sqlite3 상태 저장소(positions·orders·equity_log·kv·halt_events·realized_pnl). 마이그레이션 현재 v4. DB 모드 기록(verify_bot_mode·has_state_history, kv `bot_mode`)
state.py          DB 기반 상태(당일 매수 횟수·시각), 지연 프록시 bot_state
reconcile.py      브로커↔DB 대조, 중단 플래그(halt_trading(kind=STATE|RISK)·resume_trading·halt_events 이력)
risk.py           킬스위치 게이트(cycle_gate·assert_order_permitted·TradingHalted), HALT 파일, 재개 CLI(python -m risk), 손실 한도(evaluate_loss_limits·compute_bot_equity·evaluate_and_enforce), 연속 오류(ErrorStreak)·UNKNOWN 시한(enforce_unknown_deadline)
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
trader.py         오케스트레이션: 수집→reconcile→손실 한도 평가(_make_loss_guard)→decide()→_submit_order→_settle_order. 판단 코드는 넣지 않음
main.py           1시간 스케줄러. 시작 시 DB 검증·가드(_verify_db_mode: 이 DB 를 쓴 모드와 다르면 종료), 사이클마다 reconcile→UNKNOWN 시한 점검→정규장 구간 판정→run_trading_cycle. 연속 사이클 오류 집계(_cycle_failed)
```
미사용(T09b 시점): `broker.py`(어댑터 — `_run_cycle`이 받기만 하고 호출하지 않음), `trader._check_sell`(운영 호출 없음, 테스트만 부름). T12에서 통합·삭제를 결정합니다.

