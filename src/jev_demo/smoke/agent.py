"""A Jev-driven smoke-test agent for a real website.

The jev-ultrafast loop from the swarm, pointed at a goal instead of a persona:

1. **Code observes** the page (path, status, heading, visible text, links and
   buttons) and enumerates the concrete actions available.
2. **Jev decides** in one speculative fan-out call: which action advances the
   goal (``choice``), whether the goal is already reached (``boolean``) and
   whether the page is broken (``boolean``).
3. **Code acts**, then checks the typed contract on every answer.

When Jev reports the goal reached, code runs the deterministic checks from the
scenario (path prefix, expected words, HTTP status). Both layers must agree for
the scenario to pass. Nothing is typed into any field and no form is submitted:
smoke tests look, they do not touch.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from ..evaluators import JEV_MODEL, JevAnswerError, cost_for, validate_choice
from ..gateway import Gateway
from ..swarm.browser import _EXTRACT_JS, BrowserPool, Observation, Tab, code_signals
from .config import Scenario, Site

MAX_TEXT = 900
MAX_ELEMENTS = 40
MAX_CANDIDATES = 24
BROKEN_THRESHOLD = 0.7


# ------------------------------------------------------------------ browser
class SiteTab(Tab):
    """The swarm's Tab, adjusted for a public marketing site.

    Links are deduplicated by destination and marked external, fragment links
    and empty links are dropped, buttons never expect a navigation (nav menus
    open dropdowns), and a click that Playwright cannot perform (a link hidden
    inside a closed menu) falls back to navigating to the link's destination.
    """

    def __init__(self, context: BrowserContext, page: Page, site: Site) -> None:
        super().__init__(context, page, site.base_url)
        self.site = site
        self.has_form_fields = False
        page.set_default_timeout(15000)

    async def goto(self, path: str) -> None:
        self.console_errors = []
        await self.page.goto(self.base_url + path, wait_until="load", timeout=30000)
        await self._settle()

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001 - a busy page is still observable
            pass

    def _normalise_href(self, href: str) -> tuple[str, bool]:
        """Return (path or url, external?) for a link target."""
        u = urlparse(href)
        if u.scheme in ("mailto", "tel", "javascript"):
            return href, True
        if u.netloc and u.netloc.lower() != self.site.host:
            return href, True
        path = u.path or "/"
        if u.query:
            path += f"?{u.query}"
        return path, False

    async def observe(self) -> Observation:
        raw = await self.page.evaluate(_EXTRACT_JS)
        u = urlparse(self.page.url)
        path = u.path + (f"?{u.query}" if u.query else "")
        elements: list[dict[str, Any]] = []
        seen_hrefs: set[str] = set()
        self.has_form_fields = False
        for i, e in enumerate(raw["elements"]):
            tag = e["tag"]
            if tag == "a":
                href = e.get("href") or ""
                text = " ".join((e.get("text") or "").split())
                if not href or href.startswith("#") or not text:
                    continue
                target, external = self._normalise_href(href)
                if target in seen_hrefs:
                    continue
                seen_hrefs.add(target)
                elements.append(
                    {"i": i, "tag": "a", "text": text[:80], "href": target, "external": external}
                )
            elif tag == "button":
                text = " ".join((e.get("text") or "").split())
                if text:
                    elements.append({"i": i, "tag": "button", "text": text[:60]})
            else:
                self.has_form_fields = True
                elements.append(
                    {
                        "i": i,
                        "tag": tag,
                        "label": e.get("label") or e.get("name") or "",
                        "type": e.get("type", tag),
                    }
                )
            if len(elements) >= MAX_ELEMENTS:
                break
        text = " ".join(raw["text"].split())
        return Observation(
            path=path,
            status=self.last_status,
            title=raw["title"],
            h1=" ".join(raw["h1"].split()),
            flash="",
            text=text[:MAX_TEXT],
            elements=elements,
            console_errors=list(dict.fromkeys(self.console_errors)),
            signals=code_signals(self.last_status, text, False),
            full_text=text,
        )

    async def act(self, action: dict[str, Any]) -> None:
        kind = action["kind"]
        self.console_errors = []
        if kind == "goto":
            await self.goto(action["path"])
            return
        if kind == "back":
            await self.page.go_back(wait_until="load")
            await self._settle()
            return
        locator = self.page.locator(
            "a[href], button, input:not([type=hidden]), select, textarea"
        ).nth(action["index"])
        if kind == "click_link":
            try:
                async with self.page.expect_navigation(wait_until="load", timeout=10000):
                    await locator.click(timeout=5000)
                await self._settle()
            except Exception:  # noqa: BLE001 - hidden menu item: go where it points instead
                await self.goto(action["path"])
        elif kind == "click_button":
            await locator.click(timeout=5000)
            await self.page.wait_for_timeout(500)
        else:
            raise ValueError(f"smoke agents never {kind!r}")


class SitePool(BrowserPool):
    """One Chromium for the run; each scenario gets its own context."""

    def __init__(self, site: Site, *, headed: bool = False, slow_mo_ms: int = 0) -> None:
        super().__init__(site.base_url, headed=headed, slow_mo_ms=slow_mo_ms)
        self.site = site
        # Only for sandboxes whose egress proxy re-terminates TLS with a private CA that
        # Chromium does not trust. Default is full certificate verification.
        if os.environ.get("JEV_SMOKE_INSECURE_TLS", "") == "1":
            self.launch_args = ["--ignore-certificate-errors"]

    async def open(self) -> SiteTab:  # type: ignore[override]
        assert self.browser is not None
        ctx = await self.browser.new_context(
            java_script_enabled=True,
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()
        return SiteTab(ctx, page, self.site)


# ------------------------------------------------------------- action space
def for_model(obs: Observation) -> dict[str, Any]:
    return {
        "path": obs.path,
        "http_status": obs.status,
        "title": obs.title,
        "heading": obs.h1,
        "visible_text": obs.text,
        "elements": obs.elements,
        "automatic_checks": obs.signals,
    }


def action_space(
    obs: Observation, scenario: Scenario, has_form_fields: bool, tried: set[tuple[str, str]]
) -> dict[str, dict[str, Any]]:
    """Concrete navigation actions on this page, keyed for Jev's choice question."""
    actions: dict[str, dict[str, Any]] = {}
    page = obs.path.split("?")[0]

    def add(desc: str, **spec: Any) -> None:
        if len(actions) < MAX_CANDIDATES and (page, desc) not in tried:
            actions[f"a{len(actions)}"] = {"desc": desc, **spec}

    for e in obs.elements:
        if e["tag"] == "a":
            if e["external"] and scenario.stay_on_site:
                continue
            add(
                f'click link "{e["text"]}" (goes to {e["href"]})',
                kind="click_link",
                index=e["i"],
                path=e["href"],
            )
        elif e["tag"] == "button":
            if has_form_fields and scenario.forbid_form_input:
                continue
            add(
                f'click button "{e["text"]}" (opens a menu or panel)',
                kind="click_button",
                index=e["i"],
            )
    add("go back to the previous page", kind="back")
    actions["done"] = {
        "desc": "done: the current page already satisfies the success criteria",
        "kind": "done",
    }
    actions["give_up"] = {
        "desc": "give up: the goal cannot be reached from this site",
        "kind": "give_up",
    }
    return actions


