# The jev-ultrafast pattern, in one page

Notes for the slide deck. High level, no numbers that need a source. Everything here
describes the pattern as [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast)
demonstrates it and as this repo adopts it in `src/jev_demo/swarm/` and `src/jev_demo/smoke/`.

## One-line version

A browser agent where the model never writes anything. Code looks at the page and lists the
things a tester could do; Jev picks one; code does it. Repeat.

## Why it exists

Most browser agents hand a generative LLM a screenshot or DOM dump and ask it to write the
next step: a selector, a snippet of code, a paragraph of reasoning. That is slow (seconds per
step), expensive (dollars per session), and fragile (the model can invent a selector that does
not exist, or an action the page does not offer).

jev-ultrafast turns the problem around. It uses Jev, TypeSafe's evaluation model, which
does not generate text at all. Jev answers typed questions with calibrated probabilities:
a boolean, a choice among options you offer, or a score on a scale you define. So the agent
loop is designed so that every decision *is* a typed question.

## The loop

```
   +-------------------------------------------------------------+
   |                                                             |
   v                                                             |
 code OBSERVES the page                                          |
   path, status, heading, visible text, links, buttons, fields   |
   plus deterministic checks (404, 500, traceback, payload ran)  |
   |                                                             |
   v                                                             |
 code ENUMERATES the actions                                     |
   every link, button, option, field x payload -> "a0".."aN"    |
   plus "stop"; actions already tried here are removed by code   |
   |                                                             |
   v                                                             |
 Jev DECIDES (one call, several typed questions in parallel)     |
   action:      choice over a0..aN                               |
   is it broken: boolean                                         |
   how bad:      score                                           |
   |                                                             |
   v                                                             |
 code VALIDATES the answer against the typed contract            |
   choice is one we offered, probabilities cover exactly the     |
   options, sum to one, choice is the argmax; else reject        |
   |                                                             |
   v                                                             |
 code ACTS in the browser  ----------------------------------------+
```

Four things to say out loud about this loop:

1. **The model only chooses.** It never produces a selector, a URL, a payload or a sentence.
   Everything it can pick from was built by code from the real page, so it cannot hallucinate
   an action that does not exist.
2. **Every step is also an inspection.** The same call that picks the next action asks
   whether the current page is broken and how serious it looks. No extra round trip.
3. **The contract is enforced, not trusted.** `validate_choice` rejects any answer that is
   not a well-formed distribution over the offered options. A malformed answer is an error
   you can see, never a silent default.
4. **Code owns the rules.** Which actions are legal, which have been tried, when to stop,
   what counts as a defect: all thresholds and filters are plain code you can read and test.

## Speculative fan-out

Jev evaluates many questions in one request in parallel. Adding a question adds almost no
latency, so the pattern piles them on: pick the action, judge the page, grade the severity,
all at once. The swarm asks four questions per step; the PII lint asks one per log statement
in a file, up to twenty per call; the smoke test asks three. One round trip each.

## What it buys you

- **Speed.** A decision in a fraction of a second, so an agent step is bounded by the
  browser, not the model.
- **Cost.** Fractions of a cent per step, so "run one agent" becomes "run two hundred".
- **Determinism.** Because the observation is built by code with no timestamps or random
  ids, and the answer is a distribution rather than prose, a request can be hashed and
  replayed. Recorded once, the whole run replays offline.
- **Auditability.** The trajectory is a list of (page, offered actions, chosen action,
  probabilities). A reviewer can replay a defect step by step.

## Testing discipline it comes with

jev-ultrafast's rule is that tests must not call paid APIs. Unit tests run against a faked
transport; live smoke runs are kept separate and explicit. This repo keeps the rule and
changes one thing: instead of hand-written fake answers, the test suite replays real recorded
Jev answers (cassettes), so the offline suite exercises the real contract.

## How this repo uses the pattern

| where | the "action space" | the questions | what code checks afterwards |
|---|---|---|---|
| adversarial swarm (`task swarm`) | links, buttons, fields x adversarial payloads, URL edits | action, page_is_broken, wrong_behaviour, severity | 5xx, 404, traceback, executed payload, negative money; triage; baseline; block on new severity 2+ |
| smoke test (`task smoke:liatrio`) | links and buttons only, internal, no form input | action, goal_reached, page_is_broken | path prefix, expected words, HTTP status from the TOML |
| fraud stream (`task play`) | none: classification, not navigation | is_fraud, pattern, risk | threshold at 0.5, gray-zone escalation 0.3 to 0.7 |
| PII lint (pre-commit) | none: one choice per log statement | kind of personal data per statement | block at P(pii) 0.5, warn from 0.35 |

The same three moves every time: code cuts the problem into typed questions, Jev answers
with calibrated probabilities, code turns probabilities into decisions.

## The slide-sized takeaway

"Don't ask the model to write the test. Ask it to pick from the tests code already wrote."
