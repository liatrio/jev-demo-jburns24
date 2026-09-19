# jev-demo: Jev vs generative LLMs on fraud-classification tapes

A bake-off between [TypeSafe's Jev](https://docs.typesafe.ai/introduction) (a "System One"
evaluation model that returns typed, calibrated decisions instead of text) and two
generative LLMs, **Claude Sonnet 5** and **GPT-5.6 Luna**, on the same job: reading a stream
of bank transaction log rows and deciding, row by row, whether each one is fraud.

Every model is reached through one Vercel AI Gateway key (`API_KEY`), sees byte-identical
input, and is scored against ground truth it never sees.

## What is on the tapes

A *tape* is a synthetic, seeded stream of transactions for an enterprise bank, with one
known situation planted in it. Ground truth is stored on every row and stripped before an
evaluator sees it.

| Tape | Situation | Rows | Fraud rows |
|------|-----------|-----:|-----------:|
| `T00-clean` | Two normal customers, one legitimately large travel booking. Zero fraud; measures false positives. | 47 | 0 |
| `T01-ato` | Account takeover: new device in Romania at 02:40 UTC, crypto, Zelle to a stranger, electronics, all within 11 minutes. | 38 | 4 |
| `T02-cardtest` | Card testing: 14 sub-3 USD digital-goods charges in 12 minutes from a headless device, then a 1,490 USD purchase. | 39 | 15 |
| `T03-structuring` | Smurfing: repeated 9,300 to 9,950 USD cash deposits across four branches, each under the 10,000 USD CTR threshold. | 32 | 9 |
| `T04-travel` | Impossible travel: card-present in London, then swiped twice in Sao Paulo 22 minutes later. | 31 | 2 |
| `T05-mule` | Money mule: six-week-old account receives seven Zelle credits from strangers, wires 93 percent to a UAE company, cashes out the rest. | 25 | 9 |

At row *i* an evaluator sees the account profile, the previous rows for that account
(rolling window of 12) and the row under review. Nothing from the future.

## Quick start

```bash
cp .env.example .env          # add API_KEY (Vercel AI Gateway)
task setup
task tapes:list               # what each tape encodes
task play -- T01              # one tape, jev vs sonnet vs gpt, live results table
task play:all                 # every tape + pooled metrics
task demo                     # all tapes, all models, bypassing cassettes (paid calls)
```

Without `task`: `uv run jev-demo --help`.

`task play -- T05 -e jev,sonnet,gpt,hybrid` adds the **confidence-gated** evaluator: Jev
decides alone when its fraud probability is outside the 0.3 to 0.7 gray zone and escalates
only the uncertain rows to Sonnet.

## How the two evaluator families are called

