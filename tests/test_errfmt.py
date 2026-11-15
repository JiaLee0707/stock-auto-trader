"""errfmt.exc_summary — 예외 메시지를 읽지 않고 요약만 남기는지 확인한다 (T09b-5).

이 요약은 로그·알림·DB 사유에 쓰인다. 예외 메시지에는 요청 URL(쿼리의 계좌 구분 값)·토큰이 들어갈 수 있으므로
메시지가 한 글자라도 새면 안 된다. 변이 검사(M89~M95)로 이 테스트가 실제로 깨지는지 확인한다.
"""
import ast
import json
import sys
from pathlib import Path

import pytest
import requests

import errfmt
from errfmt import exc_summary


def _raised(factory):
    """factory() 가 만든 예외를 실제로 raise → catch 해서 돌려준다(traceback 이 붙는다)."""
    try:
        raise factory()
    except Exception as e:
        return e


class _WithCode(Exception):
    code = 429


class _Hostile(Exception):
    @property
    def status_code(self):
        raise RuntimeError("property exploded")


class _NoStr(Exception):
    def __str__(self):
        raise AssertionError("str(exc) 를 호출했다")


class _A(Exception):
    pass


class _B(Exception):
    pass


def test_message_not_included():
    out = exc_summary(_raised(lambda: RuntimeError("SECRET-TOKEN-123")))
    assert "RuntimeError" in out
    assert "SECRET-TOKEN-123" not in out


def test_requests_error_query_not_included():
    text = ("HTTPConnectionPool(host='127.0.0.1', port=9): Max retries exceeded with url: "
            "/x?CANO=DUMMY12345&ACNT_PRDT_CD=01")
    out = exc_summary(_raised(lambda: requests.exceptions.ConnectionError(text)))
    assert "ConnectionError" in out
    for leaked in ("DUMMY12345", "CANO", "ACNT_PRDT_CD", "127.0.0.1"):
        assert leaked not in out


def test_http_status_from_response():
    resp = requests.Response()
    resp.status_code = 500
    exc = _raised(lambda: requests.exceptions.HTTPError("500 Server Error", response=resp))
    assert "HTTPError(status=500)" in exc_summary(exc)


def test_status_from_code_attribute():
    assert "(status=429)" in exc_summary(_raised(lambda: _WithCode("rate limited")))


@pytest.mark.parametrize("bad", ["SECRET-STATUS", 99999, True])
def test_status_rejects_non_http_values(bad):
    exc = RuntimeError("m")
    exc.status_code = bad
    out = exc_summary(exc)
    assert "status" not in out
    assert "SECRET-STATUS" not in out


def test_explicit_cause_chain_names_only():
    def chain():
        try:
            raise ValueError("SECRET-INNER")
        except ValueError as inner:
            raise RuntimeError("SECRET-OUTER") from inner

    try:
        chain()
    except RuntimeError as e:
        out = exc_summary(e)
    assert out.startswith("RuntimeError")
    assert "<- ValueError" in out
    assert "SECRET-INNER" not in out and "SECRET-OUTER" not in out


def test_implicit_context_chain_names_only():
    try:
        try:
            raise ValueError("SECRET-INNER")
        except ValueError:
            raise RuntimeError("SECRET-OUTER")
    except RuntimeError as e:
        out = exc_summary(e)
    assert "<- ValueError" in out
    assert "SECRET-INNER" not in out and "SECRET-OUTER" not in out


def test_chain_cycle_safe():
    a, b = _A(), _B()
    a.__cause__ = b
    b.__cause__ = a
    assert exc_summary(a) == "_A <- _B"


def test_chain_is_bounded():
    exc = ValueError("e0")
    for i in range(1, 20):
        nxt = ValueError(f"e{i}")
        nxt.__cause__ = exc
        exc = nxt
    assert exc_summary(exc).count(" <- ") == errfmt._MAX_CHAIN


def test_location_is_project_frame():
    out = exc_summary(_raised(lambda: ValueError("x")))
    assert "@ tests/test_errfmt.py:" in out


def test_location_skips_stdlib_frames():
    try:
        json.loads("{")
    except ValueError as e:
        out = exc_summary(e)
    assert "@ tests/test_errfmt.py:" in out
    assert "decoder.py" not in out


def test_location_skips_venv_frames(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(errfmt, "_ROOT", root)
    lib_file = str(root / ".venv" / "lib" / "site-packages" / "lib.py")
    app_file = str(root / "app.py")
    ns = {}
    exec(compile("def boom():\n    raise ValueError('x')\n", lib_file, "exec"), ns)
    exec(compile("def run(f):\n    f()\n", app_file, "exec"), ns)
    try:
        ns["run"](ns["boom"])
    except ValueError as e:
        out = exc_summary(e)
    assert "@ app.py:2" in out
    assert "site-packages" not in out


def test_no_traceback_means_no_location():
    assert exc_summary(RuntimeError("x")) == "RuntimeError"


def test_hostile_status_property_does_not_raise():
    out = exc_summary(_raised(lambda: _Hostile("m")))
    assert "_Hostile" in out


def test_str_is_never_called():
    out = exc_summary(_raised(lambda: _NoStr("m")))
    assert out.startswith("_NoStr")
    assert "예외(요약 실패)" not in out


def test_non_exception_input():
    assert exc_summary("SECRET-STRING") == "str"


def test_oserror_path_not_included():
    out = exc_summary(_raised(lambda: PermissionError(13, "Permission denied", "/secret/data/bot.db")))
    assert "PermissionError" in out
    assert "/secret" not in out and "Permission denied" not in out


def test_imports_only_stdlib():
    """프로젝트 모듈을 import 하면 순환 import 가 생긴다 — 표준 라이브러리만 허용."""
    tree = ast.parse(Path(errfmt.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots, "import 를 하나도 못 찾았다 — 검사가 깨졌다"
    outside = sorted(roots - set(sys.stdlib_module_names))
    assert not outside, f"표준 라이브러리 밖 import: {outside}"
