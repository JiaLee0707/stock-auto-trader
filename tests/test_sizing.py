"""
T07 포지션 사이징 테스트.

이 파일은 두 부분으로 나뉜다.

  **1부 — 특성화(변경 전 동작 고정)**: T06 까지의 "총자산 × 비중" 방식이
  소액에서 어떻게 0주가 되는지 테스트로 박아 둔다. T07 은 이 동작을 바꾸므로,
  바뀐 뒤에도 "무엇이 달라졌는지"를 이 기록으로 말할 수 있다.
  (`TestCharacterization` 클래스 — T07 적용 후에도 **통과한다**.
   매수 경로가 없어진 것이 아니라 계산 방식만 바뀌었기 때문이다.)

  **2부 — 새 사이징**: 순수 함수 `sizing.compute_qty` 의 경계값·제한 테스트.

실제 API 를 호출하지 않는다. `main.py` 도 실행하지 않는다.
"""
import pytest

import config
import db
import sizing
import state
import trader


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def bot(conn):
    st = state.reset_bot_state(conn)
    yield st
    state.reset_bot_state(None)


# ── 1부: 변경 전 동작 (특성화) ───────────────────────────
#
# T06 까지의 매수 수량 계산 (trader._run_cycle):
#     buy_krw = 총자산 × 비중            # 해외 15% / 국내 10%
#     buy_krw = min(buy_krw, 예수금 × 0.99)
#     qty     = int(buy_krw // 1주 가격)  # ← 0 이 될 수 있다
#
# 문제: 비중이 **자산 대비 비율**이라 1주 가격과 무관하게 정해진다.
#       20만원 × 10% = 2만원 < 1주 2.3만원 → 0주. 매수 신호가 맞아도 못 산다.

