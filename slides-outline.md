# Slides brief: "System One for the transaction stream"

Instructions for Claude Design. Build an 18-slide deck from this outline (13 slides as
numbered, plus a two-slide Jev primer, slides 4a and 4b, placed after slide 4, plus a
three-slide second act on the adversarial e2e swarm, slides 11a to 11c, placed after
slide 11). The "Running the demos" appendix at the end is for the presenter's runbook and
optionally one backup slide per experiment. Audience is
senior engineers and engineering leaders at a technology enterprise (thousands of
engineers, regulated data, real-time decision systems). Tone: precise, no hype, every
number traceable to the repo. Dark background, one idea per slide, large type, charts
over tables where a chart is honest. Speaker notes under each slide are for the presenter,
not for the slide body.

Source of truth for all numbers: `results/summary.json`, `results/T0*.json` and
`results/pii-check.json` (fraud bake-off and PII check) and `results/swarm/*-summary.json`,
`results/swarm/*-findings.md` (swarm) in
https://github.com/liatrio/jev-demo-jburns24 (branch `main`). `results/report.html` is a
rendered single-page view of the same files. Do not invent numbers. If a number is not in
this brief or those files, leave it out.

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

## Slide 4a: Primer. A Jev call is three things: state, questions, criteria

**Headline:** You describe the situation, you list the options, Jev returns probabilities

Three labelled boxes, left to right, each with a tiny example. Keep the example small enough
to read from the back of the room.

**State**: any JSON. It is the thing being judged. No prompt, no role-play, no instructions
hidden in prose.
```
{ "log_statement": "logger.info('reset link sent to %s', user.email)" }
```

**Question**: a name, a type and one sentence of instructions. Three types exist:
```
"leaks_pii":  { "type": "boolean",
                "instructions": "The statement writes personal data to the log." }
```

**Criteria**: for `choice` and `score`, the options Jev must pick between. The text of each
option is the rubric.
```
"kind": { "type": "choice",
          "instructions": "What kind of personal data does the statement write?",
          "criteria": { "none": "opaque ids, counts, masked or hashed values",
                        "contact": "email, phone, postal address",
                        "credential": "password, token, secret" } }
```

**Answer** (right edge of the slide):
```
leaks_pii: { probability: 0.98 }
kind:      { choice: "contact",
             probabilities: { none: 0.01, contact: 0.97, credential: 0.02 } }
```

Callouts:
- `boolean` returns one probability. `choice` returns a probability per option and the
  argmax. `score` returns a probability per ordered level and an expected value.
- Many questions travel in one call and are answered in parallel (speculative fan-out).
  Twenty log statements are twenty `choice` questions in one round trip.
- Nothing in the answer is free text. There is nothing to parse and nothing outside the
  offered options for the model to invent.

Speaker notes: Say "the criteria are the rubric" out loud. Changing the policy is editing
those strings, not re-engineering a prompt. This exact shape is what `pii_questions()` in
`src/jev_demo/pii_check.py` builds, minus the four lines of code context.

---

## Slide 4b: Primer. Two layers: Jev judges meaning, code decides

**Headline:** The semantic layer answers "what is this?"; the deterministic layer answers
"so what do we do?"

Split slide. Left column "semantic (Jev)", right column "deterministic (code)". Walk one
example down both columns.

Example, a real row: `T03-structuring-0014`, a 9,431.67 USD cash deposit, the third
under-threshold deposit the bank has seen on this account. Jev's actual answers from
`results/T03-structuring.json`:

| semantic layer (Jev) | deterministic layer (code) |
|---|---|
| `is_fraud`: probability 0.77 | `p >= 0.5` -> flag the row |
| `pattern`: choice `structuring`, probabilities { structuring 1.00, none 0, mule_account 0, ... } | `validate_choice`: choice offered? keys match? sum to 1? choice is argmax? Reject anything else |
| `risk`: score 2.09, probabilities { approve 0, monitor 0.01, hold 0.89, block 0.10 } | `0.3 < p < 0.7` -> escalate to Sonnet; here p is 0.77, so Jev acts alone |
| (no text) | write the verdict, the probability and the rule that fired to the audit log |

Two contrasting examples under the table, one line each, to show why both layers are needed:
- **Semantic wins over regex.** `logger.info("welcome email queued for %s", mask_email(email))`
  and `logger.info("welcome email queued for %s", customer.email)` match the same keyword
  scanner. Jev returns P(pii) 0.20 for the first and 1.00 for the second
  (`results/pii-check.json`).
- **Deterministic wins over the model.** A page that returned HTTP 500 with a Python
  traceback is a defect whether or not a model thinks so. The swarm passes that as a fact;
  Jev only names the category and grades the severity.

