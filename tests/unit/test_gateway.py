"""Record/replay layer behaviour, exercised against a fake transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from jev_demo.gateway import CassetteMiss, Gateway


def fake_transport(payloads: list[dict], calls: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=payloads.pop(0))

    return httpx.MockTransport(handler)


async def make(tmp_path: Path, mode: str, payloads: list[dict], calls: list[dict]) -> Gateway:
    gw = Gateway(api_key="test", mode=mode, cassette_dir=tmp_path)  # type: ignore[arg-type]
    await gw._client.aclose()
    gw._client = httpx.AsyncClient(base_url=gw.base_url, transport=fake_transport(payloads, calls))
    return gw


async def test_record_then_replay(tmp_path: Path) -> None:
    calls: list[dict] = []
    gw = await make(tmp_path, "record", [{"answers": {"x": 1}}], calls)
    first = await gw.post("/v4/ai/evaluation-model", {"state": "s", "questions": {}})
    assert first.replayed is False and first.body == {"answers": {"x": 1}}
    assert gw.cassettes.count() == 1
    await gw.aclose()

    gw2 = await make(tmp_path, "replay", [], calls)
    second = await gw2.post("/v4/ai/evaluation-model", {"state": "s", "questions": {}})
    assert second.replayed is True and second.body == first.body
    assert len(calls) == 1, "replay must not touch the network"
    await gw2.aclose()


async def test_replay_miss_is_loud(tmp_path: Path) -> None:
    gw = await make(tmp_path, "replay", [], [])
    with pytest.raises(CassetteMiss, match="task e2e:record"):
        await gw.post("/v1/chat/completions", {"model": "m", "messages": []})
    await gw.aclose()


async def test_live_ignores_cassettes(tmp_path: Path) -> None:
    calls: list[dict] = []
    gw = await make(tmp_path, "record", [{"v": 1}], calls)
    await gw.post("/p", {"a": 1})
    await gw.aclose()
    gw2 = await make(tmp_path, "live", [{"v": 2}], calls)
    r = await gw2.post("/p", {"a": 1})
    assert r.body == {"v": 2} and r.replayed is False
    assert gw2.cassettes.count() == 1, "live mode never writes cassettes"
    await gw2.aclose()


async def test_empty_llm_content_is_retried_and_not_recorded(tmp_path: Path) -> None:
    calls: list[dict] = []
    empty = {"choices": [{"message": {"content": ""}}]}
    good = {"choices": [{"message": {"content": '{"ok": true}'}}]}
    gw = await make(tmp_path, "record", [empty, good], calls)
    r = await gw.chat("m", [{"role": "user", "content": "hi"}])
    assert r.body == good
    assert len(calls) == 2
    assert gw.cassettes.count() == 1
    await gw.aclose()


async def test_eval_cassettes_are_distinct_per_model(tmp_path: Path) -> None:
    calls: list[dict] = []
    gw = await make(tmp_path, "record", [{"answers": 1}, {"answers": 2}], calls)
    a = await gw.evaluate("model-a", "s", {})
    b = await gw.evaluate("model-b", "s", {})
    assert a.body != b.body
    assert gw.cassettes.count() == 2
    await gw.aclose()
