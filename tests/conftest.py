"""Shared fixtures.

The e2e suite runs in ``replay`` mode by default: every gateway request is
served from ``tests/cassettes`` and no network or API key is needed, so the
suite is deterministic and safe for a pre-commit hook. Set
``JEV_DEMO_MODE=record`` to fill in missing cassettes from the live gateway,
or ``JEV_DEMO_MODE=live`` to bypass cassettes entirely.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

from jev_demo.gateway import Gateway
from jev_demo.tapes import load_all


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    mode = os.environ.get("JEV_DEMO_MODE", "replay")
    if mode == "live":
        return
    skip_live = pytest.mark.skip(reason="live tests only run with JEV_DEMO_MODE=live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(scope="session")
def mode() -> str:
    return os.environ.get("JEV_DEMO_MODE", "replay")


@pytest.fixture
async def gateway(mode: str) -> AsyncIterator[Gateway]:
    async with Gateway(mode=mode) as gw:  # type: ignore[arg-type]
        yield gw


@pytest.fixture(scope="session")
def tapes():
    return load_all()
