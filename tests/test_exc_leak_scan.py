"""예외 문구가 로그·알림·DB 사유로 새지 않는지 소스를 스캔한다 (T09b-5).

왜: `requests` 예외 문구에는 요청 URL 이 들어간다. KIS 조회(GET)는 계좌 구분 값(CANO·ACNT_PRDT_CD)을 쿼리스트링에 싣고,
루프백 서버로 재현한 결과 GET 의 HTTPError 문구에 쿼리 값이 그대로 들어갔다. 예외 문구를 로그·알림·DB 에 남기면 안 된다.

규칙(AST 로 검사한다. 정규식은 `{type(e).__name__}: {e}` 처럼 타입을 붙여도 본문이 나가는 경우를 구분하지 못한다):
  1. `except ... as X` 안에서 X 는 `type(X)`·`exc_summary(X)`·`raise ... from X`·`getattr/isinstance`·`"..." in str(X)` 비교·대입으로만 쓴다.
     `{X}`, `str(X)`, `repr(X)`, `X.args`, `logger.error("%s", X)` 는 위반이다.
  2. 이름이 exc/exception 인 함수 인자도 예외 변수로 본다(주문 미확정 콜백 `on_uncertain(exc)`).
  3. `exc_info=`(False·None 제외), `logger.exception(`, `traceback.*` 는 어디서든 위반이다 — traceback 끝에 예외 문구가 찍힌다.
  4. 메시지를 우리 코드가 만드는 예외(SAFE_HANDLER_TYPES)를 잡는 핸들러는 `{e}` 를 찍어도 된다.
     그 클래스를 만드는 raise 에 외부 예외 문구가 끼는 것은 1번 규칙이 함께 막는다.
알려진 한계: `msg = e` 처럼 대입한 뒤 그 변수를 출력하는 것은 따라가지 않는다. exc/exception 이외 이름의 인자도 보지 않는다.
"""
import ast
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 메시지를 우리 코드가 만드는 예외. TradingHalted 는 메시지가 주문 방향·사유와 저장된 중단 사유로 만들어진다
# (확정 — halt_trading 호출부 4곳(risk.py 3·reconcile.py 1)의 사유가 모두 코드가 만든 문구와 DB 값임을
#  읽어서 확인했다. 호출부가 늘면 이 근거를 다시 확인해야 한다).
SAFE_HANDLER_TYPES = frozenset({
    "TradingHalted", "DuplicateOrderBlocked", "EquityUnavailable", "DatabaseUnavailable", "BaselineError",
})
# 예외를 넘겨도 문구가 나가지 않는 호출
_SAFE_CALLEES = frozenset({
    "type", "isinstance", "getattr", "hasattr", "exc_summary", "_handle_uncertain", "on_uncertain",
})
_SAFE_ATTRS = frozenset({"__class__", "code", "status_code"})
_EXC_PARAM_NAMES = frozenset({"exc", "exception"})
_LOGGER_NAMES = frozenset({"logger", "logging", "log"})

# 스캔에서 빼는 파일. errfmt.py 는 예외 객체를 다루지만 메시지를 읽지 않는 헬퍼(exc_summary)라 규칙이 오탐한다
# (__traceback__·id()·내부 함수 인자). 그 보장은 tests/test_errfmt.py 가 한다. 이 목록을 늘리지 않는다.
EXCLUDED_FILES = frozenset({"errfmt.py"})


