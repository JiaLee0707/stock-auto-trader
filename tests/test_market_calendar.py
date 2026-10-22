"""
market_calendar 단위 테스트 (T05 완료 기준).

검증 항목:
  - 설·추석 연휴, 크리스마스 같은 휴장일
  - 미국 서머타임 전환 전후
  - 반일장(미국 추수감사절 다음날) 샘플
  - `is_order_window` 의 안전 마진 (개장 후·마감 전)
  - **로그에서 실제로 거부당한 시각에는 주문 구간이 열리지 않을 것** (요구사항 1)

모든 시각은 tz-aware 다. 테스트는 실제 API 를 호출하지 않는다 —
`exchange_calendars` 는 오프라인 캘린더 라이브러리다.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import config
import market_calendar as mc

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")


def krx(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# ── 휴장일: 설·추석 연휴, 크리스마스 ──────────────────────

@pytest.mark.parametrize("day, why", [
    (date(2026, 2, 16), "설 연휴 (설날 전날)"),
    (date(2026, 2, 17), "설날"),
    (date(2026, 2, 18), "설 연휴 다음날"),
    (date(2026, 9, 24), "추석 연휴"),
    (date(2026, 9, 25), "추석"),
    (date(2026, 10, 5), "개천절 대체공휴일"),
    (date(2026, 10, 9), "한글날"),
    (date(2026, 12, 25), "크리스마스"),
    (date(2026, 5, 25), "부처님오신날 대체공휴일"),
])
def test_krx_holidays_are_closed(day, why):
    """한국 휴장일은 정규장이 열리지 않는다."""
    noon = krx(day.year, day.month, day.day, 12, 0)
    assert mc.is_market_open(mc.MARKET_KRX, noon) is False, why
    assert mc.is_order_window(mc.MARKET_KRX, noon) is False, why
    assert mc.is_session_day(mc.MARKET_KRX, day) is False, why


@pytest.mark.parametrize("day, why", [
    (date(2026, 1, 1), "신정"),
    (date(2026, 7, 3), "독립기념일 대체휴일"),
    (date(2026, 11, 26), "추수감사절"),
    (date(2026, 12, 25), "크리스마스"),
])
def test_us_holidays_are_closed(day, why):
    """미국 휴장일은 정규장이 열리지 않는다."""
    noon = ny(day.year, day.month, day.day, 12, 0)
    assert mc.is_market_open(mc.MARKET_US, noon) is False, why
    assert mc.is_order_window(mc.MARKET_US, noon) is False, why


def test_krx_holiday_and_normal_day_differ_only_by_calendar():
    """
    같은 시각·같은 요일(금)인데 휴장일만 False 인지 확인한다.

    2026-12-25(금)은 휴장, 2026-12-18(금)은 정상 거래일이다.
    "평일이면 열림" 규칙이었다면 둘 다 True 였다 — 그 차이가 T05 의 성과다.
    """
    assert mc.is_market_open(mc.MARKET_KRX, krx(2026, 12, 25, 12, 0)) is False
    assert mc.is_market_open(mc.MARKET_KRX, krx(2026, 12, 18, 12, 0)) is True


# ── 미국 서머타임 전환 전후 ──────────────────────────────
# 2026년 미국 서머타임: 3/8(일) 시작, 11/1(일) 종료.

def test_us_dst_spring_forward():
    """
    서머타임 시작 전(3/6, EST = UTC-5)과 후(3/9, EDT = UTC-4) 모두
    **현지 09:30 개장·16:00 마감**이 유지된다. UTC 시각은 1시간 달라진다.
    """
    before = ny(2026, 3, 6, 10, 0)      # 금, EST
    after = ny(2026, 3, 9, 10, 0)       # 월, EDT
    assert before.utcoffset().total_seconds() == -5 * 3600
    assert after.utcoffset().total_seconds() == -4 * 3600
    assert mc.is_market_open(mc.MARKET_US, before) is True
    assert mc.is_market_open(mc.MARKET_US, after) is True
    # 두 시각의 UTC 오프셋이 다르다는 것이 서머타임이 반영된 증거다.
    assert mc.get_calendar(mc.MARKET_US).session_open("2026-03-06").hour == 14  # 09:30 EST
    assert mc.get_calendar(mc.MARKET_US).session_open("2026-03-09").hour == 13  # 09:30 EDT


def test_us_dst_fall_back():
    """서머타임 종료(11/1) 전후도 개장 시각은 현지 기준 09:30 그대로다."""
    before = ny(2026, 10, 30, 10, 0)    # 금, EDT
    after = ny(2026, 11, 2, 10, 0)      # 월, EST
    assert before.utcoffset().total_seconds() == -4 * 3600
    assert after.utcoffset().total_seconds() == -5 * 3600
    assert mc.is_market_open(mc.MARKET_US, before) is True
    assert mc.is_market_open(mc.MARKET_US, after) is True


def test_us_dst_transition_day_boundary():
    """
    전환 당일 경계: 09:30 현지가 개장이다.

    서머타임 전환 전후로 KST 기준 시각이 1시간씩 밀리지만, ET 벽시계 기준
    판정은 그대로여야 한다.
    """
    opens = [
        ny(2026, 3, 6, 9, 29), ny(2026, 3, 6, 9, 30),      # EST
        ny(2026, 3, 9, 9, 29), ny(2026, 3, 9, 9, 30),      # EDT
    ]
    assert [mc.is_market_open(mc.MARKET_US, t) for t in opens] == [False, True, False, True]


def test_dst_does_not_affect_krx():
    """
    한국은 서머타임이 없다 — KST 오프셋은 연중 +9 고정이다.

    같은 날 미국 개장 시각이 KST 로는 달라진다(22:30 → 21:30).
    """
    mar = krx(2026, 3, 6, 10, 0)
    nov = krx(2026, 11, 2, 10, 0)
    assert mar.utcoffset().total_seconds() == 9 * 3600
    assert nov.utcoffset().total_seconds() == 9 * 3600
    # 국내 개장 시각은 KST 09:00 고정.
    assert mc.get_calendar(mc.MARKET_KRX).session_open("2026-03-06").hour == 0   # UTC 00:00 = 09:00 KST
    assert mc.get_calendar(mc.MARKET_KRX).session_open("2026-11-02").hour == 0


# ── 반일장 ───────────────────────────────────────────────

def test_us_half_day_thanksgiving_friday():
    """
    반일장: 2026-11-27(추수감사절 다음날)은 13:00 ET 마감이다.

    12:59 ET 는 정규장 안(True), 13:00 ET 는 마감(False).
    """
    assert mc.get_calendar(mc.MARKET_US).session_close("2026-11-27").tz_convert(ET).hour == 13
    assert mc.is_market_open(mc.MARKET_US, ny(2026, 11, 27, 12, 59)) is True
    assert mc.is_market_open(mc.MARKET_US, ny(2026, 11, 27, 13, 0)) is False


def test_us_half_day_order_window_uses_actual_close():
    """
    반일장의 안전 마진은 **그날 실제 마감(13:00 ET)** 기준이다.

    평일 마감(16:00) 기준이었다면 12:55 ET 도 주문 가능으로 나왔을 것이다.
    """
    assert config.SKIP_CLOSE_MINUTES == 10
    assert mc.is_order_window(mc.MARKET_US, ny(2026, 11, 27, 12, 49)) is True
    assert mc.is_order_window(mc.MARKET_US, ny(2026, 11, 27, 12, 50)) is False
    assert mc.is_order_window(mc.MARKET_US, ny(2026, 11, 27, 12, 55)) is False
    # 평일이면 같은 12:55 ET 는 주문 가능하다 (마감이 16:00 이므로).
    assert mc.is_order_window(mc.MARKET_US, ny(2026, 11, 25, 12, 55)) is True


# ── 정규장 경계와 안전 마진 ──────────────────────────────

@pytest.mark.parametrize("hh, mm, open_expected, window_expected", [
    (8, 59, False, False),
    (9, 0, True, False),      # 개장했지만 마진(10분) 안
    (9, 9, True, False),
    (9, 10, True, True),      # 개장 + 10분 = 주문 시작
    (12, 0, True, True),
    (15, 19, True, True),
    (15, 20, True, False),    # 마감(15:30) - 10분 = 주문 종료
    (15, 29, True, False),
    (15, 30, False, False),
])
def test_krx_order_window_margin(hh, mm, open_expected, window_expected):
    """국내: 개장 후 10분 / 마감 전 10분을 뺀 구간에서만 주문 가능하다."""
    t = krx(2026, 10, 20, hh, mm)
    assert mc.is_market_open(mc.MARKET_KRX, t) is open_expected
    assert mc.is_order_window(mc.MARKET_KRX, t) is window_expected


@pytest.mark.parametrize("hh, mm, window_expected", [
    (9, 29, False),
    (9, 30, False),     # 개장 직후 (마진)
    (9, 40, True),      # 개장 + 10분
    (15, 49, True),
    (15, 50, False),    # 마감(16:00) - 10분
])
def test_us_order_window_margin(hh, mm, window_expected):
    """미국: 같은 규칙을 ET 기준으로 적용한다."""
    assert mc.is_order_window(mc.MARKET_US, ny(2026, 10, 20, hh, mm)) is window_expected


def test_order_window_is_inside_market_open():
    """주문 구간은 항상 정규장의 부분집합이다 (마진만큼 좁다)."""
    for hh in range(0, 24):
        for mm in (0, 30):
            t = krx(2026, 10, 20, hh, mm)
            if mc.is_order_window(mc.MARKET_KRX, t):
                assert mc.is_market_open(mc.MARKET_KRX, t)


def test_skip_minutes_are_configurable(monkeypatch):
    """마진 값은 config 로 바꿀 수 있다 (요구사항 2)."""
    monkeypatch.setattr(config, "SKIP_OPEN_MINUTES", 0)
    monkeypatch.setattr(config, "SKIP_CLOSE_MINUTES", 0)
    # 마진이 없으면 개장 시각 그대로부터 주문 가능하다.
    assert mc.is_order_window(mc.MARKET_KRX, krx(2026, 10, 20, 9, 0)) is True
    assert mc.is_order_window(mc.MARKET_KRX, krx(2026, 10, 20, 15, 29)) is True


# ── 로그의 실제 거부 시각 재현 방지 (요구사항 1) ─────────
# logs/bot.log 에 실제로 남은 "장시작전/장종료" 거부 4건의 발생 시각.
#   2026-10-13 15:33:11 KST  국내? → 해외(QQQ) 매수, "장시작전"
#   2026-10-13 18:35:32 KST  해외(QQQ) 매수, "장시작전"
#   2026-10-14 09:26:13 KST  해외(QQQ) 매수, "장종료"
#   2026-10-15 03:20:58 KST  국내 매수,       "장시작전" [40570000]

@pytest.mark.parametrize("label, ts, market, kst_hhmm", [
    ("QQQ 매수 1", krx(2026, 10, 13, 15, 33), mc.MARKET_US, "15:33"),
    ("QQQ 매수 2", krx(2026, 10, 13, 18, 35), mc.MARKET_US, "18:35"),
    ("QQQ 매수 3", krx(2026, 10, 14, 9, 26), mc.MARKET_US, "09:26"),
    ("국내 매수", krx(2026, 10, 15, 3, 20), mc.MARKET_KRX, "03:20"),
])
def test_rejected_timestamps_are_not_order_windows(label, ts, market, kst_hhmm):
    """
    실제로 거부당한 시각에는 `is_order_window` 가 False 여야 한다.

    이 4건이 T05 이전에 나갔던 이유는 **당시 코드가 평일이면 열려 있다고 봤기**
    때문이다(휴장일 미판정 + 마감 3분 전에도 주문). 지금은 캘린더와 안전 마진이
    막는다. 실제 API 를 호출하지 않고 판정 함수만 검증한다.
    """
    local = ts.astimezone(mc.market_timezone(market))
    assert mc.is_order_window(market, local) is False, (
        f"{label}: {kst_hhmm} KST 는 주문 구간이면 안 됩니다"
    )


def test_which_rejected_timestamps_the_previous_gate_missed():
    """
    위 4건 중 **기존 평일+시간 게이트로도 막히지 않던 것**을 특정한다.

    로그를 보면 4건 모두 "매매 대상 없음 — 스킵" 로그 없이 곧바로 주문까지 갔다
    (`grep -c "매매 대상 없음" logs/bot.log` → 0). 즉 그때 돌던 main.py 에는
    시장 게이트가 없었다. 지금 코드 기준으로 각 시각을 다시 판정하면:

       10/13 15:33 KST = 10/13 02:33 ET (화) → 해외 닫힘 / 국내 15:30 마감 3분 전 → 게이트가 막음
       10/13 18:35 KST = 10/13 05:35 ET (화) → 해외 닫힘 / 국내 닫힘          → 게이트가 막음
       10/14 09:26 KST = 10/13 20:26 ET (화) → 해외 닫힘 / 국내 09:00 개장 26분 후 → **국내 게이트를 통과**
       10/15 03:20 KST = 10/14 14:20 ET (수) → **해외 게이트를 통과** (미국 정규장 한복판)

    마지막 2건이 핵심이다. 기존 게이트는 "그 시장이 열려 있는가"만 봤기 때문에
    **국내 주문을 내려는데 미국이 열려 있으면 통과**시켰다. 정작 주문하려는
    국내 시장은 닫혀 있었고(10/15 03:20 KST), 결과가 `장시작전` 거부였다.

    T05 의 `market_windows()` 는 시장별로 주문 구간을 따로 판정하므로 이 구멍이 없다.
    """
    def old_gate_us(t_et) -> bool:
        return t_et.weekday() < 5 and (9, 30) <= (t_et.hour, t_et.minute) < (16, 0)

    def old_gate_kr(t_kst) -> bool:
        return t_kst.weekday() < 5 and (9, 0) <= (t_kst.hour, t_kst.minute) < (15, 30)

    # 10/15 03:20 KST — 국내는 닫혔지만 미국은 열려 있어 옛 게이트를 통과했다.
    t = krx(2026, 10, 15, 3, 20)
    assert old_gate_kr(t) is False                          # 주문 대상(국내)은 닫힘
    assert old_gate_us(t.astimezone(ET)) is True            # 그런데 미국이 열려 있어 통과
    assert mc.is_order_window(mc.MARKET_KRX, t) is False    # T05 는 국내 기준으로 막는다
    assert mc.is_order_window(mc.MARKET_US, t.astimezone(ET)) is True

    # 10/14 09:26 KST — 국내 개장 26분 후. 옛 게이트는 통과시킨다.
    t2 = krx(2026, 10, 14, 9, 26)
    assert old_gate_kr(t2) is True
    assert mc.is_order_window(mc.MARKET_KRX, t2) is True    # T05 도 주문 구간으로 본다

    # 10/13 15:33 KST — 국내 마감 3분 전. 옛 게이트도 막았지만 T05 는 마진으로도 막는다.
    t3 = krx(2026, 10, 13, 15, 33)
    assert old_gate_kr(t3) is False
    assert mc.is_order_window(mc.MARKET_KRX, t3) is False


# ── next_session / trading_days_between ─────────────────

def test_next_session_from_intraday_goes_to_next_day():
    """장중이면 다음 거래일 개장이다."""
    nxt = mc.next_session(mc.MARKET_KRX, krx(2026, 10, 20, 10, 0))
    assert nxt.astimezone(KST).date() == date(2026, 10, 21)
    assert nxt.astimezone(KST).hour == 9


def test_next_session_skips_holidays():
    """연휴를 건너뛴다. 2026-09-23(수) 다음은 9/24·9/25 휴장이라 9/28(월)."""
    nxt = mc.next_session(mc.MARKET_KRX, krx(2026, 9, 23, 10, 0))
    assert nxt.astimezone(KST).date() == date(2026, 9, 28)


def test_next_session_before_open_same_day():
    """개장 전이면 같은 날 개장 시각이다."""
    nxt = mc.next_session(mc.MARKET_KRX, krx(2026, 10, 20, 7, 0))
    assert nxt.astimezone(KST).date() == date(2026, 10, 20)


def test_next_session_from_holiday():
    """휴장일에는 다음 거래일 개장을 돌려준다."""
    nxt = mc.next_session(mc.MARKET_KRX, krx(2026, 12, 25, 12, 0))
    assert nxt.astimezone(KST).date() == date(2026, 12, 28)   # 12/26·27 은 주말


def test_next_session_is_tz_aware():
    """반환값은 tz-aware 다."""
    assert mc.next_session(mc.MARKET_US, ny(2026, 10, 20, 12, 0)).tzinfo is not None


def test_trading_days_between_excludes_end_and_weekend():
    """2026-10-19(월) ~ 10-23(금): 개장일 4일(월·화·수·목), 금요일은 미포함."""
    assert mc.trading_days_between(
        mc.MARKET_KRX, krx(2026, 10, 19, 9, 0), krx(2026, 10, 23, 9, 0)
    ) == 4


def test_trading_days_between_skips_holidays():
    """연휴가 끼면 줄어든다. 9/21(월)~9/29(화): 9/24·25 휴장."""
    # 9/21(월), 9/22(화), 9/23(수) 개장 / 9/24·25 휴장 / 9/26·27 주말 / 9/28(월) 개장
    assert mc.trading_days_between(
        mc.MARKET_KRX, krx(2026, 9, 21, 9, 0), krx(2026, 9, 29, 9, 0)
    ) == 4


def test_trading_days_between_zero_when_not_increasing():
    """end <= start 면 0 이다."""
    t = krx(2026, 10, 20, 9, 0)
    assert mc.trading_days_between(mc.MARKET_KRX, t, t) == 0
    assert mc.trading_days_between(
        mc.MARKET_KRX, t, krx(2026, 10, 19, 9, 0)
    ) == 0


def test_trading_days_between_single_day_is_zero():
    """
    같은 날 ~ 같은 날은 0 이다 — 국내는 당일 매수·당일 판단 구조라
    "보유 거래일 0" 이 곧 당일이다.
    """
    assert mc.trading_days_between(
        mc.MARKET_KRX, krx(2026, 10, 20, 9, 30), krx(2026, 10, 20, 15, 0)
    ) == 0


# ── tz-aware 강제 (절대 규칙 9) ──────────────────────────

@pytest.mark.parametrize("fn", [
    lambda t: mc.is_market_open(mc.MARKET_KRX, t),
    lambda t: mc.is_order_window(mc.MARKET_KRX, t),
    lambda t: mc.next_session(mc.MARKET_KRX, t),
    lambda t: mc.trading_days_between(mc.MARKET_KRX, t, krx(2026, 10, 21, 9, 0)),
])
def test_naive_datetime_is_rejected(fn):
    """naive datetime 은 조용히 통과하지 않고 예외로 막는다."""
    with pytest.raises(ValueError, match="tz-aware"):
        fn(datetime(2026, 10, 20, 10, 0))


def test_unknown_market_name_raises():
    """모르는 시장 이름은 예외다."""
    with pytest.raises(ValueError, match="시장 이름"):
        mc.is_market_open("JP", krx(2026, 10, 20, 10, 0))


def test_market_timezone_mapping():
    """시장별 표시 시간대가 맞다."""
    assert mc.market_timezone(mc.MARKET_KRX) is KST
    assert mc.market_timezone(mc.MARKET_US) is ET


# ── EXTRA_HOLIDAYS (임시 휴장일 보완) ────────────────────

def test_extra_holiday_blocks_market(monkeypatch):
    """
    라이브러리가 모르는 임시 휴장일을 설정으로 막을 수 있다 (요구사항 1).

    2026-10-20(화)은 정상 거래일이다 — 설정에 넣으면 휴장으로 바뀐다.
    """
    day = krx(2026, 10, 20, 12, 0)
    assert mc.is_market_open(mc.MARKET_KRX, day) is True
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {"KRX": ["2026-10-20"]})
    assert mc.is_market_open(mc.MARKET_KRX, day) is False
    assert mc.is_order_window(mc.MARKET_KRX, day) is False
    assert mc.is_session_day(mc.MARKET_KRX, date(2026, 10, 20)) is False


def test_extra_holiday_only_affects_its_market(monkeypatch):
    """KRX 설정은 미국 시장에 영향을 주지 않는다."""
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {"KRX": ["2026-10-20"]})
    assert mc.is_market_open(mc.MARKET_US, ny(2026, 10, 20, 12, 0)) is True


def test_extra_holiday_skipped_in_next_session(monkeypatch):
    """
    임시 휴장일은 `next_session` 에서도 건너뛴다.

    안 건너뛰면 "다음 개장"이 휴장일을 가리켜 예약 로직이 어긋난다.
    """
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {"KRX": ["2026-10-21"]})
    nxt = mc.next_session(mc.MARKET_KRX, krx(2026, 10, 20, 10, 0))
    assert nxt.astimezone(KST).date() == date(2026, 10, 22)


def test_extra_holiday_excluded_from_trading_days(monkeypatch):
    """임시 휴장일은 보유 거래일 수에서도 빠진다."""
    args = (krx(2026, 10, 19, 9, 0), krx(2026, 10, 23, 9, 0))
    assert mc.trading_days_between(mc.MARKET_KRX, *args) == 4
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {"KRX": ["2026-10-20"]})
    assert mc.trading_days_between(mc.MARKET_KRX, *args) == 3


def test_bad_extra_holiday_raises(monkeypatch):
    """
    해석할 수 없는 날짜는 조용히 무시하지 않고 예외로 막는다.

    오타 하나로 휴장일 차단이 무력화되면 안 된다.
    """
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {"KRX": ["2026-13-45"]})
    with pytest.raises(ValueError, match="EXTRA_HOLIDAYS"):
        mc.is_market_open(mc.MARKET_KRX, krx(2026, 10, 20, 12, 0))


def test_extra_holidays_empty_by_default():
    """기본 설정은 빈 목록이다 (VERIFY 항목)."""
    assert mc.extra_holidays(mc.MARKET_KRX) == set()
    assert mc.extra_holidays(mc.MARKET_US) == set()


# ── 주문 거부 원인 분류 (요구사항 4) ─────────────────────

def test_classify_market_closed_messages():
    """로그에서 확인한 실제 거부 문구가 장 상태로 분류된다."""
    for msg in ("모의투자 장시작전 입니다.", "모의투자 장종료 입니다."):
        assert mc.classify_rejection("40570000", msg) == mc.REJECT_MARKET_CLOSED


def test_classify_other_categories():
    """가격·수량·기타도 구분한다."""
    assert mc.classify_rejection("x", "호가 단위에 맞지 않습니다") == mc.REJECT_PRICE
    assert mc.classify_rejection("x", "주문가능수량을 초과했습니다") == mc.REJECT_QUANTITY
    assert mc.classify_rejection("x", "알 수 없는 오류") == mc.REJECT_OTHER


def test_classify_handles_empty_message():
    """msg1 이 비어도 예외 없이 기타로 분류한다."""
    assert mc.classify_rejection(None, "") == mc.REJECT_OTHER
    assert mc.classify_rejection(None, None) == mc.REJECT_OTHER


def test_every_category_has_a_label():
    """모든 분류에 사람이 읽을 설명이 있다 (로그에 함께 남긴다)."""
    for category in (mc.REJECT_MARKET_CLOSED, mc.REJECT_PRICE,
                     mc.REJECT_QUANTITY, mc.REJECT_OTHER):
        assert category in mc.REJECT_LABELS


# ── 주문 거부 분류가 실제 주문 경로에 걸려 있는지 (요구사항 4) ──

class _FakeResponse:
    """requests.Response 대역. rt_cd!=0 인 거부 응답을 돌려준다."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_domestic_rejection_is_classified(monkeypatch, caplog):
    """
    국내 주문이 장 상태로 거부되면 로그에 분류가 남는다.

    로그 원문("모의투자 장시작전 입니다.")을 그대로 쓴다.
    실제 API 는 호출하지 않는다 — `request_order` 만 가짜로 바꾼다.
    """
    import logging

    import kis_domestic as kd

    rejected = {"rt_cd": "1", "msg_cd": "40570000",
                "msg1": "모의투자 장시작전 입니다."}
    monkeypatch.setattr(kd, "request_order",
                        lambda *a, **kw: _FakeResponse(rejected))
    monkeypatch.setattr(config, "assert_order_allowed", lambda: None)

    body = kd.order_body("379800", 1, 15000, side="BUY")
    with caplog.at_level(logging.WARNING, logger="bot"):
        with pytest.raises(RuntimeError, match="장시작전"):
            kd.send_order(body, side="BUY")

    text = caplog.text
    assert "장 상태" in text                    # 분류 라벨
    assert "40570000" in text                   # 원문 코드
    assert "모의투자 장시작전 입니다." in text    # 원문 메시지
    assert "is_order_window" in text            # 다음 조치 안내


