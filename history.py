"""
일봉 히스토리 캐시 (T02).

목적: SMA(200)·ATR(14)처럼 긴 과거가 필요한 지표를 계산하려면 KIS 일봉(호출당 100건 제한)만으로는
      부족하다. 종목별 일봉을 `data/bars/{symbol}.csv`에 누적 저장하고, 갱신 시 최신 구간만 이어붙인다.

저장 형식 (CSV, header 포함):
    date,open,high,low,close,volume
    2026-01-02,100.0,102.0,99.5,101.5,1200000

- date: YYYY-MM-DD (문자열). 정렬 기준은 date 오름차순.
- 같은 date가 들어오면 **새 값으로 덮어쓴다**(중복 제거). 수정주가 소급 변경을 반영하기 위함.
- pyarrow는 쓰지 않는다(사용자 지시). parquet 대신 csv.

메타 파일 `data/bars/{symbol}.meta.json`:
    {"symbol": ..., "adjusted": true, "source": "kis", "exchange": "NAS",
     "updated_at": ..., "rows": N, "min_date": ..., "max_date": ...}

데이터 확보 경로는 (1) 표준 스키마 CSV를 사용자가 직접 넣기, (2) KIS 일봉 반복 호출 두 가지다.
외부 데이터를 임의로 크롤링하지 않는다.

**반복 호출 가능 여부 (2026-10-19 T02, 공식 샘플 확인)**
  - 해외 일봉(`/uapi/overseas-price/v1/quotations/dailyprice`, HHDFS76240000):
    공식 샘플이 응답 헤더의 `tr_cont` 가 "M"/"F" 일 때 연속조회로 재귀 호출한다
    → **반복 호출(연속조회)이 공식적으로 지원된다.** 1회 호출 건수 상한은 문서화돼 있지 않다.
  - 국내 일봉(`/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice`, FHKST03010100):
    공식 샘플에 페이지네이션·연속조회 로직이 없다. 문서상 **1회 최대 100건**이고,
    종료일자 기준 100건 제한이다 → 초과분은 호출자가 기간을 나눠 여러 번 호출해야 한다.
  두 경우 모두 "기간을 나눠 여러 번 호출"하는 방식은 가능하지만, 저장소 코드에는 아직 없다.

**미구현(확인 필요)**: KIS 일봉 응답에서 종가 외의 필드명(날짜/시가/고가/저가/거래량)은
공식 샘플에서 확인하지 못했다(`output2` 를 DataFrame 으로 넘길 뿐 키를 열거하지 않음).
절대 규칙 4에 따라 추측 구현을 하지 않고 `kis_rows_to_bars()`가 NotImplementedError를 낸다.
그때까지 확보 경로 (2)는 쓸 수 없어, **CSV 직접 입력 경로만 사용 가능**하다.
"""
import csv
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("bot")

# 절대 규칙 9: 시간은 항상 tz-aware. 일봉은 한국장 기준이므로 KST를 쓴다.
# date.today()/datetime.now() 는 시스템 로컬 시간이라 해외 서버·UTC 환경에서 하루가 어긋난다.
KST = ZoneInfo("Asia/Seoul")

BARS_DIR = Path("data/bars")
COLUMNS = ["date", "open", "high", "low", "close", "volume"]

# KIS 일봉 응답에서 **저장소 코드로 확인된** 종가 키.
#   해외 일봉(dailyprice): "clos"      — kis_overseas.get_daily_closes
#   국내 일봉(inquire-daily-itemchartprice): "stck_clpr" — kis_domestic.get_daily_closes
KIS_CLOSE_KEYS = ("clos", "stck_clpr")


def _bars_path(symbol: str) -> Path:
    return BARS_DIR / f"{symbol}.csv"


def _meta_path(symbol: str) -> Path:
    return BARS_DIR / f"{symbol}.meta.json"


