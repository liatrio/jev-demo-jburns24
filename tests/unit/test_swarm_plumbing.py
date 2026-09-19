"""Offline checks on the swarm's plumbing: the target portal, the action space,
the code-level signals and the triage bookkeeping. No browser, no gateway."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jev_demo.swarm.agent import (
    MAX_CANDIDATES,
    PERSONAS,
    Trajectory,
    action_space,
    fingerprint,
    make_agent,
    pick_action,
    step_questions,
)
from jev_demo.swarm.browser import Observation, code_signals
from jev_demo.swarm.target import BUGS, FOREIGN_ACCOUNT, Portal, parse_injected
from jev_demo.swarm.triage import Finding, apply_baseline, load_baseline, new_blocking

SID = "session-a"


def post_transfer(p: Portal, amount: str, memo: str = "", sid: str = SID) -> tuple[int, str]:
    form = {"from": "1001", "to": "1002", "amount": amount, "memo": memo}
    return p.handle("POST", "/transfer", form, sid)


# ------------------------------------------------------------------ portal
def test_clean_portal_rejects_bad_transfers() -> None:
    p = Portal()
    for amount in ("-500", "0", "abc", "2500.01"):
        status, body = post_transfer(p, amount)
        assert status == 200 and "Rejected" in body, amount
    assert p.session(SID).balances == {"1001": 2500.0, "1002": 8000.0}


def test_clean_portal_accepts_a_valid_transfer_and_keeps_books() -> None:
    p = Portal()
    status, body = post_transfer(p, "100", memo="rent")
    assert status == 200 and "Transfer complete" in body
    assert p.session(SID).balances == {"1001": 2400.0, "1002": 8100.0}


def test_clean_portal_has_no_planted_defects() -> None:
    p = Portal()
    assert p.handle("GET", "/statements", {}, SID)[0] == 200
    assert p.handle("GET", f"/accounts/{FOREIGN_ACCOUNT}", {}, SID)[0] == 403
    status, body = p.handle("GET", "/search?q=%3Cb%3Ex%3C%2Fb%3E", {}, SID)
    assert status == 200 and "<b>x</b>" not in body and "&lt;b&gt;" in body
    assert post_transfer(p, "10", memo="it's")[0] == 200
    assert p.handle("POST", "/profile", {"name": "Zoë"}, SID)[0] == 200


@pytest.mark.parametrize("bug", sorted(BUGS))
def test_each_injected_bug_is_observable(bug: str) -> None:
    p = Portal(frozenset({bug}))
    if bug == "negative_transfer":
        assert "Transfer complete: -500.00" in post_transfer(p, "-500")[1]
    elif bug == "overdraft":
        post_transfer(p, "2500.01")
        assert p.session(SID).balances["1001"] < 0
    elif bug == "idor":
        status, body = p.handle("GET", f"/accounts/{FOREIGN_ACCOUNT}", {}, SID)
        assert status == 200 and "Priya Raman" in body
    elif bug == "xss_search":
        assert "<b>x</b>" in p.handle("GET", "/search?q=%3Cb%3Ex%3C%2Fb%3E", {}, SID)[1]
    elif bug == "dead_link":
        assert p.handle("GET", "/statements", {}, SID)[0] == 404
    elif bug == "stack_trace":
        status, body = post_transfer(p, "10", memo="it's")
        assert status == 500 and "Traceback" in body and "/home/" not in body
    elif bug == "unicode_crash":
        assert p.handle("POST", "/profile", {"name": "Zoë"}, SID)[0] == 500


def test_sessions_are_isolated() -> None:
    p = Portal(frozenset({"overdraft"}))
    post_transfer(p, "50000", sid="a")
    assert p.session("b").balances == {"1001": 2500.0, "1002": 8000.0}


def test_parse_injected() -> None:
    assert parse_injected(None) == frozenset()
    assert parse_injected("all") == frozenset(BUGS)
    assert parse_injected("idor, dead_link") == {"idor", "dead_link"}
    with pytest.raises(ValueError, match="unknown bug"):
        parse_injected("teleport")


# ------------------------------------------------------------- action space
def obs(path: str = "/transfer", **over) -> Observation:
    base = dict(
        path=path,
        status=200,
        title="t",
        h1="h",
        flash="",
        text="Current balances: 1001: 2,500.00",
        elements=[
            {"tag": "a", "text": "Accounts", "href": "/"},
            {
                "tag": "select",
                "name": "to",
                "label": "To",
                "options": ["1001", "1002"],
                "value": "1002",
            },
            {"tag": "input", "name": "amount", "label": "Amount", "type": "text", "value": ""},
            {"tag": "button", "text": "Send transfer"},
        ],
        console_errors=[],
    )
    base.update(over)
    return Observation(**base)


def test_action_space_is_concrete_and_bounded() -> None:
    persona, _ = make_agent(0)
    actions = action_space(obs(), persona, ["-500", "0", "abc", "1e9"], [])
    descs = [a["desc"] for a in actions.values()]
    assert 'click link "Accounts" (goes to /)' in descs
    assert 'set "To" to 1001' in descs and 'set "To" to 1002' not in descs
    assert sum(d.startswith("type ") for d in descs) == 3, "at most three payloads per field"
    assert "stop" in actions and actions["stop"]["kind"] == "stop"
    assert len(actions) <= MAX_CANDIDATES + 1
    assert set(step_questions(actions)["action"]["criteria"]) == set(actions)


def test_tried_actions_are_removed_until_the_form_changes() -> None:
    persona, _ = make_agent(0)
    o = obs()
    submit = 'click button "Send transfer" (submits the form)'
    tried = {fingerprint(o, submit)}
    assert submit not in [a["desc"] for a in action_space(o, persona, [], [], tried).values()]
    changed = obs(elements=[*o.elements[:2], {**o.elements[2], "value": "-500"}, o.elements[3]])
    assert submit in [a["desc"] for a in action_space(changed, persona, [], [], tried).values()]


def test_agents_are_deterministic_functions_of_their_seed() -> None:
    a, rng_a = make_agent(13)
    b, rng_b = make_agent(13)
    assert a is b and rng_a.random() == rng_b.random()
    assert {make_agent(i)[0].name for i in range(len(PERSONAS))} == {p.name for p in PERSONAS}


def test_contract_violation_falls_back_to_argmax_and_is_logged() -> None:
    actions = {"a0": {"desc": "x"}, "a1": {"desc": "y"}, "stop": {"desc": "s"}}
    traj = Trajectory(agent_id=0, persona="p", goal="g")
    bad = {"choice": "a0", "probabilities": {"a0": 0.2, "a1": 0.7, "stop": 0.1}}
    assert pick_action(bad, actions, traj, 1) == "a1"
    assert traj.errors and "contract violation" in traj.errors[0]
    good = {"choice": "stop", "probabilities": {"a0": 0.2, "a1": 0.1, "stop": 0.7}}
    assert pick_action(good, actions, traj, 2) == "stop" and len(traj.errors) == 1


# ----------------------------------------------------------------- signals
def test_code_signals() -> None:
    assert code_signals(500, "Traceback (most recent call last):", False) == [
        "http_500",
        "stack_trace_exposed",
    ]
    assert code_signals(404, "", False) == ["http_404"]
    assert code_signals(200, "Balance: -0.01 USD", True) == [
        "script_injection_executed",
        "negative_amount_displayed",
    ]
    assert code_signals(200, "Rejected: amount must be greater than zero.", False) == []


# ---------------------------------------------------------------- baseline
def finding(fp: str, blocking: bool = True) -> Finding:
    cat, path = fp.split(":", 1)
    return Finding(
        fingerprint=fp,
        category=cat,
        severity=2,
        defect_probability=0.9,
        path=path,
        action="a",
        persona="p",
        agents=[1],
        evidence="e",
        automatic_checks=[],
        blocking=blocking,
    )


def test_baseline_suppresses_known_findings_only(tmp_path: Path) -> None:
    baseline = tmp_path / "b.json"
    assert load_baseline(baseline) == set()
    baseline.write_text(json.dumps({"accepted": ["broken_page:/statements"]}))
    fs = [
        finding("broken_page:/statements"),
        finding("security:/search"),
        finding("cosmetic:/", False),
    ]
    apply_baseline(fs, load_baseline(baseline))
    assert [f.fingerprint for f in new_blocking(fs)] == ["security:/search"]
