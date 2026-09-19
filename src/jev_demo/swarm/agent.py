"""A Jev-driven adversarial browser agent.

Same shape as browser-use/jev-ultrafast: code enumerates the concrete actions
available on the page, Jev picks one with a typed ``choice`` question, code
validates the answer against the contract and executes it. In the same call
(speculative fan-out) Jev also answers two booleans and a score about the page
it is looking at, so every step doubles as an inspection. No free text is ever
generated; the only thing the model produces is a decision.

Agents are derived from an integer seed, so agent ``i`` always walks the same
path against the same site. That is what makes a 200-agent swarm replayable
from cassettes, and lets a 40-agent test reuse the first 40 agents' recordings.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from ..evaluators import JEV_MODEL, JevAnswerError, cost_for, validate_choice
from ..gateway import Gateway
from .browser import XSS_PAYLOAD, Observation, Tab

MAX_STEPS = 6
MAX_CANDIDATES = 22
SUSPICION_THRESHOLD = 0.6


@dataclass(frozen=True)
class Persona:
    name: str
    goal: str
    payloads: tuple[str, ...] = ()
    extra_paths: tuple[str, ...] = ()
    start_pages: tuple[str, ...] = ("/",)


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "negative-money",
        "Try to move money with amounts a bank must reject: negative, zero, absurdly large "
        "or non-numeric. A transfer that succeeds with such an amount is a defect.",
        payloads=("-500", "0", "-0.01", "1e9", "abc", "999999999", "-1,000"),
        start_pages=("/transfer", "/"),
    ),
    Persona(
        "overdraft",
        "Try to transfer more than the source account holds and check whether any balance "
        "shown afterwards is negative. Overdrawing must be rejected.",
        payloads=("3000", "2500.01", "50000", "8001", "2499.99"),
        start_pages=("/transfer", "/"),
    ),
    Persona(
        "url-tamperer",
        "Look for data that is not yours by editing ids in URLs. Seeing another customer's "
        "account or personal details is a serious defect; a 403 is correct behaviour.",
        extra_paths=("/accounts/2001", "/accounts/1003", "/accounts/0", "/accounts/9999"),
        start_pages=("/", "/help"),
    ),
    Persona(
        "injector",
        "Put HTML and script into every text field and search box, then check whether it is "
        "rendered or executed instead of shown as plain text.",
        payloads=(XSS_PAYLOAD, "<b>bold</b>", "<script>1</script>", '"><svg onload=1>'),
        start_pages=("/search", "/profile", "/transfer"),
    ),
    Persona(
        "link-walker",
        "Visit every navigation and in-page link looking for 404s, server errors and pages "
        "that fail to render. Note any link whose destination does not match its label.",
        start_pages=("/", "/help", "/statements"),
    ),
    Persona(
        "empty-hands",
        "Submit every form with empty or whitespace-only fields and confirm each is rejected "
        "with a clear message rather than accepted or crashing.",
        payloads=("", "   ", "\t"),
        start_pages=("/transfer", "/profile", "/search"),
    ),
    Persona(
        "edge-text",
        "Push text fields to their limits: quotes, SQL-looking strings, very long input and "
        "non-ASCII names. A 500 or stack trace is a defect; a polite rejection is fine.",
        payloads=("O'Brien", '"; DROP TABLE accounts;--', "Zoë Åström 名前", "A" * 200, "Priya"),
        start_pages=("/profile", "/transfer"),
    ),
    Persona(
        "bookkeeper",
        "Make one ordinary, valid transfer between the two accounts and then check that the "
        "balances shown on the accounts page, the transfer page and each account page agree.",
        payloads=("100", "250.50", "1"),
        start_pages=("/transfer", "/"),
    ),
)


@dataclass
class Step:
    n: int
    path: str
    action: str
    before: dict[str, Any]
    after: dict[str, Any] | None
    suspicion: dict[str, float]
    signals: list[str]
    latency_ms: float
    cost_usd: float


@dataclass
class Trajectory:
    agent_id: int
    persona: str
    goal: str
    steps: list[Step] = field(default_factory=list)
    jev_calls: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)


def make_agent(agent_id: int) -> tuple[Persona, random.Random]:
    persona = PERSONAS[agent_id % len(PERSONAS)]
    return persona, random.Random(agent_id)


# ------------------------------------------------------------- action space
def fingerprint(obs: Observation, desc: str) -> tuple[str, str, tuple[str, ...]]:
    """Same page, same action, same form contents: repeating it teaches nothing."""
    values = tuple(str(e.get("value", "")) for e in obs.elements)
    return (obs.path.split("?")[0], desc, values)


def action_space(
    obs: Observation,
    persona: Persona,
    rng_payloads: list[str],
    rng_paths: list[str],
    tried: set[tuple[str, str, tuple[str, ...]]] = frozenset(),  # type: ignore[assignment]
) -> dict[str, dict[str, Any]]:
    """Concrete, executable actions on this page, keyed for Jev's choice question.

    ``tried`` holds fingerprints of actions already executed (see ``fingerprint``);
    code, not the model, removes them so an agent never burns its budget
    re-submitting an unchanged form.
    """
    actions: dict[str, dict[str, Any]] = {}

    def add(desc: str, **spec: Any) -> None:
        if len(actions) < MAX_CANDIDATES and fingerprint(obs, desc) not in tried:
            actions[f"a{len(actions)}"] = {"desc": desc, **spec}

    for i, e in enumerate(obs.elements):
        tag = e["tag"]
        if tag == "a":
            add(f'click link "{e["text"]}" (goes to {e["href"]})', kind="click", index=i)
        elif tag == "button":
            add(f'click button "{e["text"]}" (submits the form)', kind="click", index=i)
        elif tag == "select":
            for opt in e.get("options", []):
                if opt != e.get("value"):
                    add(f'set "{e["label"]}" to {opt}', kind="select", index=i, value=opt)
        else:
            for p in rng_payloads[:3]:
                shown = repr(p if len(p) <= 40 else p[:37] + "...")
                add(f'type {shown} into "{e["label"] or e["name"]}"', kind="fill", index=i, value=p)
    for path in rng_paths[:3]:
        add(f"edit the URL to open {path}", kind="goto", path=path)
    add("go back to the previous page", kind="back")
    actions["stop"] = {
        "desc": "stop: the goal is exhausted on this site or a defect has already been shown",
        "kind": "stop",
    }
    return actions


def step_questions(actions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One speculative fan-out call: pick the action, inspect the page."""
    return {
        "action": {
            "type": "choice",
            "instructions": (
                "You are testing the page described in state.page against state.goal. "
                "Which single action best advances the goal right now? Prefer actions that "
                "complete an attempt (e.g. submitting a form already filled with an "
                "adversarial value) over starting a new one, and stop once the goal has been "
                "tried."
            ),
            "criteria": {k: v["desc"] for k, v in actions.items()},
        },
        "page_is_broken": {
            "type": "boolean",
            "instructions": (
                "state.page shows a server error, stack trace, dead link, crash or a page "
                "that failed to render."
            ),
        },
        "wrong_behaviour": {
            "type": "boolean",
            "instructions": (
                "state.page shows that the application accepted an input or performed an "
                "action that an online bank must reject: a negative, zero or over-balance "
                "transfer that completed, another customer's account or personal data being "
                "shown, HTML or script from an input being rendered rather than escaped, or "
                "balances that do not add up. A clear rejection message is correct behaviour, "
                "not a defect."
            ),
        },
        "severity": {
            "type": "score",
            "instructions": "How serious is the worst problem visible on state.page?",
            "criteria": [
                "No problem visible",
                "Cosmetic or confusing but harmless",
                "Functional bug: something does not work as a customer expects",
                "Security or money-loss bug: data exposure, injection, or money moved wrongly",
            ],
        },
    }


