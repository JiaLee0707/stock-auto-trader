"""
T09a: 문서가 사유 없이 중단을 해제하는 방법을 안내하지 않는다.

소스 코드는 test_halt_wiring.py 가 검사한다. 여기서는 사람이 따라 하는 문서를 검사한다.
CLAUDE.md(규칙 설명에서 함수 이름을 언급함)·docs/CHANGES.md(과거 기록, 고치지 않음)·
docs/archive/ 는 대상이 아니다.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANNED = ("resume_trading(",)


def _violations(text: str) -> list[str]:
    return [b for b in BANNED if b in text]


def _checked_docs() -> list[pathlib.Path]:
    docs = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    return [p for p in docs if p.name != "CHANGES.md"]


def test_scanner_flags_the_unsafe_instruction():
    assert _violations('.venv/bin/python -c "... reconcile.resume_trading(c) ..."')
    assert not _violations("python -m risk resume --reason ...")


def test_docs_do_not_teach_resuming_without_a_reason():
    files = _checked_docs()
    assert ROOT / "README.md" in files, "README.md 가 검사 대상에 없다"
    hits = [p.name for p in files if _violations(p.read_text(encoding="utf-8"))]
    assert hits == [], f"사유 없이 해제하는 안내가 남아 있습니다: {hits}"


def test_readme_documents_the_manual_resume_command():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'python -m risk resume --reason' in text
