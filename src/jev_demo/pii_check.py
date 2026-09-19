"""Semantic lint: does any log statement write personal data into the log?

A regex can find a call to ``logger.info``. It cannot tell whether the f-string
inside it leaks a customer's email. That is a judgement call, and it is the
kind of call a generative LLM answers in seconds of prose per statement.
Here Jev answers it as one typed *choice* per statement, every statement in
a file batched into a single speculative fan-out call, in well under a
second. The result is a pre-commit hook that blocks the commit when a log
line carries PII and lets everything else through.

Pipeline:

1. ``extract_log_statements`` walks each file and pulls out calls to the
   usual logging entry points (``logger.info``, ``log.error``,
   ``console.warn``, ``slog.Info``, ``log.Printf`` and friends), balancing
   parentheses so multi-line calls come out whole.
2. ``classify_statements`` sends batches of statements to Jev with one
   question each: *what kind of personal data, if any, does this line write
   to the log?* Answers are validated against the typed contract before they
   count (``validate_choice``).
3. ``run`` prints the findings and returns a non-zero exit status when any
   statement is flagged.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .evaluators import JEV_MODEL, cost_for, validate_choice
from .gateway import Gateway

# --------------------------------------------------------------- extraction
# Receiver names logging libraries are conventionally bound to, in Python,
# JavaScript/TypeScript, Go, Java/Kotlin, Rust, Ruby and C#.
_RECEIVERS = r"(?:logger|logging|log|LOG|LOGGER|_log|_logger|console|slog|zap|tracing|Log|Logger)"
# Level-ish method names across the same libraries.
_LEVELS = (
    r"(?:trace|debug|info|warn|warning|error|err|critical|fatal|exception|log|"
    r"Trace|Debug|Info|Warn|Warning|Error|Fatal|Panic|Print|Printf|Println|Fatalf|Panicf|"
    r"Infof|Warnf|Errorf|Debugf|Infow|Warnw|Errorw|Debugw)"
)
LOG_CALL = re.compile(rf"\b{_RECEIVERS}\s*(?:\.|::|->)\s*{_LEVELS}\s*\(")

# Rust / Go free-function macros: ``info!(...)``, ``log::warn!(...)``.
MACRO_CALL = re.compile(r"(?<![\w.:])(?:\w+::)?(?:trace|debug|info|warn|error)!\s*\(")

CONTEXT_LINES = 4
MAX_STATEMENT_CHARS = 1200

SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".java",
    ".kt",
    ".kts",
    ".rs",
    ".rb",
    ".cs",
    ".scala",
    ".php",
    ".swift",
}


@dataclass
class LogStatement:
    file: str
    line: int
    code: str
    context: str

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}"


def _balanced_end(text: str, open_idx: int) -> int:
    """Index just past the ``)`` that closes the ``(`` at ``open_idx``.

    Skips over string literals so a ``)`` inside a message does not end the
    call early. Falls back to the end of the line if the call never closes.
    """
    depth = 0
    quote: str | None = None
    i = open_idx
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    nl = text.find("\n", open_idx)
    return len(text) if nl == -1 else nl


def _display_path(path: Path) -> str:
    """Repo-relative when possible so cassette keys and output are machine-independent."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def extract_log_statements(path: Path, text: str | None = None) -> list[LogStatement]:
    """Every logging call in ``path``, whole, with a few lines of leading context."""
    if text is None:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
    lines = text.splitlines()
    out: list[LogStatement] = []
    seen_starts: set[int] = set()
    for pattern in (LOG_CALL, MACRO_CALL):
        for m in pattern.finditer(text):
            start = m.start()
            if start in seen_starts:
                continue
            seen_starts.add(start)
            end = _balanced_end(text, m.end() - 1)
            code = text[start:end].strip()
            if len(code) > MAX_STATEMENT_CHARS:
                code = code[:MAX_STATEMENT_CHARS] + " ..."
            line_no = text.count("\n", 0, start) + 1
            ctx_start = max(0, line_no - 1 - CONTEXT_LINES)
            context = "\n".join(lines[ctx_start : line_no - 1])
            out.append(LogStatement(_display_path(path), line_no, code, context))
    out.sort(key=lambda s: s.line)
    return out


def is_source_file(path: Path) -> bool:
    return path.suffix in SOURCE_SUFFIXES