def pick_action(
    answer: dict[str, Any], actions: dict[str, dict[str, Any]], traj: Trajectory, step: int
) -> str:
    """Enforce the typed contract; on a violation, code takes the argmax and logs it.

    jev-ultrafast rejects a bad answer outright. An agent that dies mid-run wastes
    its browser budget, so here the violation is recorded (it counts against the
    run) and the deterministic fallback keeps the trajectory going.
    """
    criteria = {k: v["desc"] for k, v in actions.items()}
    try:
        validate_choice(answer, criteria)
        return str(answer["choice"])
    except JevAnswerError as exc:
        traj.errors.append(f"step {step}: contract violation: {exc}"[:300])
        probs = answer.get("probabilities") or {}
        valid = {k: v for k, v in probs.items() if k in actions and isinstance(v, int | float)}
        return max(valid, key=valid.get) if valid else "stop"  # type: ignore[arg-type]


def _prob(answer: dict[str, Any]) -> float:
    return float(answer.get("probability", answer.get("noul", 0.0)))


async def run_agent(
    agent_id: int, tab: Tab, gateway: Gateway, max_steps: int = MAX_STEPS
) -> Trajectory:
    persona, rng = make_agent(agent_id)
    payloads = list(persona.payloads)
    rng.shuffle(payloads)
    paths = list(persona.extra_paths)
    rng.shuffle(paths)
    start = rng.choice(persona.start_pages)
    traj = Trajectory(agent_id=agent_id, persona=persona.name, goal=persona.goal)

    await tab.goto(start)
    obs = await tab.observe()
    tried: set[tuple[str, str, tuple[str, ...]]] = set()
    history: list[str] = []
    for n in range(max_steps):
        actions = action_space(obs, persona, payloads, paths, tried)
        questions = step_questions(actions)
        state = {
            "goal": persona.goal,
            "step": n + 1,
            "steps_remaining": max_steps - n,
            "actions_so_far": history[-5:],
            "page": obs.for_model(),
        }
        started = time.perf_counter()
        try:
            resp = await gateway.evaluate(JEV_MODEL, state, questions)
            answers = resp.body["answers"]
        except Exception as exc:  # noqa: BLE001 - recorded, agent stops
            traj.errors.append(f"step {n + 1}: {type(exc).__name__}: {exc}"[:300])
            break
        traj.jev_calls += 1
        usage = resp.body.get("usage", {})
        cost = cost_for(JEV_MODEL, usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        choice = pick_action(answers["action"], actions, traj, n + 1)
        suspicion = {
            "page_is_broken": _prob(answers["page_is_broken"]),
            "wrong_behaviour": _prob(answers["wrong_behaviour"]),
            "severity": float(answers.get("severity", {}).get("score", 0)),
        }
        step = Step(
            n=n + 1,
            path=obs.path,
            action=actions[choice]["desc"],
            before=obs.for_model(),
            after=None,
            suspicion=suspicion,
            signals=list(obs.signals),
            latency_ms=resp.latency_ms if resp.replayed else (time.perf_counter() - started) * 1000,
            cost_usd=cost,
        )
        traj.steps.append(step)
        if choice == "stop":
            break
        tried.add(fingerprint(obs, step.action))
        history.append(f"on {obs.path}: {step.action}")
        try:
            await tab.act(actions[choice])
        except Exception as exc:  # noqa: BLE001 - a timeout is itself evidence
            traj.errors.append(f"step {n + 1} action failed: {type(exc).__name__}"[:300])
            break
        obs = await tab.observe()
        step.after = obs.for_model()
    return traj


def suspicious(step: Step) -> bool:
    """Did the page this step *started on* look wrong, to code or to Jev?"""
    if step.signals:
        return True
    return (
        step.suspicion["page_is_broken"] >= SUSPICION_THRESHOLD
        or step.suspicion["wrong_behaviour"] >= SUSPICION_THRESHOLD
    )


__all__ = [
    "MAX_STEPS",
    "PERSONAS",
    "JevAnswerError",
    "Persona",
    "Step",
    "Trajectory",
    "action_space",
    "make_agent",
    "run_agent",
    "step_questions",
    "suspicious",
]
