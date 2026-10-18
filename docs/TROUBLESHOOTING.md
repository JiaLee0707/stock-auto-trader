# 문제 해결

설치·설정은 [README.md](../README.md), 개발 규칙은 [DEVELOPMENT.md](DEVELOPMENT.md) 참고.

## 빠른 참조

| 증상 | 원인 | 해결 |
|---|---|---|
| `ModuleNotFoundError: No module named 'requests'` | 의존성 미설치 | `.venv/bin/python -m pip install -r requirements.txt` |
| `python: not found` | `.venv` 미생성/미활성 | `python3 -m venv .venv` |
| KIS/Gemini 인증 실패 (401) | `.env` 키 오류 | `.env` 값 확인 (값을 터미널에 출력하지 말 것) |
| `Permission denied: logs/bot.log` | 로그 디렉터리 권한 | `mkdir -p logs` (봇이 자동 생성하지만 권한이 막힌 경우 수동 생성) |
| 매매가 전혀 안 일어남 | 정규장 아님 / 조건 미충족 | 아래 "매매가 안 일어날 때" 참고 |

## 매매가 안 일어날 때 (가장 흔함)

`main.py`는 **정규장이 아니면 사이클을 통째로 건너뜁니다.** 또한 매 사이클마다 조건 미충족 사유를 로그에 남깁니다.

```bash
# 스킵 여부
grep "매매 대상 없음\|정규장 아님" logs/bot.log | tail -10

# 매수 조건 미충족 사유
grep "매수 조건\|매수 조건 미충족" logs/bot.log | tail -20

# 매도 조건 미충족 사유
grep "매도 조건 미충족" logs/bot.log | tail -20
```

정규장 판정은 `main.py`의 `is_regular_market_open()` / `is_domestic_market_open()`에서 합니다.
- 해외: 평일 09:30~16:00 ET (서머타임은 ET 기준이라 자동 반영)
- 국내: 평일 09:00~15:30 KST

**한계**: **미국/한국 휴장일을 판정하지 않습니다.** 평일이면 무조건 정규장으로 봅니다. 따라서 휴장일에 주문을 시도했다가 `장시작전`/`장종료` 거부를 받습니다 (2026-10-18 기준 `logs/bot.log`에 이런 거부가 4건 있습니다). 휴장일 처리는 아직 구현되지 않았습니다.

## 봇이 안 멈출 때

```bash
ps aux | grep "[m]ain.py"      # PID 확인
kill <PID>                     # 정상 종료 요청
pkill -f "python main.py"      # 강제 종료
```

터미널에서 실행 중이면 `Ctrl+C`.

## KIS 호출 제한 (모의투자)

모의투자는 호출 제한이 낮아 연속 호출 시 HTTP 500(`초당 거래건수를 초과하였습니다`)이 간헐 발생합니다.
`kis_overseas._request()`가 모든 호출 전 `_MIN_CALL_INTERVAL_SEC`(1.0초) 간격을 보장하고, 500 응답은 `_RETRY_SLEEP_SEC`(1.5초) 후 재시도합니다.

**주의**: 이 재시도는 조회와 주문에 모두 적용됩니다. CLAUDE.md 규칙 3은 주문 API의 자동 재시도를 금지하므로, 이 부분은 T01 이후 점검 대상입니다 (`# VERIFY`).

## 로그 관련

```bash
tail -f logs/bot.log                     # 실시간
grep "ERROR" logs/bot.log | tail -10     # 오류
ls -lh logs/bot.log                      # 크기 확인
```

로그 파일은 자동 로테이션이 없습니다. 계속 커지면 봇을 멈추고 수동으로 옮기세요.

```bash
mv logs/bot.log logs/bot.log.old
```

## 계좌번호가 로그에 찍히는지 확인

CLAUDE.md 규칙 2에 따라 계좌번호는 마스킹(`****1234`)되어야 합니다.

```bash
grep -nE "[0-9]{8}-[0-9]{2}" logs/bot.log | head
```

## 참고

이 문서에 없던 옛 문서(`docs/archive/`)에는 `free -h`, `psutil` 미설치 상태의 메모리 확인 명령 등 macOS에서 동작하지 않거나 현재 코드와 맞지 않는 항목이 있었습니다. 그런 항목은 이관하지 않았습니다.

---

최초 작성 2025-01-02, 최종 갱신 2026-10-18 (T00)
