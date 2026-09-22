"""End-to-end smoke test of www.liatrio.ai, driven by Jev.

Live only: a public website changes, so there is nothing deterministic to
replay. Runs with ``JEV_DEMO_MODE=live`` (``task test:live`` or
``task smoke:liatrio``), needs ``API_KEY`` and a Chromium Playwright can launch.

Every scenario in ``smoke/liatrio.toml`` must pass on both layers: Jev reports
the goal reached on the final page, and the code checks (path prefix, expected
words, HTTP status) agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_demo.smoke.run import run_smoke

pytestmark = [pytest.mark.e2e, pytest.mark.live]

CONFIG = Path(__file__).resolve().parents[2] / "smoke" / "liatrio.toml"


async def test_liatrio_navigation_scenarios_pass(mode: str) -> None:
    summary, results = await run_smoke(CONFIG, mode=mode)
    failures = {
        s.id: {"landed": s.final_path, "checks": s.checks_failed, "errors": s.errors}
        for s in summary.scenarios
        if not s.passed
    }
    assert not failures, failures
    assert summary.jev_calls >= len(summary.scenarios), "at least one Jev decision per scenario"
    for r in results:
        assert r.final_path, r.id
        assert not any("type" in s.action or "fill" in s.action for s in r.steps), (
            "smoke agents never type"
        )
