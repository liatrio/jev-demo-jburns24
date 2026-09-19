"""Vercel AI Gateway client with a deterministic record/replay layer.

Two dialects share one bearer token (``API_KEY``):

* OpenAI-compatible ``POST /v1/chat/completions`` for generative LLMs.
* AI SDK evaluation-model dialect ``POST /v4/ai/evaluation-model`` for Jev.

Every request is hashed (path + canonical JSON body + model). In ``replay``
mode the response is read from a cassette on disk; in ``record`` mode a miss
goes to the network and is saved; in ``live`` mode cassettes are ignored.
This is what makes the e2e suite deterministic, offline and free to run from
a pre-commit hook, mirroring jev-ultrafast's "tests must not call paid APIs"
rule while keeping real model responses instead of hand-written fakes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

Mode = Literal["replay", "record", "live"]

DEFAULT_BASE_URL = "https://ai-gateway.vercel.sh"
DEFAULT_CASSETTE_DIR = Path(__file__).resolve().parents[2] / "tests" / "cassettes"
CHAT_PATH = "/v1/chat/completions"
EVAL_PATH = "/v4/ai/evaluation-model"


def _has_content(body: dict[str, Any]) -> bool:
    """A chat completion is usable only if it has text and was not cut off."""
    try:
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            return False
        return bool((choice["message"]["content"] or "").strip())
    except (KeyError, IndexError, TypeError):
        return False


class CassetteMiss(RuntimeError):
    """Raised in replay mode when no recorded response exists for a request."""


@dataclass
class GatewayResponse:
    body: dict[str, Any]
    latency_ms: float
    replayed: bool


def canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_key(path: str, body: Any) -> str:
    return hashlib.sha256(f"{path}\n{canonical(body)}".encode()).hexdigest()


class Cassettes:
    """One JSON file per request hash, sharded by the first two hex chars."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        p = self.path_for(key)
        return json.loads(p.read_text()) if p.exists() else None

    def put(self, key: str, path: str, body: Any, response: dict, latency_ms: float) -> None:
        p = self.path_for(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "path": path,
            "request": body,
            "response": response,
            "latency_ms": round(latency_ms, 1),
        }
        p.write_text(json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False) + "\n")

    def count(self) -> int:
        return sum(1 for _ in self.directory.rglob("*.json")) if self.directory.exists() else 0


class Gateway:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        mode: Mode | None = None,
        cassette_dir: Path | None = None,
        timeout: float = 120.0,
        concurrency: int = 8,
    ) -> None:
        self.api_key = api_key or os.environ.get("API_KEY") or os.environ.get("AI_GATEWAY_API_KEY")
        self.base_url = (
            base_url or os.environ.get("AI_GATEWAY_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        mode = mode or os.environ.get("JEV_DEMO_MODE", "record")
        if mode not in ("replay", "record", "live"):
            raise ValueError(f"unknown JEV_DEMO_MODE {mode!r}; expected replay|record|live")
        self.mode: Mode = mode  # type: ignore[assignment]
        self.cassettes = Cassettes(
            cassette_dir or Path(os.environ.get("JEV_DEMO_CASSETTES", DEFAULT_CASSETTE_DIR))
        )
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, http2=True)
        self._sem = asyncio.Semaphore(concurrency)
        self.network_calls = 0
        self.replays = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Gateway:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ core
    async def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        key_body: Any | None = None,
        accept: Callable[[dict[str, Any]], bool] | None = None,
    ) -> GatewayResponse:
        """POST ``body`` to ``path``; ``key_body`` (default ``body``) is what gets hashed.

        ``accept`` lets a caller reject a 200 response that is semantically empty
        (e.g. an LLM returning no content); such responses are retried once and
        never written to a cassette.
        """
        key = request_key(path, body if key_body is None else key_body)
        if self.mode != "live":
            hit = self.cassettes.get(key)
            if hit is not None:
                self.replays += 1
                return GatewayResponse(hit["response"], hit["latency_ms"], replayed=True)
            if self.mode == "replay":
                raise CassetteMiss(
                    f"no cassette for {path} (key {key[:12]}...). "
                    "Run `task e2e:record` with API_KEY set to refresh recordings."
                )
        if not self.api_key:
            raise RuntimeError("API_KEY is not set; cannot make live gateway calls")

        hdrs = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        hdrs.update(headers or {})
        async with self._sem:
            response = await self._post_with_retry(path, body, hdrs)
            self.network_calls += 1
            if accept is not None and not accept(response.body):
                response = await self._post_with_retry(path, body, hdrs)
                self.network_calls += 1
        if self.mode == "record" and (accept is None or accept(response.body)):
            self.cassettes.put(
                key,
                path,
                key_body if key_body is not None else body,
                response.body,
                response.latency_ms,
            )
        return response

    async def _post_with_retry(self, path: str, body: dict, headers: dict) -> GatewayResponse:
        delay = 0.5
        for attempt in range(4):
            start = time.perf_counter()
            r = await self._client.post(path, json=body, headers=headers)
            latency_ms = (time.perf_counter() - start) * 1000
            if r.status_code in (429, 503, 529) and attempt < 3:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"{r.status_code} from {path}: {r.text[:500]}", request=r.request, response=r
                )
            return GatewayResponse(r.json(), latency_ms, replayed=False)
        raise RuntimeError("unreachable")

    # -------------------------------------------------------------- dialects
    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 300,
        reasoning_effort: str | None = None,
        json_mode: bool = True,
    ) -> GatewayResponse:
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            body["reasoning"] = {"effort": reasoning_effort}
        return await self.post(CHAT_PATH, body, accept=_has_content)

    async def evaluate(
        self, model: str, state: Any, questions: dict[str, dict[str, Any]]
    ) -> GatewayResponse:
        """Call an evaluation model (Jev). The model travels in a header in this dialect."""
        headers = {
            "ai-model-id": model,
            "ai-evaluation-model-specification-version": "4",
            "ai-gateway-protocol-version": "0.0.1",
        }
        wire = {"state": state, "questions": questions}
        return await self.post(EVAL_PATH, wire, headers=headers, key_body={**wire, "model": model})
