"""
T02: 일봉 히스토리 캐시(history.py) 단위 테스트.

외부 라이브러리 불필요. tmp_path 로 실제 파일 저장/병합/확정봉 규칙을 검증한다.
"""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import history


@pytest.fixture
def bars_dir(tmp_path, monkeypatch):
    """data/bars 를 tmp_path 로 격리한다(실제 저장소 오염 방지)."""
    monkeypatch.setattr(history, "BARS_DIR", tmp_path)
    return tmp_path


def _row(d, o=100.0, h=102.0, l=99.0, c=101.0, v=1000):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


# --- 날짜 정규화 ---------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (date(2026, 10, 20), "2026-10-20"),
    (datetime(2026, 10, 20, 15, 30), "2026-10-20"),
    ("2026-10-20", "2026-10-20"),
    ("20261020", "2026-10-20"),
    ("  20261020  ", "2026-10-20"),
])
def test_norm_date(value, expected):
    assert history._norm_date(value) == expected


# --- 저장 / 읽기 ---------------------------------------------------------

def test_save_and_load_roundtrip(bars_dir):
    history.save_bars("AAA", [_row("20261018"), _row("20261020"), _row("20261019")])
    bars = history.load_bars("AAA")
    assert [b["date"] for b in bars] == ["2026-10-18", "2026-10-19", "2026-10-20"]
    assert bars[0]["close"] == 101.0


def test_load_bars_missing_file_returns_empty(bars_dir):
    assert history.load_bars("NOPE") == []


def test_csv_headers_are_stable(bars_dir):
    """표준 스키마(사용자가 직접 넣을 형식)가 그대로 유지되는지 확인."""
    history.save_bars("AAA", [_row("20261020")])
    first_line = (bars_dir / "AAA.csv").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == ",".join(history.COLUMNS)


def test_meta_written_with_source_and_adjusted(bars_dir):
    history.save_bars("AAA", [_row("20261020")],
                      {"source": "kis", "adjusted": True, "exchange": "NAS"})
    meta = json.loads((bars_dir / "AAA.meta.json").read_text(encoding="utf-8"))
    assert meta["source"] == "kis"
    assert meta["adjusted"] is True
    assert meta["rows"] == 1
    assert meta["min_date"] == meta["max_date"] == "2026-10-20"
    assert meta["updated_at"]  # 존재만 확인 (값은 현재 시각)


def test_load_meta_missing_returns_empty(bars_dir):
    assert history.load_meta("NOPE") == {}


# --- 병합 (중복 제거 + 정렬) ---------------------------------------------

def test_merge_dedupes_and_sorts():
    existing = [_row("2026-10-18"), _row("2026-10-20")]
    incoming = [_row("2026-10-19"), _row("2026-10-20")]
    merged = history.merge_bars(existing, incoming)
    assert [b["date"] for b in merged] == ["2026-10-18", "2026-10-19", "2026-10-20"]


def test_merge_new_value_wins_for_same_date():
    """수정주가 소급 변경 반영: 같은 날짜는 새 값으로 덮어쓴다."""
    existing = [_row("2026-10-20", c=101.0)]
    incoming = [_row("2026-10-20", c=555.0)]
    merged = history.merge_bars(existing, incoming)
    assert len(merged) == 1
    assert merged[0]["close"] == 555.0


def test_update_from_rows_appends_incrementally(bars_dir):
    history.update_from_rows("AAA", [_row("20261018"), _row("20261019")])
    total = history.update_from_rows("AAA", [_row("20261020")])
    assert total == 3
    assert history.load_bars("AAA")[-1]["date"] == "2026-10-20"


def test_update_from_rows_rejects_bad_row(bars_dir):
    with pytest.raises(ValueError):
        history.update_from_rows("AAA", [{"date": "20261020", "open": "abc"}])


def test_bad_date_key_raises():
    with pytest.raises(ValueError):
        history._to_bar({"open": 1, "high": 1, "low": 1, "close": 1})


# --- 확정 봉 규칙 --------------------------------------------------------

def test_confirmed_bars_excludes_as_of_day(bars_dir):
    history.save_bars("AAA", [
        _row("20261017"), _row("20261018"), _row("20261019"), _row("20261020"),
    ])
    confirmed = history.get_confirmed_bars("AAA", as_of=date(2026, 10, 20))
    assert [b["date"] for b in confirmed] == ["2026-10-17", "2026-10-18", "2026-10-19"]


def test_confirmed_bars_accepts_string_and_compact_date(bars_dir):
    history.save_bars("AAA", [_row("20261019"), _row("20261020")])
    assert len(history.get_confirmed_bars("AAA", as_of="2026-10-20")) == 1
    assert len(history.get_confirmed_bars("AAA", as_of="20261020")) == 1


def test_confirmed_bars_defaults_to_today(bars_dir):
    history.save_bars("AAA", [_row("20200101"), _row("20200102")])
    # 과거 데이터만 있으면 오늘 기준으로 전부 확정
    assert len(history.get_confirmed_bars("AAA")) == 2


def test_default_cutoff_uses_kst_today(bars_dir):
    """
    절대 규칙 9: 기본 기준일은 KST 여야 한다.
    KST 오늘 봉은 미확정으로 제외되고, 어제 봉은 확정으로 포함돼야 한다.
    시스템 로컬 시간을 쓰면 KST와 날짜가 다른 환경에서 이 경계가 어긋난다.
    """
    today_kst = datetime.now(ZoneInfo("Asia/Seoul")).date()
    yesterday = (today_kst - timedelta(days=1)).isoformat()

    history.save_bars("AAA", [_row(yesterday), _row(today_kst)])
    dates = [b["date"] for b in history.get_confirmed_bars("AAA")]

    assert yesterday in dates, "어제 봉은 확정이어야 한다"
    assert today_kst.isoformat() not in dates, "KST 오늘 봉은 미확정이어야 한다"


def test_updated_at_is_tz_aware_kst(bars_dir):
    """메타의 updated_at 은 tz-aware KST 여야 한다(naive 금지)."""
    history.save_bars("AAA", [_row("20261020")])
    meta = json.loads((bars_dir / "AAA.meta.json").read_text(encoding="utf-8"))
    parsed = datetime.fromisoformat(meta["updated_at"])
    assert parsed.tzinfo is not None, "updated_at 이 naive 다"
    assert parsed.utcoffset() == timedelta(hours=9)


def test_confirmed_closes_returns_tail(bars_dir):
    history.save_bars("AAA", [
        _row("20261001", c=1.0), _row("20261002", c=2.0),
        _row("20261003", c=3.0), _row("20261004", c=4.0),
    ])
    closes = history.get_confirmed_closes("AAA", as_of=date(2026, 10, 4), count=2)
    assert closes == [2.0, 3.0]
    assert history.get_confirmed_closes("AAA", as_of=date(2026, 10, 4)) == [1.0, 2.0, 3.0]


# --- KIS 원본 행 변환: 추측 금지 확인 ------------------------------------

def test_kis_rows_to_bars_is_not_implemented():
    """
    KIS 일봉 응답의 날짜/시가/고가/저가/거래량 필드명이 공식 문서에서 확인되지 않아
    추측 구현을 하지 않았음을 고정한다(절대 규칙 4).
    """
    with pytest.raises(NotImplementedError):
        history.kis_rows_to_bars([{"clos": "101.0"}])


def test_only_closes_key_is_documented_as_confirmed():
    assert history.KIS_CLOSE_KEYS == ("clos", "stck_clpr")
