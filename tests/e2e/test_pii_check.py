"""End-to-end test of the PII pre-commit hook, judged by Jev, replayed from cassettes.

The fixtures under ``tests/fixtures/pii`` are small services in Python,
TypeScript and Go with a known mix of clean and leaking log statements.
``expected.json`` is the ground truth. The test runs the same code path the
hook runs and checks every statement's verdict, then asserts the properties
the demo claims: a leaking file blocks, a clean file passes, and the whole
scan is one Jev call per batch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_demo.gateway import Gateway
from jev_demo.pii_check import BATCH_SIZE, check_paths

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pii"
EXPECTED: dict[str, dict[str, bool]] = {
    k: v for k, v in json.loads((FIXTURES / "expected.json").read_text()).items() if k != "_comment"
}
SOURCE_FILES = sorted(p for p in FIXTURES.iterdir() if p.suffix != ".json")


async def test_every_statement_gets_the_ground_truth_verdict(gateway: Gateway) -> None:
    result = await check_paths(SOURCE_FILES, gateway)
    verdicts = {(Path(f.statement.file).name, str(f.statement.line)): f for f in result.findings}
    assert set(verdicts) == {(f, ln) for f, lines in EXPECTED.items() for ln in lines}
    wrong = [
        f"{name}:{line} expected {'leak' if EXPECTED[name][line] else 'clean'}, "
        f"got {f.kind} P(pii)={1 - f.p_none:.2f}"
        for (name, line), f in verdicts.items()
        if f.flagged != EXPECTED[name][line]
    ]
    assert not wrong, "\n".join(wrong)
    # Every flagged statement names a concrete kind of personal data, never "none".
    assert all(f.kind != "none" for f in result.flagged)


@pytest.mark.parametrize("name", ["clean_service.py", "clean_worker.go"])
async def test_clean_file_lets_the_commit_through(gateway: Gateway, name: str) -> None:
    result = await check_paths([FIXTURES / name], gateway)
    assert result.ok, [(f.statement.location, f.kind, f.p_none) for f in result.flagged]
    assert result.calls == 1, "a whole file is one speculative fan-out call"


@pytest.mark.parametrize("name", ["leaky_service.py", "leaky_handler.ts"])
async def test_leaky_file_blocks_the_commit(gateway: Gateway, name: str) -> None:
    result = await check_paths([FIXTURES / name], gateway)
    assert not result.ok
    assert {str(f.statement.line) for f in result.flagged} == {
        ln for ln, leaks in EXPECTED[name].items() if leaks
    }


async def test_scan_is_one_call_per_file_not_per_statement(gateway: Gateway) -> None:
    result = await check_paths(SOURCE_FILES, gateway)
    assert len(result.findings) > len(SOURCE_FILES)
    assert all(len(v) <= BATCH_SIZE for v in EXPECTED.values())
    assert result.calls == len(SOURCE_FILES), result.calls
    assert result.cost_usd < 0.01, "a full scan of the fixtures costs well under a cent"
