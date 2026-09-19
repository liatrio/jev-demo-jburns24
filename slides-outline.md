# Slides brief: "System One for the transaction stream"

Instructions for Claude Design. Build a 12-slide deck from this outline. Audience is
senior engineers and engineering leaders at a technology enterprise (thousands of
engineers, regulated data, real-time decision systems). Tone: precise, no hype, every
number traceable to the repo. Dark background, one idea per slide, large type, charts
over tables where a chart is honest. Speaker notes under each slide are for the presenter,
not for the slide body.

Source of truth for all numbers: `results/summary.json` and `results/T0*.json` in
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

## Slide 10: What this could solve for an enterprise like ours

**Headline:** Inline typed decisions at stream scale

Four rows, each "today -> with a System One model":
- Fraud/AML scoring on a sample, in batch -> every row, before authorization.
- Ticket and alert triage by regex plus an occasional LLM -> calibrated routing with an
  explicit escalation threshold.
- LLM-as-judge in CI, slow and flaky -> typed rubric checks that run on every PR in
  seconds and replay deterministically.
- Prompt-and-parse glue code with retry loops -> schema-guaranteed answers, validated by
  contract, no parsing.

Speaker notes: Keep this concrete to our systems. Ask which of the four the room would
try first.

---

## Slide 11: Limits and open questions

**Headline:** What this demo does not prove

- Synthetic tapes, 212 rows, textbook patterns. Real fraud is adversarial and drifts.
- Latency measured through a public gateway from one machine at concurrency 8.
- Jev is text-only, 64k tokens total, no free-text rationale. An analyst still needs a
  reason; here the "reason" is the pattern label and the probability.
- TypeSafe documents calibration, not bit-for-bit determinism; the demo includes an
  empirical repeatability check in live mode.
- Model pinning: the gateway alias resolves to the current release. Production thresholds
  should be tuned against a pinned version.

Speaker notes: Say these before someone else does.

---

## Slide 12: Next steps and the live run

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

Then switch to the terminal and run `task play -- T01`.

Speaker notes: End on the live run. The point of the deck is to earn the two minutes of
terminal time.