# ------------------------------------------------------------- classifying
POLICY = (
    "A log statement leaks PII when the text it writes to the log output can identify or "
    "be traced to a natural person: full or partial names, email addresses, phone numbers, "
    "postal addresses, dates of birth, government identifiers (SSN, passport, driver's "
    "licence, tax id), payment card numbers or bank account numbers, IP addresses tied to a "
    "user, precise geolocation, health or biometric data, and secrets that grant access as a "
    "person (passwords, session tokens, API keys). Infer what is written from variable and "
    "attribute names and from any literal values: `user.email`, `customer_name`, `ssn`, "
    "`card.pan`, `request.remote_addr` count. Opaque internal identifiers (transaction ids, "
    "account ids, order ids, request ids, user ids that are random or numeric surrogate keys), "
    "counts, durations, status codes, error types, table names and other operational data do "
    "NOT count. A hashed, masked, truncated or explicitly redacted value does NOT count: "
    "`mask(email)`, `sha256(user_key)`, `redact(...)`, and the last four digits of a card "
    "number (`card_last4`, `pan[-4:]`) are all acceptable to log."
)

PII_KINDS: dict[str, str] = {
    "none": "Writes only operational data or opaque internal identifiers; no personal data.",
    "contact": "Writes contact details: email address, phone number, postal address.",
    "identity": (
        "Writes identity data: a person's name, date of birth, or a government identifier "
        "such as SSN, passport, tax id or driver's licence."
    ),
    "financial": "Writes a payment card number (PAN, CVV, expiry) or a bank account or IBAN.",
    "credential": "Writes a password, session token, API key, or other secret bound to a user.",
    "network_location": "Writes a user's IP address, device fingerprint or precise geolocation.",
    "sensitive": "Writes health, biometric or other special-category data about a person.",
}

FLAG_THRESHOLD = 0.5  # block when P(none) falls below this (Jev's natural boundary)
WARN_THRESHOLD = 0.65  # below this, P(pii) is in the gray zone: shown, not blocking
BATCH_SIZE = 20


@dataclass
class Finding:
    statement: LogStatement
    kind: str
    p_none: float
    probabilities: dict[str, float]

    @property
    def flagged(self) -> bool:
        return self.p_none < FLAG_THRESHOLD

    @property
    def borderline(self) -> bool:
        return FLAG_THRESHOLD <= self.p_none < WARN_THRESHOLD


@dataclass
class CheckResult:
    files_scanned: int
    findings: list[Finding] = field(default_factory=list)
    calls: int = 0
    latency_ms: list[float] = field(default_factory=list)
    cost_usd: float = 0.0
    wall_ms: float = 0.0

    @property
    def flagged(self) -> list[Finding]:
        return [f for f in self.findings if f.flagged]

    @property
    def ok(self) -> bool:
        return not self.flagged


def pii_questions(statements: list[LogStatement]) -> dict[str, dict[str, Any]]:
    """One typed choice per statement; all evaluated in parallel in one call."""
    return {
        f"s{i}": {
            "type": "choice",
            "instructions": (
                f"What kind of personal data, if any, does log statement s{i} write to the "
                "log output? Judge by the policy."
            ),
            "criteria": PII_KINDS,
        }
        for i in range(len(statements))
    }


def pii_state(statements: list[LogStatement]) -> dict[str, Any]:
    return {
        "policy": POLICY,
        "statements": {
            f"s{i}": {
                "file": s.file,
                "line": s.line,
                "preceding_lines": s.context,
                "log_statement": s.code,
            }
            for i, s in enumerate(statements)
        },
    }


async def _classify_batch(gw: Gateway, batch: list[LogStatement], result: CheckResult) -> None:
    questions = pii_questions(batch)
    resp = await gw.evaluate(JEV_MODEL, pii_state(batch), questions)
    answers = resp.body["answers"]
    usage = resp.body.get("usage", {})
    result.calls += 1
    result.latency_ms.append(resp.latency_ms)
    result.cost_usd += cost_for(
        JEV_MODEL,
        usage.get("inputTokens", usage.get("input_tokens", 0)),
        usage.get("outputTokens", usage.get("output_tokens", 0)),
    )
    for i, stmt in enumerate(batch):
        ans = answers[f"s{i}"]
        validate_choice(ans, PII_KINDS)
        probs = {k: float(v) for k, v in ans["probabilities"].items()}
        result.findings.append(Finding(stmt, ans["choice"], probs.get("none", 0.0), probs))


