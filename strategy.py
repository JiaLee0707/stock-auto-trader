"""
매매 **판단** (T08). **순수 함수 `decide()` 만 둔다 — I/O 를 하지 않는다.**

목적은 하나다: 백테스트와 실전이 **같은 판단 함수**를 쓰게 만드는 것.
그전까지 판단은 `trader._run_cycle` 안에 흩어져 있었고 `config`·`bot_state` 전역과
DB 조회에 결합돼 있었다. 그래서 백테스트에서 실전과 같은 판단을 재현할 수 없었다
(CLAUDE.md "백테스트와 실전 판단 로직이 별개일 가능성").

    거래소 상태 → [호출부: 데이터 수집] → MarketSnapshot
                                        ↘
    브로커 잔고 → [호출부: 정규화]     → AccountView / PositionView / DayCounters
                                        ↘
                                          decide(...)  ← 순수, 이 모듈
                                        ↙
    Decision  → [호출부: 주문·DB 기록] (`trader._run_cycle`)

무엇을 판단하고 무엇을 판단하지 않는가
  - **판단한다**: 진입 조건(지표), 청산(사유·수량), 수량(위험·현금 한도), 각종 제한.
  - **판단하지 않는다**: 중복 주문 차단, 미확정 주문 확정, 주문 직전 잔고 재확인,
    상태 DB 조회·기록, 알림. 이것들은 **부작용**이 있고 "판단 이전/이후"의 사실에
    의존하므로 `trader` 의 오케스트레이션에 남는다(요청 지침).
    예: `_confirm_sell_quantity` 는 "브로커가 실제로 몇 주를 갖고 있나"를 다시 묻는다.
    그 값을 미리 `PositionView` 에 넣어 판단하게 하면 조회 실패 시 **모르는 수량으로
  판단**하게 되므로, 순수 함수의 입력으로 끌어오지 않았다.

## 확정 종가 기준 (요구사항 3 — 동작이 바뀌는 부분)

  지표는 **확정 일봉만** 쓴다(`history.get_confirmed_bars`, T02). 호출부가 그 목록을
  `MarketSnapshot.bars` 로 넘긴다. 즉 판단에 쓰는 종가는 **전일까지의 확정 종가**이고,
  **장중에 형성 중인 당일 봉은 신호에 포함되지 않는다.**

  T07 까지의 `trader` 는 `kis_*.get_daily_closes()`(당일 봉 포함)를 그대로 썼다. 그래서
  장중에는 "지금까지의 오늘 종가"로 RSI·볼린저를 계산했고, 그 봉이 다시 그려지면
  같은 날 신호가 뒤집힐 수 있었다(오전에 매수 조건 충족 → 오후에 미충족). 이제 판단은
  하루 동안 고정이고, **주문만** `ORDER_WINDOW` 안에서 나간다.

  체결 가정: 백테스트는 `BACKTEST_FILL_MODEL`(기본 `next_open`)을 명시적으로 쓴다 —
  "전일 확정 종가로 신호를 내고 **다음 거래일 시가**에 체결된다"고 가정한다.
  실전은 신호가 나온 그 사이클의 장중 가격(현재가)으로 주문한다. 이 차이는 백테스트
  결과를 실전에 적용할 때 반드시 고려해야 한다.

설계 원칙
  - **I/O 없음**: DB·API·파일·시계를 만지지 않는다. `now`·설정·파라미터는 인자다.
  - **결정적**: 같은 입력이면 언제나 같은 `Decision`. 입력을 변경하지 않는다.
  - 수치는 `config` 에서 온다(절대 규칙 5). 거기에 기본값을 하드코딩하지 않는다.
  - **모두 검증 전 가설이다**(T11 백테스트로 확정). 임계값을 임의로 완화하지 말 것.
"""
import logging
from datetime import datetime

import config
import exit_rules
import indicators
import models
import sizing

logger = logging.getLogger("bot")


