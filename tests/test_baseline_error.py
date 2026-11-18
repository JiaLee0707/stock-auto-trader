"""손실 기준선 읽기 오류(BaselineError) — 메시지는 고정 문구이고 ValueError 의 하위 클래스다 (T09b-5).

스캔 테스트(test_exc_leak_scan.py)는 BaselineError 를 잡는 핸들러의 `{e}` 를 허용한다. 그래서 이 예외의 메시지가
저장된 기준선 문자열이나 외부 예외 문구를 담지 않는다는 것을 여기서 고정한다.
"""
import pytest

import risk


@pytest.mark.parametrize("raw", [
    "not json SECRET-MARKER",
    "{}",
    '{"peak": "SECRET-MARKER"}',
    "null",
    "[]",
])
def test_corrupt_baseline_raises_baseline_error(raw):
    with pytest.raises(risk.BaselineError) as info:
        risk._parse_baseline(raw)
    assert "SECRET-MARKER" not in str(info.value)


def test_non_finite_baseline_raises_baseline_error():
    raw = '{"peak": Infinity, "day": "2026-01-02", "day_start": 1.0, "last_equity": 1.0, "unknown_ack": 0}'
    with pytest.raises(risk.BaselineError, match="peak"):
        risk._parse_baseline(raw)


def test_baseline_error_is_a_value_error():
    assert issubclass(risk.BaselineError, ValueError)
    with pytest.raises(ValueError):
        risk._parse_baseline("not json")
