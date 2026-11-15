"""예외를 로그·알림·DB 사유에 남길 때 쓰는 안전한 요약 (T09b-5).

왜 필요한가: `requests` 예외 문구에는 요청 URL 이 통째로 들어가고, KIS 조회는 계좌 구분 값
(`CANO`, `ACNT_PRDT_CD`)을 쿼리스트링에 싣는다. 그래서 `str(e)` 나 `{e}` 를 로그·알림·DB 에
남기면 계좌 식별 값이 새어 나갈 수 있다. 이 모듈은 **예외 메시지를 읽지 않는다.**

돌려주는 것: 예외 타입 이름, HTTP 상태 코드(100~599 정수일 때만), 프로젝트 안의 마지막 호출 위치,
원인 체인의 타입 이름. 프로젝트 모듈을 import 하지 않는다(순환 import 방지, 테스트가 고정한다).
요약을 만들다 실패해도 예외를 올리지 않는다 — 로그를 남기는 코드가 다시 예외를 내면 안 된다.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_MAX_CHAIN = 5
_EXCLUDED_PARTS = (".venv", "site-packages")
_FALLBACK = "예외(요약 실패)"


def _http_status(exc):
    """HTTP 상태 코드. 100~599 의 int 일 때만 돌려준다(bool·문자열·범위 밖 값은 무시)."""
    candidates = []
    try:
        response = getattr(exc, "response", None)
        if response is not None:
            candidates.append(getattr(response, "status_code", None))
        candidates.append(getattr(exc, "status_code", None))
        candidates.append(getattr(exc, "code", None))
    except Exception:
        return None
    for value in candidates:
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def _project_location(exc):
    """원인이 된 코드 위치 — 프로젝트 안의 마지막 프레임. 표준 라이브러리·가상환경 프레임은 건너뛴다."""
    try:
        found = None
        tb = exc.__traceback__
        while tb is not None:
            name = tb.tb_frame.f_code.co_filename
            if not name.startswith("<"):
                try:
                    rel = Path(name).resolve().relative_to(_ROOT)
                except ValueError:
                    rel = None
                if rel is not None and not any(part in _EXCLUDED_PARTS for part in rel.parts):
                    found = f"{rel.as_posix()}:{tb.tb_lineno}"
            tb = tb.tb_next
        return found
    except Exception:
        return None


def _chain_names(exc):
    """원인 체인(`raise ... from` 과 예외 처리 중 발생한 예외)의 타입 이름. 순환·긴 체인에 안전하다."""
    names = []
    try:
        seen = {id(exc)}
        current = exc
        while len(names) < _MAX_CHAIN:
            nxt = current.__cause__
            if nxt is None and not current.__suppress_context__:
                nxt = current.__context__
            if nxt is None or id(nxt) in seen:
                break
            seen.add(id(nxt))
            names.append(type(nxt).__name__)
            current = nxt
    except Exception:
        return names
    return names


def exc_summary(exc) -> str:
    """
    예외를 한 줄로 요약한다. 예: `HTTPError(status=500) @ kis_domestic.py:261 <- ConnectionError`.

    **메시지(`str(exc)`)는 절대 읽지 않는다.** 로그·알림·DB 에 예외를 남길 때는 `{e}` 대신 이 함수를 쓴다.
    """
    try:
        text = type(exc).__name__
        status = _http_status(exc)
        if status is not None:
            text += f"(status={status})"
        location = _project_location(exc)
        if location:
            text += f" @ {location}"
        chain = _chain_names(exc)
        if chain:
            text += " <- " + " <- ".join(chain)
        return text
    except Exception:
        return _FALLBACK
