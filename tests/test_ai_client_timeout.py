"""
T09b: Gemini 클라이언트는 항상 요청 제한시간을 가진다.

제한시간이 없으면 응답이 멈췄을 때 main.job() 이 그 호출에서 기다리는 동안 손절을 포함한
매매 사이클이 실행되지 않는다. 가짜 클라이언트로 인자를 확인하고, 소스를 스캔해 제한시간 없이
클라이언트를 만드는 코드가 다시 생기지 않게 한다.
"""
import ast
import pathlib

import pytest

import ai_analyzer
import config

SOURCE = pathlib.Path(ai_analyzer.__file__).resolve().read_text(encoding="utf-8")


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return object()


@pytest.fixture
def spy(monkeypatch):
    s = _Spy()
    monkeypatch.setattr(ai_analyzer.genai, "Client", s)
    return s


def test_client_gets_the_timeout_in_milliseconds(spy):
    ai_analyzer._build_client("k", 30)
    assert len(spy.calls) == 1
    assert spy.calls[0]["api_key"] == "k"
    assert spy.calls[0]["http_options"].timeout == 30_000


def test_fractional_seconds_are_converted(spy):
    ai_analyzer._build_client("k", 2.5)
    assert spy.calls[0]["http_options"].timeout == 2_500


# nan 은 변이(검증 제거) 상태에서도 int() 변환이 ValueError 를 내서 통과한다 — 정상이다.
@pytest.mark.parametrize("bad", [0, -5, 301, float("nan"), float("inf")],
                         ids=["zero", "negative", "over_300", "nan", "inf"])
def test_unusable_timeouts_are_rejected_before_a_client_is_built(spy, bad):
    with pytest.raises(ValueError):
        ai_analyzer._build_client("k", bad)
    assert spy.calls == []


def test_default_timeout_setting_is_usable():
    assert 0 < config.GEMINI_TIMEOUT_SEC <= 300


def test_a_real_client_accepts_the_options():
    """SDK 가 이 옵션을 실제로 받아들이는지 확인한다 — 가짜 키로 만들기만 한다(생성 시점에는 요청을 보내지 않는 것으로 안다)."""
    assert ai_analyzer._build_client("fake-key-for-tests", 30) is not None


def _raw_client_calls(source: str) -> list[int]:
    """`_build_client` 밖에서 genai.Client(...) 를 직접 부르는 줄 번호."""
    bad = []
    for top in ast.parse(source).body:
        if isinstance(top, ast.FunctionDef) and top.name == "_build_client":
            continue
        for node in ast.walk(top):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "Client" and getattr(node.func.value, "id", None) == "genai"):
                bad.append(node.lineno)
    return sorted(bad)


def test_scanner_detects_a_raw_client_but_not_the_helper():
    assert _raw_client_calls("x = genai.Client(api_key='k')\n") == [1]
    assert _raw_client_calls("def _build_client(a):\n    return genai.Client(api_key=a)\n") == []
    assert _raw_client_calls("c = _build_client('k', 30)\n") == []


def test_no_client_is_built_outside_the_helper():
    assert _raw_client_calls(SOURCE) == []
    assert "_build_client(config.GEMINI_API_KEY" in SOURCE