# ── 파라미터 (설정 → 순수 함수 입력) ─────────────────────

def params_from_config() -> dict:
    """
    `config` 에서 판단 파라미터를 모은다.

    `decide()` 가 전역 설정에 직접 결합되지 않도록 호출부가 이 함수로 만들어 넘긴다.
    테스트는 dict 를 직접 만들어 설정과 무관하게 경계값을 검증할 수 있다.
    """
    return {
        # 진입 — 지표
        "use_indicators": config.USE_INDICATORS,
        "rsi_period": config.RSI_PERIOD,
        "rsi_oversold": config.RSI_OVERSOLD,
        "rsi_overbought": config.RSI_OVERBOUGHT,
        "bb_period": config.BB_PERIOD,
        "bb_num_std": config.BB_NUM_STD,
        # 진입 — 추세 필터 (T08 신규, 기본 꺼짐 = 현재 동작 유지)
        "entry_trend_filter_enabled": config.ENTRY_TREND_FILTER_ENABLED,
        "entry_trend_sma_period": config.ENTRY_TREND_SMA_PERIOD,
        # 진입 — 지표 폴백 (지표를 못 쓸 때의 하락률 기준)
        "dip_threshold": config.DIP_THRESHOLD,
        # 진입 — 제한
        "max_buys_per_day": config.MAX_BUYS_PER_DAY,
        "min_buy_interval_hours": config.MIN_BUY_INTERVAL_HOURS,
        # 진입 — AI 점수 필터 (T10 에서 기본값을 바꿀 예정. 지금은 True = 현재 동작)
        "ai_filter_enabled": config.AI_FILTER_ENABLED,
        "ai_score_floor": config.AI_SCORE_FLOOR,
        # 청산 (T06)
        "exit": exit_rules.params_from_config(),
        # 수량 (T07)
        "sizing": sizing.params_from_config(),
        "capital_cap_krw": config.CAPITAL_CAP_KRW,
        # 해외 종목의 가격(달러)을 원화로 환산하는 비율. 사이징은 원화로만 계산한다
        # (`sizing.compute_qty` 규약). 국내는 1.0 이다.
        "fx_rate": 1.0,
    }


