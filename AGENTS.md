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
6. 모르는 것·판단이 필요한 것은 **추측으로 구현하지 말고** 최종 보고 끝에 `질문:` 줄로 남기세요.
7. `try/except: pass`를 쓰지 마세요. 모든 예외는 로그 + 상태에 반영하세요.
8. 요청 범위 밖의 대규모 리팩터링, 불필요한 의존성 추가는 하지 마세요. 새 의존성은 이유를 최종 보고에 적으세요.
9. 시간은 항상 tz-aware (`Asia/Seoul`, 미국은 `America/New_York`). naive datetime은 쓰지 마세요.
10. 백테스트 성능을 높이려고 임계값을 반복 조정하거나, 신호가 없다고 조건을 임의로 완화하지 마세요. 사실은 그대로 보고하세요.
11. **증거 없는 "완료"를 쓰지 마세요.** 완료·통과·해결은 실제로 실행한 명령의 출력이나 다시 열어 본 파일 내용으로 증명되는 것만 적고, 테스트 개수는 직접 센 값만 적으세요. 파일을 고쳤다면 다시 열어 바뀐 줄을 확인하기 전에는 "완료"라고 쓰지 마세요. 실행하지 못한 명령(검증 명령 포함)은 "미실행"이라고 적으세요.

## 작업 방식
- 한 번에 **지정된 Task 하나만** 수행하세요. 다음 Task의 기능을 미리 만들지 마세요.
- 브랜치 `improve/tNN-짧은이름`에서 작업하세요. `git status` 확인·브랜치 전환·커밋은 사용자가 합니다.
- 변경마다 테스트를 작성하세요(테스트 파일이 TASK의 Files에 없으면 작성하지 말고 `질문:`으로 알리세요). 실행은 사용자가 하며, 커밋은 검증 명령이 통과한 상태에서만 합니다. 검증 명령: `.venv/bin/python -m pytest -o addopts="" -q` — Bash 규칙이 막고 있으니 Claude는 실행하지 말고 이 명령을 출력만 하세요.
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
- 중단 종류: STATE(상태 불신, 모든 주문 차단) / RISK(손실 한도·연속 오류·UNKNOWN 시한 초과, 신규 매수만 차단·청산 계속) / `HALT` 파일(모든 주문 차단, 사람이 직접 삭제). 중단 상태를 읽다 실패하면 닫으세요(모든 주문 차단).
- 매도는 `positions`에 status=OPEN 행이 있을 때만(`_may_sell_holding`), 매수는 EXTERNAL 보유 위에 쌓지 마세요(`_may_buy_into`). 모르면 팔지 마세요.
- 판단은 `strategy.decide()`만 고치세요. `trader.py`에 판단 코드를 다시 넣으면 백테스트와 실전이 갈라집니다. 순수 함수(`decide`·`evaluate_exit`·`compute_qty`)는 I/O·시계·설정을 직접 읽지 말고 인자로 받으세요.
- 새 주문 코드는 `orders`·`positions` 기록을 함께 만드세요(안 하면 첫 사이클에 reconcile이 중단합니다).
- DB 스키마는 `db._MIGRATIONS` **끝에만** 추가하고 `SCHEMA_VERSION`을 올리세요. 기존 항목은 수정하지 마세요.
- 알림·로그·DB 사유(`orders.raw_response`·중단 사유)에 예외 문구를 쓰지 마세요. `{e}`·`str(e)`·`repr(e)`·`exc_info=True`·`logger.exception(`·`traceback.*` 는 모두 금지입니다. `requests` 예외 메시지에는 요청 URL(쿼리의 계좌 구분 값·토큰이 들어갈 수 있음)이 들어 있습니다. `errfmt.exc_summary(e)`(예외 타입·HTTP 상태·프로젝트 안 위치·원인 타입만)를 쓰세요. `tests/test_exc_leak_scan.py` 가 루트 `*.py` 를 AST 로 검사하며 어기면 테스트가 실패합니다.
- 테스트는 `tests/conftest.py`가 DB·일봉 경로·텔레그램·HALT 파일을 격리합니다. 안전 규칙 테스트는 규칙을 깨는 변이를 넣었을 때 실제로 실패하는지 확인하세요.

## 우선순위 충돌 시
안전성(주문·상태·킬스위치) > 판단 로직 일관성 > 검증 > 편의.

## 모듈 구조
구조 파악이 필요할 때만 `docs/modules.md`를 읽으세요. 미사용(T09b 시점): `broker.py`, `trader._check_sell` — T12에서 통합·삭제를 결정합니다.

## 알려진 제한 (규칙에 가까운 것만 — 전체 미해결 목록은 `docs/CHANGES.md`의 VERIFY 항목)
- 해외(`IS_OVERSEAS_ENABLED`)는 체결조회·취소·시장가가 미구현이므로 `False`로 유지하세요.
- 국내 `cancel_order`는 미구현입니다(취소 요청의 `ORD_QTY`/`ORD_UNPR` 규칙이 `docs/kis_api_fields.md`에 없음). 미체결이 남으면 그 종목의 신규 매수를 막고 CRITICAL로 남깁니다.
- 신 TR_ID와 지정가(`00`)를 모의 서버가 받아들이는지는 확인되지 않았습니다. `docs/manual_checks.md` 절차로 사람이 확인합니다.

## 전략 수치
전략 수치(진입·청산·사이징·유니버스)는 모두 T11 백테스트 전의 가설입니다. 근거 없이 조정하지 마세요. 목표값과 구현 상태는 `docs/strategy.md`, 실제 기본값은 `README.md`의 환경 변수 표를 보세요.
- 청산 우선순위: 손절 > 시간청산 > 목표가 > 트레일링 > 과매수. 청산은 전량이고 사유는 하나입니다.
- **qty가 0이면 주문하지 않습니다**(최소 1주 강제 매수 금지) — `INSUFFICIENT_CAPITAL`과 필요 자본을 로그에 남깁니다.