def _callee_name(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _classify(node, parents):
    """예외 변수 노드 하나의 쓰임. 안전하면 None, 아니면 위반 종류."""
    par = parents.get(node)
    if isinstance(par, ast.Call) and node in par.args:
        callee = _callee_name(par)
        if callee in _SAFE_CALLEES:
            return None
        if callee in ("str", "repr", "format"):
            grand = parents.get(par)
            if (callee == "str" and isinstance(grand, ast.Compare) and par in grand.comparators
                    and all(isinstance(op, (ast.In, ast.NotIn)) for op in grand.ops)):
                return None
            return f"{callee}()"
        return f"arg:{callee}"
    if isinstance(par, ast.Raise) and (par.cause is node or par.exc is node):
        return None
    if isinstance(par, ast.Assign) and par.value is node:
        return None
    if isinstance(par, ast.Attribute):
        return None if par.attr in _SAFE_ATTRS else f"attr.{par.attr}"
    if isinstance(par, ast.Compare):
        return None
    if isinstance(par, ast.FormattedValue):
        return "f-string"
    if isinstance(par, ast.keyword):
        return f"kwarg:{par.arg}"
    return "other-use"


def _handler_type_names(handler):
    node = handler.type
    if node is None:
        return set()
    elements = node.elts if isinstance(node, ast.Tuple) else [node]
    names = set()
    for element in elements:
        if isinstance(element, ast.Name):
            names.add(element.id)
        elif isinstance(element, ast.Attribute):
            names.add(element.attr)
        else:
            names.add("?")
    return names


def scan_source(source):
    """소스 하나를 검사해 [(줄, 종류), ...] 를 돌려준다. 문법 오류면 SyntaxError 를 그대로 올린다."""
    tree = ast.parse(source)
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    found = set()

    def check_uses(statements, var_name):
        for stmt in statements:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and node.id == var_name and isinstance(node.ctx, ast.Load):
                    kind = _classify(node, parents)
                    if kind is not None:
                        found.add((node.lineno, node.col_offset, kind))

    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names = _handler_type_names(node)
            if not (names and names <= SAFE_HANDLER_TYPES):
                check_uses(node.body, node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                if arg.arg in _EXC_PARAM_NAMES:
                    check_uses(node.body, arg.arg)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "exc_info" and not (isinstance(kw.value, ast.Constant)
                                                 and kw.value.value in (False, None)):
                    found.add((kw.value.lineno, kw.value.col_offset, "exc_info"))
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "exception"
                    and isinstance(func.value, ast.Name) and func.value.id in _LOGGER_NAMES):
                found.add((func.lineno, func.col_offset, "exception()"))
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "traceback"):
            found.add((node.lineno, node.col_offset, f"traceback.{node.attr}"))
    return sorted((lineno, kind) for lineno, _col, kind in found)


def count_named_handlers(source):
    return sum(1 for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ExceptHandler) and n.name)


def _h(body, types="Exception"):
    """`except <types> as e:` 한 줄짜리 본문을 검사해 위반 종류만 돌려준다."""
    source = f"try:\n    f()\nexcept {types} as e:\n    {body}\n"
    return sorted(kind for _lineno, kind in scan_source(source))


def test_fstring_use_flagged():
    assert _h('logger.error(f"x {e}")') == ["f-string"]


def test_str_and_repr_flagged():
    assert _h('m = str(e)') == ["str()"]
    assert _h('m = repr(e)') == ["repr()"]


def test_conversion_flag_flagged():
    assert _h('logger.error(f"x {e!r}")') == ["f-string"]
    assert _h('logger.error(f"x {e:>10}")') == ["f-string"]


def test_format_percent_and_concat_flagged():
    assert _h('m = "{}".format(e)') == ["format()"]
    assert _h('logger.error("x %s", e)') == ["arg:error"]
    assert _h('m = "x" + str(e)') == ["str()"]
    assert _h('m = "x %s" % e') == ["other-use"]


def test_exc_info_exception_and_traceback_flagged():
    assert _h('logger.error("x", exc_info=True)') == ["exc_info"]
    assert _h('logger.error("x", exc_info=False)') == []
    assert _h('logger.exception("x")') == ["exception()"]
    assert _h('traceback.print_exc()') == ["traceback.print_exc"]
    assert _h('logger.error(traceback.format_exc())') == ["traceback.format_exc"]


def test_exc_info_flagged_outside_handlers_too():
    assert scan_source('logger.warning("x", exc_info=True)\n') == [(1, "exc_info")]


def test_safe_forms_allowed():
    safe_bodies = (
        'logger.error(f"x {type(e).__name__}")',
        'logger.error(f"x {exc_summary(e)}")',
        'logger.error(f"x {errfmt.exc_summary(e)}")',
        'code = getattr(e, "code", None)',
        'last = e',
        'ok = "429" in str(e)',
        'ok = "429" not in str(e)',
        'raise RuntimeError("fixed") from e',
        'name = e.__class__.__name__',
        'status = e.status_code',
    )
    for body in safe_bodies:
        assert _h(body) == [], body


