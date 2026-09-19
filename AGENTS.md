# Agent notes

## Git hooks

This repo uses the [pre-commit](https://pre-commit.com) framework. Hooks are defined in
`.pre-commit-config.yaml` and are offline and deterministic (lint, tape sync, unit and
cassette-replay e2e tests), with one exception: `pii-log-check` asks Jev live whether any
staged log statement writes personal data. It needs `API_KEY` in `.env`, takes about a
second, and blocks the commit on a hit. Fix or redact the log line rather than skipping the
hook. Its own fixtures under `tests/fixtures/pii/` are excluded from the hook on purpose.

Before committing, make sure the hooks are installed:

```sh
task hooks:install   # or: uv tool run pre-commit install
```

Do not bypass hooks with `--no-verify`. If a hook fails, fix the cause and commit again.
