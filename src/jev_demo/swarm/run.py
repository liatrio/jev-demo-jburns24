"""Orchestrate the swarm: target server, browser pool, N agents, triage, report."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from ..gateway import Gateway
from .agent import MAX_STEPS, Trajectory, run_agent
from .browser import BrowserPool
from .target import TargetServer, parse_injected
from .triage import Finding, apply_baseline, load_baseline, new_blocking, triage

DEFAULT_AGENTS = 200
DEFAULT_CONCURRENCY = 16
ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASELINE = ROOT / "tests" / "swarm-baseline.json"
DEFAULT_RESULTS = ROOT / "results" / "swarm"

console = Console()


class SwarmSummary(BaseModel):
    agents: int
    steps: int
    jev_calls: int
    network_calls: int
    replays: int
    agent_errors: int
    wall_seconds: float
    cost_usd: float
    injected: list[str]
    findings: list[Finding]
    new_blocking: list[str]

    @property
    def passed(self) -> bool:
        return not self.new_blocking


async def run_swarm(
    agents: int = DEFAULT_AGENTS,
    *,
    inject: str | None = None,
    mode: str | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_steps: int = MAX_STEPS,
    baseline: Path = DEFAULT_BASELINE,
    headed: bool = False,
    slow_mo_ms: int = 0,
    on_agent_done=None,
) -> tuple[SwarmSummary, list[Trajectory]]:
    injected = parse_injected(inject)
    started = time.perf_counter()
    trajectories: list[Trajectory] = []
    sem = asyncio.Semaphore(concurrency)

    with TargetServer(injected) as server:
        async with (
            Gateway(mode=mode, concurrency=32) as gateway,  # type: ignore[arg-type]
            BrowserPool(server.base_url, headed=headed, slow_mo_ms=slow_mo_ms) as pool,
        ):

            async def one(i: int) -> Trajectory:
                async with sem:
                    tab = await pool.open()
                    try:
                        traj = await run_agent(i, tab, gateway, max_steps=max_steps)
                    finally:
                        await tab.close()
                if on_agent_done:
                    on_agent_done(traj)
                return traj

            trajectories = list(await asyncio.gather(*(one(i) for i in range(agents))))
            trajectories.sort(key=lambda t: t.agent_id)
            findings, triage_calls, triage_cost = await triage(trajectories, gateway)
            apply_baseline(findings, load_baseline(baseline))
            summary = SwarmSummary(
                agents=agents,
                steps=sum(len(t.steps) for t in trajectories),
                jev_calls=sum(t.jev_calls for t in trajectories) + triage_calls,
                network_calls=gateway.network_calls,
                replays=gateway.replays,
                agent_errors=sum(len(t.errors) for t in trajectories),
                wall_seconds=round(time.perf_counter() - started, 1),
                cost_usd=sum(t.cost_usd for t in trajectories) + triage_cost,
                injected=sorted(injected),
                findings=findings,
                new_blocking=[f.fingerprint for f in new_blocking(findings)],
            )
    return summary, trajectories


# ------------------------------------------------------------------- report
def findings_table(findings: list[Finding]) -> Table:
    t = Table(title="Findings (deduplicated)", show_lines=False)
    for col in ("sev", "category", "path", "agents", "p(defect)", "checks", "status"):
        t.add_column(col, justify="right" if col in ("sev", "agents", "p(defect)") else "left")
    for f in findings:
        status = "baseline" if f.in_baseline else ("BLOCK" if f.blocking else "report")
        style = "bold red" if status == "BLOCK" else ("dim" if status == "baseline" else "yellow")
        t.add_row(
            str(f.severity),
            f.category,
            f.path,
            str(len(f.agents)),
            f"{f.defect_probability:.2f}",
            ", ".join(f.automatic_checks) or "-",
            f"[{style}]{status}[/{style}]",
        )
    return t


def print_report(summary: SwarmSummary) -> None:
    if summary.findings:
        console.print(findings_table(summary.findings))
        for f in summary.findings:
            console.print(
                f"  [bold]{f.fingerprint}[/bold] via {f.persona}: {f.action}\n"
                f"    evidence: {f.evidence!r}"
            )
    else:
        console.print("[green]No defects confirmed by triage.[/green]")
    console.print(
        f"\n{summary.agents} agents, {summary.steps} browser steps, {summary.jev_calls} Jev calls "
        f"({summary.replays} replayed, {summary.network_calls} live), "
        f"{summary.agent_errors} agent errors, {summary.wall_seconds}s wall, "
        f"${summary.cost_usd:.4f}"
    )
    if summary.injected:
        console.print(f"[dim]injected regressions: {', '.join(summary.injected)}[/dim]")
    if summary.passed:
        console.print("[bold green]PASS[/bold green]: no new blocking findings")
    else:
        console.print(
            f"[bold red]FAIL[/bold red]: {len(summary.new_blocking)} new blocking finding(s): "
            + ", ".join(summary.new_blocking)
        )


def findings_markdown(summary: SwarmSummary) -> str:
    rows = [
        "| sev | category | path | agents | p(defect) | automatic checks | status | action |",
        "|--:|---|---|--:|--:|---|---|---|",
    ]
    for f in summary.findings:
        status = "baseline" if f.in_baseline else ("**BLOCK**" if f.blocking else "report")
        rows.append(
            f"| {f.severity} | {f.category} | `{f.path}` | {len(f.agents)} | "
            f"{f.defect_probability:.2f} | {', '.join(f.automatic_checks) or '-'} | {status} | "
            f"{f.action.replace('|', '/')} |"
        )
    head = (
        f"{summary.agents} agents, {summary.steps} browser steps, {summary.jev_calls} Jev calls, "
        f"{summary.wall_seconds}s wall, ${summary.cost_usd:.4f}. "
        f"Injected: {', '.join(summary.injected) or 'none'}. "
        f"Result: {'PASS' if summary.passed else 'FAIL'}."
    )
    return head + "\n\n" + ("\n".join(rows) if summary.findings else "No findings.") + "\n"


def save_results(summary: SwarmSummary, trajectories: list[Trajectory], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tag = "injected" if summary.injected else "clean"
    (directory / f"{tag}-summary.json").write_text(summary.model_dump_json(indent=1) + "\n")
    (directory / f"{tag}-findings.md").write_text(findings_markdown(summary))
    (directory / f"{tag}-trajectories.json").write_text(
        json.dumps([asdict(t) for t in trajectories], indent=None, default=str) + "\n"
    )


__all__ = [
    "DEFAULT_AGENTS",
    "DEFAULT_BASELINE",
    "DEFAULT_RESULTS",
    "SwarmSummary",
    "findings_markdown",
    "print_report",
    "run_swarm",
    "save_results",
]
