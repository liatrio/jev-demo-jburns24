# Agent notes

## Git hooks

This repo uses the [pre-commit](https://pre-commit.com) framework. Hooks are defined in
`.pre-commit-config.yaml`. The pre-commit stage is offline and deterministic (lint, tape
sync, unit and cassette-replay e2e tests), with one exception: `pii-log-check` asks Jev live
whether any staged log statement writes personal data. It needs `API_KEY` in `.env`, takes
about a second, and blocks the commit on a hit. Fix or redact the log line rather than
skipping the hook. Its own fixtures under `tests/fixtures/pii/` are excluded from the hook
on purpose. The pre-push stage runs the adversarial swarm (`jev-demo swarm`, 200 Jev-driven
Chromium agents against the demo portal); it replays cassettes for known page states and
calls Jev live only for new ones.

Before committing, make sure both hook stages are installed:

```sh
task hooks:install   # or: uv tool run pre-commit install --hook-type pre-commit --hook-type pre-push
```

Do not bypass hooks with `--no-verify`. If a hook fails, fix the cause and commit again.
If the swarm reports a cassette miss, the portal or the agents changed: run
`task swarm:record` (needs `API_KEY`) and commit the new cassettes with the change.
The swarm needs a Chromium Playwright can launch: `task browser:install`, or set
`JEV_SWARM_CHROMIUM` to an existing binary.
