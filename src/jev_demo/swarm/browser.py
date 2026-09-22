"""Playwright layer: turn a page into a compact, deterministic observation and
execute the action an agent chose.

The observation deliberately contains no origin, no timestamps and no random
ids, only what a tester would see: path, status, headings, visible text and the
interactive elements, plus the deterministic checks. That makes it a stable
hash key for the record/replay cassettes, so a swarm recorded once replays
offline forever (until the site under test actually changes).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Response, async_playwright

XSS_FLAG = "__swarmXss"
XSS_PAYLOAD = f'<img src=x onerror="window.{XSS_FLAG}=1">'
NEGATIVE_MONEY = re.compile(r"-\d[\d,]*\.\d{2} USD")
MAX_TEXT = 1200
MAX_ELEMENTS = 24

_EXTRACT_JS = """
() => {
  const label = (el) => {
    if (el.labels && el.labels.length) {
      // only the label's own words, not the text of the control nested inside it
      return Array.from(el.labels[0].childNodes)
        .filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join(' ').trim();
    }
    return el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
  };
  const nodes = document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), select, textarea');
  const out = [];
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    const item = {tag};
    if (tag === 'a') { item.text = el.innerText.trim(); item.href = el.getAttribute('href'); }
    else if (tag === 'button') { item.text = el.innerText.trim(); }
    else {
      item.name = el.getAttribute('name') || '';
      item.label = label(el);
      item.type = el.getAttribute('type') || tag;
      if (tag === 'select') {
        item.options = Array.from(el.options).map(o => o.value);
        item.value = el.value;
      } else { item.value = el.value; }
    }
    out.push(item);
  }
  return {
    title: document.title,
    h1: (document.querySelector('h1') || {}).innerText || '',
    flash: (document.querySelector('.flash') || {}).innerText || '',
    text: document.body ? document.body.innerText : '',
    elements: out,
    xss: !!window.XSS_FLAG,
  };
}
""".replace("XSS_FLAG", XSS_FLAG)


@dataclass
class Observation:
    path: str
    status: int
    title: str
    h1: str
    flash: str
    text: str
    elements: list[dict[str, Any]]
    console_errors: list[str]
    signals: list[str] = field(default_factory=list)
    # Untruncated page text for code-side checks; never sent to the model.
    full_text: str = ""

    def for_model(self) -> dict[str, Any]:
        """What Jev sees. Element indices are the keys the action space refers to."""
        return {
            "path": self.path,
            "http_status": self.status,
            "title": self.title,
            "heading": self.h1,
            "status_message": self.flash,
            "visible_text": self.text[:MAX_TEXT],
            "elements": [{"i": i, **e} for i, e in enumerate(self.elements)],
            "automatic_checks": self.signals,
        }


def code_signals(status: int, text: str, xss: bool) -> list[str]:
    """Deterministic checks code can make without a model. Jev judges the rest.

    Console errors are deliberately *not* a signal: the browser delivers them
    asynchronously, so including them would make the observation (and therefore
    the cassette key) depend on timing.
    """
    out: list[str] = []
    if status >= 500:
        out.append(f"http_{status}")
    elif status == 404:
        out.append("http_404")
    if "Traceback (most recent call last)" in text:
        out.append("stack_trace_exposed")
    if xss:
        out.append("script_injection_executed")
    if NEGATIVE_MONEY.search(text):
        out.append("negative_amount_displayed")
    return out


class Tab:
    """One agent's browser context: isolated cookies, so isolated portal state."""

    def __init__(self, context: BrowserContext, page: Page, base_url: str) -> None:
        self.context = context
        self.page = page
        self.base_url = base_url
        self.last_status = 200
        self.console_errors: list[str] = []
        page.on("response", self._on_response)
        page.on("console", self._on_console)
        page.on("pageerror", lambda e: self.console_errors.append(str(e)))
        page.set_default_timeout(8000)

    def _on_console(self, msg: Any) -> None:
        if msg.type == "error":
            self.console_errors.append(msg.text)

    def _on_response(self, r: Response) -> None:
        if r.request.resource_type == "document":
            self.last_status = r.status

    async def goto(self, path: str) -> None:
        self.console_errors = []
        await self.page.goto(self.base_url + path, wait_until="load")

    async def observe(self) -> Observation:
        raw = await self.page.evaluate(_EXTRACT_JS)
        u = urlparse(self.page.url)
        path = u.path + (f"?{u.query}" if u.query else "")
        elements = raw["elements"][:MAX_ELEMENTS]
        for e in elements:
            if e.get("href"):
                e["href"] = urlparse(e["href"]).path or e["href"]
        text = re.sub(r"\n{2,}", "\n", raw["text"]).strip()
        errors = list(dict.fromkeys(self.console_errors))
        return Observation(
            path=path,
            status=self.last_status,
            title=raw["title"],
            h1=raw["h1"].strip(),
            flash=raw["flash"].strip(),
            text=text,
            elements=elements,
            console_errors=errors,
            signals=code_signals(self.last_status, text, raw["xss"]),
        )

    async def act(self, action: dict[str, Any]) -> None:
        """Execute one action from the agent's action space."""
        kind = action["kind"]
        self.console_errors = []
        if kind == "goto":
            await self.goto(action["path"])
            return
        if kind == "back":
            await self.page.go_back(wait_until="load")
            return
        locator = self.page.locator(
            "a[href], button, input:not([type=hidden]), select, textarea"
        ).nth(action["index"])
        if kind == "click":
            async with self.page.expect_navigation(wait_until="load", timeout=8000):
                await locator.click()
        elif kind == "fill":
            await locator.fill(action["value"])
        elif kind == "select":
            await locator.select_option(action["value"])
        else:
            raise ValueError(f"unknown action kind {kind!r}")

    async def close(self) -> None:
        await self.context.close()


class BrowserPool:
    """One Chromium process; each agent gets its own context."""

    def __init__(self, base_url: str, *, headed: bool = False, slow_mo_ms: int = 0) -> None:
        self.base_url = base_url
        self.headed = headed
        self.slow_mo_ms = slow_mo_ms
        self.launch_args: list[str] = []
        self._pw = None
        self.browser: Browser | None = None

    async def __aenter__(self) -> BrowserPool:
        self._pw = await async_playwright().start()
        exe = os.environ.get("JEV_SWARM_CHROMIUM") or None
        try:
            self.browser = await self._pw.chromium.launch(
                headless=not self.headed,
                slow_mo=self.slow_mo_ms or None,
                executable_path=exe,
                args=self.launch_args,
            )
        except Exception as exc:  # noqa: BLE001 - turn Playwright's banner into one actionable line
            await self._pw.stop()
            raise RuntimeError(
                "the swarm needs a Chromium Playwright can launch: run `task browser:install` "
                "(uv run playwright install chromium) or set JEV_SWARM_CHROMIUM to a chrome binary"
            ) from exc
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    async def open(self) -> Tab:
        assert self.browser is not None
        ctx = await self.browser.new_context(java_script_enabled=True)
        page = await ctx.new_page()
        return Tab(ctx, page, self.base_url)
