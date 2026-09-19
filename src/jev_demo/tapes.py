"""Seeded synthetic tape generator and tape loader.

Each tape is a realistic-looking stream of enterprise-bank transaction log rows
with a handful of rows encoding one known fraud situation. Ground truth is
stored on every row but stripped before an evaluator sees it. Generation is
fully deterministic per seed so ``task tapes:generate`` is idempotent and the
committed JSON is reviewable in a diff.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import AccountProfile, Pattern, Tape, Transaction

TAPES_DIR = Path(__file__).resolve().parents[2] / "tapes"

START = datetime(2026, 3, 2, 6, 0, tzinfo=UTC)

CITIES = {
    "US": [
        ("Austin", "TX"),
        ("Denver", "CO"),
        ("Chicago", "IL"),
        ("Seattle", "WA"),
        ("Boston", "MA"),
    ],
    "GB": [("London", ""), ("Manchester", "")],
    "DE": [("Berlin", ""), ("Munich", "")],
    "NG": [("Lagos", "")],
    "RO": [("Bucharest", "")],
    "BR": [("Sao Paulo", "")],
    "SG": [("Singapore", "")],
}

EVERYDAY = [
    ("Whole Foods Market", "grocery", 18, 160),
    ("Shell Oil", "fuel", 30, 85),
    ("Starbucks", "restaurant", 4, 14),
    ("Chipotle", "restaurant", 9, 32),
    ("Amazon.com", "online_retail", 12, 140),
    ("Netflix", "subscription", 15.49, 15.49),
    ("Spotify", "subscription", 11.99, 11.99),
    ("CVS Pharmacy", "pharmacy", 8, 60),
    ("Uber", "transport", 9, 45),
    ("Home Depot", "home_improvement", 25, 300),
    ("Target", "general_retail", 20, 180),
    ("Delta Air Lines", "travel", 180, 620),
    ("Marriott Hotels", "travel", 140, 420),
    ("City Utilities", "utilities", 80, 240),
    ("Verizon Wireless", "telecom", 65, 130),
]

CARD_TEST_MERCHANTS = [
    "DigitalRiver*Software",
    "PayPal*Donation",
    "Google*Play",
    "Apple.com/bill",
    "Roblox",
    "Steam Games",
    "Patreon",
    "Twitch",
    "Discord Nitro",
    "Dropbox",
    "Canva",
    "Scribd",
    "Hulu",
    "Audible",
    "Fiverr",
    "Namecheap",
    "GoDaddy",
    "Grammarly",
    "Zoom.us",
    "Adobe",
]


class TapeBuilder:
    def __init__(self, tape_id: str, seed: int) -> None:
        self.tape_id = tape_id
        self.rng = random.Random(seed)
        self.seed = seed
        self.txns: list[Transaction] = []
        self.accounts: list[AccountProfile] = []
        self.clock = START
        self._n = 0

    # ----------------------------------------------------------- helpers
    def account(
        self,
        account_id: str,
        *,
        segment: str = "retail_checking",
        country: str = "US",
        monthly: float = 3200,
        typical: float = 48,
        categories: tuple[str, ...] = (
            "grocery",
            "fuel",
            "restaurant",
            "online_retail",
            "subscription",
        ),
        devices: tuple[str, ...] = ("ios-a1",),
        age_days: int = 1460,
    ) -> AccountProfile:
        city, _ = self.rng.choice(CITIES[country])
        p = AccountProfile(
            account_id=account_id,
            customer_segment=segment,
            home_city=city,
            home_country=country,
            typical_monthly_spend_usd=monthly,
            typical_txn_usd=typical,
            usual_merchant_categories=list(categories),
            known_devices=list(devices),
            account_age_days=age_days,
        )
        self.accounts.append(p)
        return p

    def tick(self, minutes: float) -> datetime:
        self.clock += timedelta(minutes=minutes)
        return self.clock

    def add(self, acct: AccountProfile, **kw) -> Transaction:
        self._n += 1
        defaults = dict(
            txn_id=f"{self.tape_id}-{self._n:04d}",
            timestamp=self.clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
            account_id=acct.account_id,
            currency="USD",
            channel="card_present",
            direction="debit",
            city=acct.home_city,
            country=acct.home_country,
            device_id=None,
            counterparty=None,
            is_fraud=False,
            pattern=Pattern.NONE,
        )
        defaults.update(kw)
        t = Transaction(**defaults)
        self.txns.append(t)
        return t

    def everyday(
        self, acct: AccountProfile, *, gap_minutes: tuple[int, int] = (35, 240)
    ) -> Transaction:
        self.tick(self.rng.randint(*gap_minutes))
        merchant, cat, lo, hi = self.rng.choice(EVERYDAY)
        amount = round(self.rng.uniform(lo, hi), 2)
        online = cat in ("online_retail", "subscription")
        return self.add(
            acct,
            amount_usd=amount,
            channel="card_not_present" if online else "card_present",
            merchant=merchant,
            merchant_category=cat,
            device_id=acct.known_devices[0] if online else None,
        )

    def build(self, title: str, situation: str, pattern: Pattern) -> Tape:
        return Tape(
            tape_id=self.tape_id,
            title=title,
            situation=situation,
            pattern=pattern,
            seed=self.seed,
            accounts=self.accounts,
            transactions=self.txns,
        )


# ------------------------------------------------------------------ tapes
def tape_clean_baseline() -> Tape:
    b = TapeBuilder("T00-clean", seed=100)
    a1 = b.account("ACC-1001")
    a2 = b.account(
        "ACC-1002", monthly=5400, typical=95, categories=("travel", "restaurant", "grocery", "fuel")
    )
    for _ in range(45):
        b.everyday(b.rng.choice([a1, a2]))
    # a legitimately unusual-but-fine event: booked a flight + hotel
    b.tick(90)
    b.add(
        a2,
        amount_usd=612.40,
        channel="card_not_present",
        merchant="Delta Air Lines",
        merchant_category="travel",
        device_id="ios-a1",
    )
    b.tick(4)
    b.add(
        a2,
        amount_usd=418.00,
        channel="card_not_present",
        merchant="Marriott Hotels",
        merchant_category="travel",
        device_id="ios-a1",
    )
    return b.build(
        "Clean baseline",
        "Two retail customers going about a normal week, including a legitimately "
        "larger travel booking. Contains zero fraud; measures false-positive rate.",
        Pattern.NONE,
    )


def tape_account_takeover() -> Tape:
    b = TapeBuilder("T01-ato", seed=101)
    victim = b.account("ACC-2001", devices=("android-7f",))
    other = b.account("ACC-2002")
    for _ in range(22):
        b.everyday(b.rng.choice([victim, other]))
    # 02:40 UTC: new device, password reset then drain
    b.clock = b.clock.replace(hour=2, minute=40) + timedelta(days=1)
    b.add(
        victim,
        amount_usd=0.00,
        channel="card_not_present",
        merchant="Online Banking Login",
        merchant_category="account_service",
        city="Bucharest",
        country="RO",
        device_id="win-unknown-3c",
        is_fraud=True,
        pattern=Pattern.ACCOUNT_TAKEOVER,
    )
    b.tick(6)
    b.add(
        victim,
        amount_usd=1999.00,
        channel="card_not_present",
        merchant="Coinbase",
        merchant_category="crypto",
        city="Bucharest",
        country="RO",
        device_id="win-unknown-3c",
        is_fraud=True,
        pattern=Pattern.ACCOUNT_TAKEOVER,
    )
    b.tick(3)
    b.add(
        victim,
        amount_usd=2450.00,
        channel="p2p",
        merchant="Zelle Transfer",
        merchant_category="p2p_transfer",
        counterparty="unknown-recipient-8841",
        city="Bucharest",
        country="RO",
        device_id="win-unknown-3c",
        is_fraud=True,
        pattern=Pattern.ACCOUNT_TAKEOVER,
    )
    b.tick(2)
    b.add(
        victim,
        amount_usd=1300.00,
        channel="card_not_present",
        merchant="Best Buy",
        merchant_category="electronics",
        city="Bucharest",
        country="RO",
        device_id="win-unknown-3c",
        is_fraud=True,
        pattern=Pattern.ACCOUNT_TAKEOVER,
    )
    for _ in range(12):
        b.everyday(other)
    return b.build(
        "Account takeover",
        "A long-standing customer's credentials are compromised. From a never-seen "
        "Windows device in Romania at 02:40 UTC, the attacker logs in, buys crypto, "
        "sends a Zelle to an unknown recipient and buys electronics, all within 11 "
        "minutes. Everything else on the tape is legitimate.",
        Pattern.ACCOUNT_TAKEOVER,
    )


def tape_card_testing() -> Tape:
    b = TapeBuilder("T02-cardtest", seed=102)
    victim = b.account("ACC-3001", monthly=2100, typical=36)
    other = b.account("ACC-3002")
    for _ in range(14):
        b.everyday(b.rng.choice([victim, other]))
    b.tick(50)
    merchants = b.rng.sample(CARD_TEST_MERCHANTS, 14)
    for m in merchants:
        b.tick(b.rng.uniform(0.3, 1.2))
        b.add(
            victim,
            amount_usd=round(b.rng.choice([0.5, 1.0, 1.0, 1.99, 2.5, 3.0]), 2),
            channel="card_not_present",
            merchant=m,
            merchant_category="digital_goods",
            city="Lagos",
            country="NG",
            device_id="linux-headless-9e",
            is_fraud=True,
            pattern=Pattern.CARD_TESTING,
        )
    b.tick(4)
    b.add(
        victim,
        amount_usd=1489.99,
        channel="card_not_present",
        merchant="Newegg",
        merchant_category="electronics",
        city="Lagos",
        country="NG",
        device_id="linux-headless-9e",
        is_fraud=True,
        pattern=Pattern.CARD_TESTING,
    )
    for _ in range(10):
        b.everyday(other)
    return b.build(
        "Card testing burst",
        "A stolen card number is validated with 14 sub-3-dollar online charges at "
        "unrelated digital-goods merchants inside 12 minutes from a headless device "
        "in Nigeria, then used for a 1,490 USD electronics purchase.",
        Pattern.CARD_TESTING,
    )


def tape_structuring() -> Tape:
    b = TapeBuilder("T03-structuring", seed=103)
    biz = b.account(
        "ACC-4001",
        segment="small_business_checking",
        monthly=48000,
        typical=1900,
        categories=("wholesale", "payroll", "utilities", "cash_deposit"),
        age_days=800,
    )
    other = b.account("ACC-4002")
    branches = [
        "Branch 014 Austin Downtown",
        "Branch 022 Round Rock",
        "Branch 031 Cedar Park",
        "Branch 007 Pflugerville",
    ]
    for day in range(5):
        b.clock = START + timedelta(days=day, hours=9)
        # normal business activity
        b.add(
            biz,
            amount_usd=round(b.rng.uniform(900, 3200), 2),
            channel="ach",
            merchant="Sysco Foods",
            merchant_category="wholesale",
            counterparty="Sysco Corp",
        )
        if day in (1, 3):
            b.tick(60)
            b.add(
                biz,
                amount_usd=round(b.rng.uniform(4000, 6000), 2),
                channel="ach",
                merchant="Gusto Payroll",
                merchant_category="payroll",
                counterparty="Gusto Inc",
            )
        # normal cash deposit on day 0 only (well under threshold, one branch)
        if day == 0:
            b.tick(45)
            b.add(
                biz,
                amount_usd=3120.00,
                channel="cash",
                direction="credit",
                merchant=branches[0],
                merchant_category="cash_deposit",
            )
        # structured deposits on days 1-4
        if day >= 1:
            for branch in b.rng.sample(branches, 2 if day < 4 else 3):
                b.tick(b.rng.randint(40, 130))
                b.add(
                    biz,
                    amount_usd=round(b.rng.uniform(9300, 9950), 2),
                    channel="cash",
                    direction="credit",
                    merchant=branch,
                    merchant_category="cash_deposit",
                    is_fraud=True,
                    pattern=Pattern.STRUCTURING,
                )
        for _ in range(3):
            b.everyday(other)
    return b.build(
        "Cash structuring (smurfing)",
        "A small business splits large cash inflows into repeated 9,300 to 9,950 USD "
        "deposits across four branches over four days, each just under the 10,000 USD "
        "CTR threshold. Normal wholesale, payroll and one ordinary deposit are mixed in.",
        Pattern.STRUCTURING,
    )


def tape_impossible_travel() -> Tape:
    b = TapeBuilder("T04-travel", seed=104)
    traveler = b.account(
        "ACC-5001",
        monthly=6100,
        typical=110,
        categories=("travel", "restaurant", "grocery", "fuel"),
    )
    other = b.account("ACC-5002")
    for _ in range(16):
        b.everyday(b.rng.choice([traveler, other]))
    # legitimate trip to London (flight bought days earlier; arrives, spends)
    b.tick(600)
    b.add(
        traveler,
        amount_usd=64.20,
        channel="card_present",
        merchant="Heathrow Express",
        merchant_category="transport",
        city="London",
        country="GB",
    )
    b.tick(95)
    b.add(
        traveler,
        amount_usd=38.75,
        channel="card_present",
        merchant="Pret A Manger",
        merchant_category="restaurant",
        city="London",
        country="GB",
    )
    # clone used in Sao Paulo 22 minutes later while the real card is in London
    b.tick(22)
    b.add(
        traveler,
        amount_usd=712.00,
        channel="card_present",
        merchant="Casas Bahia",
        merchant_category="electronics",
        city="Sao Paulo",
        country="BR",
        is_fraud=True,
        pattern=Pattern.IMPOSSIBLE_TRAVEL,
    )
    b.tick(9)
    b.add(
        traveler,
        amount_usd=389.90,
        channel="card_present",
        merchant="Magazine Luiza",
        merchant_category="electronics",
        city="Sao Paulo",
        country="BR",
        is_fraud=True,
        pattern=Pattern.IMPOSSIBLE_TRAVEL,
    )
    # real customer keeps spending in London
    b.tick(140)
    b.add(
        traveler,
        amount_usd=142.00,
        channel="card_present",
        merchant="Dishoom",
        merchant_category="restaurant",
        city="London",
        country="GB",
    )
    for _ in range(10):
        b.everyday(other)
    return b.build(
        "Impossible travel (counterfeit card)",
        "A customer is legitimately in London. Twenty-two minutes after a card-present "
        "purchase there, the same card is swiped in Sao Paulo twice. The London spend "
        "before and after is real; the two Sao Paulo swipes are a cloned card.",
        Pattern.IMPOSSIBLE_TRAVEL,
    )


def tape_mule_account() -> Tape:
    b = TapeBuilder("T05-mule", seed=105)
    mule = b.account(
        "ACC-6001",
        segment="retail_checking",
        monthly=900,
        typical=22,
        age_days=41,
        categories=("grocery", "restaurant"),
    )
    other = b.account("ACC-6002")
    for _ in range(8):
        b.everyday(b.rng.choice([mule, other]))
    b.tick(300)
    senders = [
        "J. Okafor",
        "M. Reyes",
        "P. Lindqvist",
        "A. Nakamura",
        "S. Dubois",
        "R. Achebe",
        "K. Weber",
    ]
    total_in = 0.0
    for s in senders:
        b.tick(b.rng.randint(8, 40))
        amt = round(b.rng.uniform(800, 2400), 2)
        total_in += amt
        b.add(
            mule,
            amount_usd=amt,
            channel="p2p",
            direction="credit",
            merchant="Zelle Transfer",
            merchant_category="p2p_transfer",
            counterparty=s,
            is_fraud=True,
            pattern=Pattern.MULE_ACCOUNT,
        )
    b.tick(75)
    b.add(
        mule,
        amount_usd=round(total_in * 0.93, 2),
        channel="wire",
        merchant="International Wire",
        merchant_category="wire_transfer",
        counterparty="Meridian Trading FZE (AE)",
        city="Chicago",
        country="US",
        is_fraud=True,
        pattern=Pattern.MULE_ACCOUNT,
    )
    b.tick(30)
    b.add(
        mule,
        amount_usd=round(total_in * 0.05, 2),
        channel="cash",
        merchant="ATM Withdrawal",
        merchant_category="atm",
        is_fraud=True,
        pattern=Pattern.MULE_ACCOUNT,
    )
    for _ in range(8):
        b.everyday(other)
    return b.build(
        "Money mule account",
        "A six-week-old account with 22 USD typical spend receives seven Zelle credits "
        "from unrelated senders totalling around 11,000 USD in three hours, then wires "
        "93 percent of it to a UAE trading company. Everyday spend surrounds it.",
        Pattern.MULE_ACCOUNT,
    )


GENERATORS = [
    tape_clean_baseline,
    tape_account_takeover,
    tape_card_testing,
    tape_structuring,
    tape_impossible_travel,
    tape_mule_account,
]


def generate_all() -> list[Tape]:
    return [g() for g in GENERATORS]


def write_all(directory: Path = TAPES_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    out = []
    for tape in generate_all():
        p = directory / f"{tape.tape_id}.json"
        p.write_text(json.dumps(tape.model_dump(mode="json"), indent=1) + "\n")
        out.append(p)
    return out


def load_tape(path: Path) -> Tape:
    return Tape.model_validate_json(path.read_text())


def load_all(directory: Path = TAPES_DIR) -> list[Tape]:
    return [load_tape(p) for p in sorted(directory.glob("*.json"))]


def find_tape(name: str, directory: Path = TAPES_DIR) -> Tape:
    for t in load_all(directory):
        if (
            t.tape_id == name
            or t.tape_id.lower().startswith(name.lower())
            or name.lower() in t.title.lower()
        ):
            return t
    raise FileNotFoundError(f"no tape matching {name!r} in {directory}")
