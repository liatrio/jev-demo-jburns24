# Agent notes

## Git hooks

This repo uses the [pre-commit](https://pre-commit.com) framework. Hooks are defined in
`.pre-commit-config.yaml` and are offline and deterministic (lint, tape sync, unit and
cassette-replay e2e tests).

Before committing, make sure the hooks are installed:

```sh
task hooks:install   # or: uv tool run pre-commit install
```

Do not bypass hooks with `--no-verify`. If a hook fails, fix the cause and commit again.
