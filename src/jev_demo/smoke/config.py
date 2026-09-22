"""Load a smoke-test definition from TOML.

A definition is a site plus a list of scenarios. Each scenario is written in
English: a ``goal`` the agent pursues and ``success_criteria`` Jev judges the
final page against. The ``expected_*`` fields are the deterministic half, checked
by code after Jev says the goal is reached.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Site:
    name: str
    base_url: str
    start_path: str = "/"

    @property
    def host(self) -> str:
        return urlparse(self.base_url).netloc.lower()


@dataclass(frozen=True)
class Scenario:
    id: str
    goal: str
    success_criteria: str
    expected_path_prefix: str | None = None
    expected_text: tuple[str, ...] = ()
    start_path: str | None = None
    max_steps: int = 8
    goal_threshold: float = 0.7
    forbid_form_input: bool = True
    stay_on_site: bool = True


@dataclass(frozen=True)
class SmokeConfig:
    site: Site
    scenarios: tuple[Scenario, ...]
    path: Path | None = field(default=None, compare=False)


def _clean(text: str) -> str:
    return " ".join(text.split())


def load_config(path: str | Path) -> SmokeConfig:
    p = Path(path)
    raw = tomllib.loads(p.read_text())
    site_raw = raw["site"]
    site = Site(
        name=site_raw["name"],
        base_url=site_raw["base_url"].rstrip("/"),
        start_path=site_raw.get("start_path", "/"),
    )
    defaults = raw.get("defaults", {})
    scenarios = []
    for s in raw.get("scenarios", []):
        scenarios.append(
            Scenario(
                id=s["id"],
                goal=_clean(s["goal"]),
                success_criteria=_clean(s["success_criteria"]),
                expected_path_prefix=s.get("expected_path_prefix"),
                expected_text=tuple(t.lower() for t in s.get("expected_text", [])),
                start_path=s.get("start_path"),
                max_steps=int(s.get("max_steps", defaults.get("max_steps", 8))),
                goal_threshold=float(s.get("goal_threshold", defaults.get("goal_threshold", 0.7))),
                forbid_form_input=bool(
                    s.get("forbid_form_input", defaults.get("forbid_form_input", True))
                ),
                stay_on_site=bool(s.get("stay_on_site", defaults.get("stay_on_site", True))),
            )
        )
    if not scenarios:
        raise ValueError(f"{p}: no [[scenarios]] defined")
    ids = [s.id for s in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{p}: duplicate scenario ids {ids}")
    return SmokeConfig(site=site, scenarios=tuple(scenarios), path=p)


__all__ = ["Scenario", "Site", "SmokeConfig", "load_config"]
