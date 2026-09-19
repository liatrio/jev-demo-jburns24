# Slides brief: "System One for the transaction stream"

Instructions for Claude Design. Build a 16-slide deck from this outline (13 slides as
numbered, plus a three-slide second act on the adversarial e2e swarm, slides 11a to 11c,
placed after slide 11). Audience is
senior engineers and engineering leaders at a technology enterprise (thousands of
engineers, regulated data, real-time decision systems). Tone: precise, no hype, every
number traceable to the repo. Dark background, one idea per slide, large type, charts
over tables where a chart is honest. Speaker notes under each slide are for the presenter,
not for the slide body.

Source of truth for all numbers: `results/summary.json`, `results/T0*.json` and
`results/pii-check.json` (fraud bake-off and PII check) and `results/swarm/*-summary.json`,
`results/swarm/*-findings.md` (swarm) in
https://github.com/jburns24/jev-demo (branch `claude/fraud-detection-jev-demo-6w42lx`).
Do not invent numbers. If a number is not in this brief or those files, leave it out.

---

## Slide 1: Title

**Title:** Decide, don't generate: Jev vs generative LLMs on a live fraud stream
**Subtitle:** A reproducible bake-off on synthetic enterprise-bank transaction tapes
**Footer:** jev-demo, September 2026

Speaker notes: This is a demo of a new class of model applied to an old problem. Fifteen
minutes, then the live run.

---

## Slide 2: The problem statement

**Headline:** Most "AI decisions" in a pipeline are not conversations. We are paying
conversation prices for them.

Body, three short points:
- Enterprise systems make millions of small typed decisions per day: is this fraud, which
  queue, what severity, does this pass the rubric.
- Generative LLMs answer these by writing prose and hoping the JSON parses. Latency is
  seconds, cost is dollars per thousand, and outputs need parsing, validation and retries.
- At stream volume that is either too slow to sit inline (so it becomes batch and the
  fraud has already cleared) or too expensive to run on every row (so it becomes sampling).

Speaker notes: Name the tension. Fraud detection is the sharpest case because the decision
has to happen before authorization completes, on every transaction, and the cost of a miss
is real money.

---

## Slide 3: The hypothesis

**Headline:** A model trained to *choose* rather than *write* can sit inline on every row.

Three testable claims, shown as a checklist:
1. **Speed.** A System One model answers a typed question in well under a second, fast
   enough to run before authorization completes.
2. **Cost.** Orders of magnitude cheaper per decision, so 100 percent of rows can be scored,
   not a sample.
3. **Quality.** On textbook fraud patterns its recall is competitive with a frontier LLM,
   and its output is a calibrated probability plus a typed label, not text to parse.

Corollary we also test: when the model is unsure, code can *see* that it is unsure and
escalate. Confidence is a first-class output.

Speaker notes: Be explicit that claim 3 is the one that could fail. Claims 1 and 2 are
almost structural.

---

## Slide 4: What Jev is, in one slide

**Headline:** System One: state in, typed answers out

Left: a minimal request. Right: the response.

Request (abbreviated):
```
state:      { account_profile, recent_history[12], transaction_under_review }
questions:
  is_fraud: boolean   "row is fraud or part of a fraud/AML scheme"
  pattern:  choice    { none, account_takeover, card_testing, structuring,
                        impossible_travel, mule_account }
  risk:     score     [ approve | monitor | hold | block ]
```
Response (abbreviated):
```
is_fraud: probability 0.93
pattern:  choice account_takeover, probabilities {...}, confidence 0.77
risk:     score 3 of 3
```