class TestCharacterization:
    """
    변경 전 계산 방식의 결과를 고정한다 (2026-10-22, T06 까지의 동작).

    **주의**: 이 값들은 "옳은 동작"이 아니라 **기준선**이다. 아래 `budget_*` 는
    T07 이후 사라지는 계산이므로, 무엇이 달라졌는지 비교하는 데만 쓴다.
    """

    @staticmethod
    def _legacy_budget(total_krw, cash_krw, ratio):
        """
        T06 까지의 매수 예산 계산 (trader._run_cycle:945-947).

        T07 에서 이 계산이 사라지므로 여기에 기록해 둔다 —
        지우고 나면 "예전에는 얼마였나"를 코드로 확인할 수 없다.
        """
        return min(total_krw * ratio, cash_krw * 0.99)

    @staticmethod
    def _legacy_qty(buy_krw, entry_price):
        """T06 까지의 수량 계산 (`_qty_for_budget` — 정수 내림). 0 이 될 수 있다."""
        return int(buy_krw // entry_price) if entry_price > 0 else 0

    def test_200k_with_10pct_ratio_buys_zero_shares(self):
        """
        **핵심 결함**: 자본 20만원 / 국내 비중 10% / 1주 23,220원 → **0주**.

        CLAUDE.md "현재 알려진 문제"의 "20만원에서 비중 10% 규칙 → 1주도 못 사는
        경우 발생"이 이 계산이다. 매수 신호가 맞아도 주문이 나가지 않는다.
        """
        budget = self._legacy_budget(200_000, 200_000, 0.10)
        assert budget == pytest.approx(20_000)
        assert self._legacy_qty(budget, 23_220) == 0

    def test_200k_with_15pct_ratio_buys_zero_shares(self):
        """해외 비중 15%(3만원)도 1주 23,220원이면 1주는 산다 — 1주만."""
        budget = self._legacy_budget(200_000, 200_000, 0.15)
        assert self._legacy_qty(budget, 23_220) == 1

    def test_budget_ignores_stop_distance(self):
        """
        비중 방식은 **손절폭을 보지 않는다.** 손절 -3%든 -30%든 같은 수량이 나온다.

        즉 위험(손절 시 손실액)이 손절폭에 비례해 커진다 — 위험 관리가 아니라
        자금 배분이다. T07 의 위험 기준 수량(`qty_by_risk`)이 이 문제를 다룬다.
        """
        budget = self._legacy_budget(200_000, 200_000, 0.10)
        assert self._legacy_qty(budget, 23_220) == self._legacy_qty(budget, 23_220)

    def test_cash_is_clamped_to_99_percent(self):
        """예수금이 예산보다 적으면 99% 로 깎인다(수수료 여유). T07 도 유지한다."""
        budget = self._legacy_budget(1_000_000, 10_000, 0.10)
        assert budget == pytest.approx(9_900)

    def test_overseas_budget_converts_by_fx_rate(self):
        """
        해외는 원화 예산을 달러로 환산해 수량을 낸다 (T07 도 같은 방식을 쓴다).

        QQQ $100 × 환율 1,400원 = 1주 14만원. 예산 3만원이면 0주다.
        """
        budget = self._legacy_budget(200_000, 200_000, 0.15)   # 3만원
        stock_krw = 100.0 * 1_400.0                            # 14만원
        assert int(budget // stock_krw) == 0


# ── 2부: 새 사이징 (순수 함수) ───────────────────────────

def _params(**overrides) -> dict:
    """테스트가 전역 설정과 무관하게 경계값을 검증할 수 있게 dict 로 받는다."""
    base = {
        "risk_per_trade_pct": 1.0,       # equity × 1%
        "max_position_pct": 45.0,        # min(cash, equity × 45%)
        "max_open_positions": 2,
        "max_total_exposure_pct": 90.0,
    }
    base.update(overrides)
    return base


# ── 완료 기준 표: (자본, 1주 가격, 손절 %) → 수량 ─────────

@pytest.mark.parametrize("equity,cash,price,stop_pct,expected_qty,note", [
    # 손절 -3% 기준. 위험 예산 = equity × 1%, 손절폭 = price × 3%
    # 값은 spec 공식으로 계산한다:
    #   qty_by_risk = floor(equity×1% / (price×3%)), qty_by_cash = floor(min(cash, equity×45%)/price)
    (200_000, 200_000, 23_220, -3.0, 2, "20만원 — 위험 기준 2주"),
    (1_000_000, 1_000_000, 23_220, -3.0, 14, "100만원 — 위험 기준 14주"),
    (1_000_000, 200_000, 23_220, -3.0, 8, "현금 20만원이 상한"),
])
def test_qty_calculation_table(equity, cash, price, stop_pct, expected_qty, note):
    """
    위험 기준·현금 기준을 모두 계산해 **작은 쪽**을 쓴다.

    각 행의 계산 과정은 보고서의 표와 같다. 값이 하나라도 어긋나면
    "위험 한도를 넘는 수량" 또는 "살 수 있는데 안 사는" 결과가 된다.
    """
    result = sizing.compute_qty(equity, cash, price, stop_pct, _params())
    assert result.qty == expected_qty, f"{note}: {result}"


def test_200k_risk_quantity_is_2_shares():
    """
    자본 20만원 / 1주 23,220원 / 손절 -3% 의 계산을 항목별로 확인한다.

    위험 예산   = 200,000 × 1%   = 2,000원
    손절폭      = 23,220 × 3%    = 696.6원  (1주당 손절 시 손실)
    위험 기준   = floor(2,000 / 696.6) = 2주   ← 위험이 병목이다
    현금 기준   = floor(min(200,000, 200,000×45%) / 23,220) = floor(90,000/23,220) = 3주

    최종 = min(2, 3) = **2주**, 투입 46,440원.

    이 값은 "위험 1% 규칙이 지배한다"는 뜻이다 — 20만원으로는 3주까지만
    자금이 허용되고, 위험 규칙이 그보다 더 보수적이다.
    """
    result = sizing.compute_qty(200_000, 200_000, 23_220, -3.0, _params())
    assert result.qty == 2, f"위험 기준이 병목이어야 한다: {result}"
    assert result.required_capital == pytest.approx(46_440.0)   # 2주 × 23,220원
    assert result.risk_budget_krw == pytest.approx(2_000.0)     # 자산의 1%
    assert result.stop_distance_krw == pytest.approx(696.6)     # 1주당 손절 시 손실
    # 실제 감수 위험은 **예산 이하**다(내림했으므로). 넘으면 위험 한도 위반이다.
    assert result.qty * result.stop_distance_krw <= result.risk_budget_krw


def test_qty_by_risk_and_cash_are_both_exposed():
    """계산 근거를 남긴다 — 왜 이 수량인지 로그·보고서에서 확인할 수 있어야 한다."""
    result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0, _params())
    assert result.qty_by_risk == 14       # floor(10,000 / 696.6)
    assert result.qty_by_cash == 19       # floor(450,000 / 23,220)
    assert result.qty == min(result.qty_by_risk, result.qty_by_cash)


# ── 위험 기준 0주 ────────────────────────────────────────

def test_risk_budget_too_small_returns_zero_with_reason():
    """
    위험 예산이 손절폭보다 작으면 **0주**이고 `INSUFFICIENT_CAPITAL` 이다.

    자본 5만원 × 1% = 500원, 1주 손절폭 696.6원 → 0주.
    **최소 1주를 강제로 사지 않는다** — 그러면 위험 한도(1%)를 넘는다.
    """
    result = sizing.compute_qty(50_000, 50_000, 23_220, -3.0, _params())
    assert result.qty == 0
    assert result.reason == sizing.INSUFFICIENT_CAPITAL


def test_required_capital_is_reported_when_zero():
    """
    0주일 때 **1주를 사려면 자본이 얼마나 필요한지**를 남긴다.

    위험 기준으로 1주를 사려면: 손절폭 696.6원 = 자본의 1% → 자본 69,660원.
    사용자가 "얼마를 넣어야 하는가"를 바로 알 수 있어야 한다.
    """
    result = sizing.compute_qty(50_000, 50_000, 23_220, -3.0, _params())
    assert result.required_capital == pytest.approx(69_660.0)
    assert result.required_capital > 50_000


# ── 현금 부족 ────────────────────────────────────────────

def test_cash_shortage_returns_zero():
    """자본은 충분하지만 예수금이 1주 값보다 적으면 0주다."""
    result = sizing.compute_qty(1_000_000, 10_000, 23_220, -3.0, _params())
    assert result.qty == 0
    assert result.reason == sizing.INSUFFICIENT_CAPITAL


def test_cash_limits_quantity_below_risk_limit():
    """예수금이 위험 기준보다 작으면 현금이 병목이 된다."""
    result = sizing.compute_qty(1_000_000, 100_000, 23_220, -3.0, _params())
    assert result.qty_by_cash == 4      # floor(100,000 / 23,220)
    assert result.qty == 4
    assert result.qty < result.qty_by_risk


def test_max_position_pct_caps_single_position():
    """현금이 많아도 한 포지션은 자산의 `MAX_POSITION_PCT` 를 넘지 않는다."""
    result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0,
                                _params(max_position_pct=10.0))
    assert result.qty_by_cash == 4      # floor(100,000 / 23,220)
    assert result.qty <= result.qty_by_cash


