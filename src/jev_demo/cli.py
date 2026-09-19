"""Command-line interface: ``jev-demo tapes | play | play-all | generate-tapes | pii-check``."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

import typer
from rich.live import Live
from rich.text import Text

from .evaluators import DEFAULT_EVALUATORS, build_evaluators
from .gateway import Gateway
from .models import TapeResult
from .report import console, disagreements, metrics_table, summary_line, tape_header
from .runner import aggregate, play_tape
from .tapes import TAPES_DIR, find_tape, load_all, write_all

app = typer.Typer(
    add_completion=False, help="Fraud-classification bake-off: Jev vs generative LLMs."
)

RESULTS_DIR = Path(
    os.environ.get("JEV_DEMO_RESULTS", Path(__file__).resolve().parents[2] / "results")
)


class _Progress:
    def __init__(self, counts: dict[str, int], total: int) -> None:
        self.counts = counts
        self.total = total
        self.live: Live | None = None

    def render(self) -> Text:
        return Text("  ".join(f"{k}: {v}/{self.total}" for k, v in self.counts.items()))

    def on_verdict(self, v) -> None:  # noqa: ANN001
        self.counts[v.evaluator] += 1
        if self.live:
            self.live.update(self.render())


def _parse_evaluators(value: str) -> list[str]:
    return [v for v in value.split(",") if v.strip()]


@app.command()
def tapes() -> None:
    """List the available tapes and what each one encodes."""
    for t in load_all():
        console.print(tape_header(t))


@app.command("generate-tapes")
def generate_tapes() -> None:
    """Regenerate tapes/*.json from the seeded generators (idempotent)."""
    for p in write_all(TAPES_DIR):
        console.print(f"wrote {p.relative_to(Path.cwd()) if p.is_relative_to(Path.cwd()) else p}")


async def _play(
    names: list[str], evaluators: str, mode: str | None, effort: str | None, save: bool, quiet: bool
) -> list[TapeResult]:
    results: list[TapeResult] = []
    async with Gateway(mode=mode) as gw:  # type: ignore[arg-type]
        evs = build_evaluators(gw, _parse_evaluators(evaluators), effort)
        for name in names:
            tape = find_tape(name)
            if not quiet:
                console.print(tape_header(tape))
            progress = _Progress({e.name: 0 for e in evs}, len(tape.transactions))
            with Live(
                progress.render(), console=console, refresh_per_second=8, transient=True
            ) as live:
                progress.live = live
                result = await play_tape(tape, evs, on_verdict=progress.on_verdict)
            results.append(result)
            if not quiet:
                console.print(metrics_table(f"Results: {tape.title}", result.metrics))
                console.print(disagreements(tape, result))
                line = summary_line(result.metrics)
                if line:
                    console.print(f"[bold]{line}[/bold]\n")
            if save:
                RESULTS_DIR.mkdir(parents=True, exist_ok=True)
                out = RESULTS_DIR / f"{tape.tape_id}.json"
                out.write_text(result.model_dump_json(indent=1))
        if not quiet:
            console.print(
                f"[dim]gateway: {gw.network_calls} network calls, {gw.replays} cassette replays "
                f"(mode={gw.mode}, cassettes on disk={gw.cassettes.count()})[/dim]"
            )
    return results


@app.command()
def play(
    tape: str = typer.Argument(
        ..., help="tape id, prefix, or a word from its title (e.g. T01, ato, mule)"
    ),
    evaluators: str = typer.Option(
        ",".join(DEFAULT_EVALUATORS), "--evaluators", "-e", help="comma list: jev,sonnet,gpt,hybrid"
    ),
    mode: str | None = typer.Option(
        None, "--mode", help="replay | record | live (default: $JEV_DEMO_MODE or record)"
    ),
    effort: str | None = typer.Option(
        None, "--effort", help="reasoning effort for LLM evaluators (provider default if unset)"
    ),
    save: bool = typer.Option(True, help="write results/<tape>.json"),
) -> None:
    """Play one tape through the evaluators and print the comparison."""
    asyncio.run(_play([tape], evaluators, mode, effort, save, quiet=False))


@app.command("play-all")
def play_all(
    evaluators: str = typer.Option(",".join(DEFAULT_EVALUATORS), "--evaluators", "-e"),
    mode: str | None = typer.Option(None, "--mode"),
    effort: str | None = typer.Option(None, "--effort"),
    save: bool = typer.Option(True),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="only print the aggregate table"),
) -> None:
    """Play every tape and print per-tape plus pooled metrics."""
    all_tapes = load_all()
    names = [t.tape_id for t in all_tapes]
    results = asyncio.run(_play(names, evaluators, mode, effort, save, quiet))
    agg = aggregate(results, all_tapes)
    console.print(metrics_table("Pooled across all tapes", agg))
    line = summary_line(agg)
    if line:
        console.print(f"[bold]{line}[/bold]")
    if save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "summary.json").write_text(
            json.dumps({k: v.model_dump() for k, v in agg.items()}, indent=1)
        )


@app.command("pii-check")
def pii_check_cmd(
    paths: Annotated[
        list[str], typer.Argument(help="source files to scan (pre-commit passes these)")
    ],
    mode: str | None = typer.Option(
        None, "--mode", help="replay | record | live (default: $JEV_DEMO_MODE or record)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="also list statements that passed"),
    save: bool = typer.Option(False, "--save", help="write results/pii-check.json"),
) -> None:
    """Semantic lint: block if any log statement writes PII. One Jev call per file."""
    from .pii_check import run

    out = RESULTS_DIR / "pii-check.json" if save else None
    raise typer.Exit(run(paths, mode=mode, verbose=verbose, save=out))


@app.command()
def swarm(
    agents: int = typer.Option(200, "--agents", "-n", help="number of Jev-driven browser agents"),
    inject: str | None = typer.Option(
        None, "--inject", help="comma list of known regressions to plant in the site, or 'all'"
    ),
    mode: str | None = typer.Option(
        None, "--mode", help="replay | record | live (default: $JEV_DEMO_MODE or record)"
    ),
    concurrency: int = typer.Option(16, "--concurrency", help="browser tabs open at once"),
    steps: int = typer.Option(6, "--steps", help="browser actions per agent"),
    save: bool = typer.Option(True, help="write results/swarm/*"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Adversarial e2e: unleash N Jev-driven browser agents on the portal. Exit 1 on new defects."""
    from .swarm.run import DEFAULT_RESULTS, print_report, run_swarm, save_results

    done = {"n": 0}

    def on_done(traj) -> None:  # noqa: ANN001
        done["n"] += 1
        if not quiet and done["n"] % 25 == 0:
            console.print(f"[dim]{done['n']}/{agents} agents finished[/dim]")

    summary, trajectories = asyncio.run(
        run_swarm(
            agents,
            inject=inject,
            mode=mode,
            concurrency=concurrency,
            max_steps=steps,
            on_agent_done=on_done,
        )
    )
    print_report(summary)
    if save:
        save_results(summary, trajectories, DEFAULT_RESULTS)
    raise typer.Exit(code=0 if summary.passed else 1)


if __name__ == "__main__":
    app()