Callouts:
- Every threshold in this repo is a number in code: 0.5 to flag a fraud row, 0.3 to 0.7 for
  the escalation band, 0.5 to block a commit, 0.35 to warn, severity 2 to block a push.
- Because thresholds are code, they are unit-tested, diffable and explainable to an auditor.
  Because the judgement is calibrated, tuning a threshold changes behaviour predictably.
- The contract check (`validate_choice`, ported from jev-ultrafast) sits between the two
  layers. A malformed answer is an error, never a silent default.

Speaker notes: This is the slide that answers "why not just use an LLM with JSON mode".
JSON mode gives you a shape. It does not give you a calibrated number to put a threshold
on, and it does not stop the model inventing an option you did not offer. Point at the
right column and say: all of this is plain Python you can read in five minutes.

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

**Headline:** Recall 1.00 against the frontier LLM's 0.97, 8x faster, 63x cheaper

Chart 1 (bar, log scale): p50 latency per row, jev vs sonnet-5 vs gpt-5.6-luna.
Chart 2 (bar, log scale): total cost for all 212 rows.
Small table: precision, recall, F1, pattern accuracy per evaluator.

Numbers from the live run on main (results/summary.json):

| evaluator | rows | TP | FP | FN | precision | recall | F1 | pattern acc | errors | p50 ms | p95 ms | total cost |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| jev | 212 | 39 | 5 | 0 | 0.89 | 1.00 | 0.94 | 0.97 | 1 | 269 | 620 | $0.0182 |
| sonnet-5 | 212 | 38 | 6 | 1 | 0.86 | 0.97 | 0.92 | 1.00 | 0 | 2,188 | 3,103 | $1.1424 |
| gpt-5.6-luna | 212 | 33 | 6 | 6 | 0.85 | 0.85 | 0.85 | 1.00 | 0 | 2,068 | 3,695 | $0.0914 |
| jev+sonnet-5 | 212 | 38 | 3 | 1 | 0.93 | 0.97 | 0.95 | 0.97 | 1 | 266 | 2,308 | $0.1156 |

- vs **sonnet-5**: Jev is 8x faster at p50 and 63x cheaper
- vs **gpt-5.6-luna**: Jev is 8x faster at p50 and 5x cheaper
- vs **jev+sonnet-5**: Jev is 1x faster at p50 and 6x cheaper

The one Jev error is a gateway connection failure on a clean row, scored as "not fraud".

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
| 26 | 4 | 7 of 7 | 0 | 663 ms | 1,102 ms | $0.00048 |

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
- Injected: 200 agents, 800 browser steps, 808 Jev calls, 53 s wall, $0.048, FAIL (6 blocking).
- Clean: 200 agents, 818 browser steps, 818 Jev calls, 45 s wall, $0.050, PASS (0 findings).
- Both runs with every Jev call live (no cassette replay). Agents that reproduced each
  finding: take the column from `results/swarm/injected-findings.md` on main.

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
git clone https://github.com/liatrio/jev-demo-jburns24
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

Then switch to the terminal and run the demos in the appendix below: `task play -- T01`,
then if time allows `task pii:demo` and `task swarm:demo`.

Speaker notes: End on the live run. The point of the deck is to earn the two minutes of
terminal time.

---

## Appendix: Running the demos (presenter runbook)

One-time setup, from a clean clone. Needs `uv`, `task` and a Vercel AI Gateway key.

```
git clone https://github.com/liatrio/jev-demo-jburns24 && cd jev-demo-jburns24
cp .env.example .env        # paste API_KEY=... (one key reaches Jev, Sonnet 5 and GPT-5.6 Luna)
task setup                  # venv + install
task browser:install        # Chromium for the swarm (or export JEV_SWARM_CHROMIUM=/path/to/chrome)
```

Modes matter for what the audience sees. `JEV_DEMO_MODE=record` (the CLI default) replays
any request that has a cassette and calls the gateway only for new ones, so a demo of the
committed tapes is instant and free. Add `--mode live` to force real calls and real
latency numbers; that is the honest version for a "how fast is it" question.

### Demo 1: fraud bake-off (3 to 5 minutes)

```
task tapes:list                          # 1. show the six tapes and what each one plants
task play -- T01                         # 2. account takeover: jev vs sonnet vs gpt, replayed
task play -- T05 -e jev,sonnet,gpt,hybrid --mode live   # 3. mule tape, live, adds the gated hybrid
task play:all                            # 4. pooled table across all six tapes
```

