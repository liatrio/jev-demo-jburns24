# Plan: Check 2, a Jev-driven code review pipeline

Status: **scaffold only, not built.** This document is the design for the second runnable
check on top of the bake-off. Check 1 (the PII log lint in `src/jev_demo/pii_check.py`) is
built and is the template: regex does the cutting, Jev does the judging, code does the
thresholding, cassettes make it testable offline.

## Goal

Review a pull request diff with typed questions instead of prose. Every hunk gets a small
set of calibrated verdicts (does it need a test, does it change behaviour the PR title does
not mention, does it leak PII or secrets, does it violate a named house rule), the PR gets
a few whole-diff verdicts (does the description match the change, is the risk level right),
and code turns those into a pass/fail check plus a short comment. Target: a 400-line diff
reviewed in a few seconds for well under a cent, deterministic in replay, so it can run on
every push, not just on demand.

What this is not: it does not write review prose, propose patches or replace a human. It
produces the checklist a good reviewer runs before reading closely, and it can hold the
merge on the items that are objectively checkable.

## Shape of the pipeline

```
git diff base..head
  -> split into hunks (file, range, added/removed lines, ~30 lines of context each side)
  -> per-file batch: one Jev call, N hunks x K typed questions (speculative fan-out)
  -> per-PR call: title + description + list of files touched + hunk summaries
  -> code: threshold, aggregate, decide
  -> outputs: exit status, results/review-<sha>.json, a markdown comment, optional
     GitHub check-run / PR comment
```

Same `Gateway` class, same `validate_choice` contract check, same record/replay layer,
same `JEV_DEMO_MODE` semantics. New module `src/jev_demo/review.py`, new CLI verb
`jev-demo review`.

## Per-hunk questions (one Jev call per file, all hunks and questions in parallel)

State sent per file:

```
{
  "pr": { "title", "description" },
  "file": "path",
  "language": "python",
  "hunks": {
    "h0": { "header": "@@ -12,7 +12,9 @@", "before": "...", "after": "...", "context": "..." },
    ...
  },
  "house_rules": [ "...", "..." ]          # from .jev-review.yaml, see below
}
```

Questions per hunk `hN` (the K in N x K; keep K at 5 or fewer so a 20-hunk file stays
under 100 questions and well inside the 64k token budget):

| key | type | criteria / instructions | what code does with it |
|---|---|---|---|
| `hN_kind` | choice | `behaviour_change`, `refactor_no_behaviour_change`, `test_only`, `docs_or_comments`, `config_or_build`, `generated` | groups hunks; drives which other answers matter |
| `hN_needs_test` | boolean | "This hunk changes runtime behaviour in a way that a unit test could observe, and no test in the diff exercises it." | warn; block if `behaviour_change` and P > 0.8 and the PR adds no test files |
| `hN_secret_or_pii` | choice | reuse `PII_KINDS` from `pii_check.py` plus `hardcoded_secret` | block at P(none) < 0.5, identical to check 1 |
| `hN_rule_violation` | choice | `none` plus one option per house rule, each option's text is the rule | block when the rule is marked `blocking: true` in config |
| `hN_risk` | score | 4 levels: cosmetic, low, medium (touches error handling, auth, money, or concurrency), high (data loss or security relevant) | sets the PR risk label; medium and high get listed in the comment |

## Per-PR questions (one call)

State: title, description, list of files with their `hN_kind` tallies, and the first line of
every `behaviour_change` hunk.

| key | type | instructions | use |
|---|---|---|---|
| `description_matches_change` | boolean | "The description accurately describes what the diff does, including every behaviour change." | warn below 0.5; comment names the unmentioned hunks |
| `scope` | choice | `single_purpose`, `two_unrelated_changes`, `many_unrelated_changes` | warn on anything but single purpose |
| `overall_risk` | score | same 4 levels as `hN_risk` | label; block above a configured level only if the repo opts in |
| `ready_for_human_review` | boolean | "A reviewer could approve this after reading it once, with no blocking findings above." | headline of the comment |

## Decision rules (all in code, all readable)

- **Block**: any `hN_secret_or_pii` flagged; any blocking house-rule violation; a
  `behaviour_change` hunk with `needs_test` above 0.8 in a PR that adds no tests.
- **Warn** (comment, exit 0): needs_test between 0.5 and 0.8; description mismatch; scope
  not single purpose; any hunk at risk level 3.
- **Gray zone**: probabilities between 0.35 and 0.65 are listed as "borderline" and never
  block, mirroring `WARN_THRESHOLD` in check 1. Optional escalation: send borderline hunks
  to Sonnet 5 with the same questions as JSON, the `ConfidenceGatedEvaluator` pattern.