def test_type_prefix_does_not_hide_message():
    assert _h('logger.error(f"{type(e).__name__}: {e}")') == ["f-string"]


def test_raise_with_exception_text_flagged():
    assert _h('raise RuntimeError(f"x {e}") from e') == ["f-string"]


def test_attribute_uses():
    assert _h('m = e.args') == ["attr.args"]
    assert _h('m = e.response') == ["attr.response"]


def test_safe_handler_types():
    body = 'logger.critical(f"x {e}")'
    for types in ("TradingHalted", "risk.TradingHalted", "(DuplicateOrderBlocked, EquityUnavailable)",
                  "db.DatabaseUnavailable", "BaselineError"):
        assert _h(body, types) == [], types
    for types in ("Exception", "ValueError", "(TradingHalted, ValueError)", "sqlite3.Error"):
        assert _h(body, types) == ["f-string"], types


def test_function_param_named_exc():
    source = "def cb(exc):\n    db.update(raw={'error': str(exc)})\n    logger.error(f'x {exc}')\n"
    assert sorted(kind for _lineno, kind in scan_source(source)) == ["f-string", "str()"]
    assert scan_source("def cb(on_uncertain, exc):\n    on_uncertain(exc)\n") == []


def test_names_without_as_are_ignored():
    assert scan_source("try:\n    f()\nexcept Exception:\n    x = f'{e}'\n") == []
    assert scan_source("for e in items:\n    print(f'{e}')\n") == []


def test_line_numbers_reported():
    source = "x = 1\ntry:\n    f()\nexcept Exception as e:\n    pass\n    logger.error(f'x {e}')\n"
    assert scan_source(source) == [(6, "f-string")]


def _repo_files():
    return sorted(p for p in ROOT.glob("*.py") if p.name not in EXCLUDED_FILES)


def test_scan_covers_codebase():
    """스캔이 아무것도 안 보는 상태로 통과하는 것을 막는다."""
    files = _repo_files()
    assert len(files) >= 15, f"루트 .py 가 너무 적다: {len(files)}"
    assert any(p.name == "trader.py" for p in files)
    handlers = sum(count_named_handlers(p.read_text(encoding="utf-8")) for p in files)
    assert handlers >= 50, f"이름 있는 except 가 너무 적다: {handlers}"


def test_no_exception_text_leaks_in_source():
    found = {}
    for path in _repo_files():
        for lineno, kind in scan_source(path.read_text(encoding="utf-8")):
            found.setdefault(path.name, []).append((lineno, kind))
    if not found:
        return
    total = sum(len(v) for v in found.values())
    kinds = Counter(kind for v in found.values() for _lineno, kind in v)
    lines = [f"예외 문구가 로그·알림·DB 사유로 샐 수 있는 코드 {total}건 (errfmt.exc_summary(e) 로 요약하세요)"]
    lines += [f"  {len(v):3d} {name}" for name, v in sorted(found.items())]
    lines += [f"  {count:3d} [{kind}]" for kind, count in sorted(kinds.items())]
    lines += [f"{name}:{lineno} [{kind}]" for name, v in sorted(found.items()) for lineno, kind in sorted(v)]
    pytest.fail("\n".join(lines), pytrace=False)


def test_excluded_files_are_pinned():
    """스캔 제외 목록이 조용히 늘어나는 것을 막는다."""
    assert EXCLUDED_FILES == frozenset({"errfmt.py"})
    assert (ROOT / "errfmt.py").exists()
    assert all(p.name not in EXCLUDED_FILES for p in _repo_files())


def test_safe_handler_types_are_defined_classes():
    """허용 목록에 이름만 있고 실제 클래스가 없는 상태를 막는다."""
    defined = set()
    for path in ROOT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                defined.add(node.name)
    assert SAFE_HANDLER_TYPES <= defined, sorted(SAFE_HANDLER_TYPES - defined)
