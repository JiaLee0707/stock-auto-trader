"""
main.py 의 기존 장 시간 판정 **현재 동작을 고정**하는 테스트 (T05 대체 전).

T05 는 `main.is_regular_market_open()` / `main.is_domestic_market_open()` 을
`market_calendar` 기반 함수로 갈아끼운다. 교체가 "같은 결과"인지 비교하려면
교체 **전** 동작이 테스트로 박혀 있어야 한다. 이 파일이 그 기준이다.

고정하는 현재 동작 (main.py 2026-10-21 기준):

    is_domestic_market_open   평일(월~금) 이고 09:00 <= t < 15:30 KST  → True
    is_regular_market_open    평일(월~금) 이고 09:30 <= t < 16:00 ET   → True

**주의: 이전 동작은 휴장일을 보지 않았다.** 평일이면 설날·추석·크리스마스에도 True 였다.
이 테스트는 그 "틀린" 동작을 정답으로 고정하는 것이 아니라, **교체 전후 차이를
식별하기 위한 기준선**이었다.

교체 결과 (위 18개 중):

| 케이스 | 교체 전 | 교체 후 | 비고 |
|---|---|---|---|
| 평일 장중(09:00~15:30 KST / 09:30~16:00 ET) | True | **True** | 동일 |
| 평일 장 외 시각 | False | **False** | 동일 |
| 주말 | False | **False** | 동일 |
| 휴장일(크리스마스 등) 낮 12시 | True | **False** | **달라짐** |

달라진 휴장일 케이스는 `*_now_closed` 로 이름을 바꿔 T05 의 **새 동작**을 고정한다.

모든 시각은 tz-aware 다(절대 규칙 9).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import main

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def _at(zone, year, month, day, hour, minute):
    """해당 시간대의 tz-aware 시각을 만든다."""
    return datetime(year, month, day, hour, minute, tzinfo=zone)


@pytest.fixture
def freeze_now(monkeypatch):
    """
    `datetime.now(tz)` 를 고정한다.

    main 은 `datetime.now(ET)` / `datetime.now(KST)` 로 현재 시각을 읽는다.
    `main.datetime` 을 가짜 클래스로 바꿔 그 호출만 원하는 시각으로 돌려준다.
    """
    def _apply(instant):
        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

        monkeypatch.setattr(main, "datetime", _FrozenDatetime)
    return _apply


# ── 국내: 평일 09:00~15:30 KST ────────────────────────────
# 2026-10-20 은 화요일(평일, 휴장일 아님).

@pytest.mark.parametrize("hour, minute, expected", [
    (8, 59, False),     # 개장 1분 전
    (9, 0, True),       # 개장 (경계 포함)
    (12, 0, True),
    (15, 29, True),     # 마감 1분 전
    (15, 30, False),    # 마감 (경계 제외 — 현재 코드는 `< 15:30`)
    (18, 0, False),
    (0, 0, False),
])
def test_domestic_window_baseline(freeze_now, hour, minute, expected):
    """국내 정규장 판정의 현재 동작(09:00 <= t < 15:30 KST)."""
    freeze_now(_at(KST, 2026, 10, 20, hour, minute))
    assert main.is_domestic_market_open() is expected


@pytest.mark.parametrize("date", [
    (2026, 10, 17),     # 토
    (2026, 10, 18),     # 일
])
def test_domestic_weekend_closed_baseline(freeze_now, date):
    """주말은 시각과 무관하게 휴장 (현재 동작)."""
    freeze_now(_at(KST, date[0], date[1], date[2], 12, 0))
    assert main.is_domestic_market_open() is False


def test_domestic_holiday_now_closed(freeze_now):
    """
    **달라진 케이스 (기준선 = True, T05 이후 = False).**

    T05 이전: 평일이면 휴장일도 True 였다(휴장일 미판정).
    T05 이후: 크리스마스(2026-12-25, 금) 12:00 KST 는 XKRX 캘린더 기준 휴장 → False.
    이 차이가 곧 "장시작전 거부"를 없애는 부분이다.
    """
    freeze_now(_at(KST, 2026, 12, 25, 12, 0))
    assert main.is_domestic_market_open() is False


# ── 해외: 평일 09:30~16:00 ET ─────────────────────────────

@pytest.mark.parametrize("hour, minute, expected", [
    (9, 29, False),
    (9, 30, True),      # 개장 (경계 포함)
    (15, 59, True),
    (16, 0, False),     # 마감 (경계 제외)
    (20, 0, False),
])
def test_overseas_window_baseline(freeze_now, hour, minute, expected):
    """해외 정규장 판정의 현재 동작(09:30 <= t < 16:00 ET)."""
    # 2026-10-20 은 미국 동부 기준 화요일(EDT, 서머타임 중).
    freeze_now(_at(ET, 2026, 10, 20, hour, minute))
    assert main.is_regular_market_open() is expected


def test_overseas_uses_et_so_summer_time_is_implicit(freeze_now):
    """
    기준선: 해외 판정은 ET 벽시계 기준이라 서머타임이 자동 반영된다.

    같은 UTC 순간이라도 7월(EDT, UTC-4)과 1월(EST, UTC-5)의 ET 벽시계가 다르다.
    두 경우 모두 ET 기준 10:00 이면 True 다.
    """
    freeze_now(_at(ET, 2026, 7, 15, 10, 0))     # 서머타임 중
    assert main.is_regular_market_open() is True
    freeze_now(_at(ET, 2026, 1, 15, 10, 0))     # 서머타임 아님
    assert main.is_regular_market_open() is True


def test_overseas_weekend_closed_baseline(freeze_now):
    """주말은 휴장 (현재 동작). 2026-10-17 은 토요일."""
    freeze_now(_at(ET, 2026, 10, 17, 12, 0))
    assert main.is_regular_market_open() is False


def test_overseas_holiday_now_closed(freeze_now):
    """
    **달라진 케이스 (기준선 = True, T05 이후 = False).**

    T05 이전: 평일이면 미국 휴장일도 True 였다.
    T05 이후: 크리스마스(2026-12-25, 금) 12:00 ET 는 XNYS 캘린더 기준 휴장 → False.
    """
    freeze_now(_at(ET, 2026, 12, 25, 12, 0))
    assert main.is_regular_market_open() is False


# ── 스케줄러가 장 외에 API 를 호출하지 않는지 (요구사항 3) ──
# main.job() 은 import 시점에 로그 파일을 만들지만 매매 함수는 부르지 않는다.
# 여기서는 매매·AI·대조 함수를 전부 가짜로 바꿔 "호출 0건"을 확인한다.

@pytest.fixture
def spy(monkeypatch):
    """job() 이 부르는 외부 함수들을 가짜로 바꾸고 호출 기록을 남긴다."""
    calls = []

    def _record(name, result=None):
        def _fn(*a, **kw):
            calls.append(name)
            return result
        return _fn

    class _FakeState:
        conn = object()

    monkeypatch.setattr(main, "get_bot_state", lambda: _FakeState())
    monkeypatch.setattr(main.reconcile, "reconcile", _record("reconcile"))
    monkeypatch.setattr(main, "analyze_market_sentiment",
                        _record("analyze", {"score": 0, "reason": "stub"}))
    monkeypatch.setattr(main, "run_trading_cycle", _record("trading"))
    monkeypatch.setattr(main.reconcile, "is_trading_halted", lambda conn: False)
    return calls


def test_job_skips_all_trading_api_outside_order_window(freeze_now, spy):
    """
    주문 가능 구간이 아니면 **AI 분석·매매 사이클을 부르지 않는다** (요구사항 3).

    2026-12-25(금) 12:00 KST = 국내 휴장, 22:00 ET 전날 = 해외 휴장.
    """
    freeze_now(_at(KST, 2026, 12, 25, 12, 0))
    main.job()
    assert "trading" not in spy
    assert "analyze" not in spy


def test_job_runs_reconcile_even_outside_order_window(freeze_now, spy):
    """
    **판단 근거(요구사항): 장 외에도 reconcile 과 DB 쓰기는 계속한다.**

    이유: 대조의 목적은 "브로커 실제 상태 ↔ DB" 를 맞추는 것이다. 어긋난 것을
    장 외에 발견해야 개장 전에 고칠 수 있다. DB 쓰기를 스킵하면 장 외에 쌓인
    미확정 주문이 개장까지 방치된다. 반대로 **주문만** 막으면 안전하다.
    """
    freeze_now(_at(KST, 2026, 12, 25, 12, 0))
    main.job()
    assert spy.count("reconcile") == 1


def test_job_trades_inside_order_window(freeze_now, spy):
    """주문 구간이면 정상적으로 AI 분석·매매 사이클을 부른다 (스킵이 과하지 않은지)."""
    freeze_now(_at(KST, 2026, 10, 20, 11, 0))       # 국내 개장 중 (화)
    main.job()
    assert "analyze" in spy
    assert "trading" in spy


def test_job_skips_in_safety_margin_even_though_market_is_open(freeze_now, spy):
    """
    장은 열렸지만 안전 마진 안이면 주문하지 않는다 (요구사항 2).

    15:25 KST 는 국내 정규장 안이지만 마감(15:30) 5분 전이다 — 미체결로 남기 쉽다.
    """
    freeze_now(_at(KST, 2026, 10, 20, 15, 25))
    main.job()
    assert "trading" not in spy


def test_job_passes_market_specific_flags(freeze_now, spy, monkeypatch):
    """
    `run_trading_cycle` 에 **시장별로 따로 계산한** 주문 구간 플래그를 넘긴다.

    기존 코드의 구멍: 국내 주문을 내려는데 미국이 열려 있으면 통과했다
    (2026-10-15 03:20 KST 거부). 이제는 시장마다 따로 판정한다.
    """
    seen = {}

    def _capture(ai_score, overseas_open=True, domestic_open=True):
        seen["overseas"] = overseas_open
        seen["domestic"] = domestic_open

    monkeypatch.setattr(main, "run_trading_cycle", _capture)
    # 국내 개장 중(11:00 KST) = 미국은 닫힘.
    freeze_now(_at(KST, 2026, 10, 20, 11, 0))
    main.job()
    assert seen == {"overseas": False, "domestic": True}