def _num(value, default: float | None = 0.0) -> float | None:
    """
    숫자 변환. 실패하면 `default` 다.

    `default=None` 으로 부르면 "값이 없음"을 구분할 수 있다 — 청산 가격처럼
    "0"과 "안 넘어옴"이 다른 뜻인 곳에서 쓴다.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _closes(bars) -> list[float]:
    """
    확정 일봉 DataFrame → 종가 리스트(과거 → 최신).

    DataFrame 이 아니거나 비어 있으면 빈 리스트다. **여기서 예외를 던지지 않는다** —
    "데이터 없음"과 "조회 실패"는 다르고, 조회 실패는 호출부가 이미 구분해 처리한다
    (`indicators.IndicatorDataUnavailable`, T04).
    """
    if bars is None:
        return []
    try:
        if len(bars) == 0:
            return []
        series = bars["close"]
    except (KeyError, TypeError, AttributeError):
        return []
    out = []
    for value in series:
        out.append(_num(value))
    return out


def _now_for_interval(now: datetime, last_buy_time: datetime | None) -> float | None:
    """
    직전 매수 후 경과 시간(시). 비교할 수 없으면 None.

    naive 시각은 쓰지 않는다(절대 규칙 9) — 조용히 KST 로 승격하면 시각이 어긋난다.
    비교할 수 없으면 **매수를 허용하지 않는다**(간격을 모르는 채 사면 연속 매수가 난다).
    """
    if last_buy_time is None:
        return None
    if now is None or now.tzinfo is None or last_buy_time.tzinfo is None:
        return None
    return (now - last_buy_time).total_seconds() / 3600.0


def evaluate_entry_signal(snapshot, params: dict) -> dict:
    """
    진입 판단에 쓸 지표를 계산한다. **I/O 없음** — 확정 봉만 본다.

    반환: {
        "available": bool,      지표를 계산했는가(False = 데이터 부족, 매수 금지)
        "buy_signal": bool,     RSI·볼린저 매수 조건 충족
        "sell_signal": bool,    RSI·볼린저 매도 조건 충족(과매수 청산용)
        "rsi": float|None, "close": float, "bb_mid/upper/lower": float|None,
        "sma": float|None,      추세 필터용 SMA
        "reason": str,
    }

    **지표를 쓸지 여부**(`use_indicators`)는 여기서 판단하지 않는다 — 호출부가 정한다.
    이 함수는 "쓸 수 있으면 계산해서 돌려주는" 역할만 한다.

    확정 봉이 부족하거나 없으면 `available=False` 다. 그때 `decide` 는 하락률 폴백이
    아니라 **매수 보류**로 간다 — 이유는 `decide` 의 docstring 참고.
    """
    closes = _closes(snapshot.bars)
    out = {
        "available": False,
        "buy_signal": False,
        "sell_signal": False,
        "rsi": None, "close": 0.0,
        "bb_mid": None, "bb_upper": None, "bb_lower": None,
        "sma": None,
        "reason": "확정 일봉 없음",
    }
    if not closes:
        return out

    rsi_value = indicators.rsi(closes, int(params.get("rsi_period") or 0))
    bb = indicators.bollinger(closes, int(params.get("bb_period") or 0),
                              _num(params.get("bb_num_std"), 2.0))
    close = closes[-1]
    out["close"] = close
    out["rsi"] = rsi_value
    if bb:
        out["bb_mid"], out["bb_upper"], out["bb_lower"] = bb

    period = int(params.get("entry_trend_sma_period") or 0)
    out["sma"] = indicators.sma(closes, period) if period > 0 else None

    if rsi_value is None or bb is None:
        # 계산 자체가 불가하다. 값을 추측하지 않는다(모르면 사지 않는다).
        out["reason"] = "지표 계산 불가(확정 봉 부족)"
        return out

    _, upper, lower = bb
    out["available"] = True
    out["buy_signal"] = (rsi_value <= _num(params.get("rsi_oversold"), 30.0)
                         and close <= lower)
    out["sell_signal"] = (rsi_value >= _num(params.get("rsi_overbought"), 70.0)
                          and close >= upper)
    sma_text = f" | SMA{period} {out['sma']:,.2f}" if out["sma"] is not None else ""
    out["reason"] = (
        f"RSI {rsi_value:.1f} (기준 ≤{_num(params.get('rsi_oversold'), 30.0):g}"
        f"/≥{_num(params.get('rsi_overbought'), 70.0):g}) | 종가 {close:,.2f} | "
        f"BB 하단 {lower:,.2f} / 상단 {upper:,.2f}{sma_text}"
    )
    return out


def _entry_trend_ok(signal: dict, params: dict) -> tuple[bool | None, str]:
    """
    추세 필터(종가 > SMA(200)) 판정 (T08 신규).

    반환: (통과 여부, 설명). **`None` 은 "판정할 수 없음"이며 매수 허용이 아니다** —
    "SMA 를 못 구했으니 그냥 사자"로 해석하면 필터를 켠 의미가 사라진다.
    """
    if not params.get("entry_trend_filter_enabled"):
        return True, "추세 필터 꺼짐"

    period = int(params.get("entry_trend_sma_period") or 0)
    sma_value = signal.get("sma")
    if sma_value is None:
        return None, (f"추세 필터 켜짐(SMA{period})이나 확정 봉이 부족해 "
                      f"SMA 를 계산할 수 없음")
    close = _num(signal.get("close"))
    if close <= _num(sma_value):
        return False, (f"종가 {close:,.2f} ≤ SMA{period} {_num(sma_value):,.2f} "
                       f"— 상승 추세 아님")
    return True, f"종가 {close:,.2f} > SMA{period} {_num(sma_value):,.2f}"


def _decide_exit(snapshot, position, params: dict, signal: dict | None) -> "models.Decision | None":
    """
    청산 판단 (T06 `exit_rules.evaluate_exit` 위임). 청산이 아니면 None.

    현재가는 **한 출처만** 쓴다(`snapshot.last_price`) — 진입가와 현재가가 서로 다른
    응답에서 오면(-10% 라는데 계산은 0%) 손절이 발동하지 않는 상태가 생긴다
    (T06 `_check_sell` 주석 참고).
    """
    # 청산 판단의 가격은 **잔고 응답에서 온 값**을 우선한다 (T06 의 단일 출처 원칙).
    #
    # 왜 `snapshot.last_price` 를 그대로 쓰지 않는가: `last_price` 는 시세 API 의
    # 현재가이고, 진입가·수익률은 잔고 API 에서 온다. 두 응답이 어긋나면
    # "-10% 라는데 계산은 0%" 상태가 되어 **손절이 발동하지 않는다**(T06 실측).
    # 그래서 호출부가 잔고의 `profit_rate` 로 역산한 가격을 `exit_price` 로 넘기면
    # 그 값을 쓴다. 넘기지 않으면(백테스트 등) `last_price` 하나만 쓴다.
    price = _num(params.get("exit_price"), None)
    if price is None:
        price = _num(snapshot.last_price)
    # 과매수 신호는 두 경로로 들어온다.
    #   - `decide()` 전체 경로: `signal`(확정 봉으로 계산한 지표)에서 온다.
    #   - `signal=None` 경로(`trader._check_sell` 래퍼): 호출부가 이미 계산해 둔 신호를
    #     `params["overbought_signal"]` 로 넘긴다 — 여기서 다시 계산하면 두 번 계산한다.
    overbought = bool(params.get("overbought_signal")) or bool(
        signal and signal.get("sell_signal"))
    bar_info = {
        "atr": None,
        "overbought": overbought and bool(params["exit"].get("overbought_exit_enabled")),
    }
    judgment = {
        "qty": float(position.qty),
        "avg_price": float(position.avg_price),
        "current_price": price,
        "entry_time": position.entry_date,
        "peak_price_since_entry": position.peak_price,
        # 거래일 계산에 쓴다. 모르면 시간청산이 발동하지 않는다(exit_rules 가 경고).
        "market": params.get("market"),
    }
    exit_signal = exit_rules.evaluate_exit(
        judgment, bar_info, params["exit"], snapshot.now,
    )
    if exit_signal is None:
        return None

    return models.Decision(
        models.SELL,
        qty=float(position.qty),
        reason=exit_signal.reason,
        limit_price=price,
        meta={
            "price": f"{price:,.2f}",
            "avg_price": f"{float(position.avg_price):,.2f}",
            "detail": exit_signal.detail,
            "rsi": signal.get("rsi") if signal else None,
        },
    )


def decide(snapshot: "models.MarketSnapshot",
           position: "models.PositionView",
           account: "models.AccountView",
           params: dict,
           counters: "models.DayCounters | None",
           ai_score: int | None = None) -> "models.Decision":
    """
    한 종목의 매매 판단. **순수 함수 — I/O·시간 조회·DB 접근이 없다.**

    같은 입력이면 언제나 같은 `Decision` 을 돌려주고, 입력 객체를 변경하지 않는다.
    시간은 `snapshot.now`, 설정·수치는 `params`, AI 점수는 `ai_score` 로만 들어온다.

    Args:
        snapshot: 확정 일봉 + 현재가 + 기준 시각.
        position: 보유 상태. `qty <= 0` 이면 청산 판단을 건너뛴다.
        account: 자본(원화, `CAPITAL_CAP_KRW` 적용 후).
        params: `merge_params()` 로 채운 파라미터 dict.
            `market` 키("overseas"/"domestic")가 있어야 보유 거래일을 셀 수 있다.
        counters: 당일 매수 횟수·직전 매수 시각·보유 종목 수·총 노출.
            `None`이면 조회 실패 → 매수 보류(오직 `HOLD_EXPOSURE_UNAVAILABLE`). 청산은 계속됨.
        ai_score: AI 시장 점수. **선택 입력**이다 —
            `params["ai_filter_enabled"]` 가 False 면 쓰이지 않고, 필터가 켜져 있는데
            `None` 이면 **매수를 보류한다**(점수를 모르는 채 사면 필터가 무의미해진다).

    판단 순서 (우선순위 = 안전성 우선, CLAUDE.md "우선순위 충돌 시")
        1. **청산** — 보유가 있으면 항상 먼저 본다. 손절이 매수보다 우선이다.
        2. 진입 제한 — 당일 한도 → 매수 간격 → 보유 종목 상한.
            `counters is None`이면 `HOLD_EXPOSURE_UNAVAILABLE` 반환 (매수만 보류).
        3. 진입 조건 — 지표(RSI·볼린저) → 추세 필터.
        4. AI 점수 필터.
        5. 수량 — `sizing.compute_qty`. **0주면 주문하지 않는다**(강제 1주 매수 금지).

    지표를 쓸 수 없을 때(확정 봉 부족)
        `use_indicators=True` 인데 지표를 계산할 수 없으면 **매수를 보류**하고
        `HOLD_INDICATOR_UNAVAILABLE` 을 남긴다. 하락률 폴백으로 흘러가지 않는다 (T04).
        `use_indicators=False` 면 폴백(평단 대비 하락률)을 그대로 쓴다 — 현재 동작 유지.
        이 구분은 `indicators.evaluate_or_none` 이 "조회 실패"(`IndicatorDataUnavailable`)와
        "데이터 부족"(dict, 신호 없음)을 나누는 것과 같은 원칙이다. 여기서는 **호출부가
        조회에 성공해 확정 봉 목록을 넘겼는데 봉 자체가 부족한 경우**를 다룬다.
    """
    # **여기서 `merge_params()` 를 부르지 않는다.** 그것은 `config` 를 읽으므로,
    # 파라미터가 일부만 넘어왔을 때 `decide()` 가 전역 설정에 결합된다(순수성 위반).
    # 대신 완전한 파라미터를 **요구**하고, 빠진 키가 있으면 조용히 설정을 읽는 대신
    # 목록을 들어 예외로 알린다. 파라미터를 만드는 것은 호출부의 일이다
    # (`strategy.merge_params()` / 테스트의 명시적 dict / 백테스트의 시나리오 값).
    params = _validate_params(params)
    market = params.get("market")

    # ── 1. 청산 (보유가 있으면 최우선) ────────────────────
    if position.is_open:
        signal = None
        if params.get("use_indicators"):
            signal = evaluate_entry_signal(snapshot, params)
        decision = _decide_exit(snapshot, position, params, signal)
        if decision is not None:
            logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
            return decision

    # ── 2~3. 진입 제한 ────────────────────────────────────
    if counters is None:
        decision = models.Decision(
            models.HOLD, reason=models.HOLD_EXPOSURE_UNAVAILABLE,
            meta={"detail": "노출·보유·카운터 조회 실패 — 매수만 보류"},
        )
        logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
        return decision

    limit_decision = _check_entry_limits(snapshot, params, counters)
    if limit_decision is not None:
        logger.info(f"[판단] {snapshot.symbol} {limit_decision.log_line()}")
        return limit_decision

    # ── 4. 진입 조건 (지표) ───────────────────────────────
    signal = None
    entry_reason = ""
    if params.get("use_indicators") and not _bars_available(params, snapshot):
        # 확정 봉을 **얻지 못했다**(조회 실패 또는 캐시 없음). 청산은 위에서 이미
        # 판단했으므로 여기서는 매수만 보류한다 — 하락률 폴백으로 흘러가지 않는다.
        decision = models.Decision(
            models.HOLD, reason=models.HOLD_INDICATOR_UNAVAILABLE,
            meta={"detail": params.get("bars_unavailable_detail")
                          or "확정 일봉을 얻지 못했습니다 — 매수 보류"},
        )
        logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
        return decision

    if params.get("use_indicators"):
        signal = evaluate_entry_signal(snapshot, params)
        if not signal["available"]:
            # 봉은 **받았는데 부족하다**(SMA·RSI·BB 를 계산할 만큼 길지 않다).
            # "데이터가 없음"과 다른 사유이므로 구분해 남긴다.
            decision = models.Decision(
                models.HOLD, reason=models.HOLD_INDICATOR_UNAVAILABLE,
                meta={"detail": signal["reason"],
                      "bars": int(len(snapshot.bars)) if snapshot.bars is not None else 0},
            )
            logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
            return decision
        entry_ok = bool(signal["buy_signal"])
        entry_reason = f"지표 매수신호: {signal['reason']}"

        # 추세 필터 (T08 신규, 기본 꺼짐). SMA 를 못 구하면 매수를 보류한다.
        trend_ok, trend_desc = _entry_trend_ok(signal, params)
        if trend_ok is None:
            decision = models.Decision(
                models.HOLD, reason=models.HOLD_TREND_FILTER,
                meta={"detail": trend_desc,
                      "bars": int(len(snapshot.bars)) if snapshot.bars is not None else 0},
            )
            logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
            return decision
        entry_ok = entry_ok and trend_ok
        entry_reason = f"{entry_reason} | {trend_desc}"
    else:
        # 지표를 쓰지 않는 경로 — 평단 대비 하락률 폴백 (현재 동작 유지).
        price = _num(snapshot.last_price)
        avg = _num(position.avg_price)
        dip_pct = (price - avg) / avg * 100.0 if avg > 0 else 0.0
        entry_ok = (avg == 0) or (dip_pct <= _num(params.get("dip_threshold")))
        entry_reason = (f"딥 하락률 {dip_pct:.2f}% "
                        f"(기준: {_num(params.get('dip_threshold'))}%)")

    meta = {
        "signal": signal["reason"] if signal else entry_reason,
        "rsi": signal.get("rsi") if signal else None,
        "close": signal.get("close") if signal else None,
        "bb_lower": signal.get("bb_lower") if signal else None,
        "bb_upper": signal.get("bb_upper") if signal else None,
        "sma": signal.get("sma") if signal else None,
        "ai_score": ai_score if params.get("ai_filter_enabled") else None,
    }

    if not entry_ok:
        decision = models.Decision(models.HOLD, reason=models.HOLD_NO_SIGNAL,
                                   meta=meta | {"detail": entry_reason})
        logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
        return decision

    # ── 5. AI 점수 필터 (선택 입력) ───────────────────────
    if params.get("ai_filter_enabled"):
        floor = int(params.get("ai_score_floor") or 0)
        if ai_score is None:
            decision = models.Decision(
                models.HOLD, reason=models.HOLD_AI_FILTER,
                meta=meta | {"detail": f"AI 점수를 받지 못했습니다 (하한 {floor})"},
            )
            logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
            return decision
        if int(ai_score) < floor:
            decision = models.Decision(
                models.HOLD, reason=models.HOLD_AI_FILTER,
                meta=meta | {"detail": f"AI 점수 {ai_score} < 하한 {floor}"},
            )
            logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
            return decision

    # ── 6. 수량 (T07 sizing — 수량의 유일한 출처) ──────────
    fx = _num(params.get("fx_rate"), 1.0) or 1.0
    price_krw = _num(snapshot.last_price) * fx          # 사이징은 원화로만 계산한다
    stop_pct = sizing.effective_stop_pct(price_krw)
    # 사용 자본 상한(T07 `CAPITAL_CAP_KRW`). 모의투자 1,000만원으로 실전 20만원과 같은
    # 수량이 나오게 한다. **여기(판단 안)서 적용한다** — `trader` 에서 미리 적용하면
    # 백테스트가 파라미터로 상한을 바꿀 수 없고, 두 곳에서 자본을 만지면 어긋난다.
    capped_eq, capped_cash = sizing.apply_capital_cap(
        account.equity, account.cash, params.get("capital_cap_krw"))
    plan = sizing.compute_qty(
        capped_eq, capped_cash, price_krw, stop_pct, params["sizing"],
        open_positions=counters.open_positions,
        current_exposure_krw=counters.exposure_krw,
    )
    meta = meta | {
        "sizing": plan.detail,
        "stop_pct": plan.stop_pct,
        "equity": f"{capped_eq:,.0f}",
        "required_capital": f"{plan.required_capital:,.0f}",
    }

    if plan.qty < 1:
        # **최소 1주를 강제로 사지 않는다.** 위험 한도를 넘기 때문이다.
        # 왜 못 사는지와 얼마가 더 필요한지는 meta 에 남아 로그로 나간다.
        decision = models.Decision(
            models.HOLD, reason=plan.reason,
            meta=meta | {"detail": plan.message},
        )
        logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
        return decision

    decision = models.Decision(
        models.BUY, qty=plan.qty, reason="ENTRY",
        limit_price=_num(snapshot.last_price),
        meta=meta | {"qty_by_risk": plan.qty_by_risk, "qty_by_cash": plan.qty_by_cash},
    )
    logger.info(f"[판단] {snapshot.symbol} {decision.log_line()}")
    return decision


# `decide()` 가 요구하는 파라미터 키 (전부 있어야 한다).
_REQUIRED_PARAMS = (
    "use_indicators", "rsi_period", "rsi_oversold", "rsi_overbought",
    "bb_period", "bb_num_std", "entry_trend_filter_enabled", "entry_trend_sma_period",
    "dip_threshold", "max_buys_per_day", "min_buy_interval_hours",
    "ai_filter_enabled", "ai_score_floor", "exit", "sizing", "capital_cap_krw",
    "fx_rate", "market",
)


def _validate_params(params: dict | None) -> dict:
    """
    파라미터가 완전한지 확인한다. **빠진 키는 설정에서 채우지 않고 예외로 막는다.**

    ## 왜 이렇게 하는가

      `.get()` 으로 읽으면 키가 없을 때 `None`/기본값이 조용히 쓰인다. 그러면
      "AI 하한을 안 넘겨서 하한이 0이 됐다" 같은 상태를 아무도 모른다 —
      판단이 잘못돼도 로그에 흔적이 없다.

      더 중요하게는, 빠진 키를 `config` 에서 채우면 `decide()` 가 **전역 설정에
      결합**된다. T08 의 목적은 백테스트가 설정과 무관하게 시나리오를 돌리는 것인데,
      결합이 남아 있으면 백테스트 결과가 개발자 `.env` 에 따라 달라진다.

      그래서 `params` 는 **완전해야** 한다. 만드는 것은 호출부의 일이다:
        - 실전: `strategy.merge_params(...)` (설정 + 사이클 값)
        - 테스트·백테스트: 명시적 dict

    누락은 조용히 넘어가지 않고 목록으로 알린다(절대 규칙 7).
    """
    if params is None:
        raise ValueError(
            "decide() 에 params 가 필요합니다. `strategy.merge_params()` 로 만드세요."
        )
    missing = [key for key in _REQUIRED_PARAMS if key not in params]
    if missing:
        raise ValueError(
            f"decide() 에 필요한 파라미터가 빠졌습니다: {', '.join(sorted(missing))}. "
            f"전역 설정을 대신 읽지 않습니다(순수성) — "
            f"`strategy.merge_params()` 로 만든 dict 를 넘기세요."
        )
    if "market" not in params:
        raise ValueError("params['market'] 이 필요합니다(보유 거래일 계산).")
    return params


def _bars_available(params: dict, snapshot) -> bool:
    """
    확정 봉 목록을 **받았는가**를 본다 (조회 실패와 데이터 부족을 구분).

    `_closes()` 가 돌려주는 종가 리스트만 보면 둘이 구별되지 않는다 —
    둘 다 빈 리스트다. 그래서 `bars=None`(조회 실패, 호출부가 플래그로 알린다)과
    `DataFrame 이지만 비었음`(캐시 미구축)을 호출부가 `bars_received` 로 구분해 넘긴다.

    두 경우 모두 매수는 보류한다(T04 원칙: 모르면 사지 않는다). 사유 문자열만 다르다.
    """
    if params.get("bars_received") is False:
        return False
    bars = snapshot.bars
    if bars is None:
        return False
    try:
        return len(bars) > 0
    except TypeError:
        return False


def _check_entry_limits(snapshot, params: dict,
                        counters: "models.DayCounters") -> "models.Decision | None":
    """
    진입 **제한**만 본다(한도·간격·보유 종목 수). 걸리면 HOLD, 아니면 None.

    조건 자체(지표)보다 먼저 본다 — 이미 못 사는 상황이면 지표를 계산할 이유가 없다.
    """
    buy_count = int(counters.buy_count_today or 0)
    max_buys = int(params.get("max_buys_per_day") or 0)
    # **`max_buys > 0` 로 감싸지 않는다.** T07 의 비교는 `buy_count >= MAX_BUYS_PER_DAY`
    # 이므로 `MAX_BUYS_PER_DAY=0` 은 "오늘 0회까지"= **전부 차단**이다. 여기에 `> 0`
    # 조건을 붙이면 0 이 "무제한"으로 뒤집혀 하루 한도가 사라진다(설정 하나로 규칙이
    # 반대로 동작하는 상태). 음수도 같은 비교로 차단된다.
    if buy_count >= max_buys:
        return models.Decision(
            models.HOLD, reason=models.HOLD_DAILY_LIMIT,
            meta={"buy_count": buy_count, "max_buys_per_day": max_buys},
        )

    # 직전 매수 후 최소 간격 (2회차 이상부터 의미가 있다)
    if buy_count > 0:
        elapsed_h = _now_for_interval(snapshot.now, counters.last_buy_time)
        min_h = _num(params.get("min_buy_interval_hours"))
        if elapsed_h is not None and elapsed_h < min_h:
            return models.Decision(
                models.HOLD, reason=models.HOLD_BUY_INTERVAL,
                meta={"elapsed_hours": round(elapsed_h, 2),
                      "min_buy_interval_hours": min_h},
            )

    max_open = int(params["sizing"].get("max_open_positions") or 0)
    if max_open > 0 and int(counters.open_positions or 0) >= max_open:
        # 수량과 무관하게 막는다. `sizing` 도 같은 규칙을 갖고 있지만, 여기서 먼저
        # 걸러 "왜 못 사는지"를 명확한 사유로 남긴다(수량 계산을 하지 않아도 된다).
        return models.Decision(
            models.HOLD, reason=models.HOLD_MAX_POSITIONS,
            meta={"open_positions": int(counters.open_positions), "max": max_open},
        )
    return None


def merge_params(params: dict | None) -> dict:
    """
    넘어온 파라미터에 빠진 키를 `params_from_config()` 로 채운다.

    호출부(테스트·백테스트)가 일부 키만 주고 나머지는 설정 기본값을 쓰게 한다.
    넘어온 값이 우선이다 — 테스트가 설정과 무관하게 경계값을 고정할 수 있어야 한다.
    """
    merged = params_from_config()
    for key, value in (params or {}).items():
        if key in ("exit", "sizing") and isinstance(value, dict):
            sub = dict(merged[key])
            sub.update(value)
            merged[key] = sub
        else:
            merged[key] = value
    return merged