def step_questions(actions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        "action": {
            "type": "choice",
            "instructions": (
                "You are smoke-testing the website described in state.page. Which single "
                "action best advances state.goal right now? Choose 'done' only when "
                "state.page already meets state.success_criteria. Never type into a field "
                "or submit a form."
            ),
            "criteria": {k: v["desc"] for k, v in actions.items()},
        },
        "goal_reached": {
            "type": "boolean",
            "instructions": "state.page, as shown, satisfies state.success_criteria.",
        },
        "page_is_broken": {
            "type": "boolean",
            "instructions": (
                "state.page shows an error page, a missing page, an empty or unrendered page, "
                "or content that does not match the link that was followed to reach it."
            ),
        },
    }


def _prob(answer: dict[str, Any]) -> float:
    return float(answer.get("probability", answer.get("noul", 0.0)))


# ---------------------------------------------------------------- scenario
@dataclass
class SmokeStep:
    n: int
    path: str
    heading: str
    action: str
    goal_reached: float
    page_is_broken: float
    latency_ms: float
    cost_usd: float


@dataclass
class ScenarioResult:
    id: str
    goal: str
    passed: bool = False
    final_path: str = ""
    final_heading: str = ""
    goal_probability: float = 0.0
    checks_failed: list[str] = field(default_factory=list)
    steps: list[SmokeStep] = field(default_factory=list)
    jev_calls: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)

    @property
    def latency_ms(self) -> list[float]:
        return [s.latency_ms for s in self.steps]


