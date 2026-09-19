"""The system under test: a small, deterministic online-banking portal.

It is served from the standard library on an ephemeral port for the duration
of a swarm run. State is per browser session (a cookie), so hundreds of agents
can hammer it concurrently without seeing each other's balances. Every page is
free of timestamps, random ids and external assets so that the same action
sequence always yields the same DOM, which is what lets the Jev calls that drive
the agents be recorded once and replayed forever.

Known regressions can be *injected* by name (``BUGS``). The committed default is
the clean portal, so the pre-push swarm passes; ``--inject all`` is the demo
where the swarm has something to find.
"""

from __future__ import annotations

import html
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BUGS: dict[str, str] = {
    "negative_transfer": "transfer accepts a negative amount and moves money backwards",
    "overdraft": "transfer larger than the balance is accepted and the balance goes negative",
    "idor": "/accounts/<id> shows another customer's account without an ownership check",
    "xss_search": "the transaction search echoes the query into the page unescaped",
    "dead_link": "the Statements link in the navigation returns 404",
    "stack_trace": "a transfer memo containing a quote character returns a 500 with a traceback",
    "unicode_crash": "saving a profile name with non-ASCII characters returns a 500",
}

OWN_ACCOUNTS = ("1001", "1002")
FOREIGN_ACCOUNT = "2001"
STARTING_BALANCES = {"1001": 2500.00, "1002": 8000.00}
FOREIGN_BALANCE = 41250.75


@dataclass
class Session:
    balances: dict[str, float] = field(default_factory=lambda: dict(STARTING_BALANCES))
    name: str = "Dana Whitfield"
    transfers: list[dict] = field(default_factory=list)
    flash: str = ""


def _fake_traceback(exc: BaseException) -> str:
    """A leaked traceback with stable, machine-independent frames (keeps replay keys stable)."""
    last = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return (
        "Traceback (most recent call last):\n"
        '  File "/srv/portal/app.py", line 212, in handle\n'
        "    return handler(session, form)\n"
        '  File "/srv/portal/transfers.py", line 57, in do_transfer\n'
        f"{last}"
    )


def parse_injected(spec: str | None) -> frozenset[str]:
    if not spec:
        return frozenset()
    if spec.strip().lower() == "all":
        return frozenset(BUGS)
    names = {s.strip() for s in spec.split(",") if s.strip()}
    unknown = names - set(BUGS)
    if unknown:
        raise ValueError(f"unknown bug(s) {sorted(unknown)}; choose from {sorted(BUGS)} or 'all'")
    return frozenset(names)