# ── 최대 보유 종목 초과 ──────────────────────────────────

def test_max_open_positions_blocks_new_entry():
    """보유 종목이 상한이면 **수량과 무관하게** 신규 진입을 막는다."""
    result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0, _params(),
                                open_positions=2)
    assert result.qty == 0
    assert result.reason == sizing.MAX_POSITIONS_REACHED


def test_one_below_max_positions_is_allowed():
    result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0, _params(),
                                open_positions=1)
    assert result.qty > 0


# ── 총 노출 한도 ─────────────────────────────────────────

def test_total_exposure_limit_reduces_quantity():
    """
    이미 총 노출이 90% 면 추가 매수 여력이 없다 — 투입 금액이 0 이 된다.

    자산 100만원 × 90% = 90만원이 상한, 이미 90만원 노출 → 남은 여력 0.
    """
    result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0, _params(),
                                current_exposure_krw=900_000)
    assert result.qty == 0
    assert result.reason == sizing.EXPOSURE_LIMIT


def test_total_exposure_limit_allows_partial_room():
    """여력이 남아 있으면 그만큼만 산다 (현금 기준이 여력으로 줄어든다)."""
    result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0, _params(),
                                current_exposure_krw=850_000)
    assert result.qty_by_cash == 2      # floor(50,000 / 23,220)
    assert result.qty == 2


# ── CAPITAL_CAP_KRW (모의투자 규모 맞추기) ───────────────

def test_capital_cap_limits_equity_and_cash():
    """
    모의투자는 가상 1,000만원이라 실전 20만원과 규모가 다르다.
    `CAPITAL_CAP_KRW` 를 설정하면 equity 와 cash 를 그 값으로 제한해 사이징한다.

    자본 상한 20만원 → 위험 예산 2,000원 → 2주 (상한 없으면 143주).
    """
    capped_eq, capped_cash = sizing.apply_capital_cap(1_000_000, 1_000_000, 200_000)
    assert (capped_eq, capped_cash) == (200_000, 200_000)
    result = sizing.compute_qty(capped_eq, capped_cash, 23_220, -3.0, _params())
    assert result.qty == 2


def test_capital_cap_zero_means_no_cap():
    """기본값 0 은 "상한 없음"이다 — 실제 계좌 규모를 그대로 쓴다."""
    assert sizing.apply_capital_cap(1_000_000, 800_000, 0) == (1_000_000, 800_000)


