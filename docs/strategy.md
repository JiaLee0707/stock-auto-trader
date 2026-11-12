# 전략 기본값 (AGENTS.md에서 이동)

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
