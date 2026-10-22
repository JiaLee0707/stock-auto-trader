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

`main.py`는 **주문 가능 구간이 아니면 매매 사이클을 건너뜁니다.** 이때 AI 분석·시세 조회 등
**어떤 API 도 호출하지 않고** 로그만 남깁니다. 브로커 ↔ DB 대조(`reconcile`)는 장 외에도
계속 돕니다 — 불일치는 장 외에 발견해야 개장 전에 고칠 수 있기 때문입니다.

```bash
# 스킵 여부 (사유가 함께 남습니다)
grep "주문 가능 구간 아님\|주문 구간 확인" logs/bot.log | tail -10

# 매수 조건 미충족 사유
grep "매수 조건\|매수 조건 미충족" logs/bot.log | tail -20

# 매도 조건 미충족 사유
grep "매도 조건 미충족" logs/bot.log | tail -20
```

정규장 판정은 `market_calendar.py`가 담당합니다 (T05).

- 캘린더: `exchange_calendars` 의 `XKRX`(한국거래소) / `XNYS`(뉴욕증권거래소).
  **휴장일 표를 직접 만들지 않습니다** — 설·추석 연휴, 크리스마스, 미국 반일장,
  서머타임이 라이브러리에서 나옵니다.
- 주문 가능 구간은 정규장보다 좁습니다: 개장 후 `SKIP_OPEN_MINUTES`(기본 10분),
  마감 전 `SKIP_CLOSE_MINUTES`(기본 10분)를 뺍니다. 반일장에서는 **그날 실제 마감**
  기준으로 계산합니다(예: 미국 13:00 ET 마감일 → 12:50 ET 부터 주문 중지).
- 운용 여부: `IS_OVERSEAS_ENABLED` / `IS_DOMESTIC_ENABLED`.

**임시 휴장일**(임시공휴일, 국가 애도일 등)은 라이브러리가 모릅니다.
`config.py` 의 `EXTRA_HOLIDAYS` 에 날짜를 넣어 막습니다:

```python
# config.py
EXTRA_HOLIDAYS = {
    "KRX": ["2026-06-03"],     # 예: 임시공휴일
    "US":  ["2026-12-24"],     # 예: 조기 휴장이 공지된 날
}
```

날짜 형식이 틀리면 조용히 무시되지 않고 **예외로 막습니다**(오타 하나로 휴장일
차단이 무력화되는 것을 방지).

### `장시작전` / `장종료` 거부를 받았을 때

T05 이후로는 캘린더 판정과 실제 거부가 어긋난 경우에만 발생합니다. 로그에 분류가 남습니다.

```bash
grep "거부 — 분류" logs/bot.log | tail -20
```

| 분류 | 뜻 | 조치 |
|---|---|---|
| 장 상태(장시작전·장종료·휴장) | 캘린더가 놓친 휴장일이거나 시간외 주문 | `EXTRA_HOLIDAYS` 추가, `SKIP_*_MINUTES` 조정 |
| 가격(호가 단위·상하한가) | 지정가가 호가 단위에 맞지 않음 | `ORDER_PRICE_TICKS` 확인 (ETF 호가표는 미확인 — T04b 보고 4-6) |
| 수량·잔고 | 주문가능수량 초과 | 예산·보유수량 확인 |
| 기타(원문 확인 필요) | 분류 문구에 없는 새 메시지 | 원문 `msg_cd`/`msg1` 로 코드표 확인 |

분류는 **로그를 읽기 위한 것**이며 판단을 바꾸지 않습니다 — 거부는 거부입니다.
분류에 실패해도 원문 `msg_cd`/`msg1` 은 항상 로그에 남습니다.

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