def deterministic_checks(obs: Observation, scenario: Scenario) -> list[str]:
    """The half of the verdict that needs no model."""
    failed: list[str] = []
    if obs.status >= 400:
        failed.append(f"http_{obs.status}")
    failed.extend(s for s in obs.signals if s not in failed)
    if scenario.expected_path_prefix and not obs.path.startswith(scenario.expected_path_prefix):
        failed.append(f"path {obs.path!r} does not start with {scenario.expected_path_prefix!r}")
    haystack = f"{obs.title} {obs.h1} {obs.full_text or obs.text}".lower()
    for word in scenario.expected_text:
        if word not in haystack:
            failed.append(f"expected text {word!r} not on page")
    return failed


def pick_action(
    answer: dict[str, Any], actions: dict[str, dict[str, Any]], result: ScenarioResult, step: int
) -> str:
    criteria = {k: v["desc"] for k, v in actions.items()}
    try:
        validate_choice(answer, criteria)
        return str(answer["choice"])
    except JevAnswerError as exc:
        result.errors.append(f"step {step}: contract violation: {exc}"[:300])
        probs = answer.get("probabilities") or {}
        valid = {k: v for k, v in probs.items() if k in actions and isinstance(v, int | float)}
        return max(valid, key=valid.get) if valid else "give_up"  # type: ignore[arg-type]


async def run_scenario(
    scenario: Scenario, site: Site, tab: SiteTab, gateway: Gateway
) -> ScenarioResult:
    result = ScenarioResult(id=scenario.id, goal=scenario.goal)
    try:
        await tab.goto(scenario.start_path or site.start_path)
        obs = await tab.observe()
    except Exception as exc:  # noqa: BLE001 - the site itself is unreachable
        result.errors.append(f"open {site.base_url}: {type(exc).__name__}: {exc}"[:300])
        result.checks_failed.append("site unreachable")
        return result

    tried: set[tuple[str, str]] = set()
    history: list[str] = []
    for n in range(1, scenario.max_steps + 1):
        actions = action_space(obs, scenario, tab.has_form_fields, tried)
        questions = step_questions(actions)
        state = {
            "site": site.name,
            "goal": scenario.goal,
            "success_criteria": scenario.success_criteria,
            "step": n,
            "steps_remaining": scenario.max_steps - n + 1,
            "actions_so_far": history[-6:],
            "page": for_model(obs),
        }
        started = time.perf_counter()
        try:
            resp = await gateway.evaluate(JEV_MODEL, state, questions)
            answers = resp.body["answers"]
        except Exception as exc:  # noqa: BLE001 - recorded, scenario fails
            result.errors.append(f"step {n}: {type(exc).__name__}: {exc}"[:300])
            break
        result.jev_calls += 1
        usage = resp.body.get("usage", {})
        cost = cost_for(JEV_MODEL, usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        choice = pick_action(answers["action"], actions, result, n)
        p_goal = _prob(answers["goal_reached"])
        p_broken = _prob(answers["page_is_broken"])
        step = SmokeStep(
            n=n,
            path=obs.path,
            heading=obs.h1,
            action=actions[choice]["desc"],
            goal_reached=p_goal,
            page_is_broken=p_broken,
            latency_ms=resp.latency_ms if resp.replayed else (time.perf_counter() - started) * 1000,
            cost_usd=cost,
        )
        result.steps.append(step)
        result.final_path, result.final_heading, result.goal_probability = obs.path, obs.h1, p_goal

        if choice == "done" or p_goal >= scenario.goal_threshold:
            break
        if choice == "give_up":
            result.errors.append(f"step {n}: agent gave up on {obs.path}")
            break
        if p_broken >= BROKEN_THRESHOLD:
            result.checks_failed.append(f"jev: page {obs.path} looks broken (p={p_broken:.2f})")
            break
        tried.add((obs.path.split("?")[0], step.action))
        history.append(f"on {obs.path}: {step.action}")
        try:
            await tab.act(actions[choice])
            obs = await tab.observe()
        except Exception as exc:  # noqa: BLE001 - a navigation that fails is evidence
            result.errors.append(f"step {n} action failed: {type(exc).__name__}"[:300])
            break

    # Deterministic half of the verdict, on the page the agent finished on.
    result.checks_failed.extend(deterministic_checks(obs, scenario))
    result.final_path, result.final_heading = obs.path, obs.h1
    jev_ok = result.goal_probability >= scenario.goal_threshold
    if not jev_ok:
        result.checks_failed.insert(
            0,
            f"jev: P(goal reached)={result.goal_probability:.2f} "
            f"below {scenario.goal_threshold:.2f} on {obs.path}",
        )
    result.passed = jev_ok and not result.checks_failed and not result.errors
    return result


__all__ = [
    "ScenarioResult",
    "SitePool",
    "SiteTab",
    "SmokeStep",
    "action_space",
    "deterministic_checks",
    "run_scenario",
    "step_questions",
]
