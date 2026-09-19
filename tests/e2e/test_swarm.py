"""End-to-end: the adversarial swarm, replayed from cassettes.

Real Chromium, real portal, real (recorded) Jev decisions. Agents are
deterministic functions of their seed, so a 48-agent run here reuses the first
48 agents' cassettes from the 200-agent recording (``task swarm:record``).

Two properties are held:

1. On the clean portal the swarm confirms **no** defect, so the pre-push gate
   passes and a false positive would be caught here, on commit, not at push.
2. With every known regression injected, the swarm finds each one and reports
   it under the right category and path, and the gate fails.
"""

from __future__ import annotations

import pytest

from jev_demo.swarm.run import run_swarm
from jev_demo.swarm.target import BUGS

pytestmark = [pytest.mark.e2e, pytest.mark.swarm]

AGENTS = 48

# What each planted regression must surface as: (category, path prefix).
EXPECTED: dict[str, tuple[str, str]] = {
    "negative_transfer": ("money_loss", "/transfer"),
    "overdraft": ("money_loss", "/transfer"),
    "idor": ("security", "/accounts/2001"),
    "xss_search": ("security", "/search"),
    "dead_link": ("broken_page", "/statements"),
    "stack_trace": ("broken_page", "/transfer"),
    "unicode_crash": ("broken_page", "/profile"),
}


async def test_clean_portal_passes_the_swarm(mode: str) -> None:
    summary, trajectories = await run_swarm(AGENTS, mode=mode)
    assert summary.agents == AGENTS and len(trajectories) == AGENTS
    assert summary.steps >= AGENTS, "every agent must take at least one step"
    assert [f.fingerprint for f in summary.findings if f.blocking] == []
    assert summary.passed
    if mode == "replay":
        assert summary.network_calls == 0, "replay must never touch the network"


async def test_swarm_catches_every_injected_regression(mode: str) -> None:
    summary, trajectories = await run_swarm(AGENTS, inject="all", mode=mode)
    found = {(f.category, f.path) for f in summary.findings if f.blocking}
    missing = {
        bug
        for bug, (cat, path) in EXPECTED.items()
        if not any(c == cat and p.startswith(path) for c, p in found)
    }
    assert not missing, {"missing": missing, "found": found}
    assert not summary.passed
    assert set(summary.injected) == set(BUGS)
    # a bug hit by many agents is still one row
    assert len(summary.findings) <= len(BUGS) + 2
    assert all(len(f.agents) >= 1 for f in summary.findings)

    # findings carry reproducible evidence: the page text, the harness checks, the agents
    idor = next(f for f in summary.findings if f.path.startswith("/accounts/"))
    assert "Priya Raman" in idor.evidence
    assert idor.severity >= 2 and idor.defect_probability >= 0.7
    dead = next(f for f in summary.findings if f.path == "/statements")
    assert "http_404" in dead.automatic_checks
    by_id = {t.agent_id: t for t in trajectories}
    assert all(by_id[a].steps for a in idor.agents + dead.agents)