class Portal:
    """Pure request -> response application; the HTTP layer is below."""

    def __init__(self, injected: frozenset[str] = frozenset()) -> None:
        self.injected = injected
        self.sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def has(self, bug: str) -> bool:
        return bug in self.injected

    def session(self, sid: str) -> Session:
        with self._lock:
            return self.sessions.setdefault(sid, Session())

    # ------------------------------------------------------------- routing
    def handle(self, method: str, url: str, form: dict[str, str], sid: str) -> tuple[int, str]:
        s = self.session(sid)
        u = urlparse(url)
        path = u.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if path == "/":
                return 200, self.page_home(s)
            if path == "/transfer":
                if method == "POST":
                    return self.do_transfer(s, form)
                return 200, self.page_transfer(s)
            if path == "/search":
                return 200, self.page_search(s, query.get("q", ""))
            if path == "/profile":
                if method == "POST":
                    return self.do_profile(s, form)
                return 200, self.page_profile(s)
            if path == "/statements":
                if self.has("dead_link"):
                    return 404, self.page_error(404, "Not Found", "No such page: /statements")
                return 200, self.page_statements(s)
            if path.startswith("/accounts/"):
                return self.page_account(s, path.rsplit("/", 1)[1])
            if path == "/help":
                return 200, self.page_help()
            return 404, self.page_error(404, "Not Found", f"No such page: {html.escape(path)}")
        except Exception as exc:  # noqa: BLE001 - the whole point is to surface it
            return 500, self.page_error(500, "Internal Server Error", _fake_traceback(exc))

    # -------------------------------------------------------------- actions
    def do_transfer(self, s: Session, form: dict[str, str]) -> tuple[int, str]:
        src, dst = form.get("from", ""), form.get("to", "")
        memo = form.get("memo", "")
        raw_amount = form.get("amount", "").strip()
        if self.has("stack_trace") and ("'" in memo or '"' in memo):
            raise ValueError(f"unterminated string in memo: {memo}")
        try:
            amount = float(raw_amount.replace(",", ""))
        except ValueError:
            s.flash = f"Rejected: amount {html.escape(raw_amount)!r} is not a number."
            return 200, self.page_transfer(s, form)
        if src not in s.balances or dst not in s.balances or src == dst:
            s.flash = "Rejected: choose two different accounts you own."
            return 200, self.page_transfer(s, form)
        if amount <= 0 and not self.has("negative_transfer"):
            s.flash = "Rejected: amount must be greater than zero."
            return 200, self.page_transfer(s, form)
        if amount > s.balances[src] and not self.has("overdraft"):
            s.flash = f"Rejected: insufficient funds in account {src}."
            return 200, self.page_transfer(s, form)
        if amount > 1_000_000:
            s.flash = "Rejected: amount exceeds the 1,000,000 USD daily limit."
            return 200, self.page_transfer(s, form)
        s.balances[src] = round(s.balances[src] - amount, 2)
        s.balances[dst] = round(s.balances[dst] + amount, 2)
        s.transfers.append({"from": src, "to": dst, "amount": amount, "memo": memo[:60]})
        s.flash = f"Transfer complete: {amount:,.2f} USD from {src} to {dst}."
        return 200, self.page_home(s)

    def do_profile(self, s: Session, form: dict[str, str]) -> tuple[int, str]:
        name = form.get("name", "").strip()
        if self.has("unicode_crash"):
            name.encode("ascii")  # raises on any non-ASCII name
        if not name:
            s.flash = "Rejected: name cannot be empty."
        elif len(name) > 60:
            s.flash = "Rejected: name is limited to 60 characters."
        else:
            s.name = name
            s.flash = "Profile saved."
        return 200, self.page_profile(s)

    # ---------------------------------------------------------------- pages
    def layout(self, title: str, body: str, s: Session | None = None) -> str:
        flash = ""
        if s is not None and s.flash:
            kind = "error" if s.flash.startswith("Rejected") else "ok"
            flash = f'<p class="flash {kind}" role="status">{s.flash}</p>'
            s.flash = ""
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)} - Northwind Bank</title>
<style>body{{font:16px system-ui;margin:2rem;max-width:52rem}}nav a{{margin-right:1rem}}
.flash.error{{color:#b00020}}.flash.ok{{color:#0a7d2b}}table{{border-collapse:collapse}}
td,th{{padding:.25rem .75rem;border:1px solid #ccc}}pre{{background:#eee;padding:1rem}}</style>
</head><body>
<nav aria-label="Main"><a href="/">Accounts</a><a href="/transfer">Transfer</a>
<a href="/search">Search</a><a href="/statements">Statements</a><a href="/profile">Profile</a>
<a href="/help">Help</a></nav>
<h1>{html.escape(title)}</h1>{flash}
{body}
</body></html>"""

    def page_home(self, s: Session) -> str:
        rows = "".join(
            f'<tr><td><a href="/accounts/{a}">{a}</a></td>'
            f"<td>{'Checking' if a == '1001' else 'Savings'}</td><td>{b:,.2f} USD</td></tr>"
            for a, b in s.balances.items()
        )
        recent = (
            "".join(
                f"<li>{t['amount']:,.2f} USD from {t['from']} to {t['to']}"
                f"{' - ' + html.escape(t['memo']) if t['memo'] else ''}</li>"
                for t in s.transfers[-5:]
            )
            or "<li>No transfers yet.</li>"
        )
        return self.layout(
            "Your accounts",
            f"<p>Welcome back, {html.escape(s.name)}.</p>"
            f"<table><tr><th>Account</th><th>Type</th><th>Balance</th></tr>{rows}</table>"
            f"<h2>Recent transfers</h2><ul>{recent}</ul>",
            s,
        )

    def page_transfer(self, s: Session, form: dict[str, str] | None = None) -> str:
        form = form or {}
        accounts = list(s.balances)

        def options(selected: str) -> str:
            return "".join(
                f'<option value="{a}"{" selected" if a == selected else ""}>{a}</option>'
                for a in accounts
            )

        from_opts = options(form.get("from", accounts[0]))
        to_opts = options(form.get("to", accounts[1]))
        amount = html.escape(form.get("amount", ""))
        memo = html.escape(form.get("memo", ""))
        return self.layout(
            "Transfer between accounts",
            f"""<form method="post" action="/transfer">
<label>From <select name="from">{from_opts}</select></label>
<label>To <select name="to">{to_opts}</select></label>
<label>Amount (USD) <input name="amount" type="text" inputmode="decimal" placeholder="0.00"
 value="{amount}"></label>
<label>Memo <input name="memo" type="text" maxlength="120" value="{memo}"></label>
<button type="submit">Send transfer</button></form>
<p>Current balances: {", ".join(f"{a}: {b:,.2f}" for a, b in s.balances.items())}</p>""",
            s,
        )

    def page_search(self, s: Session, q: str) -> str:
        shown = q if self.has("xss_search") else html.escape(q)
        hits = [t for t in s.transfers if q and (q in t["memo"] or q in f"{t['amount']:.2f}")]
        result = ""
        if q:
            items = "".join(
                f"<li>{t['amount']:,.2f} USD - {html.escape(t['memo'])}</li>" for t in hits
            )
            result = (
                f'<h2>Results for "<span id="query">{shown}</span>"</h2>'
                f"<ul>{items or '<li>No matching transactions.</li>'}</ul>"
            )
        return self.layout(
            "Search transactions",
            f"""<form method="get" action="/search">
<label>Memo or amount <input name="q" type="search" value="{html.escape(q)}"></label>
<button type="submit">Search</button></form>{result}""",
            s,
        )

    def page_profile(self, s: Session) -> str:
        return self.layout(
            "Your profile",
            f"""<form method="post" action="/profile">
<label>Display name <input name="name" type="text" value="{html.escape(s.name)}"></label>
<button type="submit">Save profile</button></form>""",
            s,
        )

    def page_statements(self, s: Session) -> str:
        items = "".join(f"<li>Statement for account {a} (PDF, 42 KB)</li>" for a in s.balances)
        return self.layout("Statements", f"<ul>{items}</ul>", s)

    def page_account(self, s: Session, account_id: str) -> tuple[int, str]:
        if account_id in s.balances:
            return 200, self.layout(
                f"Account {account_id}",
                f"<p>Owner: {html.escape(s.name)}</p>"
                f"<p>Balance: {s.balances[account_id]:,.2f} USD</p>",
                s,
            )
        if account_id == FOREIGN_ACCOUNT and self.has("idor"):
            return 200, self.layout(
                f"Account {account_id}",
                "<p>Owner: Priya Raman</p>"
                f"<p>Balance: {FOREIGN_BALANCE:,.2f} USD</p>"
                "<p>Routing 021000021, SSN on file ending 4471</p>",
                s,
            )
        return 403, self.page_error(403, "Forbidden", "You do not have access to this account.")

    def page_help(self) -> str:
        return self.layout(
            "Help",
            "<p>Transfers between your own accounts settle instantly. Amounts must be positive "
            "and no more than your available balance. Contact support at 1-800-555-0100.</p>"
            f'<p>Other accounts you might try: <a href="/accounts/{FOREIGN_ACCOUNT}">'
            f"{FOREIGN_ACCOUNT}</a> (not yours).</p>",
        )

    def page_error(self, code: int, title: str, detail: str) -> str:
        detail_html = f"<pre>{html.escape(detail)}</pre>" if detail else ""
        return self.layout(f"{code} {title}", f"<p>Something went wrong.</p>{detail_html}")


# ------------------------------------------------------------------ HTTP layer
class _Handler(BaseHTTPRequestHandler):
    portal: Portal

    def log_message(self, *_: object) -> None:  # silence
        pass

    def _sid(self) -> tuple[str, bool]:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sid" and v:
                return v, False
        return uuid.uuid4().hex, True

    def _serve(self, method: str) -> None:
        form: dict[str, str] = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
            form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        sid, new = self._sid()
        status, body = self.portal.handle(method, self.path, form, sid)
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if new:
            self.send_header("Set-Cookie", f"sid={sid}; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")


class TargetServer:
    """Run the portal on 127.0.0.1:<ephemeral> in a daemon thread."""

    def __init__(self, injected: frozenset[str] = frozenset(), port: int = 0) -> None:
        self.portal = Portal(injected)
        handler = type("Handler", (_Handler,), {"portal": self.portal})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self) -> TargetServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


__all__ = [
    "BUGS",
    "FOREIGN_ACCOUNT",
    "OWN_ACCOUNTS",
    "STARTING_BALANCES",
    "Portal",
    "TargetServer",
    "parse_injected",
]