**Jev** gets one request per row with three typed questions evaluated in parallel
(TypeSafe's speculative fan-out):

- `is_fraud` (boolean): probability the row is fraud or part of a fraud/AML scheme
- `pattern` (choice): which of the six situations best fits
- `risk` (score): four ordered levels from "approve automatically" to "block and alert"

Every answer is checked against the typed contract before it counts: the choice must be one
we offered, probabilities must cover exactly the offered options and sum to one, and the
choice must be the argmax. This is a port of `validate_choice` from
[browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast).

**LLMs** get the same state as JSON plus a system prompt asking for the same three fields
as a JSON object. Provider default reasoning settings are used unless `--effort` is passed.

## Results

### Pooled across all six tapes (212 rows, 39 fraud)

| evaluator | rows | TP | FP | FN | precision | recall | F1 | pattern acc | errors | p50 ms | p95 ms | total cost |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| jev | 212 | 39 | 4 | 0 | 0.91 | 1.00 | 0.95 | 0.97 | 0 | 251 | 356 | $0.0183 |
| sonnet-5 | 212 | 39 | 5 | 0 | 0.89 | 1.00 | 0.94 | 1.00 | 0 | 1,902 | 3,584 | $1.1406 |
| gpt-5.6-luna | 212 | 35 | 7 | 4 | 0.83 | 0.90 | 0.86 | 1.00 | 0 | 2,069 | 3,474 | $0.0914 |
| jev+sonnet-5 | 212 | 38 | 5 | 1 | 0.88 | 0.97 | 0.93 | 0.97 | 0 | 254 | 2,153 | $0.1175 |

- vs **sonnet-5**: Jev is 8x faster at p50 and 62x cheaper
- vs **gpt-5.6-luna**: Jev is 8x faster at p50 and 5x cheaper
- vs **jev+sonnet-5**: Jev is 1x faster at p50 and 6x cheaper

### Per tape: recall on fraud rows / false positives

| tape | jev recall / FP | sonnet-5 recall / FP | gpt-5.6-luna recall / FP | jev+sonnet-5 recall / FP |
|---|--:|--:|--:|--:|
| T00-clean | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 |
| T01-ato | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 |
| T02-cardtest | 1.00 / 0 | 1.00 / 1 | 0.93 / 0 | 1.00 / 0 |
| T03-structuring | 1.00 / 3 | 1.00 / 2 | 1.00 / 4 | 1.00 / 3 |
| T04-travel | 1.00 / 1 | 1.00 / 2 | 1.00 / 3 | 1.00 / 2 |
| T05-mule | 1.00 / 0 | 1.00 / 0 | 0.67 / 0 | 0.89 / 0 |

Reading the table honestly:

- **Speed and cost** are structural. Jev answers three typed questions per row in about a
  quarter of a second and the whole 212-row run costs under two cents. Sonnet 5 takes about
  two seconds per row and costs over a dollar for the same rows.
- **Quality** is competitive on these textbook patterns: Jev catches every planted fraud row
  (recall 1.00) with the fewest false positives, and names the right pattern on 97 percent
  of true positives. Sonnet 5 matches its recall with one more false alarm. GPT-5.6 Luna
  misses four rows, three of them the early Zelle credits on the mule tape.
- **Structuring is the hard tape for everyone.** Every evaluator flags a few legitimate
  business rows on `T03` once it has seen the pattern of under-threshold deposits.
- **The confidence-gated hybrid** did not beat Jev alone on this run: one gray-zone mule row
  escalated to Sonnet came back as legitimate. It costs a tenth of Sonnet and sits at Jev's
  latency for the 90-plus percent of rows Jev decides alone.

Regenerate this section with `uv run python scripts/results_md.py` after `task e2e:record`.

## Determinism, tests and the pre-commit hook

The gateway client has a record/replay layer. Every request is hashed (path, canonical
JSON body, model); responses live in `tests/cassettes/`, one file per hash.

| `JEV_DEMO_MODE` | Behaviour |
|---|---|
| `replay` (tests default) | Serve from cassettes. A miss fails loudly. No network, no key. |
| `record` (CLI default) | Use a cassette if present, otherwise call the gateway and save. |
| `live` | Always call the gateway; never read or write cassettes. |

This is the pattern we took from jev-ultrafast ("tests must not call paid APIs", offline
pytest with a faked transport, live smoke runs kept separate), with one change: instead of
hand-written fake answers we replay real recorded model answers, so the suite tests the
real contract.

```bash
task test          # lint + unit + e2e, all offline (what pre-commit runs)
task test:e2e      # the Jev-driven e2e suite from cassettes
task e2e:record    # fill in missing cassettes from the live gateway (needs API_KEY)
task test:live     # run the e2e suite against the live gateway
task hooks:install # pre-commit install
```

The e2e suite uses Jev in two roles:

1. **System under test.** Each tape is played through Jev and held to ground truth:
   minimum recall on the planted fraud rows, a cap on false positives, pattern accuracy
   on true positives, and the typed-answer contract on every row.
2. **Test oracle.** Jev grades things a plain assertion cannot: whether each synthetic tape
   really encodes the situation its title claims (a fixture-quality gate over the whole
   stream), and whether the plain-text analyst report the pipeline emits is actionable
   (`labels_match` and `no_false_alarms` booleans plus a four-level `usefulness` score).
   Both are one Jev call with several typed questions, thresholded at the calibrated
   probability's natural 0.5 boundary. Recall and false positives are deliberately left
   to code, which checks them exactly against ground truth.

A third file guards the headline comparison itself (Jev at least 3x faster and cheaper than
both LLMs on the recorded runs, F1 within 0.15 of the best LLM), so if a re-record closes
the gap the build goes red before the slides go stale.

The pre-commit hook runs lint, the tape-sync check (committed `tapes/*.json` must equal
generator output), unit tests, and the e2e suite in replay mode. A commit takes seconds and
costs nothing. The one hook that goes to the network is the PII log check below.

## Check 1: semantic linting in pre-commit (PII in log statements)

Two runnable *checks* sit on top of the bake-off. This one is built; the second, a code
review pipeline, is scaffolded in `plan.md` and not built yet.

A regex can find every `logger.info(...)`. It cannot tell you whether the f-string inside
leaks a customer's email. That judgement is the kind of thing teams bolt an LLM onto and
then pull out again because it takes ten seconds per file and costs real money. Here it
is one typed Jev call per changed file.

`jev-demo pii-check <files>` (hook id `pii-log-check`):

1. Pulls every logging call out of the staged source files: `logger.*`, `log.*`,
   `console.*`, `slog.*`, `log.Printf`, Rust `info!`/`warn!` macros, in Python, JS/TS, Go,
   Java, Kotlin, Rust, Ruby, C#. Multi-line calls come out whole; four preceding lines ride
   along as context so `user.email` means something.
2. Sends each file's statements to Jev as **one call with one typed `choice` question per
   statement** (speculative fan-out, up to 20 per call): *what kind of personal data, if
   any, does this line write?* with a written policy (names, contact details, government
   ids, card and account numbers, credentials, IPs and geolocation, health data count;
   opaque ids, counts, durations, masked or hashed values do not).
3. Validates every answer against the typed contract, then blocks the commit when
   `P(pii) >= 0.5`, prints a WARN for the 0.35 to 0.5 gray zone, and stays quiet otherwise.

```bash
task pii:demo                 # clean fixtures pass, leaky fixtures block
task pii:check -- src/**/*.py # any files you like, verbose
```

To see the hook fire on a real commit, add a line such as
`logger.info("reminder sent to %s", user.email)` to any `.py` file, `git add` it and
`git commit`. The hook prints the offending `file:line`, the kind of data, the probability,
and exits 1. Redact the line and the same commit goes through.

Recorded run over the demo fixtures (`results/pii-check.json`, four files in Python,
TypeScript and Go, 26 log statements, 7 planted leaks):

| statements | files | Jev calls | leaks caught | false positives | p50 per call | wall clock | cost |
|--:|--:|--:|--:|--:|--:|--:|--:|
| 26 | 4 | 4 | 7 / 7 | 0 | 594 ms | 765 ms | $0.00048 |

The fixtures live in `tests/fixtures/pii/` with ground truth in `expected.json`;
`tests/e2e/test_pii_check.py` holds the check to that truth from cassettes, so the hook's
own behaviour is covered by the offline suite even though the hook itself runs live. Note
what passes: a masked email, a sha256 of a user key, a card brand label, opaque
`customer_id` and `txn_id` fields. A keyword scanner false-alarms on all of them.

The hook is the one exception to "every hook is offline": it needs `API_KEY` (read from
`.env`) and runs in `live` mode so it never writes cassettes for your work-in-progress
code. If the gateway is unreachable it exits 2 with a plain message instead of a traceback.

## Layout

```
src/jev_demo/
  models.py      Transaction, AccountProfile, Tape, Verdict, Metrics
  tapes.py       seeded generators for the six situations + loader
  gateway.py     Vercel AI Gateway client, chat + evaluation dialects, record/replay
  evaluators.py  JevEvaluator, LLMEvaluator, ConfidenceGatedEvaluator, contract checks
  runner.py      stream semantics, play_tape, scoring, pooling
  report.py      rich tables and the plain-text analyst report
  pii_check.py   semantic lint: log-statement extraction + Jev PII classification (pre-commit hook)
  cli.py         jev-demo tapes | play | play-all | generate-tapes | pii-check
tapes/           committed tape JSON (regenerate with task tapes:generate)
tests/unit       offline contract tests
tests/e2e        Jev-driven e2e suite (replay by default)
tests/fixtures/pii  demo services with known clean and leaking log lines + expected.json
tests/cassettes  recorded gateway responses
results/         last CLI run, per tape + pooled summary, pii-check.json
slides-outline.md  brief for the slide deck
plan.md          scaffold for check 2, a Jev-driven code review pipeline (not built yet)
```

## Caveats worth saying out loud

- Tapes are synthetic and small (212 rows, 39 fraud). They demonstrate behaviour on
  textbook patterns; they are not a benchmark of production fraud performance.
- Latency is wall-clock through the gateway from this machine, at concurrency 8.
- Cost uses the gateway's listed per-token prices at record time.
- Jev is `typesafe-ai/jev` on the gateway, which resolves to the current release. For a
  production threshold you would pin a version.
- TypeSafe documents calibration, not bit-for-bit determinism. The replayed suite is
  deterministic because it replays; `task test:live` includes an empirical repeatability
  check.