def _norm_date(value) -> str:
    """date/datetime/문자열을 YYYY-MM-DD로 정규화한다."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    # 20260102 형태도 받아준다 (KIS 응답 날짜 형식 후보)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _to_bar(row: dict) -> dict:
    """표준 스키마 행을 검증·변환한다. 숫자 변환 실패는 예외로 알린다."""
    try:
        return {
            "date": _norm_date(row["date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0) or 0),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"일봉 행 형식이 잘못되었습니다: {row!r} ({e})") from e


def kis_rows_to_bars(rows: list[dict]) -> list[dict]:
    """
    KIS 일봉 응답 행을 표준 스키마로 바꾼다. **현재는 미구현.**

    종가 키(해외 "clos", 국내 "stck_clpr")만 저장소 코드로 확인돼 있고,
    날짜·시가·고가·저가·거래량 키는 공식 문서·공식 샘플에서 확인하지 못했다.
    절대 규칙 4(추측 금지)에 따라 확인 전에는 구현하지 않는다.
    """
    raise NotImplementedError(
        "KIS 일봉 응답의 날짜/시가/고가/저가/거래량 필드명이 공식 문서·공식 샘플에서 "
        "확인되지 않았습니다. 확인된 키는 종가뿐입니다: " + "/".join(KIS_CLOSE_KEYS) + ". "
        "TODO: 공식 문서 확인 후 매핑 추가. 그 전까지는 표준 스키마 CSV를 직접 넣어 주세요 "
        "(" + ",".join(COLUMNS) + ")."
    )


def load_bars(symbol: str) -> list[dict]:
    """저장된 일봉을 date 오름차순으로 읽는다. 파일이 없으면 빈 리스트."""
    path = _bars_path(symbol)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    bars = [_to_bar(r) for r in rows if r.get("date")]
    bars.sort(key=lambda b: b["date"])
    return bars


def merge_bars(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """
    기존 일봉과 새 일봉을 병합한다. date 중복 제거(새 값 우선) 후 오름차순 정렬.

    날짜는 병합 시점에 `YYYY-MM-DD` 로 **반드시 정규화**한다. 정규화하지 않으면
    "20261020" 과 "2026-10-20" 이 서로 다른 날짜로 취급돼 중복 제거가 깨지고,
    메타의 min_date/max_date 가 원본 형식으로 남는다.
    """
    merged: dict[str, dict] = {}
    for bar in existing:
        normalized = dict(bar)
        normalized["date"] = _norm_date(bar["date"])
        merged[normalized["date"]] = normalized
    for bar in incoming:
        normalized = dict(bar)
        normalized["date"] = _norm_date(bar["date"])
        merged[normalized["date"]] = normalized
    return [merged[d] for d in sorted(merged)]


def save_bars(symbol: str, bars: list[dict], meta: dict | None = None) -> None:
    """일봉을 CSV로 저장하고 메타를 갱신한다. 원자적 쓰기(temp → replace)."""
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    ordered = merge_bars([], bars)

    path = _bars_path(symbol)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for bar in ordered:
            writer.writerow({c: bar.get(c, "") for c in COLUMNS})
    os.replace(tmp, path)

    payload = dict(meta or {})
    payload.update({
        "symbol": symbol,
        "rows": len(ordered),
        "min_date": ordered[0]["date"] if ordered else None,
        "max_date": ordered[-1]["date"] if ordered else None,
        "updated_at": datetime.now(KST).isoformat(timespec="seconds"),
    })
    _meta_path(symbol).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        f"[history] {symbol} 일봉 {len(ordered)}건 저장 "
        f"({payload['min_date']} ~ {payload['max_date']})"
    )


def load_meta(symbol: str) -> dict:
    """메타 파일을 읽는다. 없으면 빈 dict."""
    path = _meta_path(symbol)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[history] 메타 파일 읽기 실패 ({path}): {e}")
        return {}


def update_from_rows(symbol: str, rows: list[dict], meta: dict | None = None) -> int:
    """
    표준 스키마 일봉 행을 기존 캐시에 이어붙인다. 반환: 병합 후 전체 건수.

    rows 는 {date, open, high, low, close, volume} 형태여야 한다.
    KIS 원본 행을 넘기려면 먼저 kis_rows_to_bars()를 쓰세요(현재 미구현).
    """
    existing = load_bars(symbol)
    incoming = [_to_bar(r) for r in rows]
    merged = merge_bars(existing, incoming)
    merged_meta = load_meta(symbol)
    merged_meta.update(meta or {})
    save_bars(symbol, merged, merged_meta)
    return len(merged)


def get_confirmed_bars(symbol: str, as_of: date | datetime | str | None = None) -> list[dict]:
    """
    `as_of` 시점 기준 **당일 미확정 봉을 제외한** 일봉을 date 오름차순으로 반환한다.

    장중에 호출해도 as_of 당일 봉은 결과에 포함되지 않는다.
    as_of 를 주지 않으면 오늘(**KST 기준**)을 기준으로 한다(절대 규칙 9).
    """
    cutoff = _norm_date(as_of) if as_of is not None else datetime.now(KST).date().isoformat()
    return [b for b in load_bars(symbol) if b["date"] < cutoff]


def get_confirmed_closes(symbol: str, as_of=None, count: int | None = None) -> list[float]:
    """확정 봉의 종가만 과거 → 최신 순으로 반환. count를 주면 최근 count개."""
    closes = [b["close"] for b in get_confirmed_bars(symbol, as_of)]
    return closes[-count:] if count else closes
