"""
T09a-2: 킬스위치 게이트(risk.py) — HALT 파일, STATE/RISK 차단 범위, 실패 시 닫힘,
수동 재개 CLI, 자동 재개 금지.
"""
import ast
import logging
import pathlib

import pytest

import config
import db
import reconcile
import risk

ROOT = pathlib.Path(risk.__file__).resolve().parent


@pytest.fixture
def conn(tmp_path):
    c = db.open_db(tmp_path / "bot.db")
    yield c
    c.close()


@pytest.fixture
def halt_file(tmp_path, monkeypatch):
    """이 테스트의 HALT 파일 경로(아직 만들지 않음)."""
    path = tmp_path / "HALT"
    monkeypatch.setattr(config, "HALT_FILE", str(path))
    return path


# ── 게이트 ────────────────────────────────────────────────

def test_gate_is_open_by_default(conn, halt_file):
    assert risk.cycle_gate(conn) == risk.OPEN
    risk.assert_order_permitted(conn, "BUY")
    risk.assert_order_permitted(conn, "SELL")


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_halt_file_blocks_every_order(conn, halt_file, side):
    halt_file.write_text("")
    assert risk.cycle_gate(conn) == risk.FULL_STOP
    with pytest.raises(risk.TradingHalted):
        risk.assert_order_permitted(conn, side)


def test_removing_halt_file_reopens_gate(conn, halt_file):
    halt_file.write_text("")
    assert risk.cycle_gate(conn) == risk.FULL_STOP
    halt_file.unlink()
    assert risk.cycle_gate(conn) == risk.OPEN


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_state_halt_blocks_every_order(conn, halt_file, side):
    reconcile.halt_trading(conn, "불일치", "STATE")
    assert risk.cycle_gate(conn) == risk.FULL_STOP
    with pytest.raises(risk.TradingHalted):
        risk.assert_order_permitted(conn, side)


def test_risk_halt_blocks_buy_but_not_sell(conn, halt_file):
    reconcile.halt_trading(conn, "낙폭", "RISK")
    assert risk.cycle_gate(conn) == risk.ENTRIES_BLOCKED
    with pytest.raises(risk.TradingHalted):
        risk.assert_order_permitted(conn, "BUY")
    risk.assert_order_permitted(conn, "SELL")        # 청산은 계속된다


def test_state_read_failure_fails_closed(conn, halt_file, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(reconcile, "is_trading_halted", boom)
    with caplog.at_level(logging.CRITICAL, logger="bot"):
        assert risk.cycle_gate(conn) == risk.FULL_STOP
        for side in ("BUY", "SELL"):
            with pytest.raises(risk.TradingHalted):
                risk.assert_order_permitted(conn, side)
    assert "읽지 못했습니다" in caplog.text


def test_halt_file_check_failure_fails_closed(conn, halt_file, monkeypatch):
    def boom():
        raise PermissionError("denied")

    monkeypatch.setattr(risk, "_halt_file_present", boom)
    assert risk.cycle_gate(conn) == risk.FULL_STOP


# ── HALT 파일 경로 ────────────────────────────────────────

def test_default_halt_file_path_ignores_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HALT_FILE", "")
    monkeypatch.chdir(tmp_path)
    assert risk.halt_file_path() == ROOT / "HALT"


def test_relative_halt_file_setting_is_anchored_to_project_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "HALT_FILE", "STOP_NOW")
    assert risk.halt_file_path() == ROOT / "STOP_NOW"
    absolute = tmp_path / "elsewhere" / "HALT"
    monkeypatch.setattr(config, "HALT_FILE", str(absolute))
    assert risk.halt_file_path() == absolute


# ── 재개 CLI ──────────────────────────────────────────────

def test_cli_requires_reason_option(conn, halt_file):
    reconcile.halt_trading(conn, "불일치", "STATE")
    with pytest.raises(SystemExit) as exc:
        risk.main(["resume"], conn=conn)
    assert exc.value.code == 2
    assert reconcile.is_trading_halted(conn) is True
    assert len(db.list_halt_events(conn)) == 1


@pytest.mark.parametrize("reason", ["", "   "])
def test_cli_rejects_blank_reason(conn, halt_file, reason):
    reconcile.halt_trading(conn, "불일치", "STATE")
    assert risk.main(["resume", "--reason", reason], conn=conn) == 2
    assert reconcile.is_trading_halted(conn) is True
    assert len(db.list_halt_events(conn)) == 1       # HALT 한 건뿐, RESUME 없음


def test_cli_resume_with_reason_clears_halt_and_records_history(conn, halt_file):
    reconcile.halt_trading(conn, "불일치", "STATE")
    assert risk.main(["resume", "--reason", "불일치 원인 확인 완료"], conn=conn) == 0
    assert reconcile.is_trading_halted(conn) is False
    last = db.list_halt_events(conn)[0]
    assert (last["event"], last["actor"], last["reason"]) == ("RESUME", "cli", "불일치 원인 확인 완료")


def test_cli_refuses_while_halt_file_exists(conn, halt_file):
    reconcile.halt_trading(conn, "불일치", "STATE")
    halt_file.write_text("")
    assert risk.main(["resume", "--reason", "확인함"], conn=conn) == 3
    assert reconcile.is_trading_halted(conn) is True
    assert halt_file.exists(), "CLI 가 HALT 파일을 대신 지웠다"
    assert len(db.list_halt_events(conn)) == 1


def test_cli_resume_when_not_halted_records_nothing(conn, halt_file, capsys):
    assert risk.main(["resume", "--reason", "확인함"], conn=conn) == 0
    assert "중단 상태가 아닙니다" in capsys.readouterr().out
    assert db.list_halt_events(conn) == []


def test_cli_status_shows_kind_and_reason(conn, halt_file, capsys):
    reconcile.halt_trading(conn, "낙폭 테스트", "RISK")
    assert risk.main(["status"], conn=conn) == 0
    out = capsys.readouterr().out
    assert "RISK" in out and "낙폭 테스트" in out and risk.ENTRIES_BLOCKED in out


# ── 자동 재개 금지 ────────────────────────────────────────

def _resume_calls(source: str) -> list[int]:
    """소스에서 resume_trading 을 **호출**하는 줄 번호(문자열 속 언급은 제외)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name == "resume_trading":
                found.append(node.lineno)
    return sorted(found)


def test_scanner_detects_calls_but_not_strings():
    assert _resume_calls("import reconcile\nreconcile.resume_trading(c)\n") == [2]
    assert _resume_calls("resume_trading(c)\n") == [1]
    assert _resume_calls('msg = "reconcile.resume_trading(c)"\n') == []


def test_resume_trading_is_only_called_by_the_cli():
    offenders = []
    for path in sorted(ROOT.glob("*.py")):
        if path.name == "risk.py":          # 수동 재개 CLI
            continue
        offenders += [f"{path.name}:{n}" for n in _resume_calls(path.read_text(encoding="utf-8"))]
    assert offenders == [], f"자동 재개 의심 호출: {offenders}"
