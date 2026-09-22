"""Offline tests for the smoke-test plumbing: config, action space, deterministic checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from jev_demo.smoke.agent import action_space, deterministic_checks, step_questions
from jev_demo.smoke.config import Scenario, load_config
from jev_demo.swarm.browser import Observation

ROOT = Path(__file__).resolve().parents[2]


def test_liatrio_config_loads_three_navigation_scenarios() -> None:
    cfg = load_config(ROOT / "smoke" / "liatrio.toml")
    assert cfg.site.host == "www.liatrio.ai"
    assert [s.id for s in cfg.scenarios] == ["blog", "careers", "contact"]
    for s in cfg.scenarios:
        assert s.forbid_form_input and s.stay_on_site
        assert "\n" not in s.goal, "goal text is collapsed to one line for the model"
        assert s.expected_path_prefix


def test_config_rejects_duplicate_ids(tmp_path: Path) -> None:
    p = tmp_path / "dup.toml"
    p.write_text(
        '[site]\nname="x"\nbase_url="https://x.test"\n'
        '[[scenarios]]\nid="a"\ngoal="g"\nsuccess_criteria="c"\n'
        '[[scenarios]]\nid="a"\ngoal="g"\nsuccess_criteria="c"\n'
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_config(p)


def _obs(elements: list[dict], path: str = "/", text: str = "", status: int = 200) -> Observation:
    return Observation(
        path=path,
        status=status,
        title="t",
        h1="h",
        flash="",
        text=text[:50],
        elements=elements,
        console_errors=[],
        signals=[],
        full_text=text,
    )


SCENARIO = Scenario(
    id="s",
    goal="g",
    success_criteria="c",
    expected_path_prefix="/careers",
    expected_text=("apply",),
)


def test_action_space_offers_internal_links_only_and_never_fills() -> None:
    obs = _obs(
        [
            {"i": 0, "tag": "a", "text": "Careers", "href": "/careers", "external": False},
            {"i": 1, "tag": "a", "text": "Apply", "href": "https://jobs.example", "external": True},
            {"i": 2, "tag": "input", "label": "Email", "type": "email"},
            {"i": 3, "tag": "button", "text": "Submit"},
        ]
    )
    actions = action_space(obs, SCENARIO, has_form_fields=True, tried=set())
    descs = [a["desc"] for a in actions.values()]
    assert any("Careers" in d for d in descs)
    assert not any("jobs.example" in d for d in descs), "external links are not actions"
    assert not any("Submit" in d for d in descs), "buttons vanish on pages with form fields"
    assert not any(a["kind"] == "fill" for a in actions.values())
    assert {"done", "give_up"} <= set(actions)
    # the choice question offers exactly the actions code enumerated
    q = step_questions(actions)
    assert set(q["action"]["criteria"]) == set(actions)
    assert q["goal_reached"]["type"] == "boolean"


def test_action_space_drops_actions_already_tried_here() -> None:
    obs = _obs([{"i": 0, "tag": "a", "text": "Careers", "href": "/careers", "external": False}])
    first = action_space(obs, SCENARIO, has_form_fields=False, tried=set())
    desc = next(a["desc"] for a in first.values() if a["kind"] == "click_link")
    again = action_space(obs, SCENARIO, has_form_fields=False, tried={("/", desc)})
    assert not any(a["kind"] == "click_link" for a in again.values())


def test_deterministic_checks_use_full_page_text_and_path() -> None:
    ok = _obs([], path="/careers", text="Join our team. " + "x " * 600 + "Apply now")
    assert deterministic_checks(ok, SCENARIO) == []
    wrong_page = _obs([], path="/about", text="Apply")
    assert any("does not start with" in c for c in deterministic_checks(wrong_page, SCENARIO))
    missing = _obs([], path="/careers", text="nothing here")
    assert any("'apply'" in c for c in deterministic_checks(missing, SCENARIO))
    broken = _obs([], path="/careers", text="Apply", status=404)
    assert "http_404" in deterministic_checks(broken, SCENARIO)
