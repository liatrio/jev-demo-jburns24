# Demo playbook

Commands you can run from a fresh clone of `main`. Everything assumes `uv` and `task` are
installed and a Vercel AI Gateway key is in `.env`, unless a line says otherwise. For the
story behind each experiment see the README; for the slide-by-slide runbook see
`slides-outline.md`.

## Setup once

```bash
git clone https://github.com/liatrio/jev-demo-jburns24 && cd jev-demo-jburns24
cp .env.example .env            # paste API_KEY=...
task setup
task browser:install            # Chromium for the swarm (or export JEV_SWARM_CHROMIUM=/path/to/chrome)
task hooks:install              # pre-commit + pre-push hooks
```

Modes: `JEV_DEMO_MODE=record` (the CLI default) replays any request that already has a
cassette and calls the gateway only for new ones, so demos of the committed tapes are
instant and free. Add `--mode live` to force real calls and real latency numbers.

## Experiment 1: fraud bake-off

```bash
task tapes:list                                          # what each of the six tapes plants
task play -- T01                                         # account takeover, jev vs sonnet vs gpt, replayed (free)
task play -- T05 -e jev,sonnet,gpt,hybrid --mode live    # mule tape, real latency, adds the gated hybrid
task play:all                                            # pooled table across all tapes
task demo                                                # everything live, no cassettes (about $1.30)
uv run python scripts/report_html.py                     # rebuild results/report.html from results/
```

What you see: a card per tape, a live progress line per evaluator, then a metrics table
(precision, recall, F1, pattern accuracy, p50 and p95 latency, cost) and a disagreement
table listing the rows where the evaluators split. `play:all` ends with the pooled table
and the "Jev is Nx faster and Nx cheaper" line.

## Experiment 2: PII log lint

```bash
task pii:demo                                            # clean fixtures pass, leaky fixtures block
task pii:check -- src/jev_demo/*.py                      # scan any files you like, verbose
```

To see the hook itself fire, add a leaking log line in a new lint-clean file and commit.
The file has to pass ruff, which runs first, so the PII hook is the one that fails:

```bash
cat > src/jev_demo/reminders.py <<'EOF'
import logging

logger = logging.getLogger(__name__)


def remind(user) -> None:
    logger.info("reminder sent to %s", user.email)
EOF
git add -A && git commit -m "demo"          # blocked by pii-log-check
sed -i 's/user.email/user.id/' src/jev_demo/reminders.py
git add -A && git commit -m "demo"          # passes
git reset --hard HEAD~1                     # clean up
```

What you see: every statement printed as `ok` with its P(pii), or `BLOCK` with the kind of
data (identity, credential, financial, network_location), the probability and the code.
Footer: statement count, files, Jev calls, wall clock (about a second), cost (under a
twentieth of a cent). The hook needs `API_KEY` in `.env` and always runs live.

## Experiment 3: adversarial swarm

```bash
task swarm:demo                              # 200 agents, all 7 regressions injected, replayed, no key needed
task swarm                                   # what pre-push runs: 200 agents vs the clean portal
task swarm -- --inject idor,dead_link        # plant two regressions, watch only those come back
task swarm:live -- --inject all              # every Jev call live, real timing and cost (about 5 cents)
```

Headed mode, for watching the agents work:

```bash
task swarm:demo:headed                       # 4 visible Chromium windows, all regressions, replayed, 400 ms per action
task swarm:headed -- --inject idor           # a few agents live against one planted bug
AGENTS=8 SLOW_MO=200 task swarm:demo:headed  # more windows, faster
uv run jev-demo swarm --agents 2 --headed --slow-mo 1000 --inject xss_search --no-save   # raw CLI, one bug, very slow
```

What you see: a progress line every 25 agents, then one row per deduplicated finding with
severity, category, path, the number of agents that reproduced it, P(defect), the automatic
checks that fired and BLOCK or report. Footer: agents, browser steps, Jev calls (replayed vs
live), wall time, cost, PASS or FAIL. Exit code 1 on FAIL is what blocks the push.

Headed tasks need a display. On a server, prefix with `xvfb-run -a`.

## Tests and hooks

```bash
task test                                    # lint + unit + e2e replay, all offline
task test:e2e                                # just the Jev-driven e2e suite from cassettes
task test:live                               # e2e suite against the live gateway
```

## If something is off

- Gateway down: every `task play*` and `task swarm*` command works in the default record
  mode from cassettes, with the recorded latencies.
- Swarm cannot launch Chromium: `task browser:install`, or point `JEV_SWARM_CHROMIUM` at an
  existing Chrome or Chromium binary.
- Swarm reports a cassette miss in replay: the portal or the agents changed. Run
  `task swarm:record` (needs `API_KEY`) and commit the new cassettes.
