"""Run every scenario in a smoke config, report, save."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from ..gateway import Gateway
from .agent import ScenarioResult, SitePool, run_scenario
from .config import SmokeConfig, load_config

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RESULTS = ROOT / "results" / "smoke"

console = Console()


class ScenarioSummary(BaseModel):
    id: str
    passed: bool
    steps: int
    final_path: str
    final_heading: str
    goal_probability: float
    checks_failed: list[str]
    errors: list[str]
    p50_ms: float
    cost_usd: float
    path_taken: list[str]


class SmokeSummary(BaseModel):
    site: str
    base_url: str
    config: str
    scenarios: list[ScenarioSummary]
    jev_calls: int
    network_calls: int
    replays: int
    wall_seconds: float
    cost_usd: float

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios)

    @property
    def failed_ids(self) -> list[str]:
        return [s.id for s in self.scenarios if not s.passed]


def summarise(r: ScenarioResult) -> ScenarioSummary:
    return ScenarioSummary(
        id=r.id,
        passed=r.passed,
        steps=len(r.steps),
        final_path=r.final_path,
        final_heading=r.final_heading,
        goal_probability=round(r.goal_probability, 2),
        checks_failed=r.checks_failed,
        errors=r.errors,
        p50_ms=round(statistics.median(r.latency_ms), 1) if r.latency_ms else 0.0,
        cost_usd=r.cost_usd,
        path_taken=[s.path for s in r.steps] + ([r.final_path] if r.steps else []),
    )


async def run_smoke(
    config: SmokeConfig | str | Path,
    *,
    only: set[str] | None = None,
    mode: str | None = None,
    concurrency: int = 3,
    headed: bool = False,
    slow_mo_ms: int = 0,
    on_scenario_done=None,
) -> tuple[SmokeSummary, list[ScenarioResult]]:
    cfg = config if isinstance(config, SmokeConfig) else load_config(config)
    scenarios = [s for s in cfg.scenarios if not only or s.id in only]
    if not scenarios:
        raise ValueError(f"no scenarios match {sorted(only or [])}")
    started = time.perf_counter()
    sem = asyncio.Semaphore(1 if headed else concurrency)

    async with (
        Gateway(mode=mode, concurrency=8) as gateway,  # type: ignore[arg-type]
        SitePool(cfg.site, headed=headed, slow_mo_ms=slow_mo_ms) as pool,
    ):

        async def one(scenario) -> ScenarioResult:  # noqa: ANN001
            async with sem:
                tab = await pool.open()
                try:
                    result = await run_scenario(scenario, cfg.site, tab, gateway)
                finally:
                    await tab.close()
            if on_scenario_done:
                on_scenario_done(result)
            return result

        results = list(await asyncio.gather(*(one(s) for s in scenarios)))
        summary = SmokeSummary(
            site=cfg.site.name,
            base_url=cfg.site.base_url,
            config=str(cfg.path) if cfg.path else "<inline>",
            scenarios=[summarise(r) for r in results],
            jev_calls=sum(r.jev_calls for r in results),
            network_calls=gateway.network_calls,
            replays=gateway.replays,
            wall_seconds=round(time.perf_counter() - started, 1),
            cost_usd=sum(r.cost_usd for r in results),
        )
    return summary, results


# ------------------------------------------------------------------- report
def print_report(summary: SmokeSummary, results: list[ScenarioResult]) -> None:
    t = Table(title=f"Smoke: {summary.site} ({summary.base_url})", show_lines=False)
    for col, just in (
        ("scenario", "left"),
        ("steps", "right"),
        ("landed on", "left"),
        ("heading", "left"),
        ("P(goal)", "right"),
        ("p50 ms", "right"),
        ("cost", "right"),
        ("result", "left"),
    ):
        t.add_column(col, justify=just)  # type: ignore[arg-type]
    for s in summary.scenarios:
        verdict = "[bold green]PASS[/bold green]" if s.passed else "[bold red]FAIL[/bold red]"
        t.add_row(
            s.id,
            str(s.steps),
            s.final_path,
            s.final_heading[:40],
            f"{s.goal_probability:.2f}",
            f"{s.p50_ms:,.0f}",
            f"${s.cost_usd:.4f}",
            verdict,
        )
    console.print(t)
    for r in results:
        console.print(f"[bold]{r.id}[/bold]: {r.goal}")
        for st in r.steps:
            console.print(
                f"  {st.n}. on {st.path} ({st.heading[:40]!r}) "
                f"P(goal)={st.goal_reached:.2f} -> {st.action}"
            )
        for c in r.checks_failed:
            console.print(f"  [red]check failed:[/red] {c}")
        for e in r.errors:
            console.print(f"  [yellow]error:[/yellow] {e}")
    console.print(
        f"\n{len(summary.scenarios)} scenarios, {summary.jev_calls} Jev calls "
        f"({summary.replays} replayed, {summary.network_calls} live), "
        f"{summary.wall_seconds}s wall, ${summary.cost_usd:.4f}"
    )
    if summary.passed:
        console.print("[bold green]PASS[/bold green]: every scenario reached its goal")
    else:
        console.print(f"[bold red]FAIL[/bold red]: {', '.join(summary.failed_ids)}")


def results_markdown(summary: SmokeSummary) -> str:
    rows = [
        "| scenario | steps | landed on | heading | P(goal) | p50 ms | cost | result |",
        "|---|--:|---|---|--:|--:|--:|---|",
    ]
    for s in summary.scenarios:
        rows.append(
            f"| {s.id} | {s.steps} | `{s.final_path}` | {s.final_heading[:40]} | "
            f"{s.goal_probability:.2f} | {s.p50_ms:,.0f} | ${s.cost_usd:.4f} | "
            f"{'PASS' if s.passed else 'FAIL'} |"
        )
    head = (
        f"{summary.site} ({summary.base_url}), {len(summary.scenarios)} scenarios, "
        f"{summary.jev_calls} Jev calls, {summary.wall_seconds}s wall, ${summary.cost_usd:.4f}. "
        f"Result: {'PASS' if summary.passed else 'FAIL'}."
    )
    return head + "\n\n" + "\n".join(rows) + "\n"


def save_results(
    summary: SmokeSummary, results: list[ScenarioResult], directory: Path, tag: str
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{tag}-summary.json").write_text(summary.model_dump_json(indent=1) + "\n")
    (directory / f"{tag}-results.md").write_text(results_markdown(summary))
    (directory / f"{tag}-trajectories.json").write_text(
        json.dumps([asdict(r) for r in results], indent=1, default=str) + "\n"
    )


__all__ = [
    "DEFAULT_RESULTS",
    "ScenarioSummary",
    "SmokeSummary",
    "print_report",
    "results_markdown",
    "run_smoke",
    "save_results",
]
