"""Second stage: Jev triages what the swarm flagged.

Hundreds of agents produce a long tail of "this looked odd" steps. Each one is
handed back to Jev with the full before/after evidence and three typed
questions: is it a defect, what kind, how bad. Findings are deduplicated by
(category, path) so a bug hit by thirty agents is reported once, with the
number of agents that reproduced it. A baseline file lets a team accept known
findings so the pre-push gate only blocks on *new* ones.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..evaluators import JEV_MODEL, cost_for, validate_choice
from ..gateway import Gateway
from .agent import Step, Trajectory, suspicious

CATEGORIES: dict[str, str] = {
    "none": "Not a defect: correct behaviour, a proper rejection message or a false alarm",
    "cosmetic": "Confusing wording or layout with no functional impact",
    "broken_page": "Server error, stack trace, dead link, or a page that failed to render",
    "validation_gap": "Invalid input accepted without harm (e.g. saved unchanged, odd but safe)",
    "security": "Data exposure, injection executed, or access to another customer's data",
    "money_loss": (
        "Money moved in a way a bank must never allow (negative, over-balance, wrong direction)"
    ),
}
BLOCKING_CATEGORIES = {"broken_page", "security", "money_loss"}
DEFECT_THRESHOLD = 0.7

# What each deterministic check from browser.code_signals means. Sent to Jev with
# the evidence so the model knows these are harness facts, not impressions.
CHECK_MEANINGS: dict[str, str] = {
    "http_500": "the server answered this page with HTTP 500",
    "http_404": "the server answered this page with HTTP 404",
    "stack_trace_exposed": "a Python traceback is printed on the page",
    "script_injection_executed": (
        "JavaScript embedded in a value the tester typed into a field actually ran in the page"
    ),
    "negative_amount_displayed": (
        "a negative USD amount (a balance or a completed transfer) is displayed on the page"
    ),
}
# Checks that are proof on their own; Jev only classifies and grades these.
HARD_CHECKS = {
    "http_500",
    "http_404",
    "stack_trace_exposed",
    "script_injection_executed",
    "negative_amount_displayed",
}


class Finding(BaseModel):
    fingerprint: str
    category: str
    severity: int
    defect_probability: float
    path: str
    action: str
    persona: str
    agents: list[int]
    evidence: str
    automatic_checks: list[str]
    blocking: bool
    in_baseline: bool = False


def _evidence_page(page: dict[str, Any] | None) -> dict[str, Any]:
    if not page:
        return {}
    return {
        "path": page["path"],
        "http_status": page["http_status"],
        "heading": page["heading"],
        "status_message": page["status_message"],
        "visible_text": page["visible_text"][:700],
        "automatic_checks": page["automatic_checks"],
    }


def triage_questions() -> dict[str, dict[str, Any]]:
    return {
        "is_defect": {
            "type": "boolean",
            "instructions": (
                "Judged against state.goal and the tester's expectations of an online bank, "
                "the transition from state.page_before via state.action to state.page_after "
                "demonstrates a real defect in the application (not merely an attempt that was "
                "correctly rejected)."
            ),
        },
        "category": {
            "type": "choice",
            "instructions": "Which category best describes what state.page_after shows?",
            "criteria": CATEGORIES,
        },
        "severity": {
            "type": "score",
            "instructions": "How urgently should this block a release?",
            "criteria": [
                "Not a defect",
                "Low: fix eventually",
                "Medium: fix before release",
                "High: must not ship (money, data or availability at risk)",
            ],
        },
    }


@dataclass
class Incident:
    """A page that looked wrong, plus the action that led to it."""

    traj: Trajectory
    page: dict[str, Any]
    action: str
    before: dict[str, Any] | None
    severity_hint: float

    @property
    def key(self) -> tuple[str, str]:
        checks = ",".join(sorted(self.page.get("automatic_checks", [])))
        return (self.page["path"].split("?")[0], checks or self.traj.persona)


def incidents(traj: Trajectory) -> list[Incident]:
    out: list[Incident] = []
    for idx, step in enumerate(traj.steps):
        if suspicious(step):
            prev: Step | None = traj.steps[idx - 1] if idx else None
            out.append(
                Incident(
                    traj=traj,
                    page=step.before,
                    action=prev.action if prev else f"open {step.path} directly",
                    before=prev.before if prev else None,
                    severity_hint=step.suspicion["severity"],
                )
            )
    # The page the agent ended on was never inspected by Jev; hand it over on code signals.
    if traj.steps and traj.steps[-1].after and traj.steps[-1].after.get("automatic_checks"):
        last = traj.steps[-1]
        out.append(Incident(traj, last.after, last.action, last.before, 0.0))
    return out


async def triage(
    trajectories: list[Trajectory], gateway: Gateway
) -> tuple[list[Finding], int, float]:
    """Return (findings, jev_calls, cost)."""
    groups: dict[tuple[str, str], list[Incident]] = defaultdict(list)
    for traj in trajectories:
        for inc in incidents(traj):
            groups[inc.key].append(inc)

    findings: list[Finding] = []
    calls = 0
    cost = 0.0
    questions = triage_questions()
    for (path, _), hits in sorted(groups.items()):
        # The lowest-numbered agent represents the group. Agents are a prefix-stable
        # sequence, so a 48-agent run asks Jev the exact questions a 200-agent run did.
        hits.sort(key=lambda h: h.traj.agent_id)
        inc = hits[0]
        traj = inc.traj
        checks = [c for c in inc.page.get("automatic_checks", []) if c in CHECK_MEANINGS]
        state = {
            "goal": traj.goal,
            "page_before": _evidence_page(inc.before) if inc.before else "first page opened",
            "action": inc.action,
            "page_after": _evidence_page(inc.page),
            "harness_facts": [CHECK_MEANINGS[c] for c in checks],
        }
        resp = await gateway.evaluate(JEV_MODEL, state, questions)
        calls += 1
        usage = resp.body.get("usage", {})
        cost += cost_for(JEV_MODEL, usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        answers = resp.body["answers"]
        validate_choice(answers["category"], CATEGORIES)
        p = float(answers["is_defect"].get("probability", answers["is_defect"].get("noul", 0)))
        category = answers["category"]["choice"]
        severity = int(answers["severity"]["score"])
        hard = sorted(set(checks) & HARD_CHECKS)
        if not hard and (p < DEFECT_THRESHOLD or category == "none"):
            continue
        if hard and category == "none":
            category = "broken_page"  # code proved it; the model only had to name it
        page = inc.page
        suffix = f"[{','.join(hard)}]" if hard else ""
        findings.append(
            Finding(
                fingerprint=f"{category}:{path}{suffix}",
                category=category,
                severity=severity,
                defect_probability=p,
                path=path,
                action=inc.action,
                persona=traj.persona,
                agents=sorted({h.traj.agent_id for h in hits}),
                evidence=(page.get("status_message") or page.get("visible_text", ""))[:200],
                automatic_checks=page.get("automatic_checks", []),
                blocking=category in BLOCKING_CATEGORIES and severity >= 2,
            )
        )
    # one row per fingerprint, merging agents that hit the same bug via different paths
    merged: dict[str, Finding] = {}
    for f in findings:
        if f.fingerprint in merged:
            m = merged[f.fingerprint]
            m.agents = sorted(set(m.agents) | set(f.agents))
            m.severity = max(m.severity, f.severity)
            m.blocking = m.blocking or f.blocking
        else:
            merged[f.fingerprint] = f
    out = sorted(merged.values(), key=lambda f: (-f.severity, -len(f.agents), f.fingerprint))
    return out, calls, cost


# ----------------------------------------------------------------- baseline
def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    return set(data.get("accepted", []))


def apply_baseline(findings: list[Finding], accepted: set[str]) -> None:
    for f in findings:
        f.in_baseline = f.fingerprint in accepted


def new_blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.blocking and not f.in_baseline]


__all__ = [
    "BLOCKING_CATEGORIES",
    "CATEGORIES",
    "DEFECT_THRESHOLD",
    "JEV_MODEL",
    "Finding",
    "apply_baseline",
    "load_baseline",
    "new_blocking",
    "triage",
    "triage_questions",
]