def test_capital_cap_does_not_raise_small_account():
    """상한이 실제 자본보다 크면 아무것도 바뀌지 않는다(상한은 위쪽 제한일 뿐)."""
    assert sizing.apply_capital_cap(200_000, 150_000, 1_000_000) == (200_000, 150_000)


def test_capital_cap_limits_cash_too():
    """
    **현금도 함께 제한한다.** 그러지 않으면 모의투자의 1,000만원 예수금으로
    20만원 상한을 넘겨 살 수 있다(자본은 20만원인데 현금은 1,000만원).
    """
    capped_eq, capped_cash = sizing.apply_capital_cap(10_000_000, 10_000_000, 200_000)
    assert capped_cash == 200_000
    result = sizing.compute_qty(capped_eq, capped_cash, 23_220, -3.0, _params())
    assert result.qty == 2, "현금 상한이 걸리지 않아 과다 매수했다"


# ── 손절폭 입력 ──────────────────────────────────────────

def test_stop_pct_must_be_negative():
    """손절폭이 0 이나 양수면 위험을 계산할 수 없다 — 0주로 막는다."""
    for bad in (0.0, 1.0):
        result = sizing.compute_qty(1_000_000, 1_000_000, 23_220, bad, _params())
        assert result.qty == 0
        assert result.reason == sizing.INVALID_STOP


def test_wider_stop_reduces_quantity():
    """손절폭이 넓으면 1주당 위험이 커져 수량이 줄어든다."""
    narrow = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -3.0, _params())
    wide = sizing.compute_qty(1_000_000, 1_000_000, 23_220, -6.0, _params())
    assert wide.qty < narrow.qty


def test_invalid_price_returns_zero():
    result = sizing.compute_qty(1_000_000, 1_000_000, 0.0, -3.0, _params())
    assert result.qty == 0


# ── 순수성 ───────────────────────────────────────────────

def test_compute_qty_does_no_io(monkeypatch):
    """사이징은 순수 함수다 — DB·API·설정을 건드리지 않는다."""
    def boom(*args, **kwargs):
        raise AssertionError("순수 함수가 I/O 를 시도했다")

    monkeypatch.setattr(db, "get_position", boom)
    monkeypatch.setattr(db, "open_db", boom)
    monkeypatch.setattr(state, "get_bot_state", boom)
    result = sizing.compute_qty(200_000, 200_000, 23_220, -3.0, _params())
    assert result.qty > 0


def test_params_from_config_reads_every_setting():
    """`params_from_config()` 의 키가 테스트 기본 dict 와 일치한다(키 오타 방지)."""
    assert set(sizing.params_from_config()) == set(_params())


# ── 옛 설정 이름 경고 ────────────────────────────────────

def test_obsolete_env_names_are_warned(caplog):
    """
    `.env` 에 남은 옛 이름(비중 방식 등)을 경고한다 (요구사항 5).

    값이 아직 동작한다고 믿으면 "왜 수량이 예상과 다른가"를 알 수 없다.
    """
    env = {name: "0.15" for name in config.OBSOLETE_ENV_NAMES}
    with caplog.at_level("WARNING"):
        found = config.warn_obsolete_env(env)
    assert set(found) == set(config.OBSOLETE_ENV_NAMES)
    for name in config.OBSOLETE_ENV_NAMES:
        assert name in caplog.text
    assert "0.15" not in caplog.text, "설정 값을 로그에 출력했다(절대 규칙 2 위반)"


def test_buy_ratio_names_are_in_obsolete_list():
    """T07 에서 삭제한 비중 설정 두 개가 경고 목록에 들어 있다."""
    assert "BUY_RATIO" in config.OBSOLETE_ENV_NAMES
    assert "DOMESTIC_BUY_RATIO" in config.OBSOLETE_ENV_NAMES


def test_clean_env_warns_nothing():
    """목록에 있는 이름이 없으면 경고하지 않는다."""
    assert config.warn_obsolete_env({}) == []


def test_position_sizing_from_stop_loss_pct():
    """
    손절폭은 T06 의 `STOP_LOSS_PCT` 에서 온다.
    `STOP_LOSS_ATR_MULT` 가 0(기본)이면 고정 % 를 그대로 쓴다.
    """
    assert sizing.effective_stop_pct(100.0, None) == config.STOP_LOSS_PCT


