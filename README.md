# jev-demo: experiments with TypeSafe's Jev

1. **Jev vs generative LLMs on fraud-classification tapes** (below): Jev as an inline
   typed classifier and as a test oracle, with a deterministic, offline e2e suite.
2. **Semantic linting in pre-commit** ([jump](#check-1-semantic-linting-in-pre-commit-pii-in-log-statements)):
   one live Jev call per changed file asks whether any log statement writes PII.
3. **An adversarial e2e swarm on pre-push** ([jump](#experiment-2-an-adversarial-e2e-swarm-on-pre-push)):
   200 Jev-driven browser agents attack the site before every push, built on the same
   record/replay pattern.

## Experiment 1: Jev vs generative LLMs on fraud-classification tapes

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
| jev | 212 | 39 | 5 | 0 | 0.89 | 1.00 | 0.94 | 0.97 | 1 | 269 | 620 | $0.0182 |
| sonnet-5 | 212 | 38 | 6 | 1 | 0.86 | 0.97 | 0.92 | 1.00 | 0 | 2,188 | 3,103 | $1.1424 |
| gpt-5.6-luna | 212 | 33 | 6 | 6 | 0.85 | 0.85 | 0.85 | 1.00 | 0 | 2,068 | 3,695 | $0.0914 |
| jev+sonnet-5 | 212 | 38 | 3 | 1 | 0.93 | 0.97 | 0.95 | 0.97 | 1 | 266 | 2,308 | $0.1156 |

- vs **sonnet-5**: Jev is 8x faster at p50 and 63x cheaper
- vs **gpt-5.6-luna**: Jev is 8x faster at p50 and 5x cheaper
- vs **jev+sonnet-5**: Jev is 1x faster at p50 and 6x cheaper

### Per tape: recall on fraud rows / false positives

| tape | jev recall / FP | sonnet-5 recall / FP | gpt-5.6-luna recall / FP | jev+sonnet-5 recall / FP |
|---|--:|--:|--:|--:|
| T00-clean | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 |
| T01-ato | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 | 1.00 / 0 |
| T02-cardtest | 1.00 / 0 | 1.00 / 1 | 0.93 / 0 | 1.00 / 0 |
| T03-structuring | 1.00 / 4 | 1.00 / 3 | 0.89 / 4 | 1.00 / 1 |
| T04-travel | 1.00 / 1 | 1.00 / 2 | 1.00 / 2 | 1.00 / 2 |
| T05-mule | 1.00 / 0 | 0.89 / 0 | 0.56 / 0 | 0.89 / 0 |

Reading the table honestly:

- **Speed and cost** are structural. Jev answers three typed questions per row in about a
  quarter of a second and the whole 212-row run costs under two cents. Sonnet 5 takes about
  two seconds per row and costs over a dollar for the same rows.
- **Quality** is competitive on these textbook patterns: Jev catches every planted fraud row
  (recall 1.00) and names the right pattern on 97 percent of true positives. Sonnet 5 misses
  one mule row and adds one more false alarm. GPT-5.6 Luna misses six rows, four of them the
  early Zelle credits on the mule tape. One Jev row hit a gateway connection error and is
  scored as "not fraud".
- **Structuring is the hard tape for everyone.** Every evaluator flags a few legitimate
  business rows on `T03` once it has seen the pattern of under-threshold deposits.
- **The confidence-gated hybrid** trades one missed mule row (a gray-zone escalation Sonnet
  called legitimate) for three fewer false positives on structuring, edging Jev alone on F1.
  It costs a tenth of Sonnet and sits at Jev's latency for the 90-plus percent of rows Jev
  decides alone.

Regenerate this section with `uv run python scripts/results_md.py` after `task e2e:record`.

A single-page HTML report covering all three experiments (cost, speed and enterprise value,
with the latest live numbers from `results/`) is at `results/report.html`; rebuild it with
`uv run python scripts/report_html.py`.

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
costs nothing. The one pre-commit hook that goes to the network is the PII log check below;
the pre-push hook runs experiment 2's swarm (the section after it).

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


## Experiment 2: an adversarial e2e swarm on pre-push

The bake-off used Jev as a *classifier* and as a *test oracle*, and the PII check uses it as
a *linter*. This experiment uses it
the way [jev-ultrafast](https://github.com/browser-use/jev-ultrafast) does, as the
decision loop of a browser agent, and then asks: if one Jev-driven browser agent costs a
quarter of a second and a hundredth of a cent per step, why run one? Run two hundred, each
with a different adversarial goal, against the site you are about to push, and let them
try to break it.

```bash
task browser:install     # once per machine (or set JEV_SWARM_CHROMIUM)
task swarm:demo          # 200 agents vs the portal with every known regression injected, replayed
task swarm               # what the pre-push hook runs: 200 agents vs the clean portal
task swarm -- --inject idor,dead_link   # plant specific regressions
```

### What it attacks

`src/jev_demo/swarm/target.py` is a small online-banking portal (accounts, transfers,
search, profile, statements) served from the standard library on an ephemeral port for
the duration of the run. It is the "system under test" stand-in: deterministic HTML, no
timestamps or random ids, and per-session state so hundreds of agents can hit it at once
without seeing each other's balances. Seven regressions can be injected by name:

| regression | what it breaks |
|---|---|
| `negative_transfer` | a negative amount is accepted and money moves backwards |
| `overdraft` | a transfer larger than the balance goes through; the balance goes negative |
| `idor` | `/accounts/2001` shows another customer's account, routing number and SSN digits |
| `xss_search` | the search page echoes the query unescaped |
| `dead_link` | the Statements link in the navigation returns 404 |
| `stack_trace` | a memo containing a quote returns a 500 with a Python traceback |
| `unicode_crash` | saving a non-ASCII profile name returns a 500 |

### How an agent works (the jev-ultrafast loop)

Each agent is a deterministic function of its integer id: `id % 8` picks one of eight
adversarial personas (negative-money, overdraft, url-tamperer, injector, link-walker,
empty-hands, edge-text, bookkeeper), and a `random.Random(id)` shuffles that persona's
payloads and picks its starting page. Then, for up to six steps:

1. **Code observes.** Playwright extracts a compact observation: path, HTTP status,
   heading, status message, visible text, the interactive elements with their current
   values, and a few deterministic checks (HTTP 5xx/404, a traceback on the page, a
   negative USD amount, the injected `<img onerror>` payload having actually executed).
2. **Code enumerates the actions.** Every link, button, select option, and each text
   field paired with up to three of the persona's payloads becomes a concrete, executable
   candidate ("type `'-500'` into Amount (USD)", "edit the URL to open /accounts/2001",
   "stop"). Actions already taken on an unchanged form are removed by code, not by the
   model.
3. **Jev decides.** One evaluation call with speculative fan-out: a `choice` over the
   candidates, two booleans about the current page (`page_is_broken`,
   `wrong_behaviour`) and a four-level `severity` score. The choice is validated against
   the typed contract before it counts; a violation is logged and code falls back to the
   argmax.
4. **Code acts**, observes again, and loops.

No free text is generated at any point. Jev never writes a test, a selector or a report;
it picks from options code offers and grades what code shows it.

### Triage, dedupe and the gate

Two hundred agents produce a long tail of "this looked odd". Every step where code saw a
hard signal or Jev's booleans crossed 0.6 becomes an incident with its before/after
evidence. Incidents are grouped by (path, checks), the lowest-numbered agent in each
group represents it, and Jev triages that one with three more typed questions:
`is_defect`, a six-way `category` (none, cosmetic, broken_page, validation_gap,
security, money_loss) and a release-blocking `severity`. Harness facts (a 500, an
executed payload) are passed to Jev as facts and short-circuit the `is_defect` gate; the
model only has to name and grade them. Findings dedupe to one row per fingerprint with
the list of agents that reproduced it. A finding blocks the push when it is
`broken_page`, `security` or `money_loss` at severity 2 or higher and is not in
`tests/swarm-baseline.json`.

### Recorded results

From `results/swarm/`, 200 agents at concurrency 16, Chromium headless, through the
gateway from this machine:

| run | agents | browser steps | Jev calls | wall | cost | outcome |
|---|--:|--:|--:|--:|--:|---|
| clean portal | 200 | 815 | 815 | 37 s | $0.0495 | PASS, no findings |
| all seven regressions injected | 200 | 806 | 813 | 38 s | $0.0481 | FAIL, 6 blocking findings, 1 report-only |

Findings on the injected run (`results/swarm/injected-findings.md`):

| sev | category | path | agents | p(defect) | automatic checks | status |
|--:|---|---|--:|--:|---|---|
| 2 | money_loss | `/transfer` | 48 | 0.93 | negative_amount_displayed | **BLOCK** |
| 2 | broken_page | `/statements` | 25 | 0.92 | http_404 | **BLOCK** |
| 2 | security | `/accounts/2001` | 16 | 0.95 | - | **BLOCK** |
| 2 | broken_page | `/transfer` | 12 | 0.90 | http_500, stack_trace_exposed | **BLOCK** |
| 2 | broken_page | `/profile` | 3 | 0.93 | http_500, stack_trace_exposed | **BLOCK** |
| 2 | security | `/search` | 3 | 0.89 | script_injection_executed | **BLOCK** |
| 2 | validation_gap | `/transfer` | 1 | 0.88 | - | report |

All seven planted regressions are found (negative transfer and overdraft share the
`money_loss:/transfer` fingerprint; both are a missing amount check on the same form).
The report-only row is a zero-amount transfer being accepted, which is real but not
release-blocking. The clean run confirms no defect, so the gate has no false positives
on this site at these thresholds. Median Jev latency per step was 283 ms at record time.

### Why this is a pre-push hook and not a nightly job

The swarm costs about five cents and forty seconds, so it runs where a developer will
actually act on the result: before the push. The record/replay layer from experiment 1
makes it cheap to keep there. Every Jev request is keyed on the observation, and the
observation is deterministic, so a page the swarm has seen before is replayed from
`tests/cassettes` and only genuinely new page states go to the gateway. An unchanged site
is free and offline; a changed one costs cents for the pages that changed.

```
pre-commit stage   lint, unit, e2e replay (seconds, offline)           <- every commit
pre-push stage     jev-demo swarm --agents 200 (40 s, replay + live)   <- every push
```

`tests/e2e/test_swarm.py` runs a 48-agent slice of both scenarios in pure replay on
every commit, so the swarm's own regressions (a false positive on the clean portal, a
missed injected bug) are caught before the hook is trusted.

### Caveats specific to the swarm

- The portal is a stand-in, so the bugs are textbook. The mechanism (agents, triage,
  gate) is the demo; pointing it at a real staging URL is the follow-up.
- Coverage is a function of persona design. Two of the seven regressions were found by
  only three agents out of two hundred, which says the payload lists matter more than the
  agent count.
- Determinism is by construction, not by promise: the observation excludes anything
  timing-dependent (console errors arrive asynchronously and were removed for that
  reason). A site with live data needs a fixture mode to be replayable.
- One agent in two hundred hit a typed-contract violation (the chosen option was not the
  argmax after rounding). Code falls back to the argmax and counts the violation.

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
  cli.py         jev-demo tapes | play | play-all | generate-tapes | pii-check | swarm
  swarm/
    target.py    the portal under test, with injectable regressions
    browser.py   Playwright observation + action execution, deterministic checks
    agent.py     personas, action space, the Jev decision loop
    triage.py    Jev triage, dedupe, baseline, blocking rule
    run.py       orchestration, report, results/swarm writer
tapes/           committed tape JSON (regenerate with task tapes:generate)
tests/unit       offline contract tests (incl. the portal and the action space)
tests/e2e        Jev-driven e2e suite (replay by default), incl. a 48-agent swarm slice
tests/fixtures/pii  demo services with known clean and leaking log lines + expected.json
tests/cassettes  recorded gateway responses (tapes, pii check and swarm)
tests/swarm-baseline.json  accepted swarm findings (empty)
results/         last CLI run, per tape + pooled summary, pii-check.json; results/swarm for the swarm
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