def batches_for(per_file: list[list[LogStatement]]) -> list[list[LogStatement]]:
    """One batch per file (its statements share context), split only past BATCH_SIZE."""
    out: list[list[LogStatement]] = []
    for stmts in per_file:
        out.extend(stmts[i : i + BATCH_SIZE] for i in range(0, len(stmts), BATCH_SIZE))
    return out


async def classify_statements(
    gw: Gateway, per_file: list[list[LogStatement]], result: CheckResult
) -> None:
    await asyncio.gather(*(_classify_batch(gw, b, result) for b in batches_for(per_file)))
    result.findings.sort(key=lambda f: (f.statement.file, f.statement.line))


async def check_paths(paths: list[Path], gw: Gateway) -> CheckResult:
    started = time.perf_counter()
    files = [p for p in paths if p.is_file() and is_source_file(p)]
    per_file = [s for s in (extract_log_statements(p) for p in files) if s]
    result = CheckResult(files_scanned=len(files))
    if per_file:
        await classify_statements(gw, per_file, result)
    result.wall_ms = (time.perf_counter() - started) * 1000
    return result


# --------------------------------------------------------------------- CLI
def load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal ``.env`` loader so the hook finds ``API_KEY`` without ``task``.

    Only sets variables that are not already in the environment.
    """
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v


def format_result(result: CheckResult, *, verbose: bool = False) -> str:
    lines: list[str] = []
    n = len(result.findings)
    for f in result.findings:
        if f.flagged:
            lines.append(
                f"  BLOCK  {f.statement.location}  {f.kind} (P(pii)={1 - f.p_none:.2f})\n"
                f"         {f.statement.code.splitlines()[0][:110]}"
            )
        elif f.borderline:
            lines.append(
                f"  WARN   {f.statement.location}  borderline (P(pii)={1 - f.p_none:.2f}), "
                "not blocking; consider masking"
            )
        elif verbose:
            lines.append(f"  ok     {f.statement.location}  P(pii)={1 - f.p_none:.2f}")
    p50 = sorted(result.latency_ms)[len(result.latency_ms) // 2] if result.latency_ms else 0
    summary = (
        f"pii-check: {n} log statement{'s' if n != 1 else ''} in {result.files_scanned} "
        f"file{'s' if result.files_scanned != 1 else ''}, {len(result.flagged)} flagged | "
        f"{result.calls} jev call{'s' if result.calls != 1 else ''}, "
        f"p50 {p50:.0f} ms, wall {result.wall_ms:.0f} ms, cost ${result.cost_usd:.5f}"
    )
    if lines:
        return "\n".join(lines) + "\n" + summary
    return summary


def to_dict(result: CheckResult) -> dict[str, Any]:
    lat = sorted(result.latency_ms)
    return {
        "files_scanned": result.files_scanned,
        "statements": len(result.findings),
        "flagged": len(result.flagged),
        "jev_calls": result.calls,
        "p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
        "max_ms": round(lat[-1], 1) if lat else None,
        "wall_ms": round(result.wall_ms, 1),
        "cost_usd": round(result.cost_usd, 6),
        "findings": [
            {
                "file": f.statement.file,
                "line": f.statement.line,
                "kind": f.kind,
                "p_pii": round(1 - f.p_none, 2),
                "flagged": f.flagged,
                "code": f.statement.code.splitlines()[0][:120],
            }
            for f in result.findings
        ],
    }


def run(
    paths: list[str],
    mode: str | None = None,
    verbose: bool = False,
    save: Path | None = None,
) -> int:
    """Entry point used by the CLI and the pre-commit hook. Returns the exit status."""
    load_dotenv()

    async def _go() -> CheckResult:
        async with Gateway(mode=mode) as gw:  # type: ignore[arg-type]
            return await check_paths([Path(p) for p in paths], gw)

    try:
        result = asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001 - a hook must explain itself, not trace back
        print(f"pii-check: could not get a verdict from Jev ({type(exc).__name__}: {exc})")
        print("pii-check: fix the cause (API_KEY in .env? network?) and commit again.")
        return 2
    print(format_result(result, verbose=verbose))
    if save is not None:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(json.dumps(to_dict(result), indent=1) + "\n")
        print(f"pii-check: wrote {save}")
    if result.flagged:
        print("pii-check: commit blocked. Remove or redact the personal data in the lines above.")
        return 1
    return 0
