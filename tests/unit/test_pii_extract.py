"""Offline tests for the log-statement extractor behind the PII pre-commit hook."""

from __future__ import annotations

import json
from pathlib import Path

from jev_demo.pii_check import (
    BATCH_SIZE,
    PII_KINDS,
    CheckResult,
    Finding,
    LogStatement,
    extract_log_statements,
    format_result,
    is_source_file,
    pii_questions,
    pii_state,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pii"
EXPECTED = {
    k: v for k, v in json.loads((FIXTURES / "expected.json").read_text()).items() if k != "_comment"
}


def test_extractor_finds_every_statement_the_ground_truth_lists() -> None:
    """Line numbers in expected.json are the contract for the e2e test; keep them honest."""
    for name, lines in EXPECTED.items():
        found = {s.line for s in extract_log_statements(FIXTURES / name)}
        assert found == {int(n) for n in lines}, name


def test_multiline_call_is_extracted_whole() -> None:
    stmts = extract_log_statements(FIXTURES / "leaky_service.py")
    multi = next(s for s in stmts if s.line == 38)
    assert multi.code.startswith("logger.error(")
    assert multi.code.rstrip().endswith(")")
    assert "customer.card_pan" in multi.code and "customer.full_name" in multi.code


def test_parenthesis_inside_string_does_not_end_the_call(tmp_path: Path) -> None:
    src = 'x = 1\nlogger.info("closing ) early?", user.email)\n'
    (tmp_path / "a.py").write_text(src)
    [s] = extract_log_statements(tmp_path / "a.py")
    assert s.code == 'logger.info("closing ) early?", user.email)'
    assert s.line == 2


def test_context_is_the_preceding_lines_only(tmp_path: Path) -> None:
    src = "\n".join(f"line{i}" for i in range(10)) + '\nlog.warn("x")\nafter\n'
    (tmp_path / "a.js").write_text(src)
    [s] = extract_log_statements(tmp_path / "a.js")
    assert s.context.splitlines() == ["line6", "line7", "line8", "line9"]


def test_non_log_calls_are_ignored(tmp_path: Path) -> None:
    src = (
        "catalog.info(sku)\n"
        "blog.error_count += 1\n"
        "console.table(rows)\n"
        "logger.info_panel()\n"
        "info(x)\n"
        'log::warn!("rust macro {}", email)\n'
        'tracing::error!("boom")\n'
    )
    (tmp_path / "a.rs").write_text(src)
    codes = [s.code for s in extract_log_statements(tmp_path / "a.rs")]
    assert codes == ['log::warn!("rust macro {}", email)', 'tracing::error!("boom")']


def test_go_and_java_receivers(tmp_path: Path) -> None:
    src = (
        'slog.Info("started", "id", id)\n'
        'log.Printf("user %s", name)\n'
        'LOGGER.warn("Java style {}", email);\n'
        'zap.L().Info("ignored: not a direct receiver call")\n'
    )
    (tmp_path / "a.go").write_text(src)
    lines = [s.line for s in extract_log_statements(tmp_path / "a.go")]
    assert lines == [1, 2, 3]


def test_source_file_filter() -> None:
    assert is_source_file(Path("x.py")) and is_source_file(Path("x.ts"))
    assert not is_source_file(Path("x.json")) and not is_source_file(Path("README.md"))


def test_binary_file_yields_nothing(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"\xff\xfe\x00logger.info(")
    assert extract_log_statements(tmp_path / "a.py") == []


def test_questions_and_state_line_up() -> None:
    stmts = [LogStatement("f.py", i, f"logger.info({i})", "") for i in range(3)]
    q = pii_questions(stmts)
    st = pii_state(stmts)
    assert set(q) == {"s0", "s1", "s2"} == set(st["statements"])
    assert all(v["type"] == "choice" and v["criteria"] == PII_KINDS for v in q.values())
    assert "none" in PII_KINDS and BATCH_SIZE >= 1


def test_format_result_marks_blocks() -> None:
    stmt = LogStatement("svc.py", 7, 'logger.info("hi %s", user.email)', "")
    res = CheckResult(files_scanned=1, calls=1, latency_ms=[250.0], wall_ms=260.0)
    res.findings.append(Finding(stmt, "contact", 0.02, {"none": 0.02, "contact": 0.98}))
    res.findings.append(
        Finding(LogStatement("svc.py", 9, 'logger.info("ok")', ""), "none", 1.0, {"none": 1.0})
    )
    out = format_result(res)
    assert "BLOCK  svc.py:7  contact (P(pii)=0.98)" in out
    assert "svc.py:9" not in out, "passing lines are quiet unless --verbose"
    assert "2 log statements in 1 file, 1 flagged" in out
    assert not res.ok