Callouts:
- All three questions answered in parallel in one call; adding questions adds almost no
  latency (TypeSafe's "speculative fan-out").
- No free text is ever produced. Nothing to parse, nothing to hallucinate outside the
  offered options.
- Reached through Vercel AI Gateway (`typesafe-ai/jev`), same key as the LLMs.

Speaker notes: This is the same shape jev-ultrafast uses to drive a browser: one typed
call per decision cycle, code validates the answer, code acts.

---

## Slide 5: The demo setup

**Headline:** Six tapes, one known situation each, three evaluators, identical input

Diagram: tape (stream of rows) -> per-row state builder -> fan out to Jev | Sonnet 5 |
GPT-5.6 Luna -> verdicts -> scorer vs hidden ground truth -> report.

Table of tapes (from the README): clean baseline, account takeover, card testing,
cash structuring, impossible travel, money mule. 212 rows, 39 fraud rows.

Rules of the game, as bullets:
- Stream semantics: at row *i* the evaluator sees the account profile and the previous
  rows for that account only. Nothing from the future.
- Byte-identical state to every model. Ground truth never leaves the scorer.
- LLMs get the same fields requested as JSON, provider-default reasoning.
- Every Jev answer is validated against the typed contract before it counts (ported from
  jev-ultrafast's `validate_choice`).

Speaker notes: Emphasize that the tapes are synthetic and small. This is a behaviour demo
on textbook patterns, not a production benchmark.

---

## Slide 6: Results, pooled across all tapes

**Headline:** Same recall as the frontier LLM, 8x faster, 62x cheaper

Chart 1 (bar, log scale): p50 latency per row, jev vs sonnet-5 vs gpt-5.6-luna.
Chart 2 (bar, log scale): total cost for all 212 rows.
Small table: precision, recall, F1, pattern accuracy per evaluator.

Numbers from the recorded run (results/summary.json):

| evaluator | rows | TP | FP | FN | precision | recall | F1 | pattern acc | errors | p50 ms | p95 ms | total cost |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| jev | 212 | 39 | 4 | 0 | 0.91 | 1.00 | 0.95 | 0.97 | 0 | 251 | 356 | $0.0183 |
| sonnet-5 | 212 | 39 | 5 | 0 | 0.89 | 1.00 | 0.94 | 1.00 | 0 | 1,902 | 3,584 | $1.1406 |
| gpt-5.6-luna | 212 | 35 | 7 | 4 | 0.83 | 0.90 | 0.86 | 1.00 | 0 | 2,069 | 3,474 | $0.0914 |
| jev+sonnet-5 | 212 | 38 | 5 | 1 | 0.88 | 0.97 | 0.93 | 0.97 | 0 | 254 | 2,153 | $0.1175 |

- vs **sonnet-5**: Jev is 8x faster at p50 and 62x cheaper
- vs **gpt-5.6-luna**: Jev is 8x faster at p50 and 5x cheaper
- vs **jev+sonnet-5**: Jev is 1x faster at p50 and 6x cheaper

Speaker notes: Read the multiples out loud. Then say what the F1 gap is and whether it
favours Jev or the LLM, honestly.

---

## Slide 7: Per-tape view

**Headline:** Where each model wins and loses

Heatmap or small-multiples: rows = tapes, columns = evaluators, cell = recall with FP
count in the corner. Use `results/T0*.json`.

Call out two or three specific rows from the disagreement tables, for example the
first Zelle credit on the mule tape (does the model catch it on row one or only after the
pattern is obvious) and the legitimate travel booking on the clean tape (who false-alarms).

Speaker notes: This is the slide senior engineers will push on. Have the disagreement
tables open in the terminal.

---

## Slide 8: Confidence is an output, so routing is code

**Headline:** Confidence-gated escalation: Jev decides alone outside the gray zone

Diagram: row -> Jev -> p < 0.3 or p > 0.7 ? act : escalate to Sonnet 5 -> act.

Numbers from `results/T01-ato.json` for `jev+sonnet-5`: how many rows were escalated,
resulting recall and FP, cost versus Jev alone and versus Sonnet alone.

Point: the expensive model runs on the handful of rows that need it, and the routing rule
is a line of code you can read, test and audit, not a prompt.

Speaker notes: For a bank this is the compliance story. The decision boundary is explicit.

---

## Slide 9: Determinism and testing (how we keep the claims honest)

**Headline:** Jev drives the e2e suite, and the suite runs offline in pre-commit

Three columns:
1. **Record/replay.** Every gateway request is hashed; real responses are stored as
   cassettes. Replay mode is offline, deterministic, needs no key. Same idea as
   jev-ultrafast's "tests must not call paid APIs", but replaying real answers instead of
   hand-written fakes.
2. **Jev as system under test.** Each tape is held to minimum recall, a false-positive cap,
   pattern accuracy and the typed contract on every row.
3. **Jev as test oracle.** Jev grades what an assertion cannot: does each synthetic tape
   really contain the situation it claims, and is the analyst report the pipeline emits
   actually actionable (boolean plus a four-level score).

Footer line: a third test guards the headline multiples; if a re-record closes the gap,
the build goes red before this deck goes stale.

Speaker notes: `task test` is what pre-commit runs. Show the hook firing if there is time.

---

## Slide 10: Semantic linting at pre-commit speed

**Headline:** A lint rule that reads: "does this log line leak PII?" in one typed call per file

Left: the developer's view, styled as terminal output (verbatim from the repo, keep the
monospace):

```
$ git commit -m "add reminder job"
pii in log statements (jev, live)..........Failed
  BLOCK  reminders.py:5  identity (P(pii)=1.00)
         logger.info("sending reminder to %s at %s", user.full_name, user.email)
pii-check: 1 log statement in 1 file, 1 flagged | 1 jev call, p50 541 ms, cost $0.00004
pii-check: commit blocked. Remove or redact the personal data in the lines above.
```

Right: how it works, three steps:
1. Regex finds the log calls (`logger.*`, `console.*`, `slog.*`, Rust macros) in the staged
   files and cuts each one out whole, with four lines of context.
2. One Jev call per file, one typed `choice` question per statement: *what kind of
   personal data does this line write?* Options: none, contact, identity, financial,
   credential, network/location, sensitive. A written policy travels in the state.
3. Code thresholds `P(pii)` at 0.5 and blocks; 0.35 to 0.5 prints a warning and lets the
   commit through.

Numbers from `results/pii-check.json` (four fixture files in Python, TypeScript and Go,
26 log statements, 7 planted leaks):

| statements | Jev calls | leaks caught | false positives | p50 per call | wall clock | cost |
|--:|--:|--:|--:|--:|--:|--:|
| 26 | 4 | 7 of 7 | 0 | 594 ms | 765 ms | $0.00048 |

Callouts:
- Adding a statement adds a question, not a call. A 20-statement file costs one round trip.
- What *passes* is the point: a masked email, a sha256 of a user key, a card brand label,
  `customer_id`, `txn_id`. A keyword scanner false-alarms on every one of those.
- Same contract check as the fraud rows (`validate_choice`), same gateway, same key.

Speaker notes: This is the "LLM-as-judge in CI" row from slide 11 made concrete. The
generative version of this check exists at most shops and gets turned off because it adds
ten seconds and a few cents per file. This one is under a second and under a tenth of a
cent for a whole file, so it can sit in pre-commit rather than in a nightly job. If there is
time, do it live: paste a leaking log line into any `.py` file, `git add`, `git commit`,
watch it block, redact, commit again. The second check (the code review pipeline in
`plan.md`) is the same idea scaled up to a whole diff; say it is planned, not built.

---

## Slide 11: What this could solve for an enterprise like ours

**Headline:** Inline typed decisions at stream scale

Four rows, each "today -> with a System One model":
- Fraud/AML scoring on a sample, in batch -> every row, before authorization.
- Ticket and alert triage by regex plus an occasional LLM -> calibrated routing with an
  explicit escalation threshold.
- LLM-as-judge in CI, slow and flaky -> typed rubric checks that run on every PR in
  seconds and replay deterministically (slide 10 is the pre-commit version of this).
- Prompt-and-parse glue code with retry loops -> schema-guaranteed answers, validated by
  contract, no parsing.
- A handful of scripted e2e tests, or one expensive LLM browser agent, run nightly ->
  hundreds of cheap Jev-driven browser agents that attack the site on every push (slides
  11a to 11c).

Speaker notes: Keep this concrete to our systems. Ask which of the five the room would
try first. Slide 10 was the pre-commit version of the CI row; the last row is the second
act of this deck, say "we built that too".

---

## Slide 11a: Second act. If one agent is this cheap, run two hundred

**Headline:** Adversarial end-to-end testing on pre-push: 200 Jev-driven browser agents

Left: the jev-ultrafast loop as a four-box cycle:
```
code observes the page  ->  code enumerates concrete actions
        ^                              |
        |                              v
code acts in Chromium   <-  Jev picks one (choice) + inspects the page (2 booleans, 1 score)
```
Right: eight persona chips with one-line goals:
negative-money, overdraft, url-tamperer, injector, link-walker, empty-hands, edge-text,
bookkeeper.

Callouts:
- Same primitive as the fraud stream: one typed call per decision, speculative fan-out,
  contract-validated, no free text anywhere.
- Agent *i* is a pure function of *i*: persona = i mod 8, seeded payload order and start
  page. Two hundred distinct attack plans, all replayable.
- Target is a small deterministic online-banking portal with seven regressions that can
  be injected by name (negative transfer, overdraft, IDOR, reflected XSS, dead link,
  leaked traceback, unicode crash).

Speaker notes: This is the "so what" of the cost slide. When a decision costs a
hundredth of a cent, the interesting question is not "can it replace the LLM" but "what
becomes affordable that was not". Exploratory testing at swarm scale, on every push, is
one answer.

---

## Slide 11b: What the swarm found, and what it did not

**Headline:** Seven regressions injected, seven found, zero false positives on the clean
build

Chart: horizontal bars, one per finding on the injected run, length = number of agents
that reproduced it (from `results/swarm/injected-findings.md`):

| finding | category | agents | p(defect) | harness check |
|---|---|--:|--:|---|
| `/transfer` negative or over-balance transfer completes | money_loss | 48 | 0.93 | negative_amount_displayed |
| `/statements` link returns 404 | broken_page | 25 | 0.92 | http_404 |
| `/accounts/2001` shows another customer's data | security | 16 | 0.95 | (Jev judgement) |
| `/transfer` memo with a quote leaks a traceback | broken_page | 12 | 0.90 | http_500, stack_trace_exposed |
| `/profile` non-ASCII name returns 500 | broken_page | 3 | 0.93 | http_500, stack_trace_exposed |
| `/search` injected script executed | security | 3 | 0.89 | script_injection_executed |
| `/transfer` zero-amount transfer accepted | validation_gap (report only) | 1 | 0.88 | none |

Two small stat tiles, both runs from `results/swarm/*-summary.json`:
- Injected: 200 agents, 806 browser steps, 813 Jev calls, 38 s wall, $0.048, FAIL (6 blocking).
- Clean: 200 agents, 815 browser steps, 815 Jev calls, 37 s wall, $0.050, PASS (0 findings).

Callouts:
- Two-stage judgement: agents flag, a second Jev call triages each deduplicated incident
  (is_defect, six-way category, release-blocking severity). Hard harness facts (a 500, a
  payload that ran) skip the is_defect gate; Jev only names and grades them.
- Blocking rule is code you can read: category in {broken_page, security, money_loss},
  severity >= 2, not in the baseline file.
- Honest line: two bugs were found by only three agents each. Coverage is a property of
  persona and payload design, not of the agent count.

Speaker notes: Read the agents column. Forty-eight agents independently reproduced the
transfer bug; that is a very different signal from one flaky assertion. Then read the
"3"s and say out loud that persona design is where the work is.

---

## Slide 11c: Why it lives in pre-push and stays there

**Headline:** Five cents and forty seconds, replayed for free when nothing changed

Diagram: the two hook stages as a timeline:
```
git commit  ->  pre-commit: lint, unit, e2e replay (seconds, offline, $0)
git push    ->  pre-push:   jev-demo swarm --agents 200 (~40 s; $0 if the site is unchanged)
```

Three points:
- Every Jev request is keyed on the observation, and the observation is deterministic by
  construction (paths not origins, no timestamps, console noise excluded). A page state
  the swarm has seen replays from a cassette; only new states go to the gateway. Same
  record/replay layer as slide 9, same "tests must not call paid APIs" rule.
- The swarm tests itself on every commit: a 48-agent slice of both scenarios runs in pure
  replay in the e2e suite, so a false positive on the clean build or a missed regression
  is caught before the hook is trusted.
- Failure mode is loud and cheap: a changed page means a cassette miss in replay, or a
  few live calls in record mode. Re-record with `task swarm:record`, commit the cassettes
  with the change.

Speaker notes: This is the operational slide. The point is not that the swarm is clever,
it is that it is cheap enough and deterministic enough to sit in the developer's inner
loop. Show `task swarm:demo` live if there is time: 200 agents, all regressions injected,
replayed, six blocking findings in under a minute with no API key.

---

## Slide 12: Limits and open questions

**Headline:** What this demo does not prove

- Synthetic tapes, 212 rows, textbook patterns. Real fraud is adversarial and drifts.
- Latency measured through a public gateway from one machine at concurrency 8.
- Jev is text-only, 64k tokens total, no free-text rationale. An analyst still needs a
  reason; here the "reason" is the pattern label and the probability.
- TypeSafe documents calibration, not bit-for-bit determinism; the demo includes an
  empirical repeatability check in live mode.
- Model pinning: the gateway alias resolves to the current release. Production thresholds
  should be tuned against a pinned version.
- The PII lint judges from variable names and literals in the statement plus four lines of
  context. It will not see that `payload` three functions up contains an email. Extraction
  is regex-based and catches conventional logger names, not every custom wrapper.
- The swarm's target is a stand-in portal with textbook bugs. Pointing it at a real
  staging URL means giving that environment a fixture mode so observations stay
  replayable.

Speaker notes: Say these before someone else does.

---

## Slide 13: Next steps and the live run

**Headline:** Try it

```
git clone https://github.com/jburns24/jev-demo
cp .env.example .env    # API_KEY
task setup && task demo
```

Proposed follow-ups, as a short list:
1. Replay a de-identified slice of a real transaction log through the same harness.
2. Add a fourth evaluator: our current rules engine, scored on the same tapes.
3. Wire the confidence-gated router into a shadow-mode consumer and measure escalation rate.
4. Build the second check, the Jev-driven code review pipeline scaffolded in `plan.md`:
   typed rubric checks over a whole PR diff, in CI, with the same record/replay discipline.
5. Point the swarm at a staging deployment of one of our own web apps, with personas
   written by that team, and run it as its pre-push hook for a sprint.

Then switch to the terminal and run `task play -- T01`, and if time allows `task pii:demo`
and `task swarm:demo`.

Speaker notes: End on the live run. The point of the deck is to earn the two minutes of
terminal time.