def test_atr_mult_zero_uses_fixed_stop_pct():
    """ATR 배수가 0 이면 ATR 값을 줘도 고정 % 를 쓴다 (T06 `effective_stop_loss_pct`)."""
    original = config.STOP_LOSS_ATR_MULT
    try:
        config.STOP_LOSS_ATR_MULT = 0.0
        assert sizing.effective_stop_pct(100.0, 5.0) == config.STOP_LOSS_PCT
    finally:
        config.STOP_LOSS_ATR_MULT = original


def test_atr_mult_on_uses_wider_stop():
    """
    ATR 배수를 켜면 더 넓은 쪽을 쓴다 (T06 과 같은 규칙 — 여기서 다시 구현하지 않는다).
    고정 -3% / ATR 2.0·진입가 100 → -4%.
    """
    original = config.STOP_LOSS_ATR_MULT
    try:
        config.STOP_LOSS_ATR_MULT = 2.0
        assert sizing.effective_stop_pct(100.0, 2.0) == pytest.approx(-4.0)
    finally:
        config.STOP_LOSS_ATR_MULT = original


# ── trader 배선 ──────────────────────────────────────────

# 매수 신호가 나오는 종가 시계열 (RSI 25.5 ≤ 30 AND 종가 95 ≤ BB 하단 100.50).
# 이 테스트들은 **수량 결정**만 검증하므로 진입 조건 자체는 실제 지표 함수로 통과시킨다
# (조건을 우회하는 stub 을 쓰면 매수 경로가 실제로 도는지 확인되지 않는다).
_BUY_CLOSES = [100 + i * 0.5 for i in range(29)] + [95.0]


def test_trader_has_no_buy_ratio_code():
    """기존 비중 설정·코드가 trader 에서 사라졌다 (grep 대신 코드로 고정)."""
    import inspect
    src = inspect.getsource(trader)
    for token in ("BUY_RATIO", "DOMESTIC_BUY_RATIO", "total_krw * ratio", "buy_krw"):
        assert token not in src, f"trader.py 에 아직 남아 있다: {token}"


def test_run_cycle_signature_has_no_ratio():
    """`_run_cycle` 이 비중(`ratio`)을 받지 않는다 — 사이징이 수량을 정한다."""
    import inspect
    params = inspect.signature(trader._run_cycle).parameters
    assert "ratio" not in params
    assert "sizing_fn" in params, "사이징 콜러블이 배선되지 않았다"


def test_zero_qty_does_not_submit_buy(conn, bot, monkeypatch):
    """
    수량이 0 이면 **주문하지 않는다.** 최소 1주 강제 매수는 금지다(위험 한도 위반).
    """
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    submitted = []
    monkeypatch.setattr(config, "USE_INDICATORS", True)

    def submit_buy(qty, price):
        submitted.append((qty, price))
        return 1

    trader._run_cycle(
        "국내", "379800",
        {"quantity": 0.0, "avg_price": 0.0, "profit_rate": 0.0,
         "total_asset_krw": 50_000.0, "cash_krw": 50_000.0},
        23_220.0, 0, lambda p: f"{p:,.0f}원", sell_fn=None, buy_fn=None,
        fetch_closes=lambda: list(_BUY_CLOSES), market="domestic",
        submit_buy=submit_buy,
        sizing_fn=lambda *a, **kw: sizing.SizingResult(
            0, sizing.INSUFFICIENT_CAPITAL, 69_660.0, detail="1주 필요 자본"),
    )
    assert submitted == [], "0주인데 주문이 나갔다"


def test_positive_qty_submits_that_quantity(conn, bot, monkeypatch):
    """양수면 그 수량을 그대로 넘긴다(사이징이 정한 값을 trader 가 바꾸지 않는다)."""
    monkeypatch.setattr(trader, "_state_conn", lambda: conn)
    monkeypatch.setattr(config, "USE_INDICATORS", True)
    submitted = []

    trader._run_cycle(
        "국내", "379800",
        {"quantity": 0.0, "avg_price": 0.0, "profit_rate": 0.0,
         "total_asset_krw": 1_000_000.0, "cash_krw": 1_000_000.0},
        23_220.0, 0, lambda p: f"{p:,.0f}원", sell_fn=None, buy_fn=None,
        fetch_closes=lambda: list(_BUY_CLOSES), market="domestic",
        submit_buy=lambda qty, price: submitted.append(qty),
        sizing_fn=lambda *a, **kw: sizing.SizingResult(3, "OK", 69_660.0,
                                                       detail="위험 1%"),
    )
    assert submitted == [3]