- Thresholds live in `.jev-review.yaml` at the repo root with the house rules:

```yaml
rules:
  - id: no-print-in-src
    text: "Production code under src/ must use the logger, never print()."
    blocking: true
  - id: money-is-int-cents
    text: "Monetary amounts are integers in cents, never floats."
    blocking: true
  - id: public-api-docstring
    text: "New public functions have a docstring."
    blocking: false
thresholds:
  block: 0.5
  warn: 0.35
  needs_test_block: 0.8
```

## Outputs

1. Exit status: 0 pass, 1 blocking findings, 2 could not get a verdict.
2. `results/review-<short-sha>.json`: every hunk, every answer, every probability, cost and
   latency per call. This is what the tests and the slides read.
3. Markdown comment (`--comment` prints it, `--post` sends it): headline verdict, blocking
   list with `file:line`, warnings, per-file risk table, one line of cost and timing.
4. Optional GitHub check-run via the API when `GITHUB_TOKEN` is present (CI mode).

## Where it runs

- **Locally**: `jev-demo review --base main` on the current branch, `task review`.
- **pre-push hook**: same command, blocking on the block list only.
- **CI**: a GitHub Actions job on `pull_request` that runs `jev-demo review --base
  origin/${{ github.base_ref }} --post`. Live mode, `API_KEY` from secrets. Cassettes are
  not used in CI because every diff is new; the offline tests below cover the code paths.

## Tests and demo fixtures

Fixtures under `tests/fixtures/review/`: a handful of tiny synthetic PRs, each a
`base/` and `head/` directory pair plus `pr.json` (title, description) and `expected.json`
(which hunks block, which warn, overall verdict). Planned cases:

| fixture | plants | expected |
|---|---|---|
| `P00-clean-refactor` | rename plus test update, honest description | pass, single purpose, low risk |
| `P01-untested-behaviour` | new branch in money rounding, no test, description says "typo fix" | block on needs_test, warn on description |
| `P02-secret-in-config` | hardcoded token in a settings file | block on `hardcoded_secret` |
| `P03-print-in-src` | `print(customer.email)` added under `src/` | block on house rule and on PII, same hunk |
| `P04-two-features` | unrelated endpoint plus dependency bump | pass with scope warning |
| `P05-borderline` | log line with `last4` of a card | not blocked; listed as borderline |

Tests:

- `tests/unit/test_review_split.py`: hunk splitting from `git diff` output, context sizing,
  question builder shape, decision rules against hand-written answer dicts, YAML config
  loading and defaults.
- `tests/e2e/test_review.py`: every fixture PR through the real pipeline in replay mode,
  held to `expected.json`; one call per file; whole run under a cent.
- A live repeatability test (`live` marker) running `P01` three times and asserting the
  blocking set is stable.

## Demo script for Jeff

1. `task review:demo` runs all six fixture PRs and prints six verdict lines with timing.
2. Open a branch, add `print(customer.email)` to a file under `src/`, `git push`; the
   pre-push hook blocks with `file:line`, the rule id and the PII kind.
3. Show `results/review-<sha>.json` and the markdown comment; point at the probabilities.
4. Compare with the generative version: the same six PRs through Sonnet 5 asking for the
   same fields as JSON, `--evaluators jev,sonnet`, print the latency and cost side by side
   (reuse `metrics_table`). This is the slide.

## Build order (each step commits green)

1. `review.py`: diff parsing and hunk splitting, unit tests, no network.
2. Question builders and decision rules against hand-written answers, unit tests.
3. Gateway wiring, `jev-demo review` CLI, run against `P00` and `P01` live, fix prompts.
4. Remaining fixtures, record cassettes, e2e test, `expected.json` locked.
5. Markdown comment and `results/` output, `task review`, `task review:demo`.
6. pre-push hook entry and the GitHub Actions workflow.
7. Sonnet comparison run and the slide numbers.

## Open questions

- Hunk granularity: per-hunk is cheap and precise but loses cross-hunk context (a test added
  in another file). The per-PR call carries file tallies to compensate; check whether that
  is enough on `P01`, or whether the per-file state should include the names of test files
  in the diff.
- House rules as choice options cap the rule count at roughly ten per repo before the
  question gets unwieldy. Past that, one boolean per rule per hunk, or a first choice
  question that picks the relevant rule family.
- Whether to block on `overall_risk` at all. Default no: risk is a label for the human,
  the objective findings are what block.
- Token budget: 64k total per call. A 20-hunk file with 30 lines of context each side is
  fine; a generated 5,000-line lockfile is not, so `generated` files are detected by path
  and skipped before the call.