def test_domestic_price_rejection_is_classified(monkeypatch, caplog):
    """가격 문제 거부는 '장 상태'가 아니라 가격으로 분류된다."""
    import logging

    import kis_domestic as kd

    rejected = {"rt_cd": "1", "msg_cd": "APBK0400",
                "msg1": "주문단가가 호가 단위에 맞지 않습니다."}
    monkeypatch.setattr(kd, "request_order",
                        lambda *a, **kw: _FakeResponse(rejected))

    body = kd.order_body("379800", 1, 15000, side="BUY")
    with caplog.at_level(logging.ERROR, logger="bot"):
        with pytest.raises(RuntimeError):
            kd.send_order(body, side="BUY")

    assert "가격" in caplog.text
    assert "장 상태" not in caplog.text


def test_overseas_rejection_is_classified(monkeypatch, caplog):
    """해외 주문 거부도 같은 분류를 남긴다."""
    import logging

    import kis_overseas as ko

    rejected = {"rt_cd": "1", "msg_cd": "40570000",
                "msg1": "모의투자 장종료 입니다."}
    monkeypatch.setattr(ko, "request_order",
                        lambda *a, **kw: _FakeResponse(rejected))

    body = ko.order_body(1, 100.0)
    with caplog.at_level(logging.ERROR, logger="bot"):
        with pytest.raises(RuntimeError, match="장종료"):
            ko.send_order(body, side="BUY")

    assert "장 상태" in caplog.text


def test_order_guard_still_runs_first_in_send_order(monkeypatch):
    """
    요구사항: 주문 함수 맨 앞의 `config.assert_order_allowed()` 를 유지한다.

    가드가 막으면 **응답을 받기도 전에** 예외가 나야 한다(전송 시도 자체가 없어야 함).
    """
    import kis_domestic as kd
    import kis_overseas as ko

    called = {"kd": False, "ko": False}

    def _boom():
        raise RuntimeError("실전 가드")

    monkeypatch.setattr(config, "assert_order_allowed", _boom)
    monkeypatch.setattr(kd, "request_order",
                        lambda *a, **kw: called.__setitem__("kd", True))
    monkeypatch.setattr(ko, "request_order",
                        lambda *a, **kw: called.__setitem__("ko", True))

    with pytest.raises(RuntimeError, match="실전 가드"):
        kd.send_order(kd.order_body("379800", 1, 15000, side="BUY"), side="BUY")
    with pytest.raises(RuntimeError, match="실전 가드"):
        ko.send_order(ko.order_body(1, 100.0), side="BUY")

    assert called == {"kd": False, "ko": False}