What the audience sees:
1. A card per tape: title, the situation in plain English, row count and planted fraud rows.
2. A live progress line as each evaluator works through the 38 rows, then a metrics table
   (precision, recall, F1, pattern accuracy, p50 and p95 latency, cost) and a disagreement
   table listing the rows where the evaluators split, with each model's verdict and
   probability. Point at the first ATO row: Jev flags it at the new-device login, before
   any money moves.
3. The same for the mule tape, but with real latency: Jev's counter finishes in a few
   seconds, the two LLM counters take about a minute. The hybrid column shows which gray-zone
   rows were escalated to Sonnet. Cost for the tape prints at the bottom.
4. The pooled table from slide 6 and the "Jev is Nx faster and Nx cheaper" summary line.
   Every number on slide 6 comes from this command's `results/summary.json`.

Fallback if the gateway is down: `task play:all` in the default mode replays cassettes and
prints the same tables with the recorded latencies.

### Demo 2: PII lint in pre-commit (2 minutes)

```
task pii:demo                            # 1. clean fixtures pass, leaky fixtures block
```
Then live on a real commit. Plant the leak in a new, lint-clean file so ruff (which runs
first) passes and the PII hook is the one that fails:
```
cat > src/jev_demo/reminders.py <<'EOF'      # 2. plant a leak
import logging

logger = logging.getLogger(__name__)


def remind(user) -> None:
    logger.info("reminder sent to %s", user.email)
EOF
git add -A && git commit -m "demo"           # 3. hook fires and blocks
sed -i 's/user.email/user.id/' src/jev_demo/reminders.py
git add -A && git commit -m "demo"           # 4. redacted: the same commit goes through
git reset --hard HEAD~1                      # 5. clean up after the demo
```

What the audience sees:
1. Two runs of `jev-demo pii-check --mode live -v`. The clean Python and Go files print
   every statement as `ok` with its P(pii), including a masked email at 0.20 and a hashed
   key at 0.00. The leaky Python and TypeScript files print `BLOCK` lines with the kind of
   data (identity, credential, financial, network_location), the probability and the
   offending code, then "commit blocked" and exit 1. The footer shows the count: 26
   statements, 4 files, 4 Jev calls, wall clock about a second, cost under a twentieth of a
   cent.
2. to 4. The pre-commit output from slide 10, for real: lint and tests pass in seconds,
   then `pii in log statements (jev, live)` fails with the planted line, its file and line
   number, and P(pii). After the revert the same commit passes. Say that the hook only
   receives staged files, so it costs one call per changed file, not per repo.

Requires `API_KEY` in `.env`; the hook runs live on purpose so it never records cassettes
for work-in-progress code.

### Demo 3: adversarial swarm on pre-push (2 to 3 minutes)

```
task swarm:demo                          # 1. 200 agents, all seven regressions injected, replayed (no key needed)
task swarm                               # 2. the pre-push hook: 200 agents vs the clean portal
task swarm -- --inject idor,dead_link    # 3. plant two regressions and watch only those come back
task swarm:live -- --inject all          # 4. optional: every Jev call live, real timing and cost
```

What the audience sees:
1. A progress line every 25 agents, then a findings report: one row per deduplicated
   finding with severity, category, path, the number of agents that reproduced it,
   P(defect), the automatic checks that fired and BLOCK or report. Six blocking findings
   (negative transfer, dead Statements link, another customer's account and SSN digits,
   traceback on the transfer memo, traceback on a non-ASCII profile name, reflected script
   executing) and one report-only (zero-amount transfer). Footer: 200 agents, about 800
   browser steps, about 800 Jev calls, wall time, cost, `FAIL`. Exit code 1, which is what
   blocks the push.
2. The same swarm against the unbroken portal: "No defects confirmed by triage", `PASS`,
   exit 0. This is the false-positive check; say it out loud.
3. Only the two planted regressions come back, found by the url-tamperer and link-walker
   personas. Shows the finding is caused by the injected bug, not by the agents' payloads.
4. Same as 1 with `(0 replayed, N live)` in the footer, about 45 to 55 seconds of wall
   time and about five cents. Use this if someone asks whether the replay is hiding the cost.

Needs a Chromium that Playwright can launch (`task browser:install`, or set
`JEV_SWARM_CHROMIUM`). Step 1 needs no API key; steps 2 to 4 need `API_KEY` for any page
state that has no cassette yet.

### Backup: the rendered report

`results/report.html` (open it in a browser, no server needed) is the single-page view of
all three experiments with the live numbers: KPI tiles, cost and latency and F1 bars,
per-tape and per-finding tables, and an enterprise-scale cost extrapolation. Regenerate
after any rerun with `uv run python scripts/report_html.py`.
