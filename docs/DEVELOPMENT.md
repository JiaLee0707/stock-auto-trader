# 개발 가이드

설치·설정·실행 방법은 [README.md](../README.md) 참고. 이 문서는 개발 작업 규칙만 다룹니다.

## 개발 환경

- macOS, Python 3.12.15 (`.python-version`)
- 가상환경 `.venv` 사용. 전역 설치는 금지
- 의존성: `requirements.txt` (`pip freeze` 결과를 그대로 고정)
  - 직접 사용: `requests`, `python-dotenv`, `schedule`, `google-genai`
  - 나머지는 전이 의존성

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip freeze > requirements.txt   # 패키지 변경 후 갱신
```

## 자동화 테스트

실행: `.venv/bin/python -m pytest -o addopts="" -q`

테스트는 `tests/` 에 있습니다. 개발 전용 의존성은 `requirements-dev.txt` 에 있습니다.

설치: `.venv/bin/python -m pip install --pre -r requirements-dev.txt` (pandas-ta 가 pre-release 라서 `--pre` 가 필요합니다)

`tests/conftest.py` 가 모든 테스트에서 DB 경로, 일봉 캐시 경로, 텔레그램 설정, HALT 파일 경로를 임시 위치로 돌려, 테스트가 실제 `data/bot.db`·`data/bars`·텔레그램·프로젝트 루트의 `HALT` 에 닿지 않게 합니다.

아래 "수동 검증 절차"는 환경 점검용이며 테스트 통과를 대신하지 않습니다.

### 수동 검증 절차

```bash
# 1. 구문 검사
.venv/bin/python -m py_compile *.py

# 2. 임포트 검사 (config 를 읽으므로 .env 가 없으면 경고만 출력됨)
.venv/bin/python -c "import config, db, state, reconcile, risk, notifier, models, strategy, exit_rules, sizing, indicators, history, market_calendar, broker, kis_overseas, kis_domestic, ai_analyzer, trader, main; print('imports OK')"

# 3. 설정값 확인 (민감값은 출력하지 않음)
.venv/bin/python -c "import config; print(config.IS_MOCK, config.TARGET_TICKER, config.DOMESTIC_TICKER)"

# 4. 모의투자 첫 실행 (평일 정규장 시간에, 모의 모드에서만. 실전은 시도하지 않는다)
#    실행 전에 3번 출력의 IS_MOCK 이 True 인지, 아래 status 가 중단 상태가 아닌지 확인하고,
#    docs/manual_checks.md 절차로 신 TR_ID·지정가(00)를 모의 서버가 받는지 확인한다(실전 전환 전 필수).
#    DB(DB_PATH, 기본 data/bot.db)는 처음 쓸 때 모드가 기록되며 다른 모드로는 시작이 거부된다. 실전에는 새 경로를 쓴다.
.venv/bin/python -m risk status
.venv/bin/python main.py
tail -f logs/bot.log
```

**주의**: 3~4번은 `main.py` 경로상 KIS API를 호출합니다. `IS_MOCK_TRADING=True`인지 먼저 확인하세요.

## 브랜치 · 커밋 규칙

- 작업 시작 전 `git status`가 깨끗한지 확인
- 브랜치는 `improve/tNN-짧은이름` (예: `improve/t01-killswitch`)
- 한 번에 Task 하나만. 다음 Task 기능을 미리 만들지 않음
- 커밋 메시지는 `feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:` 접두사 사용

```
feat(주문): 매수 주문 후 체결 확인 추가

본문: 왜 바꿨는지 (무엇을 바꿨는지는 diff로 충분)

Co-Authored-By: Claude Code <noreply@anthropic.com>
```

기존 문서에 있던 `<base62-sha>` 커밋 템플릿은 의미 없는 자리표시자였고, `Co-authored-by: Claude Code` 표기는 현재 규칙(`Co-Authored-By: Claude Code <noreply@anthropic.com>`)과 달라 제거했습니다.

## 작업 종료 시

사용자가 요청하면 `docs/CHANGES.md`에 **덮어쓰지 말고 추가**합니다. 형식은 AGENTS.md의 "작업 방식" 참고 (6개 항목).

## 작업 규칙 요약

전체 규칙은 [AGENTS.md](../AGENTS.md)에 있습니다. 개발 시 특히 중요한 것:

1. 실전 주문이 나갈 수 있는 코드를 실행하지 않는다 (개발·테스트는 모의투자)
2. `.env`, API 키, 토큰, 계좌번호를 출력·로그·커밋하지 않는다
3. 주문(매수/매도/취소) API는 자동 재시도하지 않는다 — 타임아웃 후 재시도는 중복 주문
4. KIS 엔드포인트/TR_ID/파라미터를 추측하지 않는다 (공식 문서·공식 샘플 확인)
5. 수치는 `config.py` 설정값으로. 근거 불확실하면 `# VERIFY:` 주석
6. `try/except: pass` 금지 — 모든 예외는 로그 + 상태 반영
7. 시간은 tz-aware (`Asia/Seoul`, `America/New_York`). naive datetime 금지

## 코드 작성 시 참고

- 모듈 구조와 알려진 제한은 [AGENTS.md](../AGENTS.md) 참고
- 기존 공개 함수 시그니처는 가능하면 유지. 바꾸면 `docs/CHANGES.md`에 명시
- 범위 밖 대규모 리팩터링 금지. 새 의존성은 이유를 작업 보고에 기재

## 문서 소스

- [KIS API 포털](https://apiportal.koreainvestment.com/apis)
- [KIS 공식 샘플 (open-trading-api)](https://github.com/koreainvestment/open-trading-api)
- [Gemini API 문서](https://ai.google.dev/api)
- [Python Schedule](https://pypi.org/project/schedule/)
- [python-dotenv](https://pypi.org/project/python-dotenv/)

---

최초 작성 2026-10-13, 최종 갱신 2026-10-18 (T00)
